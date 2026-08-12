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


def test_parse_govpl_mf_raises_on_layout_change():
    """Пропавший 'art-prev' — не тихий день, а изменение вёрстки: раньше
    find(-1) давал seg=html[-1:] и парсер молча отдавал пусто."""
    import pytest
    with pytest.raises(ValueError, match="art-prev"):
        monitor.parse_govpl_mf("<html>совсем другая разметка без блока</html>")


def _run_once_env(tmp_path, monkeypatch, verdicts):
    """run_once с одним источником-заглушкой и подменённой классификацией."""
    monkeypatch.setattr(monitor, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(monitor, "SOURCES", [
        {"id": "a", "label": "ZUS", "url": "u1", "parse": lambda h: h},
        {"id": "b", "label": "ZUS", "url": "u2", "parse": lambda h: h},
    ])
    # обе «страницы» отдают один и тот же URL — проверяем дедуп внутри прохода
    monkeypatch.setattr(monitor, "_get", lambda url: [("/news/x", "Заголовок новости")])
    monkeypatch.setattr(monitor, "classify", lambda items: verdicts(items))


def test_run_once_failed_classification_is_not_saved(tmp_path, monkeypatch):
    """classify→None (API упал): новость НЕ сохраняется как relevant=0, иначе
    ушла бы в known и пропала навсегда. Следующий проход попробует снова."""
    _run_once_env(tmp_path, monkeypatch, lambda items: None)
    assert monitor.run_once() == []
    with monitor.db() as c:
        assert c.execute("SELECT COUNT(*) FROM news").fetchone()[0] == 0


def test_run_once_dedupes_same_url_across_pages(tmp_path, monkeypatch):
    """Один URL с двух страниц классифицируется и пушится один раз."""
    seen_counts = []
    def verdicts(items):
        seen_counts.append(len(items))
        return [{"relevant": True, "importance": 3, "topics": ["vat"],
                 "summary": "s", "who_vat": "any"}] * len(items)
    _run_once_env(tmp_path, monkeypatch, verdicts)
    push = monitor.run_once()
    assert seen_counts == [1]           # в классификацию ушёл один элемент
    assert len(push) == 1
    with monitor.db() as c:
        assert c.execute("SELECT COUNT(*) FROM news").fetchone()[0] == 1


def test_run_once_tolerates_malformed_verdict(tmp_path, monkeypatch):
    """Не-объект и строковые topics не роняют батч и не ломают ленту."""
    # topics строкой — в БД должен уйти список, иначе .map() в ленте падает
    _run_once_env(tmp_path, monkeypatch, lambda items: [
        {"relevant": True, "importance": 2, "topics": "VAT", "summary": "s"}])
    monitor.run_once()
    feed = monitor.get_feed()
    assert feed and feed[0]["topics"] == []      # строка приведена к []


def test_run_once_skips_non_dict_verdict(tmp_path, monkeypatch):
    """Элемент-строка вместо объекта не роняет весь проход (rollback+ребиллинг)."""
    _run_once_env(tmp_path, monkeypatch, lambda items: ["не объект"])
    assert monitor.run_once() == []
    with monitor.db() as c:
        assert c.execute("SELECT COUNT(*) FROM news").fetchone()[0] == 0
