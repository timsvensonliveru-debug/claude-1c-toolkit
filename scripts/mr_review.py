#!/usr/bin/env python3
"""Первый проход по merge request до человека: размер, описание, детерминированные проверки, список P1 и P2.

  python scripts/mr_review.py --base origin/main [--description mr.md] [--tz постановка.md]
                              [--second-opinion] [--risk normal|critical|low] [--json]

Что делает (всё бесплатное и локальное, кроме необязательного второго мнения):
  1. размер по объектам метаданных и ручным строкам BSL (`mr_size.py`);
  2. описание MR: есть ли части «что», «зачем», «как проверить», «риск»;
  3. по изменённым .bsl: `bsl_guard` (обращение к базе в цикле, P1), `rights_guard` (права и RLS, P1 или P2),
     `bsl_ls_top` (частые замечания BSL LS, P2; нужны BSL_JAR и java, иначе молча пропускается);
  4. с `--second-opinion`: запрос второй модели с диффом, постановкой и чек-листом (`second_opinion.py ask`).
Код выхода 1, если есть P1; иначе 0. Вывод короткий: только находки и их место.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mr_size  # noqa: E402

CHECKLIST = HERE.parent / "core" / "skills" / "1c-dev" / "references" / "review-checklist.md"
PARTS = {"что": ("что", "суть", "изменения"), "зачем": ("зачем", "цель", "задача"),
         "как проверить": ("как проверить", "проверка", "тест"), "риск": ("риск", "откат")}


def _git(repo: Path, *a: str) -> str:
    r = subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(repo), *a], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else ""


def changed_bsl(repo: Path, base: str) -> list[Path]:
    out = _git(repo, "diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD")
    return [repo / p for p in out.splitlines() if p.lower().endswith(".bsl") and (repo / p).is_file()]


def description_gaps(text: str) -> list[str]:
    low = (text or "").casefold()
    return [part for part, keys in PARTS.items() if not any(k in low for k in keys)]


def _run(script: str, *args: str) -> tuple[int, str]:
    try:
        r = subprocess.run([sys.executable, str(HERE / script), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
        return r.returncode, (r.stdout or "").strip()
    except Exception:
        return 0, ""


def findings(repo: Path, base: str, description: str | None, max_objects: int, max_bsl: int) -> tuple[list, dict]:
    out: list[tuple[str, str, str]] = []                # (P1|P2, место, суть)
    res = mr_size.measure(repo, base)
    v, why = mr_size.verdict(res, max_objects, max_bsl)
    if why and not (description and "обоснован" in description.casefold()):
        out.append(("P2", "MR", f"размер требует обоснования: {'; '.join(why)}. Добавьте абзац, почему не разбито на части"))
    if description is not None:
        gaps = description_gaps(description)
        if gaps:
            out.append(("P2", "описание MR", "не хватает частей: " + ", ".join(gaps)))
    for f in changed_bsl(repo, base):
        rel = os.path.relpath(f, repo)
        rc, o = _run("bsl_guard.py", str(f))
        if rc != 0 and o:
            out.append(("P1", rel, "обращение к базе в цикле: " + o.splitlines()[0][:160]))
        rc, o = _run("rights_guard.py", "--json", str(f))
        try:
            for it in json.loads(o) if o else []:
                out.append(("P1" if it.get("severity") == "error" else "P2", f'{rel}:{it.get("line")}',
                            f'права [{it.get("code")}] {it.get("message")}'))
        except ValueError:
            pass
        _, o = _run("bsl_ls_top.py", str(f))
        for ln in o.splitlines():
            out.append(("P2", f"{rel} ({ln.split(' ')[0]})", ln.split(' ', 1)[1] if ' ' in ln else ln))
    out.sort(key=lambda x: x[0])
    return out, res


def second_opinion_prompt(description: str | None, has_tz: bool) -> str:
    return ("Ты проверяющий merge request на 1С:Предприятие. Во вложениях дифф, чек-лист ревью"
            + (", постановка задачи" if has_tz else "") + (" и описание MR" if description else "") + ". "
            "Найди дефекты и риски по приоритетам. P1 блокирует слияние: потеря данных, обход прав, запрос в цикле, "
            "транзакции, необработанные ошибки обменов и заданий, правка типовой. P2 чинить до релиза. "
            "По каждой находке дай файл и место, причину и как исправить. Не пиши, что ошибок нет, пока не прошёл "
            "каждый раздел чек-листа; по разделам, где проблем не нашёл, напиши одну строку, что именно проверено. "
            "Сверяй дифф с постановкой: всё ли требуемое сделано и нет ли лишнего.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path.cwd())
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--description", type=Path)
    ap.add_argument("--tz", type=Path)
    ap.add_argument("--max-objects", type=int, default=mr_size.MAX_OBJECTS)
    ap.add_argument("--max-bsl", type=int, default=mr_size.MAX_BSL)
    ap.add_argument("--second-opinion", action="store_true")
    ap.add_argument("--risk", choices=["critical", "normal", "low"], default="normal")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    desc = a.description.read_text(encoding="utf-8") if a.description and a.description.is_file() else None
    fl, res = findings(a.repo, a.base, desc, a.max_objects, a.max_bsl)
    if a.json:
        print(json.dumps({"size": res, "findings": fl}, ensure_ascii=False))
    else:
        print(f'Размер: объектов {res["objects"]}, строк BSL {res["bsl_lines"]}.')
        if not fl:
            print("Детерминированные проверки: находок нет (это не значит, что ошибок нет: нужен человек и ревью по чек-листу).")
        for p, where, what in fl:
            print(f"{p}  {where}  {what}")
    if a.second_opinion:
        import second_opinion
        diff = Path(os.environ.get("TMPDIR", "/tmp")) / "mr_review.diff"
        diff.write_text(_git(a.repo, "diff", f"{a.base}...HEAD"), encoding="utf-8")
        files = [str(diff), str(CHECKLIST)] + ([str(a.tz)] if a.tz else []) + ([str(a.description)] if desc else [])
        who, body = second_opinion.ask(second_opinion_prompt(desc, bool(a.tz)), files=files, risk=a.risk)
        print(f"\n== Второе мнение ({who}) ==\n{body}" if body else "\nВторое мнение: никто не ответил (см. second_opinion.py status)")
    return 1 if any(p == "P1" for p, _, _ in fl) else 0


if __name__ == "__main__":
    sys.exit(main())
