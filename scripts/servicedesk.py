#!/usr/bin/env python3
"""Коннектор ManageEngine ServiceDesk Plus API v3: чтение заявок и создание заявки по шаблону (stdlib, без MCP).

  python scripts/servicedesk.py list [--technician ИМЯ] [--group ИМЯ] [--status ИМЯ] [--since ГГГГ-ММ-ДД]
                                     [--until ГГГГ-ММ-ДД] [--limit 200] [--page-size 100] [--criteria-json ФАЙЛ]
                                     [--with-people] [--with-attachments] [--json]
  python scripts/servicedesk.py create --template ПСЕВДОНИМ --subject "тема" [--description "текст"]
                                       [--group ПСЕВДОНИМ] [--send]

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
Списки шаблонов и групп обычный ключ часто не читает (403, 404), поэтому имена шаблонов и групп берутся из конфига.
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


def load_config(path: str | None = None) -> dict:
    p = Path(path or os.environ.get("SD_CONFIG") or ROOT / "config" / "servicedesk.toml")
    if not p.is_file():
        return {}
    import tomllib
    return tomllib.loads(p.read_text(encoding="utf-8"))


def build_create(cfg: dict, template: str, subject: str, description: str = "", group: str | None = None) -> dict:
    """Тело запроса создания заявки по шаблону. Имена шаблона и группы берутся из конфига (псевдоним) или как есть."""
    tpl = (cfg.get("templates") or {}).get(template, template)
    req = {"subject": subject, "template": {"name": tpl}}
    if description:
        req["description"] = description
    if group:
        req["group"] = {"name": (cfg.get("groups") or {}).get(group, group)}
    return {"request": req}


def confirm_send(payload: dict, isatty=None, ask=input) -> bool:
    """Ручное подтверждение отправки: только из интерактивного терминала, строка `отправить`."""
    if not (isatty() if isatty else sys.stdin.isatty()):
        print("Отправка требует ручного подтверждения в интерактивном терминале. Выполните команду сами "
              "(в сессии агента с префиксом `! `), агент подтвердить за вас не может.", file=sys.stderr)
        return False
    print(json.dumps(payload, ensure_ascii=False, indent=1))
    return ask("Создать заявку с этим телом? Введите слово 'отправить': ").strip().lower() == "отправить"


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
    cr.add_argument("--send", action="store_true", help="отправить после ручного подтверждения")
    cr.add_argument("--config")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "create":
            payload = build_create(load_config(a.config), a.template, a.subject, a.description, a.group)
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
