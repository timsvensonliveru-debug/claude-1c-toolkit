#!/usr/bin/env python3
"""Напоминание: новая возможность должна быть отражена в README, CHANGELOG, .env.example и оглавлении документов.

  python scripts/docs_sync_check.py [--staged | --base ORIGIN/MAIN] [--strict] [--audit-env]

По изменению (staged или diff с базой) предупреждает, если:
  * добавлены файлы в scripts/, docs/, core/skills/*/references/, config/, onboard/, а README.md и CHANGELOG.md не тронуты;
  * добавлен документ docs/*.md, на который нет ссылки из README.md;
  * в добавленных строках кода появилась переменная окружения, которой нет в .env.example.
Только предупреждения, код выхода 0 (с --strict при находках 1). `--audit-env` просматривает весь репозиторий
и перечисляет переменные окружения из кода, которых нет в .env.example.
Хук pre-commit запускает проверку без блокировки, если файл найден (рядом, в ONEC_TOOLKIT_DIR или в репозитории).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

WATCH = re.compile(r"^(scripts/|docs/|core/skills/[^/]+/references/|config/|onboard/)")
ENV_RX = re.compile(r"""(?:environ\.get|getenv|environ\[)\(?\s*['"]([A-Z][A-Z0-9_]{2,})['"]""")
IGNORE_ENV = {"PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR", "SHELL", "USER", "USERNAME",
              "PYTHONPATH", "PYTHONIOENCODING", "LANG", "TERM", "CI", "PWD", "OLDPWD", "COMSPEC", "SYSTEMROOT", "PROGRAMFILES",
              "PROGRAMDATA", "HOSTNAME", "XDG_CONFIG_HOME", "CLAUDE_PROJECT_DIR", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "BITRIX_ROLE", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "COMPUTERNAME",
              "http_proxy", "https_proxy"}


def _git(repo: Path, *a: str) -> str:
    r = subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(repo), *a], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else ""


def changes(repo: Path, base: str | None) -> tuple[list[tuple[str, str]], str]:
    args = ["--cached"] if not base else [f"{base}...HEAD"]
    names = _git(repo, "diff", "--name-status", *args)
    rows = [tuple(ln.split("\t", 1)) for ln in names.splitlines() if "\t" in ln]
    diff = _git(repo, "diff", "-U0", *args)
    return rows, diff


def env_names_in(text: str) -> set[str]:
    return {m for m in ENV_RX.findall(text) if m not in IGNORE_ENV}


def documented_env(repo: Path) -> str:
    p = repo / ".env.example"
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""


def check(repo: Path, base: str | None = None) -> list[str]:
    rows, diff = changes(repo, base)
    touched = {path for _, path in rows}
    added = [p for st, p in rows if st.startswith("A")]
    warns = []
    new_feature = [p for p in added if WATCH.match(p) and not p.endswith(("__pycache__",))]
    if new_feature:
        missing = [n for n in ("README.md", "CHANGELOG.md") if n not in touched and (repo / n).is_file()]
        if missing:
            warns.append(f"добавлены файлы ({', '.join(new_feature[:4])}{'…' if len(new_feature) > 4 else ''}), "
                         f"а {' и '.join(missing)} не тронуты: опишите возможность там")
    readme = (repo / "README.md").read_text(encoding="utf-8", errors="replace") if (repo / "README.md").is_file() else ""
    for p in added:
        if p.startswith("docs/") and p.endswith(".md") and "/" not in p[5:] and p[5:] not in readme and p not in readme:
            warns.append(f"документ {p} не упомянут в README.md: добавьте строку в список документации")
    added_code = "\n".join(ln[1:] for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    undocumented = sorted(n for n in env_names_in(added_code) if n not in documented_env(repo))
    if undocumented:
        warns.append("переменные окружения без записи в .env.example: " + ", ".join(undocumented))
    return warns


def audit_env(repo: Path) -> list[str]:
    seen: dict[str, str] = {}
    for d in ("scripts", "mcp", "onec_metadata", "server", "onboard"):
        for f in (repo / d).rglob("*.py") if (repo / d).is_dir() else []:
            for n in env_names_in(f.read_text(encoding="utf-8", errors="replace")):
                seen.setdefault(n, str(f.relative_to(repo)))
    doc = documented_env(repo)
    return [f"{n}  ({where})" for n, where in sorted(seen.items()) if n not in doc]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path.cwd())
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--staged", action="store_true")
    g.add_argument("--base")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--audit-env", action="store_true")
    a = ap.parse_args(argv)
    if a.audit_env:
        miss = audit_env(a.repo)
        print("\n".join(miss) if miss else "все переменные окружения из кода есть в .env.example")
        return 1 if (a.strict and miss) else 0
    warns = check(a.repo, a.base)
    for w in warns:
        print(f"⚠ docs-sync: {w}")
    return 1 if (a.strict and warns) else 0


if __name__ == "__main__":
    sys.exit(main())
