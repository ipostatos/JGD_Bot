"""inFakt: шифрование ключей, нормализация сумм; faq_miner: сбор Q&A (без сети)."""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("FERNET_KEY",
                      "0000000000000000000000000000000000000000000=")

import infakt  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent / "tools"))
import faq_miner  # noqa: E402


def _valid_key(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    infakt._fernet = None


def test_key_roundtrip(tmp_path, monkeypatch):
    _valid_key(monkeypatch)
    monkeypatch.setattr(infakt, "DB_PATH", tmp_path / "t.db")
    infakt.save_key(7, "secret-api-key-123")
    assert infakt.load_key(7) == "secret-api-key-123"
    # в базе лежит шифртекст, не ключ
    raw = (tmp_path / "t.db").read_bytes()
    assert b"secret-api-key-123" not in raw
    infakt.delete_key(7)
    assert infakt.load_key(7) is None


def test_amount_normalization():
    assert infakt._amount(123456) == 1234.56   # int = гроши
    assert infakt._amount("1 234,56") == 1234.56
    assert infakt._amount(1234.56) == 1234.56  # float = злотые
    assert infakt._amount(None) is None
    assert infakt._amount("abc") is None


def test_faq_collect_qa():
    export = {"messages": [
        {"id": 1, "type": "message", "text": "Подскажите, как перейти с ulga na start на preferencyjne? Что подавать?"},
        {"id": 2, "type": "message", "reply_to_message_id": 1,
         "text": "Подаёшь ZUS ZWUA и потом ZUA с кодом 05 70, в течение 7 дней"},
        {"id": 3, "type": "message", "reply_to_message_id": 1,
         "text": "Я делал через eZUS, всё онлайн, инструкция есть в гайде"},
        {"id": 4, "type": "message", "text": "короткий вопрос?"},
        {"id": 5, "type": "message", "text": "/start"},
    ]}
    qa = faq_miner.collect_qa(export)
    assert len(qa) == 1
    assert qa[0]["n"] == 2
    assert "preferencyjne" in qa[0]["q"]


def test_faq_index_merge(tmp_path, monkeypatch):
    import ai
    faq_file = tmp_path / "faq.json"
    faq_file.write_text(json.dumps([
        {"q": "Как открыть фирменный счёт для JDG?",
         "a": "Подходит любой банк, главное отдельный счёт для бизнеса.",
         "topic": "банки"}]), encoding="utf-8")
    monkeypatch.setattr(ai, "FAQ_JSON", faq_file)
    monkeypatch.setattr(ai, "_index", None)
    arts = ai.retrieve("какой банк выбрать для фирменного счёта")
    assert any(a["id"].startswith("chatfaq") for a in arts)
    monkeypatch.setattr(ai, "_index", None)  # не отравлять другие тесты
