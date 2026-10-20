"""bsl_ls_top: выбор нужных ключей и тихий выход без java/jar или при отказе."""
import importlib.util
import pathlib

_spec = importlib.util.spec_from_file_location("bsl_ls_top", pathlib.Path(__file__).parent.parent / "scripts" / "bsl_ls_top.py")
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def test_pick_only_top_keys():
    rep = {"fileinfos": [{"path": "/x/Module.bsl", "diagnostics": [
        {"code": "QueryNestedFieldsByDot", "message": "m", "range": {"start": {"line": 4}}},
        {"code": "LineLength", "message": "n", "range": {"start": {"line": 1}}}]}]}
    assert m.pick(rep) == ["Module.bsl:5 [QueryNestedFieldsByDot] m"]


def test_silent_without_jar(monkeypatch, tmp_path):
    f = tmp_path / "a.bsl"
    f.write_text("x", encoding="utf-8")
    monkeypatch.delenv("BSL_JAR", raising=False)
    assert m.analyze(str(f)) == []


def test_opt_out(monkeypatch, tmp_path):
    f = tmp_path / "a.bsl"
    f.write_text("x", encoding="utf-8")
    monkeypatch.setenv("AI_TOOLKIT_BSLLS_GATE", "off")
    monkeypatch.setenv("BSL_JAR", str(f))
    assert m.analyze(str(f)) == []
