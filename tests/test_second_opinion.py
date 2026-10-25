#!/usr/bin/env python3
"""second_opinion и codex_setup: цепочка по порядку, пропуск ненастроенного, без ПД в модель без обучения.

Запуск:  python3 tests/test_second_opinion.py   или   pytest tests/test_second_opinion.py
"""
import json
import os
import sys
import tempfile

import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import codex_setup as cs  # noqa: E402
import second_opinion as so  # noqa: E402


def test_chain_order():
    calls = []

    def mk(name, ans):
        def f(prompt, files=()):
            calls.append(name)
            return ans
        return f
    no_codex = lambda *a, **k: None  # noqa: E731
    assert so.ask("q", chain=(("a", mk("a", None)), ("b", mk("b", "ответ"))), codex=no_codex) == ("b", "ответ")
    assert calls == ["a", "b"]
    assert so.ask("q", chain=(("a", mk("a", None)),), codex=no_codex) == ("", "")
    assert so.ask("q", chain=(), codex=lambda *a, **k: "codex ok") == ("codex", "codex ok")


def test_pii_goes_only_to_trusted_assistant():
    saved = dict(os.environ)
    try:
        seen = []
        trusted = lambda p, files=(): seen.append(p) or "ответ"  # noqa: E731
        outside = lambda p, files=(): seen.append("ВНЕШНИЙ") or "ответ"  # noqa: E731
        codex_called = []
        codex = lambda *a, **k: codex_called.append(1) or "codex"  # noqa: E731
        chain = (("copilot", trusted), ("notrain", outside))
        pii_texts = ("пиши ivan@example.com", "звони +375 29 123-45-67", "+1 (415) 555-2671", "+44 20 7946 0958",
                     "пиши иван@почта.рф", "8 029 123 45 67", "(415) 555-2671", "029 123-45-67",
                     "8(029)123-45-67", "80291234567", "+375291234567")
        os.environ.pop("AI_TOOLKIT_COPILOT_CMD", None)
        for text in pii_texts:                              # доверенного нет — отказ, никому не уходит
            who, why = so.ask(text, chain=chain, codex=codex)
            assert who == "" and "доверенный" in why, text
        assert not codex_called and not seen
        os.environ["AI_TOOLKIT_COPILOT_CMD"] = "x"
        for text in pii_texts:                              # есть доверенный — только ему, текст без правок
            seen.clear()
            assert so.ask(text, chain=chain, codex=codex)[0] == "copilot", text
            assert seen == [text]
        assert not codex_called
        silent = (("copilot", lambda p, files=(): None), ("notrain", outside))   # доверенный молчит — Codex всё равно нельзя
        for text in pii_texts:
            seen.clear()
            assert so.ask(text, chain=silent, codex=codex) == ("", ""), text
        assert not codex_called and not seen
        for text in ("версия 8.3.27.1606, задача DEV1C-2476", "дата 2026-10-12", "строки 120-135"):
            assert not so.has_pii(text), text
        assert so.ask("без данных", chain=(), codex=codex)[0] == "codex"
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_risk_order_and_files():
    saved = dict(os.environ)
    os.environ.pop("AI_TOOLKIT_COPILOT_CMD", None)
    calls = []

    def mk(n):
        return lambda p, files=(): calls.append((n, tuple(files))) or None
    chain = (("copilot", mk("copilot")), ("notrain", mk("notrain")))
    cdx = lambda *a, **k: calls.append(("codex", ())) or None  # noqa: E731
    for risk, want in (("critical", ["codex", "copilot", "notrain"]), ("normal", ["copilot", "codex", "notrain"]),
                       ("low", ["copilot", "notrain"])):
        calls.clear()
        so.ask("q", chain=chain, codex=cdx, risk=risk)
        assert [c[0] for c in calls] == want, risk
    with tempfile.TemporaryDirectory() as t:
        ok = Path(t) / "a.md"
        ok.write_text("текст без данных")
        bad = Path(t) / "b.md"
        bad.write_text("пиши ivan@example.com")
        calls.clear()
        assert so.ask("q", chain=chain, codex=cdx, files=[str(ok)], risk="low")[0] == ""
        assert calls[0][1] == (str(ok.resolve()),)           # вложение дошло до звена
        os.environ.pop("AI_TOOLKIT_COPILOT_CMD", None)
        who, why = so.ask("q", chain=chain, codex=cdx, files=[str(bad)])
        assert who == "" and "доверенный" in why              # файл с ПД без доверенного ассистента не уходит никому
        assert so.ask("q", chain=chain, codex=cdx, files=[str(Path(t) / "нет.md")])[1].startswith("вложения нет")
    os.environ.clear()
    os.environ.update(saved)


