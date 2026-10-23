#!/usr/bin/env python3
"""Коннектор ManageEngine ServiceDesk Plus API v3: чтение заявок и создание заявки по шаблону (stdlib, без MCP).

  python scripts/servicedesk.py list [--technician ИМЯ] [--group ИМЯ] [--status ИМЯ] [--since ГГГГ-ММ-ДД]
                                     [--until ГГГГ-ММ-ДД] [--limit 200] [--page-size 100] [--criteria-json ФАЙЛ]
                                     [--with-people] [--with-attachments] [--json]
  python scripts/servicedesk.py create --template ПСЕВДОНИМ --subject "тема" [--description "текст"]
                                       [--group ПСЕВДОНИМ] [--technician ИМЯ] [--priority ИМЯ] [--check] [--send]
  python scripts/servicedesk.py note --request НОМЕР --text "текст" [--send]
  python scripts/servicedesk.py meta [--what templates|technicians|observed|all] [--name ЧАСТЬ_ИМЕНИ]

Окружение (.env, не коммитить): SD_BASE_URL — адрес портала до /api/v3 (для облака вместе с /app/<портал>),
SD_API_KEY — ключ из профиля пользователя SDP (раздел API Key generation), права ключа: чтение и создание заявок.
Необязательно: SD_AUTH_SCHEME = authtoken (по умолчанию, ключ в заголовке authtoken) или oauth (Authorization:
Zoho-oauthtoken ...), SD_CONFIG — путь к конфигу шаблонов и групп (по умолчанию config/servicedesk.toml).

Безопасность:
  * структурные поля заявителя и исполнителя и вложения в вывод не попадают без явных флагов `--with-people`,
    `--with-attachments`; тема и описание выводятся как есть и могут содержать персональные данные, проверяйте их;
  * создание заявки по умолчанию сухой прогон (печатается тело запроса); отправка только с `--send` и только
    после ручного подтверждения в терминале (нужен интерактивный ввод), без него команда отказывает;
  * ключ нигде не печатается и не сохраняется; в ошибках его нет; адрес должен быть https (внутренний http только с
    SD_ALLOW_HTTP=1), узел можно ограничить SD_ALLOWED_HOSTS, перенаправление на другой узел отклоняется;
  * подтверждение отправки защищает от случайного запуска, но не является механизмом авторизации: процесс с терминалом
    и доступом к окружению его автоматизирует. Для строгого разделения выдайте ключу только нужные права.
Списки групп, приоритетов и категорий обычный ключ часто не читает (403, 404), поэтому их имена берутся из конфига и
сверяются по фактам: `meta observed` показывает группы и приоритеты, реально стоящие в последних заявках. Шаблоны и
техники читаются (`meta templates`, `meta technicians`), `create --check` сверяет по ним шаблон и исполнителя до отправки.
Заметка к заявке (`note`) работает как создание: сухой прогон, отправка только с `--send` и ручным подтверждением.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ACCEPT = "application/vnd.manageengine.sdp.v3+json"
ROOT = Path(__file__).resolve().parent.parent

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def load_env() -> None:
    for d in (Path.cwd(), ROOT):
        p = d / ".env"
        if p.is_file():
            for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
                ln = ln.strip()
                if ln and not ln.startswith("#") and "=" in ln:
                    k, v = ln.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip("'\""))


class SDError(RuntimeError):
    """Ошибка API с понятным текстом; ключ в сообщение не попадает."""


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """Не пересылаем ключ на другой узел при перенаправлении."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).hostname != urllib.parse.urlsplit(req.full_url).hostname:
            raise SDError("перенаправление на другой узел отклонено (ключ на него не отправляется)")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def default_transport(method: str, url: str, headers: dict, data: bytes | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.build_opener(_SameHostRedirect).open(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:      # адрес с параметрами запроса в сообщение не кладём
        raise SDError(f"сеть недоступна или ошибка TLS ({type(e).__name__}); проверьте SD_BASE_URL и доступ") from None


def validate_base_url(base: str) -> str:
    """HTTPS обязателен (SD_ALLOW_HTTP=1 разрешает http для внутреннего узла); узел можно ограничить SD_ALLOWED_HOSTS."""
    u = urllib.parse.urlsplit(base)
    if not u.hostname or u.scheme not in ("https", "http"):
        raise SystemExit("SD_BASE_URL должен быть абсолютным адресом (https://узел/...)")
    if u.scheme == "http" and os.environ.get("SD_ALLOW_HTTP") != "1":
        raise SystemExit("SD_BASE_URL с http отклонён: ключ шёл бы открытым текстом. Для внутреннего узла задайте SD_ALLOW_HTTP=1")
    allowed = {x.strip().lower() for x in os.environ.get("SD_ALLOWED_HOSTS", "").split(",") if x.strip()}
    if allowed and u.hostname.lower() not in allowed:
        raise SystemExit("узел из SD_BASE_URL отсутствует в SD_ALLOWED_HOSTS")
    return base.rstrip("/")


def explain_error(status: int, body: str) -> str:
    msg = ""
    try:
        rs = json.loads(body).get("response_status")
        rs = rs[0] if isinstance(rs, list) and rs else rs
        for m in (rs or {}).get("messages", []) or []:
            msg += f' [{m.get("status_code")}] {m.get("message")}'
    except Exception:
        pass
    hint = {401: "ключ не принят: проверьте SD_API_KEY и схему SD_AUTH_SCHEME",
            403: "у ключа нет права на эту операцию (чтение или создание заявок, списки шаблонов и групп обычному ключу часто закрыты)",
            404: "адрес или объект не найден: проверьте SD_BASE_URL (для облака с /app/<портал>), номер заявки, имя шаблона"
            }.get(status, "запрос не выполнен")
    return f"HTTP {status}: {hint}.{msg}".strip()


class SDClient:
    def __init__(self, base: str, key: str, scheme: str = "authtoken", transport=default_transport):
        self.base, self.key, self.scheme, self.transport = base.rstrip("/"), key, scheme, transport

    def _headers(self) -> dict:
        h = {"Accept": ACCEPT}
        if self.scheme == "oauth":
            h["Authorization"] = f"Zoho-oauthtoken {self.key}"
        else:
            h["authtoken"] = self.key
        return h

    def get(self, path: str, input_data: dict | None = None) -> dict:
        url = f"{self.base}/api/v3/{path}"
        if input_data is not None:
            url += "?" + urllib.parse.urlencode({"input_data": json.dumps(input_data, ensure_ascii=False)})
        status, body = self.transport("GET", url, self._headers(), None)
        return self._parse(status, body)

    def post(self, path: str, input_data: dict) -> dict:
        h = self._headers()
        h["Content-Type"] = "application/x-www-form-urlencoded"
        data = urllib.parse.urlencode({"input_data": json.dumps(input_data, ensure_ascii=False)}).encode()
        status, body = self.transport("POST", f"{self.base}/api/v3/{path}", h, data)
        return self._parse(status, body)

    @staticmethod
    def _parse(status: int, body: str) -> dict:
        if status >= 400:
            raise SDError(explain_error(status, body))
        try:
            d = json.loads(body or "{}")
        except ValueError:
            raise SDError(f"HTTP {status}: ответ не JSON") from None
        rs = d.get("response_status")
        rs = rs[0] if isinstance(rs, list) and rs else rs
        if isinstance(rs, dict) and rs.get("status") == "failed":
            raise SDError(explain_error(400, body))
        return d


def crit(field: str, value, condition: str = "is") -> dict:
    return {"field": field, "condition": condition, "values": [value], "logical_operator": "and"}


def epoch_ms(day: str, end: bool = False) -> str:
    d = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    if end:
        d += dt.timedelta(days=1)
    return str(int(d.timestamp() * 1000))


def build_list_info(technician=None, group=None, status=None, since=None, until=None, extra=None,
                    start_index: int = 1, row_count: int = 100) -> dict:
    """list_info с search_criteria: фильтры соединены через AND. Имена полей по документации API v3."""
    sc = []
    if group:
        sc.append(crit("group.name", group))
    if status:
        sc.append(crit("status.name", status))
    if technician:
        sc.append(crit("technician.name", technician))
    if since:
        sc.append(crit("created_time", epoch_ms(since), "greater than"))
    if until:
        sc.append(crit("created_time", epoch_ms(until, end=True), "lesser than"))
    sc += list(extra or [])
    li = {"row_count": row_count, "start_index": start_index, "sort_field": "created_time", "sort_order": "desc",
          "get_total_count": True}
    if sc:
        sc[0] = {k: v for k, v in sc[0].items() if k != "logical_operator"}
        li["search_criteria"] = sc
    return {"list_info": li}


def fetch_requests(client: SDClient, limit: int = 200, page_size: int = 100, **filters) -> list:
    """Постранично: start_index растёт на row_count, пока has_more_rows, но не больше limit записей."""
    out, start = [], 1
    while len(out) < limit:
        n = min(page_size, limit - len(out))
        d = client.get("requests", build_list_info(start_index=start, row_count=n, **filters))
        rows = d.get("requests") or []
        out += rows
        if not (d.get("list_info") or {}).get("has_more_rows") or not rows:
            break
        start += len(rows)
    return out[:limit]


def _name(x):
    return x.get("name") if isinstance(x, dict) else x


def shape(req: dict, people: bool = False, attachments: bool = False) -> dict:
    """Короткая запись заявки. Люди и вложения только по явным флагам."""
    ct = req.get("created_time") or {}
    out = {"id": req.get("id"), "display_id": req.get("display_id"), "subject": req.get("subject"),
           "status": _name(req.get("status")), "group": _name(req.get("group")),
           "priority": _name(req.get("priority")), "template": _name(req.get("template")),
           "created": ct.get("display_value") if isinstance(ct, dict) else ct}
    if people:
        out["requester"] = _name(req.get("requester"))
        out["technician"] = _name(req.get("technician"))
    if attachments:
        out["attachments"] = req.get("attachments")
    return {k: v for k, v in out.items() if v is not None}


def fetch_templates(client: SDClient, page_size: int = 100, max_pages: int = 20) -> list:
    """Все шаблоны заявок постранично (сервер отдаёт не больше 100 за страницу)."""
    out, start = [], 1
    for _ in range(max_pages):
        d = client.get("requests/template", {"list_info": {"row_count": page_size, "start_index": start}})
        rows = d.get("template") or []
        out += rows
        if not (d.get("list_info") or {}).get("has_more_rows") or not rows:
            return out
        start += len(rows)
    raise SDError("список шаблонов прочитан не полностью (достигнут предел страниц)")


def fetch_technicians(client: SDClient, name: str | None = None, page_size: int = 100, max_pages: int = 10) -> list:
    """Пользователи-техники (is_technician is True) по части имени. Сервер не фильтрует по is_technician (проверено на
    портале), поэтому список всех пользователей слишком велик: ищем по имени (`contains`), роль проверяем на клиенте."""
    out, start = [], 1
    for _ in range(max_pages):
        li = {"row_count": page_size, "start_index": start}
        if name:
            li["search_criteria"] = [{"field": "name", "condition": "contains", "value": name}]
        d = client.get("users", {"list_info": li})
        rows = d.get("users") or []
        out += [{"name": u.get("name"), "id": u.get("id")} for u in rows if u.get("is_technician") is True]
        if not (d.get("list_info") or {}).get("has_more_rows") or not rows:
            return out
        start += len(rows)
    raise SDError("список техников прочитан не полностью (достигнут предел страниц)")


def observed_values(client: SDClient, limit: int = 200) -> dict:
    """Группы, приоритеты и шаблоны, реально стоящие в последних заявках: справочники групп и приоритетов API не отдаёт."""
    rows = fetch_requests(client, limit=limit)
    res = {"groups": {}, "priorities": {}}
    for r in rows:
        for key, fld in (("groups", "group"), ("priorities", "priority")):
            n = _name(r.get(fld))
            if n:
                res[key][n] = res[key].get(n, 0) + 1
    return res


def check_create(client: SDClient, payload: dict, cfg: dict | None = None) -> list:
    """Проверка тела создания по фактам портала (только чтение). Возвращает список проблем, пусто значит замечаний нет."""
    req, problems = payload.get("request") or {}, []
    tpl = req.get("template") or {}
    try:
        tpls = fetch_templates(client)
        names = {t.get("name") for t in tpls}
        ids = {str(t.get("id")) for t in tpls}
        if tpl.get("id") is not None and str(tpl["id"]) not in ids:
            problems.append(f'шаблон id={tpl["id"]} не найден в портале')
        if tpl.get("name") is not None and tpl["name"] not in names:
            problems.append(f'шаблон "{tpl["name"]}" не найден в портале (имя должно совпадать точно)')
    except SDError as e:
        problems.append(f"шаблоны проверить не удалось: {e}")
    tech = (req.get("technician") or {}).get("name")
    if tech:
        try:
            techs = {t["name"] for t in fetch_technicians(client, tech.split()[0])}
            if tech not in techs:
                near = sorted(n for n in techs if n.lower().startswith(tech.lower()))[:3]
                problems.append(f'техник "{tech}" не найден среди техников портала (имя пишется полностью, как в портале)'
                                + (f", похожие: {', '.join(near)}" if near else ""))
        except SDError as e:
            problems.append(f"техников проверить не удалось: {e}")
    grp = (req.get("group") or {}).get("name")
    try:
        if grp and not fetch_requests(client, limit=1, group=grp):
            problems.append(f'группа "{grp}" не найдена: нет ни одной заявки с такой группой (справочник групп API не отдаёт), проверьте имя')
    except SDError as e:
        problems.append(f"группу проверить не удалось: {e}")
    prio = (req.get("priority") or {}).get("name")
    try:
        if prio and prio not in observed_values(client)["priorities"]:
            problems.append(f'приоритет "{prio}" не встречается в последних заявках (справочник API недоступен): проверьте имя вручную')
    except SDError as e:
        problems.append(f"приоритет проверить не удалось: {e}")
    return problems


def load_config(path: str | None = None) -> dict:
    p = Path(path or os.environ.get("SD_CONFIG") or ROOT / "config" / "servicedesk.toml")
    if not p.is_file():
        return {}
    import tomllib
    return tomllib.loads(p.read_text(encoding="utf-8"))


def build_create(cfg: dict, template: str, subject: str, description: str = "", group: str | None = None,
                 technician: str | None = None, priority: str | None = None) -> dict:
    """Тело запроса создания заявки по шаблону. Шаблон, группа и техник берутся из конфига (псевдоним) или как есть.
    Значение шаблона в конфиге: строка это имя, целое число это id. Техник по умолчанию: [defaults] technician."""
    tpl = (cfg.get("templates") or {}).get(template, template)
    req = {"subject": subject, "template": {"id": str(tpl)} if isinstance(tpl, int) else {"name": tpl}}
    if description:
        req["description"] = description
    if group:
        req["group"] = {"name": (cfg.get("groups") or {}).get(group, group)}
    tech = technician or (cfg.get("defaults") or {}).get("technician")
    if tech:
        req["technician"] = {"name": (cfg.get("technicians") or {}).get(tech, tech)}
    prio = priority or (cfg.get("defaults") or {}).get("priority")
    if prio:
        req["priority"] = {"name": prio}
    return {"request": req}


def build_note(request_id: str, text: str, show_to_requester: bool = False) -> dict:
    """Тело заметки к заявке. По умолчанию заметка внутренняя (заявителю не показывается, уведомлений нет)."""
    return {"request_note": {"description": text, "show_to_requester": show_to_requester, "notify_technician": False}}


def confirm_send(payload: dict, isatty=None, ask=input, what: str = "заявку") -> bool:
    """Ручное подтверждение отправки: только из интерактивного терминала, строка `отправить`."""
    if not (isatty() if isatty else sys.stdin.isatty()):
        print("Отправка требует ручного подтверждения в интерактивном терминале. Выполните команду сами "
              "(в сессии агента с префиксом `! `), агент подтвердить за вас не может.", file=sys.stderr)
        return False
    print(json.dumps(payload, ensure_ascii=False, indent=1))
    return ask(f"Отправить {what} с этим телом? Введите слово 'отправить': ").strip().lower() == "отправить"


def load_extra_criteria(path: str | None) -> list:
    if not path:
        return []
    try:
        v = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SDError(f"не удалось прочитать --criteria-json ({type(e).__name__})") from None
    if not isinstance(v, list) or not all(isinstance(x, dict) and "field" in x and "condition" in x for x in v):
        raise SDError("--criteria-json: нужен массив объектов с полями field и condition")
    return v


def make_client() -> SDClient:
    load_env()
    base, key = os.environ.get("SD_BASE_URL", ""), os.environ.get("SD_API_KEY", "")
    if not base or not key:
        raise SystemExit("Не заданы SD_BASE_URL и SD_API_KEY (в окружении или .env; шаблон в .env.example)")
    return SDClient(validate_base_url(base), key, os.environ.get("SD_AUTH_SCHEME", "authtoken"))


def main(argv=None, client: SDClient | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="список заявок по исполнителю, группе, статусу и периоду")
    for f in ("technician", "group", "status", "since", "until"):
        ls.add_argument(f"--{f}")
    ls.add_argument("--limit", type=int, default=200)
    ls.add_argument("--page-size", type=int, default=100)
    ls.add_argument("--criteria-json", help="файл с дополнительными search_criteria (массив)")
    ls.add_argument("--with-people", action="store_true", help="выводить заявителя и исполнителя (персональные данные)")
    ls.add_argument("--with-attachments", action="store_true", help="выводить вложения")
    ls.add_argument("--json", action="store_true")
    cr = sub.add_parser("create", help="создать заявку по шаблону (по умолчанию сухой прогон)")
    cr.add_argument("--template", required=True, help="псевдоним из конфига или точное имя шаблона")
    cr.add_argument("--subject", required=True)
    cr.add_argument("--description", default="")
    cr.add_argument("--group")
    cr.add_argument("--technician", help="имя техника или псевдоним из [technicians]; по умолчанию [defaults] technician")
    cr.add_argument("--priority", help="имя приоритета; по умолчанию [defaults] priority")
    cr.add_argument("--check", action="store_true", help="до отправки сверить шаблон, техника, группу и приоритет по порталу (только чтение)")
    cr.add_argument("--send", action="store_true", help="отправить после ручного подтверждения")
    cr.add_argument("--config")
    nt = sub.add_parser("note", help="добавить заметку к заявке (по умолчанию сухой прогон)")
    nt.add_argument("--request", required=True, help="номер заявки (id)")
    nt.add_argument("--text", required=True)
    nt.add_argument("--visible-to-requester", action="store_true", help="показать заметку заявителю (по умолчанию внутренняя)")
    nt.add_argument("--send", action="store_true", help="отправить после ручного подтверждения")
    mt = sub.add_parser("meta", help="что реально есть в портале: шаблоны, техники, группы и приоритеты из последних заявок")
    mt.add_argument("--what", choices=("templates", "technicians", "observed", "all"), default="all")
    mt.add_argument("--name", help="часть имени для поиска техников (без неё список всех пользователей слишком велик)")
    mt.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "meta":
            c = client or make_client()
            out = {}
            if a.what in ("templates", "all"):
                try:
                    out["templates"] = [{"id": t.get("id"), "name": t.get("name")} for t in fetch_templates(c)]
                except SDError as e:
                    out["templates"] = f"недоступно: {e}"
            if a.what == "technicians" or (a.what == "all" and a.name):
                try:
                    out["technicians"] = [t["name"] for t in fetch_technicians(c, a.name)] if a.name else \
                        "укажите --name (часть имени): список всех пользователей портала слишком велик"
                except SDError as e:
                    out["technicians"] = f"недоступно: {e}"
            if a.what in ("observed", "all"):
                try:
                    out["observed"] = observed_values(c)
                except SDError as e:
                    out["observed"] = f"недоступно: {e}"
            if a.json:
                print(json.dumps(out, ensure_ascii=False))
            else:
                for k, v in out.items():
                    print(f"== {k}")
                    if isinstance(v, str):
                        print(v)
                    elif isinstance(v, dict):
                        for kk, vv in v.items():
                            print(f"{kk}: " + ", ".join(f"{n} ({cnt})" for n, cnt in sorted(vv.items(), key=lambda x: -x[1])))
                    else:
                        for it in v:
                            print(f'{it["id"]}\t{it["name"]}' if isinstance(it, dict) else it)
            return 0
        if a.cmd == "note":
            payload = build_note(a.request, a.text, a.visible_to_requester)
            if not a.send:
                print("Сухой прогон, ничего не отправлено. Заметка к заявке " + str(a.request) + ", тело запроса:\n"
                      + json.dumps(payload, ensure_ascii=False, indent=1))
                return 0
            if not confirm_send(payload, what=f"заметку к заявке {a.request}"):
                print("Отправка отменена.")
                return 1
            (client or make_client()).post(f"requests/{urllib.parse.quote(str(a.request), safe='')}/notes", payload)
            print(f"заметка добавлена к заявке {a.request}")
            return 0
        if a.cmd == "create":
            payload = build_create(load_config(a.config), a.template, a.subject, a.description, a.group,
                                   a.technician, a.priority)
            if a.check:
                problems = check_create(client or make_client(), payload)
                for pr in problems:
                    print("замечание: " + pr)
                print("проверка по порталу: " + ("есть замечания" if problems else "замечаний нет"))
                if problems and a.send:
                    return 1
            if not a.send:
                print("Сухой прогон, ничего не отправлено. Тело запроса:\n" + json.dumps(payload, ensure_ascii=False, indent=1))
                return 0
            if not confirm_send(payload):
                print("Отправка отменена.")
                return 1
            res = (client or make_client()).post("requests", payload)
            r = res.get("request") or {}
            print(f'создана заявка id={r.get("id")} display_id={r.get("display_id")}')
            return 0
        extra = load_extra_criteria(a.criteria_json)
        rows = fetch_requests(client or make_client(), a.limit, a.page_size, technician=a.technician, group=a.group,
                              status=a.status, since=a.since, until=a.until, extra=extra)
    except SDError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 2
    items = [shape(r, a.with_people, a.with_attachments) for r in rows]
    if a.json:
        print(json.dumps(items, ensure_ascii=False))
    else:
        print("номер\tстатус\tгруппа\tтема")
        for it in items:
            print(f'{it.get("display_id") or it.get("id")}\t{it.get("status")}\t{it.get("group")}\t{it.get("subject")}')
        print(f"-- заявок: {len(items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
