"""Интеграция wFirma API2: обзор бухгалтерии на данных пользователя.

Авторизация — ключами API: пользователь создаёт accessKey и secretKey в своём
кабинете (Ustawienia → Bezpieczeństwo → Aplikacje → Klucze API). Третий ключ,
appKey, wFirma выдаёт под приложение. Если он есть у сервера (.env WFIRMA_APP_KEY),
пользователю хватает двух своих ключей; если нет — пользователь вводит и свой
appKey в окне ключей (webapp/wfirma.html), и он хранится зашифрованным вместе
с остальными. Свой appKey пользователя важнее серверного.

Что API отдаёт и что мы показываем (doc.wfirma.pl):
- companies/get           → плательщик ли VAT, KPiR или ryczałt, название;
- invoices/find           → продажи: даты, netto/VAT/brutto, оплата, KSeF;
- expenses/find           → расходы и их оплата;
- declaration_body_jpkvat → JPK_V7 за месяц: VAT к оплате, посчитанный wFirma.
Чего в API нет: начисленные ZUS и аванс PIT/ryczałt («pola wykorzystywane przy
rozliczaniu deklaracji, które nie są dostępne w API2»). Эти суммы мы не
выдумываем: в обзоре они помечены как недоступные.

Формат: запросы — XML, как в документации (у неё нет JSON-примеров), ответы —
JSON (`outputFormat=json`). Записи в ответе пронумерованы:
{"invoices": {"0": {"invoice": {...}}, "parameters": {...}}, "status": {"code": "OK"}}.
Ошибка приходит не HTTP-кодом, а в status.code («AUTH», «COMPANY ID REQUIRED»…).
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from datetime import date
from pathlib import Path
from typing import NamedTuple
from xml.sax.saxutils import escape

import httpx
from cryptography.fernet import InvalidToken

import infakt  # общий Fernet и кэш-подход: один ключ шифрования на приложение

log = logging.getLogger("jdg.wfirma")

DB_PATH = Path(os.environ.get("JDG_DB") or Path(__file__).parent / "news.db")
BASE = "https://api2.wfirma.pl"
APP_KEY = os.environ.get("WFIRMA_APP_KEY", "")
CACHE_TTL = 900
PAGE_LIMIT = 100
MAX_PAGES = 20            # 2000 документов — больше, чем у ИП за год с запасом

# Типы документов, которые не являются продажей: проформы, оферты, черновики
NOT_SALES = {"proforma", "offer", "proforma_bill", "offer_bill",
             "normal_draft", "margin_draft", "bill_draft"}

# Ошибки API → понятный человеку текст
ERRORS = {
    "AUTH": "wFirma не приняла ключи — проверь accessKey и secretKey",
    "ACCESS DENIED": "У ключа нет прав на чтение этих данных в wFirma",
    "AUTH FAILED LIMIT WAIT 5 MINUTES": "Слишком много неудачных попыток — подожди 5 минут",
    "TOTAL REQUESTS LIMIT EXCEEDED": "wFirma просит сделать паузу — попробуй через пару минут",
    "TOTAL EXECUTION TIME LIMIT EXCEEDED": "wFirma просит сделать паузу — попробуй через пару минут",
    "OUT OF SERVICE": "wFirma на техническом обслуживании — попробуй позже",
    "SNAPSHOT LOCK": "wFirma восстанавливает данные фирмы из копии — попробуй позже",
    "NO APP KEY": "Нужен ещё ключ приложения (appKey) — введи его в окне ключей",
}


def enabled() -> bool:
    # интеграция доступна всегда: appKey или у сервера, или у пользователя
    return True


def server_app_key() -> bool:
    return bool(APP_KEY)


class Keys(NamedTuple):
    access: str
    secret: str
    company_id: str | None
    app_key: str | None = None
    company_name: str = ""
    created: int = 0


class WfirmaError(RuntimeError):
    def __init__(self, code: str, companies: list | None = None):
        super().__init__(code)
        self.code = code
        self.companies = companies or []

    @property
    def human(self) -> str:
        return ERRORS.get(self.code, f"wFirma ответила ошибкой ({self.code})")


# ── хранилище ключей ─────────────────────────────────────────────────────────
def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS wfirma_keys(
        user_id INTEGER PRIMARY KEY, enc BLOB, company_id TEXT, created INTEGER)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS wfirma_cache(
        user_id INTEGER, kind TEXT, payload TEXT, ts INTEGER,
        PRIMARY KEY(user_id, kind))""")
    return conn