def test_copilot_default_needs_login():
    saved = dict(os.environ)
    try:
        with tempfile.TemporaryDirectory() as t:
            os.environ.pop("AI_TOOLKIT_COPILOT_CMD", None)
            os.environ["HOME"] = os.environ["USERPROFILE"] = t
            import importlib
            importlib.reload(so)
            assert so.copilot_cmd() == ""                     # вход не выполнен — звена нет
            prof = Path(t) / ".cache" / "ai_toolkit_copilot_profile"
            prof.mkdir(parents=True)
            (prof / "Default").write_text("x")
            if so.shutil.which("uv"):
                assert "copilot_adapter.py" in so.copilot_cmd()   # вошли — адаптер по умолчанию
            os.environ["AI_TOOLKIT_COPILOT_CMD"] = "mycmd"
            assert so.copilot_cmd() == "mycmd"                # явная команда главнее
    finally:
        os.environ.clear()
        os.environ.update(saved)
        import importlib
        importlib.reload(so)


def test_split_diff():
    diff = "".join(f"diff --git a/f{i} b/f{i}\n" + "x" * 50 + "\n" for i in range(10))
    parts = so.split_diff(diff, limit=200)
    assert "".join(parts) == diff and all(len(p) <= 200 for p in parts)


def test_notrain_gates():
    saved = dict(os.environ)
    try:
        os.environ.pop("OPENROUTER_API_KEY", None)
        assert so.notrain("q") is None                     # нет ключа
        os.environ["OPENROUTER_API_KEY"] = "k"
        os.environ.pop("AI_TOOLKIT_NOTRAIN", None)
        assert so.notrain("q") is None                     # нет явного разрешения
        os.environ["AI_TOOLKIT_NOTRAIN"] = "1"
        os.environ["AI_TOOLKIT_NOTRAIN_MODEL"] = "x/model:free"
        import importlib
        importlib.reload(so)
        assert so.notrain("q") is None                     # бесплатная модель запрещена
        os.environ["AI_TOOLKIT_NOTRAIN_MODEL"] = "openrouter/free"
        importlib.reload(so)
        assert so.notrain("q") is None
        os.environ.pop("AI_TOOLKIT_NOTRAIN_MODEL")
        importlib.reload(so)
        os.environ.pop("AI_TOOLKIT_COPILOT_CMD", None)
        assert so.copilot("q") is None                     # Copilot не настроен
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_collect_diff_sees_worktree_and_merge():
    import subprocess as sp
    with tempfile.TemporaryDirectory() as t:
        g = lambda *a: sp.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=t,  # noqa: E731
                              capture_output=True, text=True, check=True).stdout
        g("init", "-q", "-b", "main")
        Path(t, "a.txt").write_text("1\n")
        g("add", "-A"); g("commit", "-qm", "base")
        g("checkout", "-qb", "f"); Path(t, "b.txt").write_text("2\n"); g("add", "-A"); g("commit", "-qm", "f")
        g("checkout", "-q", "main"); g("merge", "-q", "--no-ff", "f", "-m", "merge")
        Path(t, "new.txt").write_text("не добавлен\n")
        assert "new.txt" in so.collect_diff(Path(t), "HEAD", None)          # новый файл виден
        assert "b.txt" in so.collect_diff(Path(t), None, "HEAD")       # merge — к первому родителю
        Path(t, "a.txt").write_text("changed\n")                       # незакоммиченная правка
        assert "changed" in so.collect_diff(Path(t), "HEAD", None)


def test_codex_setup_respects_opt_out():
    saved = dict(os.environ)
    try:
        with tempfile.TemporaryDirectory() as t:
            os.environ["CLAUDE_CONFIG_DIR"] = t
            assert not cs.plugin_disabled()
            (Path(t) / "settings.json").write_text(json.dumps({"enabledPlugins": {cs.PLUGIN: False}}))
            assert cs.plugin_disabled()
    finally:
        os.environ.clear()
        os.environ.update(saved)


