"""Мониторинг: парсеры на фикстурах, БД, фильтр подписчиков (без сети)."""
import os

import monitor


def test_parse_podatki_fixture():
    html = '''
    <li><a class="x" href="/aktualnosci/nowy-limit-vat-od-2026">
      <div>Nowy limit zwolnienia z VAT od 2026 roku</div></a></li>
    <a href="/aktualnosci/x">коротко</a>
    <a href="/other/page">Не новость, просто длинная ссылка в меню</a>'''
    items = monitor.parse_podatki(html)
    assert items == [("https://www.podatki.gov.pl/aktualnosci/nowy-limit-vat-od-2026",
                      "Nowy limit zwolnienia z VAT od 2026 roku")]


def test_parse_govpl_mf_fixture():
    html = '''junk <div class="art-prev"><ul><li>
    <a href="/web/finanse/ksef-obowiazkowy-od-2026"><picture><img></picture>
      <div class="title">KSeF obowiązkowy od lutego 2026</div></a>
    </li></ul></div></article><a href="/web/finanse/menu-item">menu long link here</a>'''
    items = monitor.parse_govpl_mf(html)
    assert items == [("https://www.gov.pl/web/finanse/ksef-obowiazkowy-od-2026",
                      "KSeF obowiązkowy od lutego 2026")]


def test_db_subs_and_matching(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "DB_PATH", tmp_path / "t.db")
    monitor.upsert_sub(1, "ryczalt", vat=False)
    monitor.upsert_sub(2, "skala", vat=True)
    assert set(monitor.subs_for({"who_vat": "any"})) == {1, 2}
    assert monitor.subs_for({"who_vat": "vat_only"}) == [2]
    assert monitor.subs_for({"who_vat": "nonvat_only"}) == [1]
    monitor.delete_sub(2)
    assert set(monitor.subs_for({"who_vat": "any"})) == {1}


def test_feed_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "DB_PATH", tmp_path / "t.db")
    assert monitor.get_feed() == []


def test_classify_without_key(monkeypatch):
    monkeypatch.setattr(monitor, "ANTHROPIC_KEY", "")
    assert monitor.classify([("u", "s", "t")]) is None
