"""Интеграция inFakt API v3: реальные суммы вместо ручного ввода.

Ключ юзер генерирует сам (inFakt → Ustawienia → Inne opcje → API), мы храним
его зашифрованным (Fernet) в SQLite и ходим только на чтение:
- invoices.json      → продажи netto с начала года (лимит VAT)
- insurance_fees.json→ взносы ZUS за последний месяц (сумма к оплате/статус)
- income_taxes.json  → налог за последний период

Ответы inFakt: {"entities": [...], "metainfo": {...}}. Поля сумм в грошах
или злотых зависят от ресурса — нормализуем защитно (см. _amount).
"""
import json
import logging
import os
import sqlite3
import time
from datetime import date
from pathlib import Path

import httpx
from cryptography.fernet import Fernet

log = logging.getLogger("jdg.infakt")

DB_PATH = Path(__file__).parent / "news.db"
BASE = "https://api.infakt.pl/api/v3"
_fernet = None


def fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = os.environ.get("FERNET_KEY", "")
        if not key:
            raise RuntimeError("FERNET_KEY не задан")
        _fernet = Fernet(key.encode())
    return _fernet


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS infakt_keys(
        user_id INTEGER PRIMARY KEY, enc BLOB, created INTEGER)""")
    return conn


def save_key(user_id: int, api_key: str):
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO infakt_keys VALUES(?,?,?)",
                  (user_id, fernet().encrypt(api_key.encode()), int(time.time())))


def load_key(user_id: int) -> str | None:
    with _db() as c:
        row = c.execute("SELECT enc FROM infakt_keys WHERE user_id=?",
                        (user_id,)).fetchone()
    return fernet().decrypt(row[0]).decode() if row else None


def delete_key(user_id: int):
    with _db() as c:
        c.execute("DELETE FROM infakt_keys WHERE user_id=?", (user_id,))


async def _get(cl: httpx.AsyncClient, key: str, path: str, params: dict):
    r = await cl.get(f"{BASE}/{path}", params=params,
                     headers={"X-inFakt-ApiKey": key})
    r.raise_for_status()
    return r.json()


def _amount(v) -> float | None:
    """inFakt отдаёт суммы то числом в грошах, то строкой в злотых."""
    if v is None:
        return None
    try:
        if isinstance(v, str):
            return float(v.replace(" ", "").replace(",", "."))
        # int в грошах (типовое для v3), float считаем злотыми
        return v / 100 if isinstance(v, int) else float(v)
    except (ValueError, TypeError):
        return None


def _pick_amount(entity: dict, *names) -> float | None:
    for n in names:
        if n in entity:
            a = _amount(entity[n])
            if a is not None:
                return a
    return None


async def check_key(api_key: str) -> bool:
    """Валидация ключа лёгким read-запросом."""
    try:
        async with httpx.AsyncClient(timeout=20) as cl:
            await _get(cl, api_key, "invoices.json", {"limit": 1})
        return True
    except Exception:
        return False


async def summary(user_id: int) -> dict:
    """Сводка для «Моего плана»: продажи YTD, ZUS и налог за последний период."""
    key = load_key(user_id)
    if not key:
        return {"connected": False}
    out = {"connected": True, "fetched_at": int(time.time())}
    year_start = date.today().replace(month=1, day=1).isoformat()

    async with httpx.AsyncClient(timeout=30) as cl:
        # продажи netto с начала года (постранично, максимум 5×100 фактур)
        try:
            net, offset = 0.0, 0
            while offset < 500:
                data = await _get(cl, key, "invoices.json", {
                    "limit": 100, "offset": offset,
                    "q[invoice_date_gteq]": year_start})
                ents = data.get("entities", [])
                for e in ents:
                    net += _pick_amount(e, "net_price", "net_value") or 0.0
                total = data.get("metainfo", {}).get("total_count", 0)
                offset += 100
                if offset >= total or not ents:
                    break
            out["net_ytd"] = round(net, 2)
        except Exception as e:
            log.warning("invoices failed: %s", e)

        # взносы ZUS: последняя запись
        try:
            data = await _get(cl, key, "insurance_fees.json", {"limit": 3})
            ents = data.get("entities", [])
            if ents:
                e = ents[0]
                out["zus"] = {
                    "period": e.get("period") or e.get("month") or "",
                    "total": _pick_amount(
                        e, "total_amount", "total", "amount", "gross_amount"),
                    "paid": bool(e.get("paid_date") or e.get("status") == "paid"),
                    "raw_keys": sorted(e.keys())[:20],
                }
        except Exception as e:
            log.warning("insurance_fees failed: %s", e)

        # налог: последняя запись
        try:
            data = await _get(cl, key, "income_taxes.json", {"limit": 3})
            ents = data.get("entities", [])
            if ents:
                e = ents[0]
                out["tax"] = {
                    "period": e.get("period") or e.get("month") or "",
                    "total": _pick_amount(
                        e, "tax_amount", "total_amount", "amount", "total"),
                    "paid": bool(e.get("paid_date") or e.get("status") == "paid"),
                    "raw_keys": sorted(e.keys())[:20],
                }
        except Exception as e:
            log.warning("income_taxes failed: %s", e)

    return out
