# -*- coding: utf-8 -*-
"""Кликабельные ссылки на страницы Confluence.

Название страницы в тексте («Журнал решений», "Словарь", жирным) остаётся простым текстом: markdown → storage
делает ссылками только явные [текст](url). Этот пост-проход по storage-HTML заменяет название в кавычках или
жирным, совпавшее с заголовком существующей страницы, на <ac:link>.

Чистая логика без сети: поиск страницы по названию передаёт вызывающий (resolve). Сетевой resolve по пространству
лежит в atlassian.py. Текст внутри <a>, <ac:link>, <code>, <pre> и макросов не трогаем.
"""
from __future__ import annotations

import html as H
import re
from difflib import SequenceMatcher

SKIP = {"a", "code", "pre", "ac:link", "ac:structured-macro", "ac:parameter", "ac:plain-text-body",
        "ac:plain-text-link-body", "ac:rich-text-body"}
_TAG = re.compile(r"(<[^>]+>)")
_TAGNAME = re.compile(r"<\s*(/?)\s*([A-Za-z][\w:.-]*)[^>]*?(/?)\s*>")
_QUOTE = re.compile(r"«([^«»<>]{4,200})»|&laquo;(.{4,200}?)&raquo;|“([^“”<>]{4,200})”|\"([^\"<>]{4,200})\"|&quot;(.{4,200}?)&quot;")
# слова перед кавычками, по которым видно, что это ссылка на страницу, а не термин
_REFWORD = re.compile(r"(страниц\w*|раздел\w*|см\.|смотри\w*|по ссылке|в статье|документ\w*|карточк\w*|описан\w*)\s*[:,]?\s*$", re.I)


def norm(s: str) -> str:
    s = H.unescape(s).casefold().replace("ё", "е")
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _variants(title: str) -> list[str]:
    """Как страницу называют в тексте: полностью, без номера в начале, до двоеточия или тире (короткое имя)."""
    out = [title]
    t = re.sub(r"^\d+[.)]?\s+", "", title)
    out.append(t)
    for sep in (":", " — ", " - ", " ("):
        if sep in t:
            head = t.split(sep)[0].strip()
            if len(norm(head)) >= 6:
                out.append(head)
    return out


def best_title(candidate: str, titles: list[str], fuzzy: bool = True) -> str | None:
    """Выбор заголовка из списка по названию из текста: точное совпадение (в том числе по короткому имени),
    затем нечёткое (другая редакция названия)."""
    c = norm(candidate)
    if not c:
        return None
    for t in titles:
        if any(norm(v) == c for v in _variants(t)):
            return t
    if not fuzzy:
        return None
    best, score = None, 0.0
    for t in titles:
        for v in _variants(t):
            n = norm(v)
            r = SequenceMatcher(None, c, n).ratio()
            if (n.startswith(c) and len(c) >= 14) or (c.startswith(n) and len(n) >= 14):
                r = max(r, 0.9)
            if r > score:
                best, score = t, r
    return best if score >= 0.85 else None


def link_xml(title: str, shown: str) -> str:
    shown = shown.replace("]]>", "]] >")
    return (f'<ac:link><ri:page ri:content-title="{H.escape(title, quote=True)}" />'
            f'<ac:plain-text-link-body><![CDATA[{shown}]]></ac:plain-text-link-body></ac:link>')


def linkify_storage(storage: str, resolve) -> tuple[str, list, list]:
    """resolve(text, fuzzy) -> заголовок страницы или None. Возвращает (storage, [(показано, заголовок)], [не найдены])."""
    parts = _TAG.split(storage)
    out, linked, unresolved = [], [], []
    depth: dict[str, int] = {}
    cache: dict = {}

    def res(text: str, fuzzy: bool):
        k = (text, fuzzy)
        if k not in cache:
            cache[k] = resolve(H.unescape(text), fuzzy)
        return cache[k]

    i = 0
    while i < len(parts):
        tok = parts[i]
        if tok.startswith("<") and not tok.startswith("<!"):
            m = _TAGNAME.match(tok)
            if m:
                closing, name, selfc = m.group(1) == "/", m.group(2).lower(), bool(m.group(3))
                if name in SKIP and not selfc:
                    depth[name] = depth.get(name, 0) + (-1 if closing else 1)
            out.append(tok)
            i += 1
            continue
        if any(v > 0 for v in depth.values()) or not tok.strip():
            out.append(tok)
            i += 1
            continue
        # жирное название целиком: <strong>Название</strong>
        if (out and re.match(r"<(strong|b)>$", out[-1], re.I) and i + 1 < len(parts)
                and re.match(r"</(strong|b)>$", parts[i + 1], re.I) and 6 <= len(tok.strip()) <= 200):
            t = res(tok.strip(), False)
            if t:
                out.append(link_xml(t, H.unescape(tok.strip())))
                linked.append((tok.strip(), t))
                i += 1
                continue

        def repl(m: re.Match, before: str = tok) -> str:
            text = next(g for g in m.groups() if g)
            t = res(text, True)
            if t:
                linked.append((H.unescape(text), t))
                return link_xml(t, H.unescape(text))
            if _REFWORD.search(before[:m.start()][-40:]):
                unresolved.append(H.unescape(text))
            return m.group(0)
        out.append(_QUOTE.sub(repl, tok))
        i += 1
    return "".join(out), linked, sorted(set(unresolved))