def save_keys(user_id: int, access: str, secret: str, company_id: str | None,
              app_key: str | None = None, company_name: str = ""):
    payload = {"a": access, "s": secret, "n": company_name}
    if app_key:
        payload["k"] = app_key
    blob = infakt.fernet().encrypt(json.dumps(payload).encode())
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO wfirma_keys VALUES(?,?,?,?)",
                  (user_id, blob, company_id or None, int(time.time())))
        c.execute("DELETE FROM wfirma_cache WHERE user_id=?", (user_id,))


def load_keys(user_id: int) -> Keys | None:
    with _db() as c:
        row = c.execute("SELECT enc, company_id, created FROM wfirma_keys WHERE user_id=?",
                        (user_id,)).fetchone()
    if not row:
        return None
    try:
        k = json.loads(infakt.fernet().decrypt(row[0]))
    except (InvalidToken, ValueError):
        log.warning("wfirma: ключи user=%s не расшифровались (ротация FERNET_KEY?)", user_id)
        return None
    return Keys(k["a"], k["s"], row[1], k.get("k") or None, k.get("n") or "", row[2] or 0)


def status(user_id: int) -> dict:
    """Что сохранено — без запроса в wFirma и без самих ключей."""
    k = load_keys(user_id)
    out = {"server_app_key": server_app_key(), "connected": bool(k)}
    if k:
        out |= {"company_name": k.company_name, "since": k.created,
                "own_app_key": bool(k.app_key),
                "access_tail": k.access[-4:]}
    return out


def delete_keys(user_id: int):
    with _db() as c:
        c.execute("DELETE FROM wfirma_keys WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM wfirma_cache WHERE user_id=?", (user_id,))


def cache_get(user_id: int, kind: str) -> dict | None:
    with _db() as c:
        row = c.execute("SELECT payload, ts FROM wfirma_cache WHERE user_id=? AND kind=?",
                        (user_id, kind)).fetchone()
    if row and time.time() - row[1] < CACHE_TTL:
        return json.loads(row[0])
    return None


def cache_put(user_id: int, kind: str, payload: dict):
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO wfirma_cache VALUES(?,?,?,?)",
                  (user_id, kind, json.dumps(payload, ensure_ascii=False), int(time.time())))


# ── клиент ───────────────────────────────────────────────────────────────────
def _conditions(conds: list[tuple[str, str, str]]) -> str:
    return "".join(f"<condition><field>{escape(f)}</field><operator>{escape(o)}</operator>"
                   f"<value>{escape(str(v))}</value></condition>" for f, o, v in conds)


def find_body(module: str, conds=(), page: int = 1, limit: int = PAGE_LIMIT,
              order_desc: str | None = None) -> str:
    order = f"<order><desc>{escape(order_desc)}</desc></order>" if order_desc else ""
    cond = f"<conditions>{_conditions(list(conds))}</conditions>" if conds else ""
    return (f"<api><{module}><parameters>{cond}<page>{page}</page>"
            f"<limit>{limit}</limit>{order}</parameters></{module}></api>")


def records(resp: dict, module: str, single: str) -> list[dict]:
    """{"invoices": {"0": {"invoice": {...}}, "parameters": ...}} → [{...}]."""
    block = resp.get(module) or {}
    if isinstance(block, list):                      # на всякий случай
        items = block
    else:
        items = [v for k, v in block.items() if k != "parameters"]
    return [i.get(single) for i in items if isinstance(i, dict) and isinstance(i.get(single), dict)]


def _total(resp: dict, module: str) -> int:
    try:
        return int(((resp.get(module) or {}).get("parameters") or {}).get("total") or 0)
    except (TypeError, ValueError, AttributeError):
        return 0


