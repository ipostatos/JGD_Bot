"""Проверка контрагента по NIP: White List VAT (MF) + VIES + GUS BIR + CEIDG.

Источники и что дают:
- **White List MF** (`wl-api.mf.gov.pl`, БЕЗ ключа) — статус VAT, REGON, KRS,
  адрес, дата регистрации, номера счетов, отказ/удаление из реестра.
- **VIES** (`ec.europa.eu`, БЕЗ ключа) — действителен ли VAT-UE для сделок с ЕС.
- **GUS BIR 1.1** (SOAP, нужен ключ `GUS_BIR_KEY`) — название, REGON, адрес,
  тип субъекта; работает и для тех, кого нет в реестре VAT.
- **CEIDG v3** (нужен токен `CEIDG_TOKEN`) — статус деятельности JDG
  (активна/приостановлена/вычеркнута) и PKD.

⚠️ Главная тонкость: у **VAT-zwolnionego JDG в White List записи НЕТ**
(`subject: null`). Это НЕ значит «фирмы не существует» — только «не плательщик
VAT». Проверено живьём на реальном NIP zwolnionego. Поэтому пустой White List
никогда не показываем как «не найдено»: за базовыми данными идём в GUS/CEIDG.

Без ключей модуль работает в урезанном режиме: см. `sources` в ответе.
"""
import asyncio
import json
import logging
import os
import re
import sqlite3
import time
from datetime import date
from pathlib import Path

import httpx

log = logging.getLogger("jdg.registries")

DB_PATH = Path(__file__).parent / "news.db"
WL_BASE = "https://wl-api.mf.gov.pl/api"
VIES_BASE = "https://ec.europa.eu/taxation_customs/vies/rest-api"
GUS_BASE = os.environ.get(
    "GUS_BIR_URL", "https://wyszukiwarkaregon.stat.gov.pl/wsBIR/UslugaBIRzewnPubl.svc")
CEIDG_BASE = "https://dane.biznes.gov.pl/api/ceidg/v3"
ACC_LIMIT = 50  # сколько счетов из белого списка отдаём наружу

CEIDG_STATUS_RU = {
    "AKTYWNY": ("ok", "деятельность активна"),
    "ZAWIESZONY": ("warn", "деятельность приостановлена (zawieszona)"),
    "WYKRESLONY": ("bad", "вычеркнут из CEIDG (деятельность закрыта)"),
    "WYLACZNIE_W_FORMIE_SPOLKI": ("warn", "работает только в форме spółki cywilnej"),
    "OCZEKUJE_NA_ROZPOCZECIE_DZIALALNOSCI": ("warn", "деятельность ещё не начата"),
}


