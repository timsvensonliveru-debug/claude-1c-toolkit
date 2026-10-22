#!/usr/bin/env python3
"""Какой коммит и какая задача принесли дефект: простой трассировщик по исправляющему коммиту.

Для исправляющего коммита (или диапазона) берём удалённые и изменённые строки, находим по `git blame` коммиты,
которые эти строки ввели, и достаём из их сообщений ключи задач (PROJ-123). Это основа для квартального разбора
пропущенных дефектов: «дефект после релиза → какая задача и какой MR его принесли → как он прошёл ревью».

  python scripts/defect_trace.py --repo . --fix <sha|a..b> [--task-regex "[A-Z][A-Z0-9]+-\\d+"] [--json]
Ограничения: находит автора строки, а не причину; рефакторинг и перенос кода дают шум, смотри на повторяющиеся задачи.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import subprocess
import sys
from pathlib import Path

TASK_RX = r"[A-Z][A-Z0-9]+-\d+"
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")


def _git(repo: Path, *a: str) -> str:
    r = subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(repo), *a], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else ""


def fix_commits(repo: Path, fix: str) -> list[str]:
    spec = fix if ".." in fix else f"{fix}^!"
    return _git(repo, "rev-list", spec).split()


def removed_lines(repo: Path, sha: str) -> list[tuple[str, int, int]]:
    """(файл, с какой строки, сколько) удалённых или изменённых строк в родителе коммита."""
    out, path = [], None
    for ln in _git(repo, "diff", "-U0", "--no-renames", f"{sha}^", sha).splitlines():
        if ln.startswith("--- a/"):
            path = ln[6:]
        elif ln.startswith("--- "):
            path = None
        m = _HUNK.match(ln)
        if m and path:
            n = int(m.group(2)) if m.group(2) is not None else 1
            if n:
                out.append((path, int(m.group(1)), n))
    return out


def trace(repo: Path, fix: str, task_rx: str = TASK_RX) -> dict:
    introduced: collections.Counter = collections.Counter()
    for sha in fix_commits(repo, fix):
        for path, start, n in removed_lines(repo, sha):
            blame = _git(repo, "blame", "--porcelain", "-L", f"{start},+{n}", f"{sha}^", "--", path)
            for ln in blame.splitlines():
                if re.match(r"^[0-9a-f]{40} ", ln):
                    introduced[ln.split()[0]] += 1
    commits, tasks = [], collections.Counter()
    for sha, lines in introduced.most_common():
        subj = _git(repo, "log", "-1", "--format=%an|%as|%s%n%b", sha).strip()
        keys = sorted(set(re.findall(task_rx, subj)))
        for k in keys:
            tasks[k] += lines
        commits.append({"commit": sha[:10], "lines": lines, "tasks": keys, "subject": subj.splitlines()[0][:120]})
    return {"fix": fix, "commits": commits, "tasks": dict(tasks.most_common())}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path.cwd())
    ap.add_argument("--fix", required=True)
    ap.add_argument("--task-regex", default=TASK_RX)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = trace(a.repo, a.fix, a.task_regex)
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(f'Исправление {res["fix"]}: строки принесли коммиты')
        for c in res["commits"]:
            print(f'  {c["commit"]} строк {c["lines"]} задачи {",".join(c["tasks"]) or "не найдены"}  {c["subject"]}')
        print("Задачи по числу строк: " + (", ".join(f"{k} ({v})" for k, v in res["tasks"].items()) or "не найдены"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