class Client:
    def __init__(self, access: str, secret: str, company_id: str | None = None,
                 http: httpx.AsyncClient | None = None, app_key: str | None = None):
        self.company_id = company_id
        self._own = http is None
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(40, connect=15))
        self._headers = {"accessKey": access, "secretKey": secret,
                         "appKey": app_key or APP_KEY,
                         "Content-Type": "application/xml"}

    async def close(self):
        if self._own:
            await self._http.aclose()

    async def call(self, action: str, body: str = "", **extra) -> dict:
        params = {"inputFormat": "xml", "outputFormat": "json", **extra}
        if self.company_id:
            params["company_id"] = self.company_id
        r = await self._http.post(f"{BASE}/{action}", params=params,
                                  content=body.encode(), headers=self._headers)
        if r.status_code >= 500:
            raise WfirmaError("OUT OF SERVICE")
        try:
            data = r.json()
        except ValueError:
            # declaration_body_jpkvat отдаёт сам XML декларации
            if action.startswith("declaration_body") and r.status_code == 200:
                return {"status": {"code": "OK"}, "xml": r.text}
            raise WfirmaError(f"HTTP {r.status_code}")
        code = ((data.get("status") or {}).get("code") or "").upper()
        if code != "OK":
            raise WfirmaError(code or f"HTTP {r.status_code}")
        return data

    async def find_all(self, module: str, single: str, conds=(), order_desc="date") -> list[dict]:
        out: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            resp = await self.call(f"{module}/find",
                                   find_body(module, conds, page, PAGE_LIMIT, order_desc))
            recs = records(resp, module, single)
            out += recs
            total = _total(resp, module)
            if not recs or len(recs) < PAGE_LIMIT or (total and len(out) >= total):
                break
        return out

    async def company(self) -> dict:
        resp = await self.call("companies/get")
        recs = records(resp, "companies", "company")
        return recs[0] if recs else {}

    async def companies_of_user(self) -> list[dict]:
        """Фирмы пользователя — когда у ключа их несколько (COMPANY ID REQUIRED)."""
        resp = await self.call("user_companies/find", find_body("user_companies"))
        out = []
        for uc in records(resp, "user_companies", "user_company"):
            comp = uc.get("company") if isinstance(uc.get("company"), dict) else {}
            cid = comp.get("id") or uc.get("company_id")
            if cid:
                out.append({"id": str(cid), "name": comp.get("name") or comp.get("altname")
                            or f"Фирма {cid}"})
        return out

    async def jpk_vat(self, year: int, month: int) -> dict | None:
        resp = await self.call(f"declaration_body_jpkvat/get/{year}/{month}")
        return parse_jpk(resp.get("xml") or json.dumps(resp))


# ── разбор ───────────────────────────────────────────────────────────────────
def amount(v) -> float:
    """wFirma отдаёт суммы строками в злотых: "970.00"."""
    if v in (None, ""):
        return 0.0
    try:
        return float(str(v).replace(" ", "").replace(",", "."))
    except ValueError:
        return 0.0


def norm_invoice(i: dict) -> dict:
    total = amount(i.get("total_composed") or i.get("total"))
    paid = amount(i.get("alreadypaid"))
    return {
        "number": i.get("fullnumber") or "",
        "date": (i.get("date") or "")[:10],
        "netto": amount(i.get("netto")),
        "vat": amount(i.get("tax")),
        "total": total,
        "paid": i.get("paymentstate") == "paid",
        "left": round(max(0.0, total - paid), 2),
        "due": (i.get("paymentdate") or "")[:10] or None,
        "type": i.get("type") or "",
        "ksef_number": i.get("ksef_reference_number") or None,
        "currency": i.get("currency") or "PLN",
    }


def norm_expense(e: dict) -> dict:
    # суммы расхода в документации модуля не описаны: берём первое найденное
    # поле итога, иначе складываем части расхода (expense_parts)
    netto = next((amount(e[k]) for k in ("netto", "netto_sum", "total_netto") if e.get(k)), None)
    if netto is None:
        parts = e.get("expense_parts") or {}
        parts = [v.get("expense_part") for v in (parts.values() if isinstance(parts, dict) else parts)
                 if isinstance(v, dict) and isinstance(v.get("expense_part"), dict)]
        netto = sum(amount(p.get("netto") or p.get("price")) for p in parts)
    return {"date": (e.get("date") or "")[:10], "netto": round(netto, 2),
            "paid": str(e.get("paid")) == "1",
            "due": (e.get("payment_date") or "")[:10] or None}


