"""confluence_linkify: названия страниц в кавычках и жирным становятся ссылками, остальное не трогаем."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import confluence_linkify as cl  # noqa: E402

TITLES = ["Журнал решений", "Словарь: типы данных и имена полей", "Решение по справочникам и размеру, 22.10.2026"]
RES = lambda text, fuzzy=True: cl.best_title(text, TITLES, fuzzy)  # noqa: E731


def run(html):
    return cl.linkify_storage(html, RES)


def test_quoted_title_becomes_link():
    h, linked, _ = run("<p>См. страницу «Журнал решений» ниже</p>")
    assert 'ri:content-title="Журнал решений"' in h and "«" not in h and linked == [("Журнал решений", "Журнал решений")]


def test_fuzzy_title_and_bold():
    h, linked, _ = run('<p>страница "Решение по справочникам и размеру 22.10"</p><p><strong>Журнал решений</strong></p>')
    assert h.count("<ac:link>") == 2 and "<strong><ac:link>" in h


def test_existing_links_code_and_unknown_untouched():
    src = '<p><a href="x">«Журнал решений»</a> <code>«Журнал решений»</code> «Просто термин»</p>'
    h, linked, unresolved = run(src)
    assert h == src and not linked and not unresolved


def test_unresolved_page_reference_is_reported():
    _, _, unresolved = run("<p>см. страницу «Несуществующая страница про всё»</p>")
    assert unresolved == ["Несуществующая страница про всё"]


def test_ampersand_title_is_escaped():
    res = lambda t, f=True: "Q&A и ответы" if "q" in t.lower() else None  # noqa: E731
    h, _, _ = cl.linkify_storage("<p>«Q&amp;A и ответы»</p>", res)
    assert 'ri:content-title="Q&amp;A и ответы"' in h


def test_short_name_and_numbered_title():
    titles = ["5 Схема сервиса и пакет партнёра: как устроено", "Словарь: типы данных и имена полей"]
    assert cl.best_title("Схема сервиса и пакет партнёра", titles) == titles[0]
    assert cl.best_title("Словарь", titles, fuzzy=False) == titles[1]
    assert cl.best_title("Что-то ещё", titles) is None
