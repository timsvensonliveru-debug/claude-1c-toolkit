#!/usr/bin/env python3
"""Размер merge request для 1С: объекты метаданных и ручные строки BSL, а не строки XML.

Строки выгрузки (XML, .mdo, .dcs) раздувают дифф и ничего не говорят о риске, поэтому считаем:
  * объекты метаданных, которых коснулся MR (по путям файлов EDT и Конфигуратора-XML);
  * ручные строки BSL (добавлено плюс удалено в .bsl);
  * затронутые верхние каталоги (контуры, расширения).
Жёсткого лимита нет: выше порога MR «требует обоснования» (абзац в описании, почему не разбит).

  python scripts/mr_size.py --base origin/main [--repo .] [--max-objects 15] [--max-bsl 400] [--json]
Код выхода всегда 0, если не задан --strict (тогда 1 при превышении порога).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

MAX_OBJECTS = 15
MAX_BSL = 400
# EDT: src/<Каталог>/<Объект>/...  (Catalogs, Documents, CommonModules ...), Конфигуратор: <Каталог>/<Объект>.xml
_KINDS = ("Catalogs|Documents|DocumentJournals|Enums|Reports|DataProcessors|ChartsOfCharacteristicTypes|"
          "ChartsOfAccounts|ChartsOfCalculationTypes|InformationRegisters|AccumulationRegisters|AccountingRegisters|"
          "CalculationRegisters|BusinessProcesses|Tasks|ExchangePlans|CommonModules|CommonForms|CommonCommands|"
          "CommonTemplates|CommonPictures|Roles|Subsystems|SessionParameters|Constants|DefinedTypes|ScheduledJobs|"
          "EventSubscriptions|FunctionalOptions|HTTPServices|WebServices|XDTOPackages|WSReferences|"
          "Справочники|Документы|Перечисления|Отчеты|Обработки|РегистрыСведений|РегистрыНакопления|ОбщиеМодули|Роли")
_OBJ = re.compile(rf"(?:^|/)(?:src/)?(?:[^/]+/)?(?P<kind>{_KINDS})/(?P<name>[^/.]+)", re.I)


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else ""


def measure(repo: Path, base: str) -> dict:
    rng = f"{base}...HEAD"
    objects: set[str] = set()
    tops: set[str] = set()
    bsl = 0
    files = 0
    for ln in _git(repo, "diff", "--numstat", rng).splitlines():
        parts = ln.split("\t")
        if len(parts) != 3:
            continue
        add, dele, path = parts
        files += 1
        path = path.replace("\\", "/")
        m = _OBJ.search(path)
        if m:
            objects.add(f'{m.group("kind")}/{m.group("name")}')
        tops.add(path.split("/")[0])
        if path.lower().endswith(".bsl") and add.isdigit() and dele.isdigit():
            bsl += int(add) + int(dele)
    return {"objects": len(objects), "object_list": sorted(objects), "bsl_lines": bsl, "areas": sorted(tops),
            "files": files}


def verdict(res: dict, max_objects: int = MAX_OBJECTS, max_bsl: int = MAX_BSL) -> tuple[str, list[str]]:
    why = []
    if res["objects"] > max_objects:
        why.append(f'объектов метаданных {res["objects"]} (порог {max_objects})')
    if res["bsl_lines"] > max_bsl:
        why.append(f'ручных строк BSL {res["bsl_lines"]} (порог {max_bsl})')
    return ("требует обоснования" if why else "размер в норме"), why


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path.cwd())
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--max-objects", type=int, default=MAX_OBJECTS)
    ap.add_argument("--max-bsl", type=int, default=MAX_BSL)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args(argv)
    res = measure(a.repo, a.base)
    v, why = verdict(res, a.max_objects, a.max_bsl)
    if a.json:
        print(json.dumps(dict(res, verdict=v, why=why), ensure_ascii=False))
    else:
        print(f'Размер MR: объектов метаданных {res["objects"]}, ручных строк BSL {res["bsl_lines"]}, '
              f'каталогов {len(res["areas"])}, файлов {res["files"]}. Вывод: {v}' + (f' ({"; ".join(why)})' if why else ""))
    return 1 if (a.strict and why) else 0


if __name__ == "__main__":
    sys.exit(main())
