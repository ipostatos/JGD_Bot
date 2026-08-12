"""Закрытие месяца (Phase 1: read-only): сбор -> расчёт -> сверка -> отчёт.

Каждое число в отчёте имеет источник; итог сверки PASS/BLOCK.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from . import rules_tax, rules_zus
from .audit import Audit
from .config import Profile
from .infakt import Infakt, invoice_paid, zl
from .reconcile import Check, Reconciliation

# Обязанность выставлять фактуры в KSeF (zwolnieni z VAT включительно).
# Держим локально: hermes — самодостаточный движок, не тянет ksef.py из бота.
KSEF_SINCE = date(2026, 4, 1)


def _prev_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return f"{y}-{m:02d}"


@dataclass
class MonthReport:
    month: str
    lines: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    reconciliation: Reconciliation | None = None

    @property
    def verdict(self) -> str:
        if self.problems:
            return "REVIEW_REQUIRED"
        return self.reconciliation.verdict if self.reconciliation else "REVIEW_REQUIRED"

    def text(self) -> str:
        out = [f"=== Закрытие месяца {self.month} ===", *self.lines]
        if self.reconciliation:
            out.append("Сверка Hermes vs inFakt:")
            out.extend(self.reconciliation.lines())
        if self.problems:
            out.append(f"Проблемы ({len(self.problems)}):")
            out.extend(f"  ! {p}" for p in self.problems)
        else:
            out.append("Найдено проблем: 0")
        out.append(f"ИТОГ: {self.verdict}")
        return "\n".join(out)


def close_month(month: str, client: Infakt | None = None, profile: Profile | None = None,
                audit: Audit | None = None) -> MonthReport:
    client = client or Infakt()
    profile = profile or Profile()
    audit = audit or Audit()
    rep = MonthReport(month=month)
    year = int(month[:4])
    # Движок умеет считать налог и zdrowotną только для ryczałtu: у skala/liniowy
    # zdrowotna идёт от дохода (9%/4.9%), а не от тиров, и rules_zus/rules_tax
    # их не знают. Для не-ryczałt эти две проверки НЕ включаем в сверку — иначе
    # верный месяц краснит ложным BLOCK. Кокпит про это уже пишет в notes.
    is_ryczalt = profile.tax_form == "ryczalt"

    audit.log("month_close.start", {"month": month, "profile": vars(profile)})

    # 1. Продажи
    invoices = client.invoices(month)
    paid = [i for i in invoices if invoice_paid(i)]
    unpaid = [i for i in invoices if not invoice_paid(i)]
    przychod = sum((zl(i["net_price"]) for i in invoices), Decimal(0))
    rep.lines.append(f"Продажи: {len(invoices)} фактур, netto {przychod} zł  [api:invoices]")
    for i in unpaid:
        rep.problems.append(f"Фактура {i['number']} не оплачена (срок {i.get('payment_date')})")

    # 2. KSeF — номер требуется только с даты обязанности (01.04.2026):
    #    фактуры до неё законно без номера, проблемой их считать нельзя
    since = KSEF_SINCE.isoformat()
    no_ksef = [i for i in invoices
               if (i.get("invoice_date") or "") >= since and not i.get("ksef_number")]
    rep.lines.append(f"KSeF: {len(invoices) - len(no_ksef)}/{len(invoices)} с номером  [api:invoices.ksef_number]")
    for i in no_ksef:
        rep.problems.append(f"Фактура {i['number']} без номера KSeF")

    # 3. Расходы
    costs = client.costs(month)
    not_accounted = [
        c for c in costs
        if not any(s.get("symbol") == "cost_accounted" for s in c.get("statuses", []))
    ]
    rep.lines.append(f"Расходы: {len(costs)} документов, не проведено: {len(not_accounted)}  [api:documents/costs]")

    # 4. ZUS: расчёт Hermes vs inFakt
    #    revenue_ytd — накопленный przychód года ПО КОНЕЦ закрываемого месяца:
    #    суммировать весь год, включая месяцы после закрываемого, нельзя —
    #    перезакрытие марта в августе брало бы августовский тир zdrowotnej
    #    и краснило месяц, который в марте сходился грош-в-грош.
    revenue_ytd = sum(
        (zl(i["net_price"]) for i in client.invoices()
         if f"{year}-01" <= (i.get("invoice_date") or "")[:7] <= month),
        Decimal(0),
    )
    zus_calc = rules_zus.compute(year, profile.zus_regime, revenue_ytd, profile.chorobowe)
    fee = client.insurance_fee(month)
    checks: list[Check] = []
    if fee:
        checks += [
            Check("ZUS społeczne", zus_calc.spoleczne, zl(fee["social_amount_price"]), "api:insurance_fees.social"),
            Check("ZUS FP/FS", zus_calc.fp_fs, zl(fee["work_amount_price"]), "api:insurance_fees.work"),
        ]
        # zdrowotna сверяется только у ryczałtu: тир из rules_zus верен лишь для
        # него, у skala/liniowy это процент от дохода — сверять не с чем
        if is_ryczalt:
            checks.append(Check("ZUS zdrowotna", zus_calc.zdrowotna,
                                zl(fee["health_amount_price"]), "api:insurance_fees.health"))
        else:
            rep.lines.append("ZUS zdrowotna: сверка пропущена — не ryczałt "
                             "(zdrowotna идёт от дохода, движок её не считает)  [skip]")
        rep.lines.append(
            f"ZUS {month} ({zus_calc.regime}, {zus_calc.rule_version}): "
            f"społeczne+FP {zus_calc.spoleczne + zus_calc.fp_fs} zł, "
            f"срок {fee['payment_date']}, статус {fee['status']}  [rules+api]"
        )
    else:
        rep.problems.append(f"В inFakt нет składki ZUS за {month}")

    # 5. Ryczałt: расчёт Hermes vs inFakt
    #    składki, уплаченные в расчётном месяце = DRA прошлого месяца (решение D4)
    #    Только для ryczałtu: skala/liniowy считают налог по КПиР и лестнице/19%,
    #    чего движок не делает — гнать 8.5% против их PIT-zaliczki = ложный BLOCK.
    prev_fee = client.insurance_fee(_prev_month(month))
    tax_calc = None
    if not is_ryczalt:
        rep.lines.append(f"Налог: сверка пропущена — режим «{profile.tax_form}», "
                         f"движок считает только ryczałt  [skip]")
    elif prev_fee and prev_fee.get("status") == "paid":
        tax_calc = rules_tax.compute(
            month,
            przychod=przychod,
            spoleczne_paid_in_month=zl(prev_fee["social_amount_price"]),
            zdrowotna_paid_in_month=zl(prev_fee["health_amount_price"]),
            rate_pct=profile.ryczalt_rate,
            year=year,
        )
        tax = client.income_tax(month)
        if tax:
            checks.append(Check("Ryczałt (PPE)", tax_calc.tax, zl(tax["to_pay_price"]), "api:income_taxes"))
            rep.lines.append(
                f"Ryczałt {profile.ryczalt_rate}% ({tax_calc.rule_version}): podstawa {tax_calc.podstawa} zł, "
                f"налог {tax_calc.tax} zł, срок {tax['payment_date']}, статус {tax['status']}  [rules+api]"
            )
        else:
            rep.problems.append(f"В inFakt нет налога за {month}")
    else:
        rep.problems.append(f"Składki за {_prev_month(month)} не оплачены — расчёт ryczałtu заблокирован")

    # 6. Итого обязательства
    if fee and tax_calc:
        total = zl(fee["sum_amount_price"]) + (tax_calc.tax if tax_calc else Decimal(0))
        rep.lines.append(f"Обязательства месяца: ZUS {zl(fee['sum_amount_price'])} + PPE {tax_calc.tax} = {total} zł")

    rep.reconciliation = Reconciliation(checks)
    audit.log("month_close.done", {
        "month": month,
        "verdict": rep.verdict,
        "checks": [{"name": c.name, "hermes": c.hermes, "source": c.source, "ref": c.source_ref, "ok": c.ok}
                   for c in checks],
        "problems": rep.problems,
    })
    return rep
