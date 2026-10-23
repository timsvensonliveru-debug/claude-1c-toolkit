"""servicedesk.py: сборка list_info, пагинация, разбор ошибок, приватность вывода, сухой прогон и подтверждение. Без сети."""
import json
import os
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import pytest  # noqa: E402
import servicedesk as sd  # noqa: E402

FX = Path(__file__).parent / "fixtures" / "servicedesk"


def fx(name):
    return (FX / name).read_text(encoding="utf-8")


class Fake:
    """Подменяемый транспорт: отдаёт заготовленные ответы по очереди и запоминает запросы."""
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, method, url, headers, data=None):
        self.calls.append((method, url, headers, data))
        return self.responses.pop(0)


def li_of(url):
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    return json.loads(q["input_data"][0])["list_info"]


def test_build_list_info_filters_and_epoch():
    li = sd.build_list_info(group="Поддержка", status="Open", since="2026-10-01", until="2026-10-02")["list_info"]
    sc = li["search_criteria"]
    assert [c["field"] for c in sc] == ["group.name", "status.name", "created_time", "created_time"]
    assert "logical_operator" not in sc[0] and all(c["logical_operator"] == "and" for c in sc[1:])
    assert sc[2]["condition"] == "greater than" and sc[2]["values"] == ["1790812800000"]
    assert sc[3]["condition"] == "lesser than" and sc[3]["values"] == ["1790985600000"]
    assert li["row_count"] == 100 and li["start_index"] == 1


def test_pagination_follows_has_more_rows_and_limit():
    t = Fake((200, fx("list_page1.json")), (200, fx("list_page2.json")))
    c = sd.SDClient("https://sd.example/", "KEY", transport=t)
    rows = sd.fetch_requests(c, limit=10, page_size=2, group="Поддержка")
    assert [r["id"] for r in rows] == ["101", "102", "103"]
    assert li_of(t.calls[0][1])["start_index"] == 1 and li_of(t.calls[1][1])["start_index"] == 3
    # лимит режет выдачу и останавливает запросы
    t2 = Fake((200, fx("list_page1.json")))
    assert len(sd.fetch_requests(sd.SDClient("https://sd.example", "KEY", transport=t2), limit=1, page_size=1)) == 1
    assert len(t2.calls) == 1


def test_auth_header_schemes_and_url():
    t = Fake((200, fx("list_page2.json")))
    sd.SDClient("https://sd.example/app/p/", "KEY", transport=t).get("requests", {"list_info": {}})
    m, url, h, _ = t.calls[0]
    assert url.startswith("https://sd.example/app/p/api/v3/requests?input_data=") and h["authtoken"] == "KEY"
    assert h["Accept"] == sd.ACCEPT
    t = Fake((200, "{}"))
    sd.SDClient("https://sd.example", "KEY", "oauth", t).get("requests")
    assert t.calls[0][2]["Authorization"] == "Zoho-oauthtoken KEY" and "authtoken" not in t.calls[0][2]


@pytest.mark.parametrize("status,needle", [(401, "ключ не принят"), (403, "нет права"), (404, "не найден")])
def test_http_errors_are_explained_without_key(status, needle):
    c = sd.SDClient("https://sd.example", "SECRETKEY", transport=Fake((status, fx("error_403.json"))))
    with pytest.raises(sd.SDError) as e:
        c.get("requests")
    assert needle in str(e.value) and "SECRETKEY" not in str(e.value)


def test_403_message_text_is_included():
    c = sd.SDClient("https://sd.example", "K", transport=Fake((403, fx("error_403.json"))))
    with pytest.raises(sd.SDError) as e:
        c.get("requests")
    assert "4002" in str(e.value)


def test_failed_status_inside_200_raises():
    c = sd.SDClient("https://sd.example", "K", transport=Fake((200, fx("error_403.json"))))
    with pytest.raises(sd.SDError):
        c.get("requests")


def test_people_and_attachments_hidden_by_default():
    req = json.loads(fx("list_page1.json"))["requests"][0]
    short = json.dumps(sd.shape(req), ensure_ascii=False)
    assert "Иванов" not in short and "Петров" not in short and "ivan@" not in short and "scan.pdf" not in short
    assert sd.shape(req)["subject"] == "Не печатает принтер" and sd.shape(req)["group"] == "Поддержка"
    full = sd.shape(req, people=True, attachments=True)
    assert full["requester"] == "Иван Иванов" and full["technician"] == "Пётр Петров" and full["attachments"]


def test_create_dry_run_by_default_and_template_alias(tmp_path, capsys):
    cfg = tmp_path / "sd.toml"
    cfg.write_text('[templates]\n"Тип" = "Точное имя шаблона"\n[groups]\n"П" = "Группа поддержки"\n', encoding="utf-8")
    t = Fake()
    rc = sd.main(["create", "--template", "Тип", "--subject", "Тема", "--group", "П", "--config", str(cfg)],
                 client=sd.SDClient("https://sd.example", "K", transport=t))
    out = capsys.readouterr().out
    assert rc == 0 and "Сухой прогон" in out and not t.calls
    body = json.loads(out.split("Тело запроса:\n", 1)[1])["request"]
    assert body["template"] == {"name": "Точное имя шаблона"} and body["group"] == {"name": "Группа поддержки"}


