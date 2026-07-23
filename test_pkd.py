"""Подбор PKD: словарь профессий, ранжирование, переход 2007→2025, флаги VAT."""
import pkd


def test_data_loaded():
    idx = pkd.index()
    assert len(idx.codes) > 700, "в PKD 2025 около 728 подклассов"
    assert idx.codes["62.10.B"]["section"] == "K"
    assert idx.keys["62.01.Z"]["to"] == ["62.10.A", "62.10.B"]


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


def test_vat_flag_only_when_activity_is_in_the_name():
    """Doradztwo в названии — предупреждение; упоминание в пояснениях — не более чем
    повод присмотреться, иначе графический дизайн ложно объявляется потерей льготы."""
    doradztwo = pkd.index().analyse("70.20.Z")
    assert any(f["level"] == "warn" for f in doradztwo["flags"])
    assert "113" in doradztwo["flags"][0]["text"]

    design = pkd.index().analyse("74.12.Z")
    assert all(f["level"] != "warn" for f in design["flags"])


def test_rate_note_is_always_there():
    """PKD не определяет ставку ryczałtu — про это спрашивают чаще всего."""
    assert "PKWiU" in pkd.lookup("маникюр на дому")["note"]


def test_empty_query_is_safe():
    assert pkd.lookup("")["results"] == []
    assert pkd.lookup("qwertyuiop")["results"] == []
