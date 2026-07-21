"""Детерминированный расчёт ryczałtu (PPE).

Формула верифицирована реверсом трёх реальных месяцев (inFakt income_taxes):
  май 598.00 / июнь 393.00 / июль 462.00 — см. DIARY решение D4.

Тонкости (найдены реверсом, не из головы):
  1) вычитаются składki, УПЛАЧЕННЫЕ в расчётном месяце (= DRA прошлого месяца);
  2) Fundusz Pracy НЕ уменьшает przychód;
  3) podstawa округляется до полного злотого, затем налог — до полного злотого.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .config import load_rates

RULE_VERSION = "pl-jdg-ryczalt-2026.07"


def _zl_full(x: Decimal) -> Decimal:
    """Округление до полного злотого, HALF_UP (art. 63 Ordynacja podatkowa)."""
    return x.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class RyczaltResult:
    rule_version: str
    month: str
    przychod: Decimal
    spoleczne_paid: Decimal
    zdrowotna_paid: Decimal
    zdrowotna_deducted: Decimal
    podstawa: Decimal
    rate: Decimal
    tax: Decimal


def compute(
    month: str,
    przychod: Decimal,
    spoleczne_paid_in_month: Decimal,
    zdrowotna_paid_in_month: Decimal,
    rate_pct: str = "8.5",
    year: int = 2026,
) -> RyczaltResult:
    """przychod — netto фактур месяца (memoriał, по дате sprzedaży/выставления).

    spoleczne_paid_in_month — społeczne БЕЗ Fundusz Pracy, уплаченные в этом месяце.
    """
    share = Decimal(str(load_rates()["years"][str(year)]["zdrowotna"]["ryczalt_deduct_share"]))
    zdrow_deduct = zdrowotna_paid_in_month * share
    podstawa = _zl_full(przychod - spoleczne_paid_in_month - zdrow_deduct)
    rate = Decimal(rate_pct) / 100
    tax = _zl_full(podstawa * rate)
    return RyczaltResult(
        rule_version=RULE_VERSION,
        month=month,
        przychod=przychod,
        spoleczne_paid=spoleczne_paid_in_month,
        zdrowotna_paid=zdrowotna_paid_in_month,
        zdrowotna_deducted=zdrow_deduct,
        podstawa=podstawa,
        rate=rate,
        tax=tax,
    )
