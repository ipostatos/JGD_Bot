"""Финансовый дашборд предпринимателя на данных inFakt.

Считает то, что уже лежит в inFakt, — никаких новых интеграций:
доход/расходы/налоги по месяцам, итоги года, запас до лимита VAT с прогнозом
даты и «здоровье учёта» с явными компонентами (никакого магического индекса —
каждый снятый балл имеет причину и текст).

Суммы inFakt приходят в грошах (int) — наружу отдаём злотые (float, 2 знака).
"""
from __future__ import annotations

import logging
from datetime import date

from calc import RATES
from hermes.infakt import Infakt, invoice_paid

log = logging.getLogger("jdg.dashboard")

MONTHS = 12
# Лимит zwolnienia podmiotowego берём из общего файла ставок, а не константой:
# он уже менялся (200 000 → 240 000), и второе место всегда забывают обновить.
VAT_LIMIT = float(RATES["vat_exemption_limit"])


def _zl(grosze) -> float:
    return round((grosze or 0) / 100, 2)


def _month_list(today: date, n: int = MONTHS) -> list[str]:
    """Последние n месяцев включая текущий, от старого к новому."""
    out, y, m = [], today.year, today.month
    for _ in range(n):
        out.append(f"{y}-{m:02d}")
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return list(reversed(out))


def _accounted(cost: dict) -> bool:
    return any(s.get("symbol") == "cost_accounted" for s in cost.get("statuses") or [])


def _health(invoices: list[dict], costs: list[dict], fees: list[dict],
            taxes: list[dict], today: date, vat_left_pct: float | None) -> dict:
    """Здоровье учёта: 100 минус явные вычеты. Каждый компонент виден юзеру."""
    score, items = 100, []

    overdue = [i for i in invoices if not invoice_paid(i)
               and (i.get("payment_date") or "9999") < today.isoformat()]
    if overdue:
        cut = min(30, 10 * len(overdue))
        score -= cut
        items.append({"level": "bad", "cut": cut,
                      "text": f"Просроченных неоплаченных фактур: {len(overdue)}"})

    year = str(today.year)
    this_year = [i for i in invoices if (i.get("invoice_date") or "").startswith(year)]
    no_ksef = [i for i in this_year if not i.get("ksef_number")]
    if no_ksef:
        score -= 5
        items.append({"level": "warn", "cut": 5,
                      "text": f"Фактур без номера KSeF в этом году: {len(no_ksef)}"})

    # ⚠️ Проверено на живых данных: статус «printed» значит «распечатано», а не
    # «не оплачено», а «not_applicable» и нулевые суммы — вовсе не обязательства.
    # Обвинять в просрочке по ним нельзя, поэтому: только реальные суммы, только
    # окно показанных месяцев и формулировка «не отмечено», а не «просрочено».
    window = set(_month_list(today))
    for kind, rows, amount_key in (("ZUS", fees, "sum_amount_price"),
                                   ("налога", taxes, "to_pay_price")):
        late = [r for r in rows
                if r.get("status") not in ("paid", "not_applicable")
                and (r.get(amount_key) or 0) > 0
                and (r.get("period") or "")[:7] in window
                and (r.get("payment_date") or "9999") < today.isoformat()]
        if late:
            score -= 10
            periods = ", ".join(sorted((r.get("period") or "")[:7] for r in late)[:3])
            items.append({"level": "warn", "cut": 10,
                          "text": f"Оплата {kind} не отмечена в inFakt: {periods} — "
                                  f"проверь, прошёл ли платёж"})

    not_acc = [c for c in costs if not _accounted(c)]
    if not_acc:
        score -= 5
        items.append({"level": "warn", "cut": 5,
                      "text": f"Расходов не проведено: {len(not_acc)}"})

    if vat_left_pct is not None and vat_left_pct < 10:
        score -= 10
        items.append({"level": "warn", "cut": 10,
                      "text": "До лимита VAT осталось меньше 10% — пора готовиться к регистрации"})

    score = max(0, min(100, score))
    if not items:
        items.append({"level": "ok", "cut": 0, "text": "Замечаний нет: всё оплачено и проведено"})
    return {"score": score, "items": items,
            "level": "ok" if score >= 80 else ("warn" if score >= 50 else "bad")}


