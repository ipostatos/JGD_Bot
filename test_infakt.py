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


def test_cache_ttl_and_clear(tmp_path, monkeypatch):
    """Кэш сводок: отдаёт свежее, молчит про протухшее, чистится по юзеру."""
    monkeypatch.setattr(infakt, "DB_PATH", tmp_path / "t.db")
    infakt.cache_put(1, "overview", {"income": 100})
    assert infakt.cache_get(1, "overview") == {"income": 100}
    assert infakt.cache_get(2, "overview") is None          # чужой кэш не отдаём
    assert infakt.cache_get(1, "ksef_sales") is None        # другой вид — тоже
    assert infakt.cache_get(1, "overview", ttl=0) is None    # протухший
    infakt.cache_clear(1)
    assert infakt.cache_get(1, "overview") is None


def test_disconnect_drops_cached_data(tmp_path, monkeypatch):
    """Отключил ключ — сводки о его деньгах в базе остаться не должны."""
    _valid_key(monkeypatch)
    monkeypatch.setattr(infakt, "DB_PATH", tmp_path / "t.db")
    infakt.save_key(9, "key-abc-123456")
    infakt.cache_put(9, "overview", {"income": 4200})
    infakt.delete_key(9)
    assert infakt.load_key(9) is None
    assert infakt.cache_get(9, "overview") is None


def test_amount_normalization():
    assert infakt._amount(123456) == 1234.56   # int = гроши
    assert infakt._amount("1 234,56") == 1234.56
    assert infakt._amount(1234.56) == 1234.56  # float = злотые
    assert infakt._amount(None) is None
    assert infakt._amount("abc") is None


def test_summary_amount_fields_are_real_api_names():
    """Имена полей сумм сверены живым ключом — они должны идти первыми.

    `_pick_amount` берёт первое существующее поле, поэтому порядок важен:
    подставим сразу и настоящее имя, и запасное с другим числом.
    """
    zus = {"sum_amount_price": 274864, "total_amount": 999999}
    tax = {"to_pay_price": 184000, "tax_amount": 999999}
    assert infakt._pick_amount(zus, "sum_amount_price", "total_amount") == 2748.64
    assert infakt._pick_amount(tax, "to_pay_price", "tax_amount") == 1840.0


def test_no_debug_dump_of_api_fields():
    """Отладочный raw_keys наружу больше не отдаём."""
    src = Path(__file__).with_name("infakt.py").read_text(encoding="utf-8")
    assert "raw_keys" not in src


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
