# -*- coding: utf-8 -*-
"""Когда раздел пора группировать: критическая масса разнородных элементов.

Когда в одном месте (раздел Confluence, папка, каталог репозитория) набирается много элементов разных типов,
их полезно группировать по типу материала. Без лишнего дробления: однородное и короткое не трогаем.

Пороги (подстраиваются аргументами):
  * меньше MIN_ITEMS (8) элементов: ничего не предлагаем;
  * от 8: предлагаем группировку, если типов материала не меньше двух и в каждом из них не меньше двух элементов;
  * в группе больше MAX_GROUP (9): предлагаем разбить дальше;
  * глубина не больше 3 уровней (проверяет вызывающий).
Тип определяется по названию (ключевые слова ниже). Элемент без типа идёт в «Прочее»: если Прочее больше трети,
нужно дать названиям понятный тип, а не группировать.
CLI: python scripts/structure_check.py "Название раздела" "Страница 1" "Страница 2" ...  или  --stdin (по строке на элемент).
"""
from __future__ import annotations

import re
import sys

MIN_ITEMS = 8
MAX_GROUP = 9

# порядок важен: первое совпавшее правило задаёт тип
CATEGORIES = [
    ("Задачи и планирование", r"постановк|задач[аи]|план\b|план,|оценк|риск|бэклог|спринт|roadmap|дорожн"),
    ("Архитектура и решения", r"архитектур|итоги встреч|решени|протокол|adr\b|протокол"),
    ("Техпроекты и контракты", r"техпроект|технический проект|контракт|\bapi\b|шлюз|интерфейс|openapi|swagger|схем"),
    ("Руководства и справочные материалы", r"руководств|инструкци|шаблон|регламент|faq|предыстори|факты|справочн|памятк|чек-?лист|состав данных"),
    ("Журналы и отчёты", r"журнал|отч[её]т|сводк|лог\b|аудит|ревью|анализ"),
]
OTHER = "Прочее"


def categorize(name: str) -> str:
    low = name.casefold().replace("ё", "е")
    for cat, rx in CATEGORIES:
        if re.search(rx.replace("ё", "е"), low):
            return cat
    return OTHER


def analyze(section: str, names: list[str], min_items: int = MIN_ITEMS, max_group: int = MAX_GROUP) -> dict:
    groups: dict[str, list[str]] = {}
    for n in names:
        groups.setdefault(categorize(n), []).append(n)
    typed = {k: v for k, v in groups.items() if k != OTHER}
    big_other = len(groups.get(OTHER, [])) * 3 > len(names)
    ok_groups = [k for k, v in typed.items() if len(v) >= 2]
    if len(names) < min_items:
        verdict, why = "не трогать", f"элементов {len(names)}, меньше порога {min_items}"
    elif big_other:
        verdict, why = "уточнить названия", "больше трети элементов без понятного типа в названии: сначала переименовать"
    elif len(ok_groups) >= 2:
        verdict, why = "сгруппировать", f"элементов {len(names)}, разных типов {len(ok_groups)}"
    else:
        verdict, why = "не трогать", "элементы однородны или типов меньше двух с парой элементов"
    split = [k for k, v in groups.items() if len(v) > max_group]
    return {"section": section, "count": len(names), "verdict": verdict, "why": why,
            "groups": {k: v for k, v in groups.items() if k in ok_groups or k == OTHER},
            "too_big": split}


def report(res: dict) -> str:
    out = [f'{res["section"]}: {res["verdict"]} ({res["why"]})']
    if res["verdict"] == "сгруппировать":
        for i, (k, v) in enumerate(res["groups"].items(), 1):
            out.append(f"  {i} {k} ({len(v)}): " + "; ".join(x[:50] for x in v))
        out.append("  Группа минимум из двух страниц, не больше девяти, глубина до трёх уровней.")
    if res["too_big"]:
        out.append("  Слишком большие группы, разбить дальше: " + ", ".join(res["too_big"]))
    return "\n".join(out)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--stdin":
        names = [ln.strip() for ln in sys.stdin if ln.strip()]
        section = args[1] if len(args) > 1 else "раздел"
    else:
        section, names = (args[0] if args else "раздел"), args[1:]
    print(report(analyze(section, names)))
