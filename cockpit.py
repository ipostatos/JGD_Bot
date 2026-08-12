"""Кокпит бухгалтерии (движок Hermes) поверх мультиюзерных ключей inFakt.

Каждый юзер подключает свой ключ (Fernet-хранилище в infakt.py) и получает:
- закрытие месяца: чек-лист + независимый расчёт ZUS/ryczałtu + сверка;
- платёжные пакеты ZUS и налога со своими реквизитами.

Профиль расчёта не спрашиваем у юзера — выводим из его же данных:
режим налога из account/details, ставку ryczałtu из последних фактур,
режим ZUS и dobrowolne chorobowe подбором к фактическим суммам inFakt
(движок детерминирован, кандидатов мало — перебор честнее анкеты).
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal, InvalidOperation

import infakt as keystore
from hermes.audit import Audit
from hermes.checklist import close_month
from hermes.config import Profile
from hermes.infakt import Infakt, zl
from hermes import rules_zus
from hermes.payments import tax_package, zus_package

log = logging.getLogger("jdg.cockpit")

_ZUS_LABELS = {"standard": "duzy", "prefer": "pref", "start": "ulga_na_start"}


def _derive_profile(client: Infakt, month: str) -> tuple[Profile, list[str]]:
    """Профиль из данных юзера + подбор режима ZUS/chorobowe к суммам inFakt."""
    notes: list[str] = []
    acc = client.account()
    settings = acc.get("accounting_settings", {})

    tax_form = "ryczalt" if settings.get("pit_type", {}).get("value") == "flat_rate" else "other"
    if tax_form != "ryczalt":
        notes.append("налоговый режим не ryczałt — проверка налога недоступна (пока)")

    # ставка из фактур: символ inFakt может прийти как «8,5» или лейбл —
    # нормализуем и проверяем, что это положительное число, иначе Decimal(rate)
    # в rules_tax уронит всё закрытие в 502. Смотрим ВСЕ фактуры года, а не
    # первые 10: _list_all не обещает порядок, и срез мог не застать смену ставки
    def _norm_rate(sym: str | None) -> str | None:
        s = (sym or "").strip().replace(",", ".").rstrip("%").strip()
        try:
            return s if Decimal(s) > 0 else None
        except (InvalidOperation, ValueError):
            return None

    rate = "8.5"
    year_str = str(int(month[:4]))
    rates_seen = {r for i in client.invoices()
                  if (i.get("invoice_date") or "").startswith(year_str)
                  for s in i.get("services", [])
                  if (r := _norm_rate(s.get("flat_rate_tax_symbol")))}
    if len(rates_seen) == 1:
        rate = rates_seen.pop()
    elif len(rates_seen) > 1:
        notes.append(f"смешанные ставки ryczałtu {sorted(rates_seen)} — проверка налога пропущена")
        tax_form = "mixed"

    # стартовая гипотеза из label inFakt, затем подбор к фактической składce
    zus_value = settings.get("zus", {}).get("value", "")
    mapped = next((v for k, v in _ZUS_LABELS.items() if k in zus_value), None)
    regime, chorobowe = mapped or "duzy", False
    fee = client.insurance_fee(month)
    if fee:
        year = int(month[:4])
        candidates = [mapped] if mapped else []
        candidates += [r for r in ("duzy", "pref", "ulga_na_start") if r != mapped]
        found = False
        for cand in candidates:
            for chor in (False, True):
                calc = rules_zus.compute(year, cand, Decimal("0"), chor)
                if calc.spoleczne == zl(fee["social_amount_price"]) and \
                   calc.fp_fs == zl(fee["work_amount_price"]):
                    regime, chorobowe, found = cand, chor, True
                    break
            if found:
                break
        if not found:
            notes.append("режим ZUS не удалось подобрать к суммам inFakt — сверка покажет расхождение")
        elif mapped and regime != mapped:
            notes.append(f"режим ZUS по суммам = {regime}, хотя inFakt пишет «{zus_value}»")

    return Profile(tax_form=tax_form, ryczalt_rate=rate,
                   chorobowe=chorobowe, zus_regime=regime), notes


def _close_sync(user_id: int, api_key: str, month: str) -> dict:
    client = Infakt(api_key)
    profile, notes = _derive_profile(client, month)
    audit = Audit()
    rep = close_month(month, client, profile, audit)
    rec = rep.reconciliation
    return {
        "month": month,
        "verdict": rep.verdict,
        "lines": rep.lines,
        "problems": rep.problems,
        "profile_notes": notes,
        "profile": {"zus": profile.zus_regime, "rate": profile.ryczalt_rate,
                    "chorobowe": profile.chorobowe},
        "checks": [{"name": c.name, "hermes": str(c.hermes), "source": str(c.source),
                    "ok": c.ok} for c in (rec.checks if rec else [])],
    }


def _pay_sync(api_key: str, month: str) -> dict:
    client = Infakt(api_key)
    packages = [p for p in (zus_package(client, month), tax_package(client, month)) if p]
    return {"month": month, "packages": [
        {"kind": p.kind, "recipient": p.recipient, "account": p.account,
         "amount": str(p.amount), "title": p.title, "deadline": p.deadline,
         "status": p.status} for p in packages]}


async def overview(user_id: int) -> dict:
    """Финансовый обзор: месяц, год, 12-месячная серия, VAT-прогноз, здоровье.

    Собирается из четырёх списков inFakt целиком, поэтому держим результат
    в кэше (см. infakt.CACHE_TTL): открытие вкладки не должно каждый раз
    выгребать всю бухгалтерию.
    """
    import dashboard
    key = keystore.load_key(user_id)
    if not key:
        return {"connected": False}
    cached = keystore.cache_get(user_id, "overview")
    if cached:
        return {"connected": True, "cached": True} | cached
    data = await asyncio.to_thread(dashboard.build, key)
    keystore.cache_put(user_id, "overview", data)
    return {"connected": True, "cached": False} | data


async def close(user_id: int, month: str) -> dict:
    key = keystore.load_key(user_id)
    if not key:
        return {"connected": False}
    return {"connected": True} | await asyncio.to_thread(_close_sync, user_id, key, month)


async def pay(user_id: int, month: str) -> dict:
    key = keystore.load_key(user_id)
    if not key:
        return {"connected": False}
    keystore.cache_clear(user_id)   # платёж меняет картину — сводку пересобрать
    return {"connected": True} | await asyncio.to_thread(_pay_sync, key, month)