def parse_jpk(xml: str) -> dict | None:
    """VAT за месяц из JPK_V7: P_51 — к уплате, P_62 — переносится дальше."""
    def num(tag):
        m = re.search(rf"<(?:\w+:)?{tag}>([-\d.,]+)</", xml or "")
        return amount(m.group(1)) if m else None
    to_pay, carry = num("P_51"), num("P_62")
    if to_pay is None and carry is None:
        return None
    return {"to_pay": to_pay or 0.0, "carry": carry or 0.0}


# ── сводки ───────────────────────────────────────────────────────────────────
def _invoices_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "фактура"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "фактуры"
    return "фактур"


def _months(today: date, n: int = 12) -> list[str]:
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        out.append(f"{y}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out[::-1]


async def fetch_all(client: Client, today: date) -> dict:
    since = _months(today)[0] + "-01"
    comp = await client.company()
    invoices = [norm_invoice(i) for i in await client.find_all(
        "invoices", "invoice", [("date", "ge", since)])
        if (i.get("type") or "") not in NOT_SALES]
    expenses = [norm_expense(e) for e in await client.find_all(
        "expenses", "expense", [("date", "ge", since)])
        if str(e.get("draft") or "0") != "1"]
    jpk = None
    if str(comp.get("vat_payer")) == "1":
        y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        try:
            jpk = await client.jpk_vat(y, m)
            if jpk:
                jpk["period"] = f"{y}-{m:02d}"
        except WfirmaError as e:
            log.info("wfirma jpk %s-%s: %s", y, m, e.code)
    return {"company": comp, "invoices": invoices, "expenses": expenses, "jpk": jpk}


def build_overview(raw: dict, today: date) -> dict:
    """Та же форма, что dashboard.build для inFakt — кокпит рисует обе.

    ZUS и налог в API wFirma нет: zus/tax = 0, а taxes_available = False —
    интерфейс подписывает прибыль как «до налогов» и не рисует обязательства.
    """
    comp, invs, exps = raw["company"], raw["invoices"], raw["expenses"]
    months = _months(today)
    by = {m: {"month": m, "income": 0.0, "costs": 0.0, "zus": 0.0, "tax": 0.0} for m in months}
    for i in invs:
        if i["date"][:7] in by:
            by[i["date"][:7]]["income"] += i["netto"]
    for e in exps:
        if e["date"][:7] in by:
            by[e["date"][:7]]["costs"] += e["netto"]
    series = []
    for m in months:
        r = {k: round(v, 2) if isinstance(v, float) else v for k, v in by[m].items()}
        r["profit"] = round(r["income"] - r["costs"], 2)
        series.append(r)

    year = str(today.year)
    ytd_inc = round(sum(i["netto"] for i in invs if i["date"].startswith(year)), 2)
    ytd_cost = round(sum(e["netto"] for e in exps if e["date"].startswith(year)), 2)
    ytd = {"income": ytd_inc, "costs": ytd_cost, "zus": 0.0, "tax": 0.0,
           "profit": round(ytd_inc - ytd_cost, 2), "burden_pct": None}

    vat_payer = str(comp.get("vat_payer")) == "1"
    vat = None
    if not vat_payer:
        import dashboard
        vat = dashboard._vat_forecast(series, ytd_inc, False)

    unpaid = sorted(({"number": i["number"], "amount": i["left"], "due": i["due"]}
                     for i in invs if not i["paid"] and i["left"] > 0),
                    key=lambda u: u["due"] or "")
    overdue = [u for u in unpaid if u["due"] and u["due"] < today.isoformat()]
    cost_unpaid = [e for e in exps if not e["paid"] and e["due"] and e["due"] < today.isoformat()]

    ks_since = "2026-04-01"            # с этой даты фактура без номера KSeF — сигнал
    no_ksef = [i for i in invs if i["date"] >= ks_since and not i["ksef_number"]]
    with_ksef = [i for i in invs if i["ksef_number"]]

    items, score = [], 100
    if overdue:
        cut = min(30, 10 * len(overdue))
        items.append({"level": "bad", "cut": cut,
                      "text": f"Клиенты просрочили оплату: {len(overdue)} {_invoices_word(len(overdue))}"})
        score -= cut
    else:
        items.append({"level": "ok", "cut": 0, "text": "Просроченных оплат от клиентов нет"})
    if cost_unpaid:
        cut = min(20, 5 * len(cost_unpaid))
        items.append({"level": "warn", "cut": cut,
                      "text": f"Расходы, не оплаченные в срок: {len(cost_unpaid)}"})
        score -= cut
    if no_ksef:
        cut = min(30, 5 * len(no_ksef))
        items.append({"level": "warn", "cut": cut,
                      "text": f"Без номера KSeF с апреля 2026: {len(no_ksef)} {_invoices_word(len(no_ksef))}"})
        score -= cut
    elif with_ksef:
        items.append({"level": "ok", "cut": 0, "text": "Все фактуры с апреля 2026 — в KSeF"})
    if not invs:
        items.append({"level": "warn", "cut": 10, "text": "За 12 месяцев в wFirma нет продаж"})
        score -= 10
    score = max(0, score)
    health = {"score": score, "items": items,
              "level": "ok" if score >= 80 else "warn" if score >= 50 else "bad"}

    return {
        "provider": "wfirma",
        "company_name": comp.get("altname") or comp.get("name") or "",
        "tax_form": {"taxregister": "KPiR", "lumpregister": "ryczałt"}.get(comp.get("tax"), ""),
        "taxes_available": False,
        "month": series[-1], "prev": series[-2], "series": series, "ytd": ytd,
        "vat": vat, "vat_payer": vat_payer, "jpk": raw.get("jpk"),
        "health": health,
        "ksef": {"with": len(with_ksef), "without": len(no_ksef)},
        "unpaid": unpaid[:5], "unpaid_total": round(sum(u["amount"] for u in unpaid), 2),
        "year": today.year,
    }


def ksef_invoices(raw: dict) -> list[dict]:
    """Фактуры в форме inFakt для ksef.sales_from_invoices (гроши, ksef_number)."""
    return [{"invoice_date": i["date"], "gross_price": round(i["total"] * 100),
             "ksef_number": i["ksef_number"]} for i in raw["invoices"]]


async def _raw(user_id: int, today: date | None = None) -> dict | None:
    keys = load_keys(user_id)
    if not keys:
        return None
    cached = cache_get(user_id, "raw")
    if cached:
        return cached
    client = Client(keys.access, keys.secret, keys.company_id, app_key=keys.app_key)
    try:
        raw = await fetch_all(client, today or date.today())
    finally:
        await client.close()
    cache_put(user_id, "raw", raw)
    return raw


async def overview(user_id: int) -> dict:
    raw = await _raw(user_id)
    if raw is None:
        return {"connected": False}
    return {"connected": True} | build_overview(raw, date.today())


async def summary(user_id: int) -> dict:
    """Короткая сводка для «Моего плана» — в той же форме, что у inFakt."""
    raw = await _raw(user_id)
    if raw is None:
        return {"connected": False}
    year = str(date.today().year)
    out = {"connected": True, "provider": "wfirma", "fetched_at": int(time.time()),
           "company_name": raw["company"].get("altname") or raw["company"].get("name") or "",
           "net_ytd": round(sum(i["netto"] for i in raw["invoices"]
                                if i["date"].startswith(year)), 2)}
    if raw.get("jpk"):
        out["vat_due"] = raw["jpk"]
    return out


async def connect(user_id: int, access: str, secret: str, company_id: str | None,
                  app_key: str | None = None) -> dict:
    """Проверка ключей лёгким запросом; несколько фирм — вернуть список для выбора."""
    if not (app_key or APP_KEY):
        raise WfirmaError("NO APP KEY")
    client = Client(access, secret, company_id, app_key=app_key)
    try:
        try:
            comp = await client.company()
        except WfirmaError as e:
            if e.code == "COMPANY ID REQUIRED":
                raise WfirmaError(e.code, await client.companies_of_user())
            raise
    finally:
        await client.close()
    name = comp.get("altname") or comp.get("name") or ""
    save_keys(user_id, access, secret, company_id, app_key, name)
    return {"ok": True, "company_name": name}
