"""Подбор PKD: словарь профессий, ранжирование, переход 2007→2025, флаги VAT.

Логику гоняем на фикстуре (tests/fixtures/pkd) — полный справочник весит мегабайт,
генерируется из файлов GUS и в репозиторий не едет, так что на CI его нет.
Отдельный тест проверяет полный набор, если он собран локально.
"""
import json
from pathlib import Path

import pytest

import pkd

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "tests" / "fixtures" / "pkd"
FULL = ROOT / "webapp" / "data" / "pkd.json"


@pytest.fixture(autouse=True)
def small_index(monkeypatch):
    """Индекс кэшируется на процесс — сбрасываем, чтобы подменить данные."""
    monkeypatch.setenv("JDG_PKD_DATA", str(FIXTURE))
    pkd.index.cache_clear()
    yield
    pkd.index.cache_clear()


def test_fixture_loads():
    idx = pkd.index()
    assert idx.codes["62.10.B"]["section"] == "K"
    assert idx.keys["62.01.Z"]["to"] == ["62.10.A", "62.10.B"]
    assert idx.syn and idx.rates["rates"], "словарь и ставки лежат в git, не в webapp/data"


def test_blogger_finds_official_wording():
    """«Блогер» есть в самом тексте GUS — подкласс должен всплыть первым."""
    top = pkd.lookup("я блогер")["results"][0]
    assert top["code"] == "90.11.Z"
    assert "bloger" in " ".join(top["includes"]).lower()


def test_specific_synonym_beats_general():
    """«Дизайн интерьера» не должен уступать общему «дизайнеру»."""
    assert pkd.lookup("я делаю дизайн интерьеров")["results"][0]["code"] == "74.13.Z"


def test_programming_query_without_dictionary_word():
    codes = [r["code"] for r in pkd.lookup("пишу код на питоне")["results"]]
    assert "62.10.B" in codes[:2]


def test_old_code_shows_migration():
    r = pkd.lookup("62.01.Z")
    assert r["kind"] == "migration"
    assert [x["code"] for x in r["migration"]["to"]] == ["62.10.A", "62.10.B"]
    assert "31.12.2026" in r["note"]


def test_search_hides_old_numbering():
    """В обычной выдаче «раньше это было 62.01.Z» — шум: спрашивают про сейчас."""
    assert all("was_pkd2007" not in r for r in pkd.lookup("пишу код")["results"])
    # но там, где старый код и есть предмет разговора, он остаётся
    assert "was_pkd2007" in pkd.index().analyse("62.10.B")
    assert pkd.audit(["62.01.Z"])["items"][0]["status"] == "outdated"


def test_vat_flag_only_when_activity_is_in_the_name():
    """Doradztwo в названии — предупреждение; упоминание в пояснениях — не более чем
    повод присмотреться, иначе графический дизайн ложно объявляется потерей льготы."""
    doradztwo = pkd.index().analyse("70.20.Z")
    assert any(f["level"] == "warn" for f in doradztwo["flags"])
    assert "113" in doradztwo["flags"][0]["text"]

    design = pkd.index().analyse("74.12.Z")
    assert all(f["level"] != "warn" for f in design["flags"])


def test_rate_hints_are_a_hint_not_a_verdict():
    it = pkd.index().analyse("62.10.B")["rate_hints"]
    assert it and it[0]["rate"] == 12
    assert pkd.index().analyse("69.10.Z")["rate_hints"][0]["rate"] == 17
    # услуги без отдельного пункта закона идут по 8,5%
    assert pkd.index().analyse("96.21.Z")["rate_hints"][0]["rate"] == 8.5
    assert "PKWiU" in pkd.lookup("маникюр на дому")["note"]


def test_audit_marks_outdated_codes():
    r = pkd.audit(["62.01.Z", "70.20.Z", "99.99.Z"])
    by_code = {i["code"]: i["status"] for i in r["items"]}
    assert by_code == {"62.01.Z": "outdated", "70.20.Z": "ok", "99.99.Z": "unknown"}
    assert r["outdated"] == 1 and "31.12.2026" in r["summary"]
    assert r["vat_warning_codes"] == ["70.20.Z"]


def test_empty_query_is_safe():
    assert pkd.lookup("")["results"] == []
    assert pkd.lookup("qwertyuiop")["results"] == []


@pytest.mark.skipif(not FULL.is_file(),
                    reason="полный справочник собирается tools/pkd_build.py и не в git")
def test_full_catalogue_when_built(monkeypatch):
    monkeypatch.delenv("JDG_PKD_DATA", raising=False)
    pkd.index.cache_clear()
    data = json.loads(FULL.read_text(encoding="utf-8"))
    assert len(data["codes"]) > 700, "в PKD 2025 около 728 подклассов"
    assert data["version"] == "PKD 2025"
    assert pkd.lookup("я блогер")["results"][0]["code"] == "90.11.Z"
