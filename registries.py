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

DB_PATH = Path(os.environ.get("JDG_DB")
                or Path(__file__).parent / "news.db")
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
        # молчание VIES — это отказ сервиса, а не отрицательный ответ:
        # источник должен показаться «ошибкой», иначе человек прочитает
        # недоступность как «VAT-UE недействителен»
        r.raise_for_status()
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


# Лимиты GUS на один ключ (официальные, по часам суток): секунда, минута, час.
# Превышение блокировку не даёт сразу, но «пользователей, превышающих лимиты,
# будут информировать» — то есть это путь к отзыву ключа, а не к 429.
GUS_LIMITS = {
    "day": (3, 120, 6000),     # 08:00-16:59
    "shoulder": (3, 150, 8000),  # 06:00-07:59 и 17:00-21:59
    "night": (4, 200, 10000),  # 22:00-05:59
}
_gus_calls: list[float] = []
_gus_session: dict = {"sid": None, "born": 0.0}
_GUS_SESSION_TTL = 55 * 60     # сессия GUS живёт 60 минут, берём с запасом


def _gus_window(hour: int) -> tuple[int, int, int]:
    if 8 <= hour <= 16:
        return GUS_LIMITS["day"]
    if 22 <= hour or hour <= 5:
        return GUS_LIMITS["night"]
    return GUS_LIMITS["shoulder"]


def _gus_allow() -> bool:
    """Влезаем ли в лимиты ключа. Считаем свои же вызовы за секунду/минуту/час."""
    now = time.time()
    _gus_calls[:] = [t for t in _gus_calls if now - t < 3600]
    per_sec, per_min, per_hour = _gus_window(time.localtime(now).tm_hour)
    if (sum(1 for t in _gus_calls if now - t < 1) >= per_sec
            or sum(1 for t in _gus_calls if now - t < 60) >= per_min
            or len(_gus_calls) >= per_hour):
        return False
    _gus_calls.append(now)
    return True


async def _gus_sid(cl: httpx.AsyncClient, key: str, headers: dict) -> str | None:
    """Сессия GUS переиспользуется: логиниться на каждый запрос — прямой путь
    в «слишком много обращений», которые инструкция запрещает отдельным пунктом."""
    now = time.time()
    if _gus_session["sid"] and now - _gus_session["born"] < _GUS_SESSION_TTL:
        return _gus_session["sid"]
    if not _gus_allow():
        return None
    r = await cl.post(GUS_BASE, headers=headers, content=_gus_envelope(
        "Zaloguj", f"<ns:Zaloguj><ns:pKluczUzytkownika>{key}</ns:pKluczUzytkownika></ns:Zaloguj>"))
    r.raise_for_status()
    sid = _tag(r.text, "ZalogujResult")
    _gus_session.update(sid=sid or None, born=now if sid else 0.0)
    return sid or None


async def gus_lookup(cl: httpx.AsyncClient, nip: str) -> dict | None:
    """GUS BIR 1.1: логин ключом → sid → поиск по NIP. Протокол проверен на
    тестовой среде (wyszukiwarkaregontest + публичный тестовый ключ)."""
    key = os.environ.get("GUS_BIR_KEY")
    if not key:
        return None
    headers = {"Content-Type": "application/soap+xml; charset=utf-8"}
    sid = await _gus_sid(cl, key, headers)
    if not sid:
        log.warning("GUS: нет сессии (логин не дал sid либо упёрлись в лимит ключа)")
        return None
    if not _gus_allow():
        log.warning("GUS: свой лимит запросов исчерпан, пропускаем реестр")
        return None
    r = await cl.post(GUS_BASE, headers=dict(headers, sid=sid), content=_gus_envelope(
        "DaneSzukajPodmioty",
        f"<ns:DaneSzukajPodmioty><ns:pParametryWyszukiwania>"
        f"<dat:Nip>{nip}</dat:Nip></ns:pParametryWyszukiwania></ns:DaneSzukajPodmioty>"))
    r.raise_for_status()
    payload = (_tag(r.text, "DaneSzukajPodmiotyResult") or "").replace("&lt;", "<").replace("&gt;", ">")
    if not _tag(payload, "Regon") and _gus_session["sid"]:
        # пустой ответ бывает и когда сессия протухла раньше нашего TTL —
        # один раз перелогиниваемся, прежде чем считать, что записи нет
        _gus_session.update(sid=None, born=0.0)
        sid = await _gus_sid(cl, key, headers)
        if sid and _gus_allow():
            r = await cl.post(GUS_BASE, headers=dict(headers, sid=sid), content=_gus_envelope(
                "DaneSzukajPodmioty",
                f"<ns:DaneSzukajPodmioty><ns:pParametryWyszukiwania>"
                f"<dat:Nip>{nip}</dat:Nip></ns:pParametryWyszukiwania></ns:DaneSzukajPodmioty>"))
            payload = (_tag(r.text, "DaneSzukajPodmiotyResult") or "").replace(
                "&lt;", "<").replace("&gt;", ">")
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


