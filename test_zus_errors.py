"""База кодов ошибок ZUS: целостность данных + поиск и ответ бота."""
import json
import os
from pathlib import Path

os.environ["DISABLE_BOT"] = "1"
os.environ.setdefault("BOT_TOKEN", "12345:TESTTOKEN")

import server  # noqa: E402

DATA = json.loads((Path(__file__).parent / "webapp" / "zus_errors.json")
                  .read_text(encoding="utf-8"))
ERRORS = DATA["errors"]


def test_schema_and_required_fields():
    assert len(ERRORS) >= 25
    for e in ERRORS:
        assert e["severity"] in ("K", "Z", "I"), e
        for field in ("msg_pl", "title_ru", "why_ru", "fix_ru", "doc", "sources"):
            assert e.get(field), (field, e.get("code"))
        assert isinstance(e["fix_ru"], list) and e["fix_ru"]
        assert all(s in DATA["sources"] for s in e["sources"]), e["sources"]


def test_codes_are_unique_and_8_digits():
    codes = [e["code"] for e in ERRORS if e.get("code")]
    assert len(codes) == len(set(codes)), "дубли кодов"
    for c in codes:
        assert c.isdigit() and len(c) == 8, c


def test_guide_codes_present():
    """Три кода из статьи гайда должны остаться в базе после расширения."""
    codes = {e.get("code") for e in ERRORS}
    assert {"68015101", "69012001", "69004101"} <= codes


def test_find_by_exact_code():
    e = server.find_zus_error("69004101")
    assert e and "застрахован" in e["title_ru"].lower()


def test_find_by_text_fragment():
    """Поиск ищет и по тегам/описанию, а не только по коду и польскому тексту."""
    assert server.find_zus_error("Mały ZUS Plus") is not None
    assert server.find_zus_error("wakacje składkowe") is not None
    assert server.find_zus_error("zawieszenie") is not None


def test_find_unknown_returns_none():
    assert server.find_zus_error("99999999") is None


def test_bot_reply_has_steps_and_severity():
    text = server._zus_error_reply("61111302")
    assert "DRA cz. II" in text
    assert "критическая" in text
    assert "1." in text and "2." in text


def test_bot_reply_unknown_code_is_helpful():
    text = server._zus_error_reply("12345678")
    assert "JDG_PBH" in text


def test_page_and_data_served():
    from fastapi.testclient import TestClient
    with TestClient(server.app) as c:
        assert c.get("/zus_err.html").status_code == 200
        payload = c.get("/zus_errors.json").json()
        assert payload["version"] >= 1
        assert len(payload["errors"]) == len(ERRORS)


def test_ai_index_includes_errors():
    import ai
    ai._index = None
    idx = ai._load_index()
    assert any(d["id"].startswith("zuserr-") for d in idx)
    hit = ai.retrieve("ошибка 69004101 brak raportu za ubezpieczonego")
    assert any("69004101" in (d.get("title") or "") for d in hit), [d["title"] for d in hit]
