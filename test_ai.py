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