if __name__ == "__main__":
    test_chain_order()
    test_pii_goes_only_to_trusted_assistant()
    test_risk_order_and_files()
    test_copilot_default_needs_login()
    test_split_diff()
    test_notrain_gates()
    test_collect_diff_sees_worktree_and_merge()
    test_codex_setup_respects_opt_out()
    print("OK: second_opinion")


def test_copilot_roundtrip_utf8_even_under_cp1251_locale(monkeypatch, tmp_path):
    """Windows с русской локалью: вопрос и ответ идут байтами UTF-8, а не в cp1251 (инцидент «модель смотрела мусор»)."""
    child = tmp_path / "echo_child.py"
    child.write_text("import sys\nd = sys.stdin.buffer.read()\nsys.stdout.buffer.write(d)\n", encoding="utf-8")
    # локаль дочернего процесса притворяется cp1251: без явного UTF-8 текст не пережил бы поездку
    monkeypatch.setenv("PYTHONIOENCODING", "cp1251")
    monkeypatch.setenv("PYTHONUTF8", "0")
    monkeypatch.setenv("AI_TOOLKIT_COPILOT_CMD", f'"{sys.executable}" "{child}"')
    prompt = "Проверь функцию КоличествоСтрок: русский текст и 🙂 не должны ломаться"
    assert so.copilot(prompt) == prompt


def test_utf8_env_forces_python_utf8():
    env = so.utf8_env()
    assert env["PYTHONUTF8"] == "1" and env["PYTHONIOENCODING"] == "utf-8"


def test_read_stdin_utf8_reads_bytes_not_locale(monkeypatch):
    import io

    class FakeStdin:
        buffer = io.BytesIO("Вопрос на кириллице".encode("utf-8"))
    monkeypatch.setattr(sys, "stdin", FakeStdin())
    assert so.read_stdin_utf8() == "Вопрос на кириллице"


def test_adapter_reads_stdin_as_utf8(monkeypatch):
    import io
    import copilot_adapter as ca

    class FakeStdin:
        buffer = io.BytesIO("Проверка".encode("utf-8"))
    monkeypatch.setattr(sys, "stdin", FakeStdin())
    assert ca.read_stdin_utf8() == "Проверка"


