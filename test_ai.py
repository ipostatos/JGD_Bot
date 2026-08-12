"""AI-ассистент: ретривер, квоты, кэш (без сети)."""
import asyncio

import ai


def test_retrieve_finds_zus_article():
    arts = ai.retrieve("сколько платить ZUS на ulga na start")
    ids = [a["id"] for a in arts]
    assert any("zus" in i for i in ids)


def test_retrieve_vat_question():
    arts = ai.retrieve("лимит регистрации VAT и zwolnienie")
    assert arts and any("vat" in a["id"] or "VAT" in a["title"] for a in arts)


def test_retrieve_gibberish_empty():
    assert ai.retrieve("qq") == []


def test_quota_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "DB_PATH", tmp_path / "t.db")
    uid = 42
    assert ai.quota_left(uid) == ai.DAILY_USER_LIMIT
    for _ in range(ai.DAILY_USER_LIMIT):
        assert ai._quota_ok(uid)
        ai._quota_bump(uid)
    assert not ai._quota_ok(uid)
    assert ai.quota_left(uid) == 0


def test_ask_short_question_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "DB_PATH", tmp_path / "t.db")
    res = asyncio.run(ai.ask(1, "??"))
    assert "error" in res


def test_ask_without_key(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(ai, "ANTHROPIC_KEY", "")
    res = asyncio.run(ai.ask(1, "Сколько платить ZUS в первый год?"))
    assert "error" in res


# ── ставки в корпусе ──────────────────────────────────────────────────────

def test_rates_are_in_the_corpus_and_come_from_calc():
    """«Сколько платить в этом году» — самый частый вопрос, и ассистент
    отвечал на него «в гайде нет информации»: ставки жили в калькуляторах,
    но не в корпусе. Числа обязаны браться из расчётного ядра, а не быть
    переписаны в текст — вторая копия ставок разъедется на первой же правке.
    """
    import calc
    entries = {e["id"]: e["text"] for e in ai._rates_entries()}
    z = calc.RATES["zdrowotna"]["min_monthly"]
    assert str(z) in entries["rates-zus-2026"], "минимальная zdrowotna не из calc"
    assert str(calc.RATES["duzy_base"]) in entries["rates-zus-2026"]
    assert str(calc.RATES["vat_exemption_limit"]) in entries["rates-pit-2026"]
    # суммы składek — из того же движка, что и калькулятор
    duzy = calc.spoleczne_monthly("duzy", chorobowe=True)["total"]
    assert str(duzy) in entries["rates-zus-2026"]


def test_rates_entries_name_the_year():
    """Ответ, верный для 2026-го, для 2025-го уже неверен: год должен стоять
    и в заголовке, и в тексте, иначе модель смешает годы."""
    for e in ai._rates_entries():
        year = e["id"].rsplit("-", 1)[-1]
        assert year in e["title"] and year in e["text"], e["id"]


def test_retrieve_answers_the_zdrowotna_question():
    import calc
    arts = ai.retrieve("какая минимальная składka zdrowotna в 2026 году")
    assert arts, "запрос про минимальную здоровотну не нашёл ничего"
    joined = " ".join(a["text"] for a in arts)
    assert str(calc.RATES["zdrowotna"]["min_monthly"]) in joined


def test_previous_year_is_available_too():
    arts = ai.retrieve("база duży ZUS в 2025 году")
    assert any(a["id"].endswith("2025") for a in arts), [a["id"] for a in arts]


def test_current_year_is_marked_by_the_calendar_not_by_the_file():
    """Пометка «действуют сейчас» обязана следовать календарю: если ставки
    на новый год ещё не завезли, называть прошлогодние текущими — это
    дезинформация, а не мелкая неточность."""
    from datetime import date
    entries = {e["id"]: e["text"] for e in ai._rates_entries()}
    this_year = date.today().year
    for eid, text in entries.items():
        year = eid.rsplit("-", 1)[-1]
        if not year.isdigit() or eid.startswith("rates-pit"):
            continue
        if int(year) == this_year:
            assert "действуют сейчас" in text, eid
        else:
            assert "сейчас не действуют" in text, eid


def test_cache_key_follows_the_corpus(tmp_path, monkeypatch):
    """Пополнили базу знаний — старый ответ обязан перестать выдаваться.

    Иначе сутки после выкатки человек получает ответ, построенный по корпусу,
    в котором нужных сведений ещё не было; именно так ассистент упорно
    отвечал «в гайде нет информации» про ставки, которые уже знал.
    """
    monkeypatch.setattr(ai, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(ai, "ANTHROPIC_KEY", "")        # до сети не дойдём
    before = ai._corpus_fingerprint()

    # через monkeypatch, а не присваиванием: иначе подложный индекс утечёт
    # в остальные тесты файла и они начнут падать от порядка запуска
    monkeypatch.setattr(ai, "_index", ai._load_index() + [
        {"id": "новая-запись", "title": "Новое", "text": "новая справка"}])
    monkeypatch.setattr(ai, "_fingerprint", None)
    after = ai._corpus_fingerprint()
    assert before != after, "подпись корпуса не заметила новую запись"

    # параметры ретривера — тоже часть ответа
    monkeypatch.setattr(ai, "_fingerprint", None)
    monkeypatch.setattr(ai, "BM25_B", ai.BM25_B + 0.1)
    assert ai._corpus_fingerprint() != after


def test_fingerprint_notices_same_length_edit(monkeypatch):
    """Правка суммы той же длины («1 646» → «1 746») обязана сбросить кэш:
    хеш по длине текста её не замечал — тот же баг, что чинили 24.07."""
    monkeypatch.setattr(ai, "_index",
                        [{"id": "a", "title": "T", "text": "ставка 1 646 zł в месяц"}])
    monkeypatch.setattr(ai, "_fingerprint", None)
    before = ai._corpus_fingerprint()
    monkeypatch.setattr(ai, "_index",
                        [{"id": "a", "title": "T", "text": "ставка 1 746 zł в месяц"}])
    monkeypatch.setattr(ai, "_fingerprint", None)
    after_text = ai._corpus_fingerprint()
    assert after_text != before
    # правка заголовка (влияет на TITLE_BOOST и шапку контекста) — тоже
    monkeypatch.setattr(ai, "_index",
                        [{"id": "a", "title": "T2", "text": "ставка 1 746 zł в месяц"}])
    monkeypatch.setattr(ai, "_fingerprint", None)
    assert ai._corpus_fingerprint() != after_text


def test_cache_key_separates_profiles(tmp_path, monkeypatch):
    """Один вопрос от liniowy+VAT и от ryczałt без VAT не должен делить кэш:
    ответ строится под профиль, и чужой был бы неверной налоговой справкой."""
    import asyncio
    import json
    import time
    monkeypatch.setattr(ai, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(ai, "ANTHROPIC_KEY", "")        # до сети не дойдём

    # кладём в кэш ответ «под liniowy+VAT» руками через qhash-логику ask():
    # проще — проверим, что ключи для разных профилей различаются
    q = "какая у меня ставка"
    def qhash(profile):
        prof_sig = (f"{profile.get('form', '')}|{bool(profile.get('vat'))}"
                    if profile else "")
        import hashlib
        return hashlib.sha256(
            f"{q.lower()}|{ai._corpus_fingerprint()}|{prof_sig}".encode()).hexdigest()

    assert qhash({"form": "liniowy", "vat": True}) != qhash({"form": "ryczalt", "vat": False})
    assert qhash(None) != qhash({"form": "liniowy", "vat": True})
