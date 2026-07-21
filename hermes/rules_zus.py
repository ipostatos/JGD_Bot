"""Детерминированный расчёт składek ZUS. Никакого LLM, только Decimal.

Верификация (KEDU user, 01-06/2026, грош-в-грош):
  pref (podstawa 1441.80): 281.44 + 115.34 + 24.08 = 420.86, FP нет
  duzy (podstawa 5652.00): 1103.27 + 452.16 + 94.39 = 1649.82, FP 138.47
  zdrowotna ryczałt тир 1: 498.35
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .config import load_rates

RULE_VERSION = "pl-jdg-zus-2026.07"


def _gr(x: Decimal) -> Decimal:
    """Округление до grosza, HALF_UP (как ZUS)."""
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ZusResult:
    rule_version: str
    year: int
    regime: str
    podstawa: Decimal
    emerytalne: Decimal
    rentowe: Decimal
    wypadkowe: Decimal
    chorobowe: Decimal
    fp_fs: Decimal
    zdrowotna: Decimal

    @property
    def spoleczne(self) -> Decimal:
        """Suma społecznych — то, что в API insurance_fees.social_amount_price."""
        return self.emerytalne + self.rentowe + self.wypadkowe + self.chorobowe

    @property
    def total(self) -> Decimal:
        return self.spoleczne + self.fp_fs + self.zdrowotna


def zdrowotna_ryczalt(year: int, revenue_ytd: Decimal) -> Decimal:
    """Тир składki zdrowotnej по накопленному przychodu года."""
    tiers = load_rates()["years"][str(year)]["zdrowotna"]["ryczalt_tiers"]
    for tier in tiers:
        if tier["max_revenue"] is None or revenue_ytd <= Decimal(str(tier["max_revenue"])):
            return Decimal(str(tier["monthly"]))
    raise AssertionError("unreachable: последний тир без max_revenue")


def compute(year: int, regime: str, revenue_ytd: Decimal, chorobowe: bool = False) -> ZusResult:
    """regime: 'ulga_na_start' | 'pref' | 'duzy'. revenue_ytd — для тира zdrowotnej (ryczałt)."""
    rates = load_rates()
    y = rates["years"][str(year)]
    sp = rates["spoleczne_rates"]

    if regime == "ulga_na_start":
        podstawa = Decimal("0")
    elif regime == "pref":
        podstawa = Decimal(str(y["pref_base"]))
    elif regime == "duzy":
        podstawa = Decimal(str(y["duzy_base"]))
    else:
        raise ValueError(f"неизвестный режим ZUS: {regime}")

    emeryt = _gr(podstawa * Decimal(str(sp["emerytalna"])))
    rent = _gr(podstawa * Decimal(str(sp["rentowa"])))
    wypadk = _gr(podstawa * Decimal(str(sp["wypadkowa"])))
    chor = _gr(podstawa * Decimal(str(sp["chorobowa"]))) if chorobowe else Decimal("0.00")
    # FP+FS: не платится, если podstawa < minimalne wynagrodzenie (pref/ulga)
    fp = _gr(podstawa * Decimal(str(sp["fp_fs"]))) if podstawa >= Decimal(str(y["min_wage"])) else Decimal("0.00")

    return ZusResult(
        rule_version=RULE_VERSION,
        year=year,
        regime=regime,
        podstawa=podstawa,
        emerytalne=emeryt,
        rentowe=rent,
        wypadkowe=wypadk,
        chorobowe=chor,
        fp_fs=fp,
        zdrowotna=zdrowotna_ryczalt(year, revenue_ytd),
    )