def _ceidg_address(adr: dict | None) -> str:
    """Адрес из adresDzialalnosci (ключи сверены с живым ответом API v3)."""
    if not adr:
        return ""
    street = " ".join(x for x in (adr.get("ulica"), adr.get("budynek")) if x)
    if adr.get("lokal"):
        street += f"/{adr['lokal']}"
    city = " ".join(x for x in (adr.get("kod"), adr.get("miasto")) if x)
    return ", ".join(x for x in (street, city) if x)


async def ceidg_lookup(cl: httpx.AsyncClient, nip: str) -> dict | None:
    """CEIDG v3 (Bearer-токен из dane.biznes.gov.pl). Статус JDG, PKD и адрес.

    Проверено живым токеном 2026-07-22: поиск `/firmy?nip=` отдаёт только имя,
    статус и дату начала — PKD, адрес и dataWznowienia лежат в детальной
    карточке по ссылке `link`, поэтому ходим вторым запросом. Если деталь не
    ответила, деградируем до краткой записи, а не теряем ответ целиком.
    """
    token = os.environ.get("CEIDG_TOKEN")
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    r = await cl.get(f"{CEIDG_BASE}/firmy", params={"nip": nip}, headers=headers)
    if r.status_code == 204:
        return None            # 204 = записи нет (юрлицо, а не JDG) — это не ошибка
    if r.status_code != 200:
        # реестр не ответил ≠ «записи нет»: 2026-07-23 CEIDG ушёл на przerwa
        # serwisowa и стал редиректить весь /api на главную, а карточка при
        # этом писала «нет записи» про живую фирму. Ошибку поднимаем наверх —
        # там она станет источником в состоянии «ошибка».
        log.warning("CEIDG: HTTP %s", r.status_code)
        r.raise_for_status()   # httpx поднимает и на 3xx, а редирект — как раз наш случай
    firms = r.json().get("firmy") or []
    if not firms:
        return None
    f = firms[0]

    detail: dict = {}
    if f.get("link"):
        try:
            d = await cl.get(f["link"], headers=headers)
            if d.status_code == 200:
                body = d.json()
                inner = body.get("firma")
                detail = (inner[0] if isinstance(inner, list) and inner else body) or {}
        except Exception as e:                       # noqa: BLE001 — деградация
            log.warning("CEIDG detail: %s", e)
    src = {**f, **detail}

    pkd = [p.get("kod") if isinstance(p, dict) else p for p in (src.get("pkd") or [])]
    main = src.get("pkdGlowny") or {}
    main_code = main.get("kod") if isinstance(main, dict) else main
    if main_code:                                    # основной PKD — первым
        pkd = [main_code] + [p for p in pkd if p != main_code]
    return {"status": src.get("status"), "name": src.get("nazwa"),
            "started": src.get("dataRozpoczecia"), "resumed": src.get("dataWznowienia"),
            "suspended": src.get("dataZawieszenia"), "ended": src.get("dataZakonczenia"),
            "pkd": [p for p in pkd if p][:10],
            "pkd_main": f"{main_code} · {main.get('nazwa')}" if main_code and main.get("nazwa")
                        else (main_code or ""),
            "address": _ceidg_address(src.get("adresDzialalnosci")),
            "owner": (src.get("wlasciciel") or {}).get("imie", "") + " "
                     + (src.get("wlasciciel") or {}).get("nazwisko", "")}


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
    # ⚠️ vies=None означает «сервис не ответил», а не «VAT-UE недействителен»:
    # VIES регулярно флапает, и выдавать его таймаут за отрицательный ответ нельзя
    if vies and vies["valid"]:
        sig.append({"level": "ok", "text": "VAT-UE действителен (VIES) — можно WDT/reverse charge"})
        score += 5
    elif vies and not vies["valid"] and wl and wl.get("statusVat") == "Czynny":
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
        "pkd_main": (ceidg or {}).get("pkd_main") or "",
        "address": (wl or {}).get("workingAddress") or (wl or {}).get("residenceAddress")
                   or (gus or {}).get("address") or (ceidg or {}).get("address")
                   or (vies or {}).get("address") or "",
        "registered": (wl or {}).get("registrationLegalDate") or (ceidg or {}).get("started"),
        # у крупных субъектов счетов бывают тысячи (у m.st. Warszawa — 4340):
        # весь список раздувает ответ и суточный кэш, показываем первые ACC_LIMIT
        "accounts": ((wl or {}).get("accountNumbers") or [])[:ACC_LIMIT],
        "accounts_total": len((wl or {}).get("accountNumbers") or []),
        "vies": vies, "signals": signals, "score": score,
        "level": "ok" if score >= 70 else ("warn" if score >= 40 else "bad"),
        "sources": sources,
    }
    # кэшируем на сутки только полный результат: иначе разовый таймаут источника
    # застрянет в кэше на день и будет выглядеть как факт о контрагенте
    if any(v == "ok" for v in sources.values()) and "error" not in sources.values():
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