def test_send_requires_human_confirmation(monkeypatch, capsys):
    t = Fake((200, json.dumps({"request": {"id": "9", "display_id": "9"}, "response_status": {"status": "success"}})))
    client = sd.SDClient("https://sd.example", "K", transport=t)
    monkeypatch.setattr(sd, "confirm_send", lambda payload, **k: False)
    assert sd.main(["create", "--template", "Т", "--subject", "С", "--send"], client=client) == 1 and not t.calls
    monkeypatch.setattr(sd, "confirm_send", lambda payload, **k: True)
    assert sd.main(["create", "--template", "Т", "--subject", "С", "--send"], client=client) == 0 and len(t.calls) == 1
    assert t.calls[0][0] == "POST"


def test_confirm_refuses_without_tty_and_needs_exact_word():
    assert sd.confirm_send({"a": 1}, isatty=lambda: False) is False
    assert sd.confirm_send({"a": 1}, isatty=lambda: True, ask=lambda q: "да") is False
    assert sd.confirm_send({"a": 1}, isatty=lambda: True, ask=lambda q: "Отправить") is True


def test_base_url_validation(monkeypatch):
    monkeypatch.delenv("SD_ALLOW_HTTP", raising=False)
    monkeypatch.delenv("SD_ALLOWED_HOSTS", raising=False)
    assert sd.validate_base_url("https://sd.example/app/p/") == "https://sd.example/app/p"
    with pytest.raises(SystemExit):
        sd.validate_base_url("http://sd.example")
    monkeypatch.setenv("SD_ALLOW_HTTP", "1")
    assert sd.validate_base_url("http://sd.local:8080") == "http://sd.local:8080"
    monkeypatch.setenv("SD_ALLOWED_HOSTS", "other.example")
    with pytest.raises(SystemExit):
        sd.validate_base_url("http://sd.local:8080")
    with pytest.raises(SystemExit):
        sd.validate_base_url("не адрес")


def test_network_errors_become_sderror_without_url(monkeypatch):
    import urllib.error
    import urllib.request

    class Boom:
        def open(self, *a, **k):
            raise urllib.error.URLError("dns")

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a, **k: Boom())
    with pytest.raises(sd.SDError) as e:
        sd.default_transport("GET", "https://sd.example/api/v3/requests?input_data=%7Bsecret%7D", {})
    assert "input_data" not in str(e.value) and "URLError" in str(e.value)


def test_criteria_json_validation(tmp_path):
    f = tmp_path / "c.json"
    f.write_text('[{"field": "subject", "condition": "contains", "value": "x"}]', encoding="utf-8")
    assert sd.load_extra_criteria(str(f))[0]["field"] == "subject"
    f.write_text('{"a": 1}', encoding="utf-8")
    with pytest.raises(sd.SDError):
        sd.load_extra_criteria(str(f))
    with pytest.raises(sd.SDError):
        sd.load_extra_criteria(str(tmp_path / "нет.json"))


TPL1 = json.dumps({"template": [{"id": "1", "name": "Шаблон А"}, {"id": "2", "name": "Шаблон Б"}], "list_info": {"has_more_rows": True}})
TPL2 = json.dumps({"template": [{"id": "3", "name": "Шаблон В"}], "list_info": {"has_more_rows": False}})
USERS = json.dumps({"users": [{"id": "10", "name": "Техник Один", "is_technician": True}], "list_info": {"has_more_rows": False}})
REQS = json.dumps({"requests": [{"id": "1", "group": {"name": "Группа 1"}, "priority": {"name": "Низкий"}},
                                {"id": "2", "group": {"name": "Группа 1"}, "priority": {"name": "Высокий"}}],
                   "list_info": {"has_more_rows": False}})


def test_fetch_templates_paginates():
    t = Fake((200, TPL1), (200, TPL2))
    rows = sd.fetch_templates(sd.SDClient("https://sd.example", "KEY", transport=t), page_size=2)
    assert [r["id"] for r in rows] == ["1", "2", "3"]
    assert li_of(t.calls[1][1])["start_index"] == 3


def test_build_create_template_id_and_defaults():
    cfg = {"templates": {"work": 700, "named": "Шаблон А"}, "technicians": {"main": "Техник Один"},
           "defaults": {"technician": "main", "priority": "Низкий"}}
    r = sd.build_create(cfg, "work", "тема")["request"]
    assert r["template"] == {"id": "700"} and r["technician"] == {"name": "Техник Один"} and r["priority"] == {"name": "Низкий"}
    r2 = sd.build_create(cfg, "named", "тема", technician="Другой", priority="Высокий")["request"]
    assert r2["template"] == {"name": "Шаблон А"} and r2["technician"] == {"name": "Другой"} and r2["priority"]["name"] == "Высокий"
    assert "technician" not in sd.build_create({}, "x", "т")["request"]