def test_status_reports_reason_and_probe(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("AI_TOOLKIT_COPILOT_CMD", raising=False)
    monkeypatch.setattr(so, "COPILOT_PROFILE", tmp_path / "нет_профиля")
    so.status()
    out = capsys.readouterr().out
    assert "не настроен" in out and ("вход не выполнен" in out or "uv" in out)
    # проба: ответ с нужным словом проходит, мусор нет
    monkeypatch.setenv("AI_TOOLKIT_COPILOT_CMD", "x")
    monkeypatch.setattr(so, "copilot", lambda prompt, files=(): "проверка")
    assert so.probe_copilot()[0] is True
    monkeypatch.setattr(so, "copilot", lambda prompt, files=(): "РїСЂРѕРІРµСЂРєР°")
    ok, msg = so.probe_copilot()
    assert ok is False and "кодировк" in msg
    monkeypatch.setattr(so, "copilot", lambda prompt, files=(): None)
    assert so.probe_copilot()[0] is False


def test_git_diff_is_decoded_as_utf8(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    f = tmp_path / "м.bsl"
    f.write_text("Процедура Тест()\nКонецПроцедуры\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"], check=True)
    f.write_text("Процедура Тест2()\nКонецПроцедуры\n", encoding="utf-8")
    d = so.collect_diff(tmp_path, "HEAD", None)
    assert "Процедура Тест2" in d and "м.bsl" in d


def test_adapter_hidden_by_default_and_visible_flag(monkeypatch):
    import copilot_adapter as ca
    monkeypatch.delenv("AI_TOOLKIT_COPILOT_VISIBLE", raising=False)
    assert ca._headless_default() is True
    monkeypatch.setenv("AI_TOOLKIT_COPILOT_VISIBLE", "1")
    assert ca._headless_default() is False


def test_adapter_shows_window_only_when_login_needed(monkeypatch, tmp_path):
    import copilot_adapter as ca
    calls = []
    monkeypatch.delenv("AI_TOOLKIT_COPILOT_NO_LOGIN_WINDOW", raising=False)
    monkeypatch.setattr(ca, "_can_show_window", lambda: True)
    # 1) обычная работа: сессия есть, окно не открывается, режим скрытый
    prof = tmp_path / "profile"
    prof.mkdir()
    monkeypatch.setattr(ca, "PROFILE_DIR", prof)
    monkeypatch.setattr(ca, "_login_visible", lambda reason: calls.append(("login", reason)) or True)
    monkeypatch.setattr(ca, "_ask_once", lambda *a: calls.append(("ask", a[3])) or "ответ")
    assert ca.ask("в", headless=None) == "ответ"
    assert calls == [("ask", True)]
    # 2) ошибка аутентификации: окно входа, потом повтор скрыто
    calls.clear()
    state = {"n": 0}

    def flaky(*a):
        state["n"] += 1
        calls.append(("ask", a[3]))
        if state["n"] == 1:
            raise ca.AuthError("страница входа")
        return "после входа"
    monkeypatch.setattr(ca, "_ask_once", flaky)
    assert ca.ask("в") == "после входа"
    assert [c[0] for c in calls] == ["ask", "login", "ask"] and calls[2] == ("ask", True)
    # 3) первого входа не было: окно до вопроса
    calls.clear()
    monkeypatch.setattr(ca, "PROFILE_DIR", tmp_path / "нет")
    monkeypatch.setattr(ca, "_ask_once", lambda *a: calls.append(("ask", a[3])) or "ок")
    assert ca.ask("в") == "ок" and calls[0][0] == "login"


def test_adapter_no_window_in_headless_environments(monkeypatch, tmp_path):
    import copilot_adapter as ca
    monkeypatch.setattr(ca, "PROFILE_DIR", tmp_path / "нет")
    monkeypatch.setattr(ca, "_can_show_window", lambda: False)
    monkeypatch.setattr(ca, "_login_visible", lambda r: (_ for _ in ()).throw(AssertionError("окно не должно открываться")))
    with pytest.raises(RuntimeError, match="Нет сессии"):
        ca.ask("в")
    prof = tmp_path / "p"
    prof.mkdir()
    monkeypatch.setattr(ca, "PROFILE_DIR", prof)
    monkeypatch.setattr(ca, "_ask_once", lambda *a: (_ for _ in ()).throw(ca.AuthError("вход")))
    with pytest.raises(RuntimeError, match="--login"):
        ca.ask("в")
    monkeypatch.setenv("AI_TOOLKIT_COPILOT_NO_LOGIN_WINDOW", "1")
    monkeypatch.undo()
    monkeypatch.setenv("AI_TOOLKIT_COPILOT_NO_LOGIN_WINDOW", "1")
    assert ca._can_show_window() is False


def test_adapter_login_url_detection():
    import copilot_adapter as ca
    assert ca._LOGIN_URL.search("https://login.microsoftonline.com/common/oauth2/v2.0/authorize?x=1")
    assert not ca._LOGIN_URL.search("https://m365.cloud.microsoft/chat")


def test_adapter_forced_headless_without_window_and_second_auth_error_is_final(monkeypatch, tmp_path):
    import copilot_adapter as ca
    seen = []
    prof = tmp_path / "p"
    prof.mkdir()
    monkeypatch.setattr(ca, "PROFILE_DIR", prof)
    monkeypatch.setattr(ca, "_can_show_window", lambda: False)
    monkeypatch.setenv("AI_TOOLKIT_COPILOT_VISIBLE", "1")
    monkeypatch.setattr(ca, "_ask_once", lambda *a: seen.append(a[3]) or "ок")
    assert ca.ask("в") == "ок" and seen == [True]          # VISIBLE=1 не открывает окно там, где его показать нельзя
    # после входа второй AuthError подряд уходит наружу, новое окно не открывается
    monkeypatch.setattr(ca, "_can_show_window", lambda: True)
    logins = []
    monkeypatch.setattr(ca, "_login_visible", lambda r: logins.append(r) or True)
    monkeypatch.setattr(ca, "_ask_once", lambda *a: (_ for _ in ()).throw(ca.AuthError("вход")))
    with pytest.raises(ca.AuthError):
        ca.ask("в")
    assert len(logins) == 1
