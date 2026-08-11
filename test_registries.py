"""Реестры: чистая логика без сети + помеченные live-тесты против MF/VIES/GUS.

Live-тесты ходят в интернет; отключаются переменной NO_NET=1.
"""
import asyncio
import os
import re
from pathlib import Path

import httpx
import pytest
from dotenv import load_dotenv

import registries as R

load_dotenv()   # чтобы live-тесты видели CEIDG_TOKEN / GUS_BIR_KEY из .env

LIVE = pytest.mark.skipif(os.environ.get("NO_NET") == "1", reason="сеть отключена")
CEIDG_LIVE = pytest.mark.skipif(
    os.environ.get("NO_NET") == "1" or not os.environ.get("CEIDG_TOKEN"),
    reason="нет CEIDG_TOKEN или сеть отключена")
GUS_LIVE = pytest.mark.skipif(
    os.environ.get("NO_NET") == "1" or not os.environ.get("GUS_BIR_KEY"),
    reason="нет боевого GUS_BIR_KEY или сеть отключена")

WARSAW_NIP = "5252248481"          # m.st. Warszawa — VAT czynny, много счетов
WARSAW_ACC = "04103015080000000551183000"
ZWOLNIONY_NIP = "1133117581"       # реальный JDG на zwolnieniu: в White List пусто


async def _sid_stub(cl, key, headers):
    """Сессия GUS в тестах без сети: логин отдельно проверяется live-тестом."""
    return "sid-stub"


@pytest.fixture(autouse=True)
def _fresh_gus_counters():
    """Счётчик вызовов GUS живёт в модуле: без сброса соседний тест съедает
    лимит «3 в секунду», и следующий видит отказ вместо запроса."""
    R._gus_calls.clear()
    R._gus_session.update(sid=None, born=0.0)
    yield
    R._gus_calls.clear()


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


def test_registry_outage_is_error_not_empty_record(monkeypatch):
    """Реестр в дауне обязан выглядеть ошибкой, а не «записи нет».

    2026-07-23 CEIDG ушёл на przerwa serwisowa и ответил 301 на весь /api.
    Пока недоступность возвращалась как None, карточка живой фирмы честно
    писала «CEIDG: нет записи» — то есть врала о существующем предприятии.
    """
    import httpx

    class FakeResp:
        status_code = 301
        headers = {"location": "https://dane.biznes.gov.pl/"}

        def raise_for_status(self):
            raise httpx.HTTPStatusError("301", request=None, response=None)

    async def fake_get(self, url, **kw):
        return FakeResp()

    monkeypatch.setenv("CEIDG_TOKEN", "test-token")
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(R, "_cache_get", lambda nip: None)

    res = asyncio.run(R.check_nip(WARSAW_NIP))
    assert res["sources"]["ceidg"] == "error"
    assert res["sources"]["vies"] == "error"
    assert res["activity"] is None          # статус не выдумываем
    assert not any("CEIDG" in s["text"] for s in res["signals"])


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


def test_ceidg_address_built_from_live_shape():
    """Ключи взяты из живого ответа API v3 (Kraków, 2026-07-22)."""
    adr = {"ulica": "ul. Andrzeja Stopki", "budynek": "18c", "miasto": "Kraków",
           "kod": "31-999", "wojewodztwo": "MAŁOPOLSKIE", "kraj": "PL"}
    assert R._ceidg_address(adr) == "ul. Andrzeja Stopki 18c, 31-999 Kraków"
    assert R._ceidg_address({"ulica": "ul. Kwiatowa", "budynek": "1",
                             "lokal": "5", "miasto": "Gdańsk"}) == "ul. Kwiatowa 1/5, Gdańsk"
    assert R._ceidg_address(None) == ""
    assert R._ceidg_address({}) == ""


