#!/usr/bin/env python3
"""Оценочный стенд агента: задачи с проверяемым признаком верного ответа, прогон агента, таблица по темам.

Каждая задача лежит в tests/agent_eval/tasks/<id>.json: промпт агенту, проверки ответа регулярными выражениями
(`must`, все должны найтись; `must_not`, ни одно не должно) и тема. Проверки детерминированные: они ловят наличие
ключевых элементов верного ответа, а не оценивают текст целиком. Провал по теме сигнал: справочник или правило
требуют усиления.

  python scripts/agent_eval.py list
  python scripts/agent_eval.py check <id> <файл-с-ответом>          # проверить готовый ответ без запуска агента
  python scripts/agent_eval.py run [--task ID ...] [--repeat 3] [--cmd "claude -p"] [--timeout 300] [--bare]

Запуск агента: команда из --cmd или AGENT_EVAL_CMD (по умолчанию `claude -p`), промпт идёт в STDIN, ответ из STDOUT,
рабочий каталог корень набора (скиллы подхватываются из .claude/skills); `--bare` запускает в пустом каталоге, без
наших скиллов: так видно, что именно даёт набор (сравните две таблицы). Результаты: ~/.operkontur/agent_eval/<время>.json.
Прогон тратит токены агента. Ничего не публикует, фикстур с данными организаций нет.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "tests" / "agent_eval" / "tasks"


def load_tasks(ids=None) -> list[dict]:
    tasks = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(TASKS.glob("*.json"))]
    return [t for t in tasks if not ids or t["id"] in ids]


def grade(task: dict, answer: str) -> dict:
    missing = [rx for rx in task.get("must", []) if not re.search(rx, answer, re.I | re.S)]
    forbidden = [rx for rx in task.get("must_not", []) if re.search(rx, answer, re.I | re.S)]
    return {"passed": bool(answer.strip()) and not missing and not forbidden, "missing": missing, "forbidden": forbidden}


def run_agent(cmd: str, prompt: str, timeout: int, cwd: Path = ROOT) -> str:
    try:
        r = subprocess.run(shlex.split(cmd), input=prompt, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, cwd=str(cwd))
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"[ошибка запуска: {type(e).__name__}]"
    return r.stdout if r.returncode == 0 else f"[код {r.returncode}] {(r.stderr or '')[-300:]}"


def summarize(results: list[dict]) -> str:
    by = collections.defaultdict(lambda: [0, 0])
    for r in results:
        by[r["topic"]][1] += 1
        by[r["topic"]][0] += 1 if r["passed"] else 0
    lines = [f'{t:34} {p}/{n}' for t, (p, n) in sorted(by.items())]
    ok = sum(p for p, _ in by.values())
    total = sum(n for _, n in by.values())
    return "\n".join(lines + [f"{'ИТОГО':34} {ok}/{total}"])


def main(argv=None, runner=run_agent) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    ck = sub.add_parser("check")
    ck.add_argument("id")
    ck.add_argument("file")
    rn = sub.add_parser("run")
    rn.add_argument("--task", action="append")
    rn.add_argument("--repeat", type=int, default=1)
    rn.add_argument("--cmd", default=os.environ.get("AGENT_EVAL_CMD", "claude -p"))
    rn.add_argument("--timeout", type=int, default=300)
    rn.add_argument("--bare", action="store_true", help="пустой рабочий каталог: агент без скиллов набора")
    rn.add_argument("--out", type=Path, default=Path.home() / ".operkontur" / "agent_eval")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        for t in load_tasks():
            print(f'{t["id"]:26} {t["topic"]}')
        return 0
    if a.cmd == "check":
        t = load_tasks([a.id])[0]
        g = grade(t, Path(a.file).read_text(encoding="utf-8"))
        print(("ПРОЙДЕНО" if g["passed"] else "НЕ ПРОЙДЕНО"), g["missing"] or "", g["forbidden"] or "")
        return 0 if g["passed"] else 1
    results = []
    import tempfile
    bare = Path(tempfile.mkdtemp(prefix="agent_eval_bare_")) if a.bare else None
    for t in load_tasks(a.task):
        for i in range(a.repeat):
            ans = runner(a.cmd, t["prompt"], a.timeout, bare) if bare else runner(a.cmd, t["prompt"], a.timeout)
            g = grade(t, ans)
            results.append({"id": t["id"], "topic": t["topic"], "run": i + 1, "answer": ans, **g})
            print(f'{t["id"]:26} #{i + 1} {"ok" if g["passed"] else "НЕТ"} {g["missing"] or ""}')
    a.out.mkdir(parents=True, exist_ok=True)
    path = a.out / f'{dt.datetime.now():%Y%m%d-%H%M%S}{"-bare" if a.bare else ""}.json'
    path.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    print("\n" + summarize(results) + f"\nРезультаты: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
