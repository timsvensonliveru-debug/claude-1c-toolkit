#!/usr/bin/env python3
"""Необязательная проверка: BSL Language Server по изменённому .bsl, показываются только самые частые ключи.

Те же ключи, на которые ругается корпоративный SonarQube (см. core/skills/1c-dev/references/sonar-top-rules.md).
Работает, только если есть jar (`BSL_JAR`) и java (PATH, `JAVA` или JDK из 1C:EDT). Нет чего-то из этого
или сбой — тихий выход 0, работа не ломается. Отказаться совсем: `AI_TOOLKIT_BSLLS_GATE=off`.

  python scripts/bsl_ls_top.py <файл.bsl> [...]   # печатает находки, код 0
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

TOP = {"QueryNestedFieldsByDot", "MissingParameterDescription", "MissingReturnedValueDescription",
       "CognitiveComplexity", "CyclomaticComplexity", "UnusedLocalMethod", "JoinWithVirtualTable",
       "EmptyCodeBlock", "IfElseIfEndsWithElse"}
TIMEOUT = 45


def _java():
    j = os.environ.get("JAVA") or shutil.which("java")
    if j:
        return j
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import detect_tools
        p, _ = detect_tools.find_jdk_java()
        return str(p) if p else None
    except Exception:
        return None


def pick(report: dict) -> list[str]:
    out = []
    for fi in report.get("fileinfos", []):
        for d in fi.get("diagnostics", []):
            if d.get("code") in TOP:
                ln = d.get("range", {}).get("start", {}).get("line", 0) + 1
                out.append(f"{os.path.basename(fi.get('path', '?'))}:{ln} [{d['code']}] {(d.get('message') or '').strip()}")
    return out


def analyze(path: str) -> list[str]:
    jar = os.environ.get("BSL_JAR", "")
    java = _java()
    if os.environ.get("AI_TOOLKIT_BSLLS_GATE", "").lower() == "off" or not (jar and os.path.isfile(jar) and java):
        return []
    src, out = tempfile.mkdtemp(prefix="bsl_top_src_"), tempfile.mkdtemp(prefix="bsl_top_")
    try:
        shutil.copy2(path, os.path.join(src, os.path.basename(path)))
        cmd = [java, "-Dfile.encoding=UTF-8", "-jar", jar, "analyze", "-s", ".", "-o", out, "-r", "json", "-q"]
        cfg = os.environ.get("BSL_CONFIG", "")
        if cfg and os.path.isfile(cfg):
            cmd += ["-c", cfg]
        subprocess.run(cmd, cwd=src, capture_output=True, timeout=TIMEOUT)
        reports = glob.glob(os.path.join(out, "*.json"))
        if not reports:
            return []
        with open(reports[0], encoding="utf-8", errors="replace") as f:
            return pick(json.load(f))
    except Exception:
        return []
    finally:
        shutil.rmtree(src, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


def main(argv) -> int:
    lines = [x for p in argv if p.lower().endswith(".bsl") and os.path.isfile(p) for x in analyze(p)]
    if lines:
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