@CEIDG_LIVE
def test_live_ceidg_gives_data_for_zwolniony():
    """Живой токен: у zwolnionego в White List пусто, а CEIDG отдаёт фирму и PKD.

    Идём мимо check_nip, чтобы не попасть в суточный кэш.
    """
    import httpx

    async def run():
        async with httpx.AsyncClient(timeout=30) as cl:
            return await R.ceidg_lookup(cl, ZWOLNIONY_NIP)

    try:
        data = asyncio.run(run())
    except httpx.HTTPError as e:
        # przerwa serwisowa на стороне CEIDG — это не регрессия у нас
        # (2026-07-23 весь /api редиректили на главную)
        pytest.skip(f"CEIDG сейчас не отвечает: {e}")
    assert data, "CEIDG не ответил по реальному JDG"
    assert data["status"] == "AKTYWNY"
    assert data["name"] and data["owner"].strip()
    assert data["started"] == "2023-11-21"
    # PKD и pkd_main приходят только из детальной карточки — проверяем, что дошли
    assert data["pkd"], "PKD пустой: не подтянулась детальная карточка"
    assert data["pkd"][0] == data["pkd_main"].split(" · ")[0]


GUS_FIXTURE = Path(__file__).resolve().parent / "tests" / "fixtures" / "gus"


def test_pkd_dots():
    """GUS отдаёт код одной строкой, справочник — с точками."""
    assert R.pkd_dots("6210B") == "62.10.B"
    assert R.pkd_dots("9311Z") == "93.11.Z"
    assert R.pkd_dots("62.10.B") == "62.10.B"
    assert R.pkd_dots("1920") == "19.20"
    assert R.pkd_dots("") == "" and R.pkd_dots(None) == ""


def test_gus_pkd_parse_legal_entity():
    """Живой отчёт BIR12OsPrawnaPkd (ORLEN, боевая среда 2026-08-11)."""
    data = R.gus_pkd_parse((GUS_FIXTURE / "pkd_osprawna.xml").read_text(encoding="utf-8"))
    assert data["version"] == "2007"          # ORLEN в REGON ещё в старой классификации
    assert data["codes"][0] == {"code": "19.20.Z", "main": True, "silos": None,
                                "name": "Wytwarzanie i przetwarzanie produktów "
                                        "rafinacji ropy naftowej"}
    assert len(data["codes"]) == 22
    assert [c["main"] for c in data["codes"][1:]] == [False] * 21


def test_gus_pkd_parse_natural_person_dedupes_silos():
    """У особы физичной один код приходит по разу на «силос» — в ответе он один,
    и главным считается, если главный хотя бы в одном из них."""
    data = R.gus_pkd_parse((GUS_FIXTURE / "pkd_osfizyczna.xml").read_text(encoding="utf-8"))
    assert data["version"] == "2025"
    codes = [c["code"] for c in data["codes"]]
    assert len(codes) == len(set(codes))
    assert data["codes"][0]["code"] == "63.10.D" and data["codes"][0]["main"] is True
    assert data["codes"][0]["silos"] == "Prawna"


def test_gus_pkd_parse_error_payload_is_not_a_subject_without_codes():
    """Когда данных нет, GUS кладёт ErrorCode в тот же <dane> — принять это
    за «субъект без кодов» значит соврать про запись."""
    data = R.gus_pkd_parse((GUS_FIXTURE / "pkd_error.xml").read_text(encoding="utf-8"))
    assert data is None


