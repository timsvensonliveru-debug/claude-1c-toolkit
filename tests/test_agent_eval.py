"""agent_eval: проверки ответов, сводка по темам, прогон с подменой агента. Без вызова модели."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import agent_eval as ev  # noqa: E402


def test_all_tasks_are_wellformed():
    tasks = ev.load_tasks()
    assert len(tasks) >= 10
    ids = [t["id"] for t in tasks]
    assert len(ids) == len(set(ids))
    for t in tasks:
        assert t["prompt"] and t["topic"] and t["must"], t["id"]
        for rx in t["must"] + t.get("must_not", []):
            __import__("re").compile(rx)


def test_grade_requires_all_must_and_no_forbidden():
    t = {"must": [r"ЗагрузитьНастройки", r"копи"], "must_not": [r"ПолучитьНастройки\(\)\.Отбор"]}
    assert ev.grade(t, "ПолучитьНастройки возвращает копию, нужен ЗагрузитьНастройки")["passed"]
    assert not ev.grade(t, "нужен ЗагрузитьНастройки")["passed"]
    assert not ev.grade(t, "копия, ЗагрузитьНастройки, но ПолучитьНастройки().Отбор")["passed"]
    assert not ev.grade(t, "")["passed"]


def test_each_task_passes_its_own_reference_keywords():
    """Для каждой задачи собираем ответ из ключевых слов её проверок: проверки сами себе не противоречат."""
    for t in ev.load_tasks():
        sample = " ".join(rx.replace("\\", "").split("|")[0] for rx in t["must"])
        assert ev.grade(t, sample)["passed"], t["id"]


def test_run_with_fake_agent_and_summary(tmp_path, capsys):
    def fake(cmd, prompt, timeout, cwd=None):
        return "ВЫРАЗИТЬ и соединение" if "составного" in prompt else "ничего"
    rc = ev.main(["run", "--task", "composite-deref", "--task", "null-vs-undefined", "--out", str(tmp_path)], runner=fake)
    out = capsys.readouterr().out
    assert rc == 0 and "ИТОГО" in out and "1/2" in out
    saved = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert {r["id"]: r["passed"] for r in saved} == {"composite-deref": True, "null-vs-undefined": False}


def test_agent_failure_is_reported_not_raised(tmp_path):
    out = ev.run_agent("/nonexistent/binary", "x", 5)
    assert out.startswith("[ошибка запуска")
