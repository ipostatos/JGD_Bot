"""Раздел «Счета и расчёты»: структура фактов и попадание в индекс ассистента.

Раздел закрывает самую большую дыру по данным чата: пять кластеров про банки и
счета (~3800 вопросов), которых в гайде нет вообще.
"""
import json
from pathlib import Path

BANKS = Path(__file__).parent / "webapp" / "banks.json"


def facts():
    return json.loads(BANKS.read_text(encoding="utf-8"))


def test_structure():
    d = facts()
    assert d["sources"], "у раздела должны быть источники: это правовые нормы"
    ids = [b["id"] for b in d["blocks"]]
    assert len(ids) == len(set(ids)), "id блоков уникальны — по ним строится индекс"
    for b in d["blocks"]:
        assert b["title"] and b["short"] and len(b["text"]) > 200
        for f in b.get("flags", []):
            assert f["level"] in ("warn", "note")


def test_key_numbers_are_present():
    """Порог сделки и срок ZAW-NR — то, ради чего раздел писался."""
    text = json.dumps(facts(), ensure_ascii=False)
    assert "15 000" in text
    assert "7 дней" in text and "ZAW-NR" in text
    assert "ROR" in text, "личный счёт в белый список не попадает — ключевой факт"


def test_no_bank_comparison():
    """Тарифы и сравнения банков в раздел не кладём: устаревшая таблица хуже пустой."""
    text = json.dumps(facts(), ensure_ascii=False).lower()
    for bank in ("mbank", "pko", "santander", "revolut", "ing ", "millennium"):
        assert bank not in text, f"в разделе появилось имя банка: {bank}"


def test_goes_into_assistant_index(monkeypatch):
    import ai
    ai._index = None
    entries = ai._load_index()
    banks = [e for e in entries if e["id"].startswith("banks-")]
    assert len(banks) == len(facts()["blocks"])
    assert any("15 000" in e["text"] for e in banks)