def test_gus_pkd_report_chosen_by_subject_type(monkeypatch):
    """Отчёт зависит от типа субъекта, а на несовпадение есть один запасной заход."""
    asked: list[str] = []

    class Resp:
        def __init__(self, body): self.text = body
        def raise_for_status(self): pass

    def payload(inner):
        return ("<s><DanePobierzPelnyRaportResult>"
                + inner.replace("<", "&lt;").replace(">", "&gt;")
                + "</DanePobierzPelnyRaportResult></s>")

    async def fake_post(self, url, headers=None, content=None):
        report = re.search(rb"<ns:pNazwaRaportu>(.*?)</ns:pNazwaRaportu>", content).group(1).decode()
        asked.append(report)
        if report == "BIR12OsFizycznaPkd":       # «нет данных» в первом отчёте
            return Resp(payload("<root><dane><ErrorCode>4</ErrorCode></dane></root>"))
        return Resp(payload("<root><dane><praw_pkdWersja>2025</praw_pkdWersja>"
                            "<praw_pkdKod>6210B</praw_pkdKod>"
                            "<praw_pkdNazwa>PROGRAMOWANIE</praw_pkdNazwa>"
                            "<praw_pkdPrzewazajace>1</praw_pkdPrzewazajace></dane></root>"))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(R, "_gus_sid", _sid_stub)
    monkeypatch.setenv("GUS_BIR_KEY", "x" * 20)

    async def run():
        async with httpx.AsyncClient() as cl:
            return await R.gus_pkd(cl, "527131300", "F")

    data = asyncio.run(run())
    assert asked == ["BIR12OsFizycznaPkd", "BIR12OsPrawnaPkd"]
    assert data["report"] == "BIR12OsPrawnaPkd" and data["version"] == "2025"
    assert data["codes"][0]["code"] == "62.10.B"


def test_cache_of_previous_shape_is_ignored(tmp_path, monkeypatch):
    """Карточка старого состава в суточном кэше не должна пережить выкатку:
    иначе в день релиза человек видит ответ без новых полей."""
    monkeypatch.setattr(R, "DB_PATH", tmp_path / "cache.db")
    R._cache_put("7740001454", {"nip": "7740001454", "pkd_version": "2007"})
    assert R._cache_get("7740001454")["pkd_version"] == "2007"
    assert "_schema" not in R._cache_get("7740001454")   # служебное поле наружу не течёт
    monkeypatch.setattr(R, "CACHE_SCHEMA", R.CACHE_SCHEMA + 1)
    assert R._cache_get("7740001454") is None


def test_check_nip_falls_back_to_regon_codes(monkeypatch):
    """У юрлица в CEIDG записи нет, и раньше карточка оставалась вовсе без PKD.
    Коды берём из REGON, но не выдаём их за CEIDG: источник назван в ответе."""
    async def no_wl(cl, nip): return None
    async def no_vies(cl, nip): return None
    async def no_ceidg(cl, nip): return None
    async def gus(cl, nip):
        return {"name": "ORLEN", "regon": "610188201", "address": "Płock",
                "type": "P", "closed": None, "pkd": ["19.20.Z", "06.10.Z"],
                "pkd_items": [{"code": "19.20.Z", "name": "Rafinacja", "main": True},
                              {"code": "06.10.Z", "name": "Ropa", "main": False}],
                "pkd_version": "2007", "pkd_main": "19.20.Z · Rafinacja"}

    monkeypatch.setattr(R, "wl_subject", no_wl)
    monkeypatch.setattr(R, "vies_check", no_vies)
    monkeypatch.setattr(R, "ceidg_lookup", no_ceidg)
    monkeypatch.setattr(R, "gus_lookup", gus)
    monkeypatch.setattr(R, "_cache_get", lambda nip: None)
    monkeypatch.setattr(R, "_cache_put", lambda nip, payload: None)

    res = asyncio.run(R.check_nip("7740001454"))
    assert res["pkd"] == ["19.20.Z", "06.10.Z"]
    assert res["pkd_source"] == "gus"
    assert res["pkd_version"] == "2007"
    assert res["pkd_main"].startswith("19.20.Z")
    assert len(res["pkd_regon"]) == 2


