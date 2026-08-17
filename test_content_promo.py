"""Реклама партнёров гайда не должна попадать в приложение.

Решение user 2026-08-17: приложение денег не собирает и чужую рекламу не носит.
Контент приезжает из чужого репозитория, поэтому фильтр обязан переживать
переверстку upstream — здесь проверяем и сам фильтр, и уже собранный контент,
если он есть на диске (в CI его нет: webapp/data не в git).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "tools"))
import build_content as bc  # noqa: E402

ARTICLES = Path(__file__).parent / "webapp" / "data" / "articles"
PARTNER_URL = ("https://legaltaxlevel.com/ip_pl?utm_source=jdg-guide"
               "&amp;utm_medium=referral")


def test_promo_callout_removed_whole():
    html = ('<h1>Налоги</h1>\n<p>Текст до.</p>\n'
            '<div class="admonition success"><p class="admonition-title">Бухгалтерия</p>'
            f'<p>Партнёр гайда <a href="{PARTNER_URL}">Legal Tax Level</a> ведёт JDG '
            'под ключ.</p></div>\n<p>Текст после.</p>')
    out = bc.strip_promo(html)
    assert "legaltaxlevel" not in out
    assert "admonition" not in out
    assert "Текст до." in out and "Текст после." in out


def test_promo_callout_with_nested_block_does_not_eat_neighbours():
    """Границу блока ищем счётчиком вложенности, а не первым </div>."""
    html = ('<div class="admonition success"><p class="admonition-title">Реклама</p>'
            '<div class="inner"><p>вложенный блок</p></div>'
            f'<p><a href="{PARTNER_URL}">партнёр</a></p></div>'
            '<div class="admonition info"><p class="admonition-title">Полезное</p>'
            '<p>это остаётся</p></div>')
    out = bc.strip_promo(html)
    assert "вложенный блок" not in out
    assert "это остаётся" in out


def test_promo_list_item_removed_but_list_survives():
    html = ('<ul><li>Обычный пункт</li>'
            '<li>Новая страница: <a href="article.html?id=accounting">«Бухгалтер '
            'под ключ»</a> — условия партнёра.</li>'
            '<li>Ещё пункт</li></ul>')
    out = bc.strip_promo(html)
    assert "accounting" not in out
    assert "Обычный пункт" in out and "Ещё пункт" in out
    assert out.count("<li>") == 2


def test_lone_partner_link_unwraps_to_text():
    """Одиночная ссылка теряет адрес и utm, но текст абзаца остаётся связным."""
    html = f'<p>Бухгалтерия для JDG: <a href="{PARTNER_URL}">Legal Tax Level</a>.</p>'
    out = bc.strip_promo(html)
    assert "legaltaxlevel" not in out and "utm_source" not in out
    assert out == "<p>Бухгалтерия для JDG: Legal Tax Level.</p>"


def test_ordinary_content_untouched():
    html = ('<div class="admonition info"><p class="admonition-title">Важно</p>'
            '<p>Срок — до 20 числа, см. <a href="article.html?id=zus">ZUS</a>.</p></div>')
    assert bc.strip_promo(html) == html


def test_excluded_article_is_not_built():
    assert "accounting" in bc.EXCLUDE_ARTICLES


@pytest.mark.skipif(not ARTICLES.is_dir(), reason="контент не собран (webapp/data не в git)")
def test_built_articles_have_no_partner_traces():
    dirty = {f.name for f in ARTICLES.glob("*.html")
             if bc._is_promo(f.read_text(encoding="utf-8"))}
    assert not dirty, f"реклама партнёра в собранных статьях: {sorted(dirty)}"
    assert not (ARTICLES / "accounting.html").exists()
