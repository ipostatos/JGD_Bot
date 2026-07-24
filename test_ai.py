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