def test_check_nip_prefers_ceidg_codes(monkeypatch):
    """Для JDG первоисточник — CEIDG: там коды меняет сам предприниматель,
    в REGON они доезжают позже. Версию классификации всё равно берём у REGON."""
    async def none_src(cl, nip): return None
    async def ceidg(cl, nip):
        return {"status": "AKTYWNY", "pkd": ["62.10.B"], "pkd_main": "62.10.B · …",
                "name": "JDG", "started": "2023-11-21"}
    async def gus(cl, nip):
        return {"name": "JDG", "regon": "527131300", "address": "", "type": "F",
                "closed": None, "pkd": ["62.10.B", "93.11.Z"],
                "pkd_items": [{"code": "62.10.B", "name": "", "main": False},
                              {"code": "93.11.Z", "name": "", "main": True}],
                "pkd_version": "2025", "pkd_main": "93.11.Z · …"}

    monkeypatch.setattr(R, "wl_subject", none_src)
    monkeypatch.setattr(R, "vies_check", none_src)
    monkeypatch.setattr(R, "ceidg_lookup", ceidg)
    monkeypatch.setattr(R, "gus_lookup", gus)
    monkeypatch.setattr(R, "_cache_get", lambda nip: None)
    monkeypatch.setattr(R, "_cache_put", lambda nip, payload: None)

    res = asyncio.run(R.check_nip("1133117581"))
    assert res["pkd"] == ["62.10.B"] and res["pkd_source"] == "ceidg"
    assert res["pkd_version"] == "2025"
    assert [c["code"] for c in res["pkd_regon"]] == ["62.10.B", "93.11.Z"]


def test_gus_pkd_needs_key_and_regon(monkeypatch):
    monkeypatch.delenv("GUS_BIR_KEY", raising=False)

    async def run(regon):
        async with httpx.AsyncClient() as cl:
            return await R.gus_pkd(cl, regon, "P")

    assert asyncio.run(run("610188201")) is None
    monkeypatch.setenv("GUS_BIR_KEY", "x" * 20)
    assert asyncio.run(run(None)) is None      # без REGON спрашивать нечего


@LIVE
def test_live_gus_test_environment():
    """GUS BIR-адаптер против тестовой среды с публичным тестовым ключом.
    Боевой ключ отличается только значением GUS_BIR_KEY и URL."""
    import httpx
    # ключ возвращаем как был: с приходом боевого ключа «просто удалить» значит
    # оставить следующие тесты без него
    old_key, old_base = os.environ.get("GUS_BIR_KEY"), R.GUS_BASE
    os.environ["GUS_BIR_KEY"] = "abcde12345abcde12345"
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
        if old_key is None:
            os.environ.pop("GUS_BIR_KEY", None)
        else:
            os.environ["GUS_BIR_KEY"] = old_key


@GUS_LIVE
def test_live_gus_pkd_gives_classification_version():
    """Боевой ключ: REGON отдаёт коды вместе с версией классификации.

    Версию не фиксируем числом — GUS переводит субъектов на PKD 2025 по мере
    перекодировки, и тест не должен падать в день, когда очередь дойдёт до ORLEN.
    """
    async def run():
        async with httpx.AsyncClient(timeout=30) as cl:
            base = await R.gus_lookup(cl, "7740001454")
            # логин + поиск + отчёт — это уже три вызова, а больше трёх в секунду
            # ключу нельзя: без паузы следующий запрос честно отсекает наш же лимит
            await asyncio.sleep(1.1)
            return base, await R.gus_pkd(cl, "610188201", "P")

    base, data = asyncio.run(run())
    assert data and data["version"] in ("2007", "2025", "2007+2025")
    assert data["report"] == "BIR12OsPrawnaPkd"
    main = [c for c in data["codes"] if c["main"]]
    assert len(main) == 1 and re.fullmatch(r"\d\d\.\d\d\.[A-Z]", main[0]["code"])
    assert not main[0]["name"].isupper(), "капс из REGON должен быть приведён к тексту"
    # gus_lookup складывает то же самое в карточку — иначе UI покажет пустоту
    assert base["pkd_version"] == data["version"] and base["pkd"]
