"""Страницы не должны содержать инлайнового JS: на нём держится CSP.

Пока в script-src стоит 'unsafe-inline', заголовок почти не защищает — любой
внедрённый <script> выполнится. Инлайновые стили здесь намеренно не проверяются:
через них скрипты не выполняются, а 175 атрибутов style — это вёрстка.
"""
import re
from pathlib import Path

WEB = Path(__file__).parent / "webapp"
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>\s*\S", re.I)
INLINE_HANDLER = re.compile(r'\son(click|change|input|submit|load|error)\s*=\s*"', re.I)


def pages():
    return sorted(WEB.glob("*.html"))


def test_no_inline_script_blocks():
    bad = [p.name for p in pages() if INLINE_SCRIPT.search(p.read_text(encoding="utf-8"))]
    assert not bad, f"инлайновый JS вернулся: {bad} (вынести в webapp/js/)"


def test_no_inline_handlers():
    bad = [p.name for p in pages() if INLINE_HANDLER.search(p.read_text(encoding="utf-8"))]
    assert not bad, f"onclick= в разметке: {bad} (вешать addEventListener)"


def test_every_page_keeps_its_script():
    """Вынести код и забыть подключить — тихая поломка страницы."""
    for p in pages():
        html = p.read_text(encoding="utf-8")
        js = WEB / "js" / f"{p.stem}.js"
        if js.is_file():
            assert f'src="js/{p.stem}.js"' in html, f"{p.name} не подключает свой js"


def test_reader_stays_a_module():
    """pdf.js — ESM: без type=module страница падает на первом import."""
    html = (WEB / "reader.html").read_text(encoding="utf-8")
    assert 'type="module" src="js/reader.js"' in html
    assert "../vendor/pdfjs/pdf.mjs" in (WEB / "js" / "reader.js").read_text(encoding="utf-8"), \
        "путь к вендору считается от файла модуля, а он лежит на уровень глубже"
