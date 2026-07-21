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


def test_parse_zus_fixture():
    html = '''
    <a href="https://www.zus.pl/-/liwiusz-laska-prezesem-zus?redirect=%2Fx">
      Liwiusz Laska prezesem ZUS</a>
    <a href="/-/zmiany-w-skladkach-2027?redirect=%2Fy"><span>Zmiany w
      sk&#322;adkach od 2027 roku</span></a>
    <a href="https://www.zus.pl/-/liwiusz-laska-prezesem-zus?redirect=%2Fz">
      Liwiusz Laska prezesem ZUS</a>
    <a href="/o-zus/kalendarium">Kalendarium wydarzen w ZUS</a>'''
    items = monitor.parse_zus(html)
    assert items == [
        ("https://www.zus.pl/-/liwiusz-laska-prezesem-zus", "Liwiusz Laska prezesem ZUS"),
        ("https://www.zus.pl/-/zmiany-w-skladkach-2027", "Zmiany w składkach od 2027 roku"),
    ]


def test_subs_matching_via_profiles(tmp_path, monkeypatch):
    import profiles
    monkeypatch.setattr(profiles, "DB_PATH", tmp_path / "t.db")
    profiles.upsert(1, form="ryczalt", vat=0, news_sub=1)
    profiles.upsert(2, form="skala", vat=1, news_sub=1)
    profiles.upsert(3, form="skala", vat=1, news_sub=0)  # не подписан
    assert set(monitor.subs_for({"who_vat": "any"})) == {1, 2}
    assert monitor.subs_for({"who_vat": "vat_only"}) == [2]
    assert monitor.subs_for({"who_vat": "nonvat_only"}) == [1]


def test_feed_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "DB_PATH", tmp_path / "t.db")
    assert monitor.get_feed() == []


def test_classify_without_key(monkeypatch):
    monkeypatch.setattr(monitor, "ANTHROPIC_KEY", "")
    assert monitor.classify([("u", "s", "t")]) is None