# ── валидация ────────────────────────────────────────────────────────────────
def clean_nip(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


def nip_valid(nip: str) -> bool:
    """Контрольная сумма NIP (веса 6,5,7,2,3,4,5,6,7 mod 11)."""
    if len(nip) != 10 or not nip.isdigit():
        return False
    weights = (6, 5, 7, 2, 3, 4, 5, 6, 7)
    checksum = sum(w * int(d) for w, d in zip(weights, nip)) % 11
    return checksum != 10 and checksum == int(nip[9])


def nrb_valid(acc: str) -> bool:
    """Контрольная сумма польского счёта NRB (26 цифр) по алгоритму IBAN mod-97."""
    acc = re.sub(r"\s", "", acc or "").upper().removeprefix("PL")
    if len(acc) != 26 or not acc.isdigit():
        return False
    # IBAN = PL + NRB; первые 4 символа (PL + 2 контрольные) переносим в конец,
    # буквы в цифры: P=25, L=21
    return int(acc[2:] + "2521" + acc[:2]) % 97 == 1


# ── кэш (реестры обновляются раз в сутки) ────────────────────────────────────
def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS nip_cache(
        nip TEXT, day TEXT, payload TEXT, PRIMARY KEY(nip, day))""")
    return conn


def _cache_get(nip: str) -> dict | None:
    with _db() as c:
        row = c.execute("SELECT payload FROM nip_cache WHERE nip=? AND day=?",
                        (nip, date.today().isoformat())).fetchone()
    return json.loads(row[0]) if row else None


def _cache_put(nip: str, payload: dict):
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO nip_cache VALUES(?,?,?)",
                  (nip, date.today().isoformat(), json.dumps(payload, ensure_ascii=False)))
        c.execute("DELETE FROM nip_cache WHERE day < ?", (date.today().isoformat(),))


# ── источники ────────────────────────────────────────────────────────────────
async def wl_subject(cl: httpx.AsyncClient, nip: str) -> dict | None:
    """White List: карточка субъекта или None, если его нет в реестре VAT."""
    r = await cl.get(f"{WL_BASE}/search/nip/{nip}",
                     params={"date": date.today().isoformat()})
    r.raise_for_status()
    return (r.json().get("result") or {}).get("subject")


async def wl_check_account(cl: httpx.AsyncClient, nip: str, account: str) -> bool:
    """Привязан ли счёт к NIP в белом списке на сегодня."""
    r = await cl.get(f"{WL_BASE}/check/nip/{nip}/bank-account/{account}",
                     params={"date": date.today().isoformat()})
    r.raise_for_status()
    return (r.json().get("result") or {}).get("accountAssigned") == "TAK"


async def vies_check(cl: httpx.AsyncClient, nip: str) -> dict | None:
    r = await cl.get(f"{VIES_BASE}/ms/PL/vat/{nip}")
    if r.status_code != 200:
        return None
    d = r.json()
    return {"valid": bool(d.get("isValid")), "name": (d.get("name") or "").strip(),
            "address": (d.get("address") or "").replace("\n", ", ").strip()}


_GUS_NS = ('xmlns:soap="http://www.w3.org/2003/05/soap-envelope" '
           'xmlns:ns="http://CIS/BIR/PUBL/2014/07" '
           'xmlns:dat="http://CIS/BIR/PUBL/2014/07/DataContract"')


def _gus_envelope(action: str, body: str) -> bytes:
    return (f'<soap:Envelope {_GUS_NS}>'
            f'<soap:Header xmlns:wsa="http://www.w3.org/2005/08/addressing">'
            f'<wsa:To>{GUS_BASE}</wsa:To>'
            f'<wsa:Action>http://CIS/BIR/PUBL/2014/07/IUslugaBIRzewnPubl/{action}</wsa:Action>'
            f'</soap:Header><soap:Body>{body}</soap:Body></soap:Envelope>').encode()


def _tag(xml: str, name: str) -> str | None:
    m = re.search(rf"<{name}>(.*?)</{name}>", xml, re.S)
    return m.group(1).strip() if m else None


async def gus_lookup(cl: httpx.AsyncClient, nip: str) -> dict | None:
    """GUS BIR 1.1: логин ключом → sid → поиск по NIP. Протокол проверен на
    тестовой среде (wyszukiwarkaregontest + публичный тестовый ключ)."""
    key = os.environ.get("GUS_BIR_KEY")
    if not key:
        return None
    headers = {"Content-Type": "application/soap+xml; charset=utf-8"}
    r = await cl.post(GUS_BASE, headers=headers, content=_gus_envelope(
        "Zaloguj", f"<ns:Zaloguj><ns:pKluczUzytkownika>{key}</ns:pKluczUzytkownika></ns:Zaloguj>"))
    r.raise_for_status()
    sid = _tag(r.text, "ZalogujResult")
    if not sid:
        log.warning("GUS: логин не дал sid")
        return None
    r = await cl.post(GUS_BASE, headers=dict(headers, sid=sid), content=_gus_envelope(
        "DaneSzukajPodmioty",
        f"<ns:DaneSzukajPodmioty><ns:pParametryWyszukiwania>"
        f"<dat:Nip>{nip}</dat:Nip></ns:pParametryWyszukiwania></ns:DaneSzukajPodmioty>"))
    r.raise_for_status()
    payload = (_tag(r.text, "DaneSzukajPodmiotyResult") or "").replace("&lt;", "<").replace("&gt;", ">")
    if not _tag(payload, "Regon"):
        return None
    street = " ".join(x for x in (_tag(payload, "Ulica"), _tag(payload, "NrNieruchomosci")) if x)
    lokal = _tag(payload, "NrLokalu")
    addr = ", ".join(x for x in (
        f"{street}/{lokal}" if lokal else street,
        " ".join(x for x in (_tag(payload, "KodPocztowy"), _tag(payload, "Miejscowosc")) if x),
    ) if x)
    return {"name": _tag(payload, "Nazwa"), "regon": _tag(payload, "Regon"),
            "address": addr, "type": _tag(payload, "Typ"),
            "closed": _tag(payload, "DataZakonczeniaDzialalnosci") or None}


async def ceidg_lookup(cl: httpx.AsyncClient, nip: str) -> dict | None:
    """CEIDG v3 (Bearer-токен из dane.biznes.gov.pl). Даёт статус JDG и PKD.
    ⚠️ адаптер не проверен живым токеном — при ошибке молча деградируем."""
    token = os.environ.get("CEIDG_TOKEN")
    if not token:
        return None
    r = await cl.get(f"{CEIDG_BASE}/firmy", params={"nip": nip},
                     headers={"Authorization": f"Bearer {token}"})
    if r.status_code != 200:
        log.warning("CEIDG: HTTP %s", r.status_code)
        return None
    firms = r.json().get("firmy") or []
    if not firms:
        return None
    f = firms[0]
    pkd = f.get("pkd") or []
    return {"status": f.get("status"), "name": f.get("nazwa"),
            "started": f.get("dataRozpoczecia"), "suspended": f.get("dataZawieszenia"),
            "ended": f.get("dataZakonczenia"),
            "pkd": [p if isinstance(p, str) else p.get("kod") for p in pkd][:10],
            "owner": (f.get("wlasciciel") or {}).get("imie", "") + " "
                     + (f.get("wlasciciel") or {}).get("nazwisko", "")}


# ── агрегация ────────────────────────────────────────────────────────────────
def _signals(wl: dict | None, vies: dict | None, gus: dict | None,
             ceidg: dict | None) -> tuple[list[dict], int]:
    """Сигналы на русском + балл. Балл — сумма явных компонентов, не «магия»."""
    sig: list[dict] = []
    score = 50

    if wl:
        status = wl.get("statusVat")
        if status == "Czynny":
            sig.append({"level": "ok", "text": "Плательщик VAT — czynny"})
            score += 20
        elif status == "Zwolniony":
            sig.append({"level": "warn", "text": "VAT zwolniony — фактуру выставит без VAT"})
            score += 10
        if wl.get("registrationDenialDate"):
            sig.append({"level": "bad", "text": f"Отказ в регистрации VAT {wl['registrationDenialDate']}"})
            score -= 40
        if wl.get("removalDate"):
            sig.append({"level": "bad", "text": f"Удалён из реестра VAT {wl['removalDate']}"})
            score -= 40
        if wl.get("restorationDate"):
            sig.append({"level": "warn", "text": f"Восстановлен в реестре {wl['restorationDate']}"})
        reg = wl.get("registrationLegalDate")
        if reg:
            years = (date.today() - date.fromisoformat(reg)).days / 365.25
            if years >= 2:
                sig.append({"level": "ok", "text": f"В реестре VAT с {reg} ({years:.0f} г.)"})
                score += 10
            else:
                sig.append({"level": "warn", "text": f"В реестре VAT недавно — с {reg}"})
        n_acc = len(wl.get("accountNumbers") or [])
        if n_acc:
            sig.append({"level": "ok", "text": f"Счетов в белом списке: {n_acc}"})
            score += 10
        else:
            sig.append({"level": "warn",
                        "text": "Счетов в белом списке нет — оплату свыше 15 000 zł нельзя списать в расходы"})
            score -= 10
        if wl.get("hasVirtualAccounts"):
            sig.append({"level": "warn", "text": "Использует виртуальные счета — проверяй счёт отдельно"})
    else:
        sig.append({"level": "warn",
                    "text": "В реестре VAT не значится (обычное дело для zwolnionego JDG)"})

    if ceidg and ceidg.get("status"):
        level, text = CEIDG_STATUS_RU.get(ceidg["status"], ("warn", f"статус CEIDG: {ceidg['status']}"))
        sig.append({"level": level, "text": f"CEIDG: {text}"})
        score += 15 if level == "ok" else (-40 if level == "bad" else -15)
    if gus and gus.get("closed"):
        sig.append({"level": "bad", "text": f"GUS: деятельность прекращена {gus['closed']}"})
        score -= 40
    if vies and vies["valid"]:
        sig.append({"level": "ok", "text": "VAT-UE действителен (VIES) — можно WDT/reverse charge"})
        score += 5
    elif wl and wl.get("statusVat") == "Czynny":
        sig.append({"level": "warn", "text": "В VIES не подтверждён — сделки внутри ЕС проверь отдельно"})

    return sig, max(0, min(100, score))


async def check_nip(raw_nip: str) -> dict:
    """Единая карточка контрагента. Кэш — на сутки (реестры суточные)."""
    nip = clean_nip(raw_nip)
    if not nip_valid(nip):
        return {"nip": nip, "valid": False,
                "error": "NIP не проходит проверку контрольной суммы — проверь цифры"}
    cached = _cache_get(nip)
    if cached:
        return cached | {"cached": True}

    async with httpx.AsyncClient(timeout=25, headers={"User-Agent": "JDG-Hub/1.0"}) as cl:
        wl, vies, gus, ceidg = await asyncio.gather(
            wl_subject(cl, nip), vies_check(cl, nip),
            gus_lookup(cl, nip), ceidg_lookup(cl, nip),
            return_exceptions=True)

    sources = {}
    for name, val in (("whitelist", wl), ("vies", vies), ("gus", gus), ("ceidg", ceidg)):
        if isinstance(val, Exception):
            log.warning("%s failed: %s", name, val)
            sources[name] = "error"
        elif val is None:
            sources[name] = "off" if name in ("gus", "ceidg") and not os.environ.get(
                {"gus": "GUS_BIR_KEY", "ceidg": "CEIDG_TOKEN"}[name]) else "empty"
        else:
            sources[name] = "ok"
    wl, vies, gus, ceidg = [None if isinstance(v, Exception) else v for v in (wl, vies, gus, ceidg)]

    signals, score = _signals(wl, vies, gus, ceidg)
    out = {
        "nip": nip, "valid": True, "cached": False, "checked_at": int(time.time()),
        "name": (wl or {}).get("name") or (gus or {}).get("name")
                or (ceidg or {}).get("name") or (vies or {}).get("name") or "",
        "vat_status": (wl or {}).get("statusVat") or "brak",
        "activity": (ceidg or {}).get("status"),
        "regon": (wl or {}).get("regon") or (gus or {}).get("regon"),
        "krs": (wl or {}).get("krs"),
        "pkd": (ceidg or {}).get("pkd") or [],
        "address": (wl or {}).get("workingAddress") or (wl or {}).get("residenceAddress")
                   or (gus or {}).get("address") or (vies or {}).get("address") or "",
        "registered": (wl or {}).get("registrationLegalDate") or (ceidg or {}).get("started"),
        # у крупных субъектов счетов бывают тысячи (у m.st. Warszawa — 4340):
        # весь список раздувает ответ и суточный кэш, показываем первые ACC_LIMIT
        "accounts": ((wl or {}).get("accountNumbers") or [])[:ACC_LIMIT],
        "accounts_total": len((wl or {}).get("accountNumbers") or []),
        "vies": vies, "signals": signals, "score": score,
        "level": "ok" if score >= 70 else ("warn" if score >= 40 else "bad"),
        "sources": sources,
    }
    if any(v == "ok" for v in sources.values()):
        _cache_put(nip, out)
    return out


async def check_account(raw_nip: str, raw_account: str) -> dict:
    """Проверка счёта перед оплатой: контрольная сумма + белый список MF."""
    nip, acc = clean_nip(raw_nip), re.sub(r"\s", "", raw_account or "").upper().removeprefix("PL")
    if not nip_valid(nip):
        return {"ok": False, "error": "NIP не проходит проверку контрольной суммы"}
    if not nrb_valid(acc):
        return {"ok": False, "error": "Номер счёта не проходит проверку контрольной суммы (нужны 26 цифр)"}
    async with httpx.AsyncClient(timeout=25, headers={"User-Agent": "JDG-Hub/1.0"}) as cl:
        assigned = await wl_check_account(cl, nip, acc)
    return {"ok": True, "assigned": assigned, "nip": nip, "account": acc,
            "note": "Счёт есть в белом списке на сегодня" if assigned else
                    "Счёта НЕТ в белом списке: при оплате свыше 15 000 zł — потеря расхода "
                    "и солидарная ответственность за VAT контрагента"}