def test_check_create_flags_unknown_by_facts():
    good = {"request": {"template": {"name": "Шаблон Б"}, "technician": {"name": "Техник Один"},
                        "group": {"name": "Группа 1"}, "priority": {"name": "Низкий"}}}
    t = Fake((200, TPL1), (200, TPL2), (200, USERS), (200, REQS), (200, REQS))
    assert sd.check_create(sd.SDClient("https://sd.example", "KEY", transport=t), good) == []
    bad = {"request": {"template": {"name": "Нет такого"}, "technician": {"name": "Чужой"},
                       "group": {"name": "Группа 9"}, "priority": {"name": "Срочный"}}}
    t = Fake((200, TPL1), (200, TPL2), (200, USERS), (200, json.dumps({"requests": []})), (200, REQS))
    p = sd.check_create(sd.SDClient("https://sd.example", "KEY", transport=t), bad)
    assert len(p) == 4 and "Нет такого" in p[0]
    # шаблон по id
    t = Fake((200, TPL1), (200, TPL2), (200, REQS))
    p = sd.check_create(sd.SDClient("https://sd.example", "KEY", transport=t), {"request": {"template": {"id": "99"}}})
    assert p and "id=99" in p[0]


def test_check_create_technicians_forbidden_is_a_warning_not_a_crash():
    t = Fake((200, TPL2), (403, "{}"), (200, REQS))
    p = sd.check_create(sd.SDClient("https://sd.example", "KEY", transport=t),
                        {"request": {"template": {"name": "Шаблон В"}, "technician": {"name": "Техник Один"}}})
    assert len(p) == 1 and "не удалось" in p[0]


def test_note_dry_run_sends_nothing_and_default_internal(capsys):
    t = Fake()
    c = sd.SDClient("https://sd.example", "KEY", transport=t)
    assert sd.main(["note", "--request", "5", "--text", "комментарий"], client=c) == 0
    out = capsys.readouterr().out
    assert "Сухой прогон" in out and '"show_to_requester": false' in out and t.calls == []


def test_note_send_needs_interactive_confirmation(monkeypatch, capsys):
    t = Fake((200, "{}"))
    c = sd.SDClient("https://sd.example", "KEY", transport=t)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert sd.main(["note", "--request", "5", "--text", "x", "--send"], client=c) == 1
    assert t.calls == []
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    orig = sd.confirm_send
    monkeypatch.setattr(sd, "confirm_send", lambda payload, what="": orig(payload, ask=lambda _: "отправить", what=what))
    assert sd.main(["note", "--request", "5", "--text", "x", "--send"], client=c) == 0
    m, url, _, data = t.calls[0]
    assert m == "POST" and url.endswith("/api/v3/requests/5/notes")


def test_meta_observed_and_templates(capsys):
    t = Fake((200, TPL2), (200, REQS))
    c = sd.SDClient("https://sd.example", "KEY", transport=t)
    assert sd.main(["meta", "--what", "templates"], client=c) == 0
    assert "Шаблон В" in capsys.readouterr().out
    assert sd.main(["meta", "--what", "observed", "--json"], client=c) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["observed"]["groups"] == {"Группа 1": 2}


def test_check_create_technician_hint_prefix():
    users = json.dumps({"users": [{"id": "1", "name": "Фамилия Имя Отчество", "is_technician": True}], "list_info": {}})
    t = Fake((200, TPL2), (200, users))
    p = sd.check_create(sd.SDClient("https://sd.example", "KEY", transport=t),
                        {"request": {"template": {"name": "Шаблон В"}, "technician": {"name": "Фамилия Имя"}}})
    assert len(p) == 1 and "Фамилия Имя Отчество" in p[0]


def test_check_create_403_and_truncation_become_findings_not_crash():
    more = json.dumps({"template": [{"id": "1", "name": "А"}], "list_info": {"has_more_rows": True}})
    t = Fake((200, more))
    with pytest.raises(sd.SDError):
        sd.fetch_templates(sd.SDClient("https://sd.example", "KEY", transport=t), page_size=1, max_pages=1)
    t = Fake((403, "{}"), (403, "{}"), (403, "{}"))
    p = sd.check_create(sd.SDClient("https://sd.example", "KEY", transport=t),
                        {"request": {"template": {"name": "А"}, "group": {"name": "Г"}, "priority": {"name": "Низкий"}}})
    assert len(p) == 3 and all("не удалось" in x for x in p)


def test_fetch_technicians_only_explicit_true():
    users = json.dumps({"users": [{"id": "1", "name": "Нет поля"}, {"id": "2", "name": "Техник", "is_technician": True},
                                  {"id": "3", "name": "Не техник", "is_technician": False}], "list_info": {}})
    t = Fake((200, users))
    assert [x["name"] for x in sd.fetch_technicians(sd.SDClient("https://sd.example", "KEY", transport=t))] == ["Техник"]


def test_meta_403_reports_unavailable(capsys):
    t = Fake((403, "{}"), (403, "{}"))
    assert sd.main(["meta", "--what", "all", "--json"], client=sd.SDClient("https://sd.example", "KEY", transport=t)) == 0
    d = json.loads(capsys.readouterr().out)
    assert set(d) == {"templates", "observed"} and all(str(v).startswith("недоступно") for v in d.values())