def _vat_forecast(series: list[dict], year_income: float, vat_payer: bool) -> dict | None:
    """Запас до лимита VAT и дата исчерпания по темпу последних 3 месяцев."""
    if vat_payer:
        return None
    left = round(VAT_LIMIT - year_income, 2)
    recent = [m["income"] for m in series[-4:-1]]  # 3 полных месяца, без текущего
    pace = round(sum(recent) / len(recent), 2) if recent else 0.0
    out = {"limit": VAT_LIMIT, "used": year_income, "left": max(0.0, left),
           "used_pct": round(min(100.0, year_income / VAT_LIMIT * 100), 1),
           "pace": pace, "eta": None, "months_left": None}
    if left <= 0:
        out["eta"] = "уже превышен"
    elif pace > 0:
        months = left / pace
        out["months_left"] = round(months, 1)
        today = date.today()
        y, m = today.year, today.month + int(months)
        y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
        out["eta"] = f"{y}-{m:02d}" if months < 24 else "далеко"
    return out


def build(api_key: str | None = None, today: date | None = None,
          client: Infakt | None = None) -> dict:
    """Собирает дашборд одним проходом по данным inFakt (client — для тестов)."""
    today = today or date.today()
    client = client or Infakt(api_key)
    invoices = client.invoices()
    costs = client.costs()
    fees = client.insurance_fees()
    taxes = client.income_taxes()

    acc = client.account()
    vat_setting = str((acc.get("accounting_settings", {}).get("vat") or {}).get("value", ""))
    vat_payer = "zwol" not in vat_setting.lower() and bool(vat_setting)

    by_month = {m: {"month": m, "income": 0.0, "costs": 0.0, "zus": 0.0, "tax": 0.0}
                for m in _month_list(today)}
    for i in invoices:
        m = (i.get("invoice_date") or "")[:7]
        if m in by_month:
            by_month[m]["income"] += _zl(i.get("net_price"))
    for c in costs:
        m = (c.get("issue_date") or "")[:7]
        if m in by_month:
            by_month[m]["costs"] += _zl(c.get("net_price"))
    for f in fees:
        m = (f.get("period") or "")[:7]
        if m in by_month:
            by_month[m]["zus"] += _zl(f.get("sum_amount_price"))
    for t in taxes:
        m = (t.get("period") or "")[:7]
        if m in by_month:
            by_month[m]["tax"] += _zl(t.get("to_pay_price"))

    series = []
    for m in _month_list(today):
        row = by_month[m]
        row = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in row.items()}
        row["profit"] = round(row["income"] - row["costs"] - row["zus"] - row["tax"], 2)
        series.append(row)

    year = str(today.year)
    ytd = {"income": 0.0, "costs": 0.0, "zus": 0.0, "tax": 0.0}
    for i in invoices:
        if (i.get("invoice_date") or "").startswith(year):
            ytd["income"] += _zl(i.get("net_price"))
    for c in costs:
        if (c.get("issue_date") or "").startswith(year):
            ytd["costs"] += _zl(c.get("net_price"))
    for f in fees:
        if (f.get("period") or "").startswith(year):
            ytd["zus"] += _zl(f.get("sum_amount_price"))
    for t in taxes:
        if (t.get("period") or "").startswith(year):
            ytd["tax"] += _zl(t.get("to_pay_price"))
    ytd = {k: round(v, 2) for k, v in ytd.items()}
    ytd["profit"] = round(ytd["income"] - ytd["costs"] - ytd["zus"] - ytd["tax"], 2)
    ytd["burden_pct"] = (round((ytd["zus"] + ytd["tax"]) / ytd["income"] * 100, 1)
                         if ytd["income"] else None)

    vat = _vat_forecast(series, ytd["income"], vat_payer)
    health = _health(invoices, costs, fees, taxes, today,
                     None if not vat else 100 - vat["used_pct"])

    unpaid = [{"number": i.get("number"), "amount": _zl(i.get("gross_price")),
               "due": i.get("payment_date")}
              for i in invoices if not invoice_paid(i)]
    unpaid.sort(key=lambda x: x["due"] or "")

    return {
        "month": series[-1], "prev": series[-2] if len(series) > 1 else None,
        "series": series, "ytd": ytd, "vat": vat, "health": health,
        "vat_payer": vat_payer,
        "unpaid": unpaid[:5], "unpaid_total": round(sum(u["amount"] for u in unpaid), 2),
        "year": today.year,
    }
