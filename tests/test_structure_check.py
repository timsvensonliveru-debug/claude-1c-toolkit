"""structure_check: когда раздел пора группировать."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import structure_check as sc  # noqa: E402


def test_short_homogeneous_section_untouched():
    assert sc.analyze("Раздел", ["Постановка 1", "Постановка 2", "План"])["verdict"] == "не трогать"


def test_mixed_section_gets_grouped():
    names = ["Архитектура обмена", "Решение по справочникам", "Итоги встречи 01.10", "Технический проект модуля",
             "Технический проект сервиса", "Контракт API и пакет", "Задача разработчику на октябрь",
             "Постановка аналитику", "План, оценка и риски", "Руководство аналитику", "Факты по коду"]
    r = sc.analyze("Интеграция", names)
    assert r["verdict"] == "сгруппировать" and len(r["groups"]) >= 3


def test_many_unknown_names_ask_to_rename():
    assert sc.analyze("Папка", [f"Файл {i}" for i in range(10)])["verdict"] == "уточнить названия"


def test_same_type_long_list_not_grouped():
    r = sc.analyze("Постановки", [f"Постановка {i}" for i in range(12)])
    assert r["verdict"] == "не трогать" and r["too_big"] == ["Задачи и планирование"]
