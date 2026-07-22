"""Реестры: чистая логика без сети + помеченные live-тесты против MF/VIES/GUS.

Live-тесты ходят в интернет; отключаются переменной NO_NET=1.
"""
import asyncio
import os

import pytest

import registries as R

LIVE = pytest.mark.skipif(os.environ.get("NO_NET") == "1", reason="сеть отключена")

WARSAW_NIP = "5252248481"          # m.st. Warszawa — VAT czynny, много счетов
WARSAW_ACC = "04103015080000000551183000"
ZWOLNIONY_NIP = "1133117581"       # реальный JDG на zwolnieniu: в White List пусто


def test_nip_checksum():
    assert R.nip_valid(WARSAW_NIP)
    assert R.nip_valid("7740001454")
    assert not R.nip_valid("5252248482")     # испорченная контрольная цифра
    assert not R.nip_valid("123")
    assert not R.nip_valid("abcdefghij")


def test_clean_nip():
    assert R.clean_nip(" 525-224-84-81 ") == WARSAW_NIP
    assert R.clean_nip("PL5252248481") == WARSAW_NIP


def test_nrb_checksum():
    assert R.nrb_valid(WARSAW_ACC)
    assert R.nrb_valid("PL " + WARSAW_ACC)
    assert not R.nrb_valid("04103015080000000551183001")
    assert not R.nrb_valid("12345")


def test_signals_vat_czynny_scores_higher_than_missing():
    wl = {"statusVat": "Czynny", "registrationLegalDate": "2002-10-01",
          "accountNumbers": ["x"]}
    good, score_good = R._signals(wl, {"valid": True}, None, None)
    _, score_none = R._signals(None, None, None, None)
    assert score_good > score_none
    assert any("czynny" in s["text"] for s in good)


def test_signals_removal_is_red_flag():
    wl = {"statusVat": "Czynny", "removalDate": "2025-01-05", "accountNumbers": []}
    sig, score = R._signals(wl, None, None, None)
    assert any(s["level"] == "bad" for s in sig)
    assert score < 40


def test_unavailable_vies_is_not_reported_as_invalid():
    """Таймаут VIES (None) не должен выглядеть как «VAT-UE не подтверждён»."""
    wl = {"statusVat": "Czynny", "accountNumbers": ["x"]}
    sig_down, _ = R._signals(wl, None, None, None)
    assert not any("VIES" in s["text"] for s in sig_down)
    sig_invalid, _ = R._signals(wl, {"valid": False}, None, None)
    assert any("VIES" in s["text"] and s["level"] == "warn" for s in sig_invalid)


def test_signals_ceidg_suspended_warns():
    sig, _ = R._signals(None, None, None, {"status": "ZAWIESZONY"})
    assert any("приостановлена" in s["text"] for s in sig)


def test_signals_no_accounts_warns_about_15k():
    sig, _ = R._signals({"statusVat": "Czynny", "accountNumbers": []}, None, None, None)
    assert any("15 000" in s["text"] for s in sig)


def test_check_nip_rejects_bad_checksum():
    res = asyncio.run(R.check_nip("1234567890"))
    assert res["valid"] is False


@LIVE
def test_live_whitelist_and_vies():
    res = asyncio.run(R.check_nip(WARSAW_NIP))
    assert res["valid"] and res["vat_status"] == "Czynny"
    assert res["regon"] == "015259640"
    assert res["accounts"], "у Варшавы должны быть счета в белом списке"
    # у крупных субъектов счетов тысячи — наружу отдаём не больше ACC_LIMIT
    assert len(res["accounts"]) <= R.ACC_LIMIT
    assert res["accounts_total"] >= len(res["accounts"])
    assert res["sources"]["whitelist"] == "ok"
    assert res["level"] == "ok"


@LIVE
def test_live_zwolniony_not_reported_as_missing_company():
    """Ключевая проверка: у zwolnionego в White List пусто, но это не «нет фирмы»."""
    res = asyncio.run(R.check_nip(ZWOLNIONY_NIP))
    assert res["valid"] is True
    assert res["vat_status"] == "brak"
    assert res["sources"]["whitelist"] == "empty"
    assert any("zwolnionego" in s["text"] for s in res["signals"])


@LIVE
def test_live_account_check_both_ways():
    good = asyncio.run(R.check_account(WARSAW_NIP, WARSAW_ACC))
    assert good["ok"] and good["assigned"] is True
    bad = asyncio.run(R.check_account(WARSAW_NIP, "61109010140000071219812874"))
    assert bad["ok"] and bad["assigned"] is False
    assert "15 000" in bad["note"]


@LIVE
def test_live_gus_test_environment():
    """GUS BIR-адаптер против тестовой среды с публичным тестовым ключом.
    Боевой ключ отличается только значением GUS_BIR_KEY и URL."""
    import httpx
    os.environ["GUS_BIR_KEY"] = "abcde12345abcde12345"
    old_base = R.GUS_BASE
    R.GUS_BASE = "https://wyszukiwarkaregontest.stat.gov.pl/wsBIR/UslugaBIRzewnPubl.svc"
    try:
        async def run():
            async with httpx.AsyncClient(timeout=30) as cl:
                return await R.gus_lookup(cl, "7740001454")
        data = asyncio.run(run())
        assert data and "ORLEN" in data["name"]
        assert data["regon"] == "610188201"
        assert "Płock" in data["address"]
    finally:
        R.GUS_BASE = old_base
        os.environ.pop("GUS_BIR_KEY", None)
