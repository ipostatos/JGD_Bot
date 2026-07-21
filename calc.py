"""Расчётное ядро JDG Гид — python-зеркало webapp/calc.js.

Единственный источник ставок — rates_2026.json. Любая правка формул здесь
обязана попасть и в calc.js (test_calc.py гоняет оба через сверку фикстур).
Все суммы — злотые (float, округление до грошей на выходе).
"""
import json
from pathlib import Path

RATES = json.loads((Path(__file__).parent / "rates_2026.json").read_text(encoding="utf-8"))

STAGES = ("ulga", "pref", "duzy")
FORMS = ("skala", "liniowy", "ryczalt")


def _r2(x: float) -> float:
    return round(x + 1e-9, 2)


def spoleczne_monthly(stage: str, chorobowe: bool = False) -> dict:
    """Składki społeczne + FP/FS в месяц по режиму ZUS."""
    assert stage in STAGES
    r = RATES["spoleczne_rates"]
    if stage == "ulga":
        base, fp = 0.0, False
    elif stage == "pref":
        base, fp = RATES["pref_base"], False  # FP не платится: база ниже минималки
    else:
        base, fp = RATES["duzy_base"], True
    parts = {
        "emerytalna": base * r["emerytalna"],
        "rentowa": base * r["rentowa"],
        "wypadkowa": base * r["wypadkowa"],
        "chorobowa": base * r["chorobowa"] if (chorobowe and stage != "ulga") else 0.0,
        "fp_fs": base * r["fp_fs"] if fp else 0.0,
    }
    parts = {k: _r2(v) for k, v in parts.items()}
    parts["total"] = _r2(sum(parts.values()))
    return parts


def zdrowotna_monthly(form: str, annual_income: float) -> float:
    """Składka zdrowotna в месяц.

    skala/liniowy: annual_income = годовой dochód (доход минус расходы и społeczne);
    ryczałt: annual_income = годовой przychód (выручка).
    """
    assert form in FORMS
    z = RATES["zdrowotna"]
    if form == "ryczalt":
        for tier in z["ryczalt_tiers"]:
            if tier["max_revenue"] is None or annual_income <= tier["max_revenue"]:
                return tier["monthly"]
    rate = z["skala_rate"] if form == "skala" else z["liniowy_rate"]
    return _r2(max(annual_income / 12 * rate, z["min_monthly"]))


def pit_skala(base: float) -> float:
    """PIT по скале: 12% до порога, 32% выше; kwota wolna учтена в пороге 30k."""
    p = RATES["pit"]
    if base <= p["free_amount"]:
        return 0.0
    if base <= p["skala_threshold"]:
        return _r2((base - p["free_amount"]) * p["skala_rate1"])
    t1 = (p["skala_threshold"] - p["free_amount"]) * p["skala_rate1"]
    return _r2(t1 + (base - p["skala_threshold"]) * p["skala_rate2"])


def compare_forms(annual_revenue: float, annual_costs: float,
                  stage: str, chorobowe: bool = False,
                  ryczalt_rate: float = 0.12) -> dict:
    """Годовое сравнение skala / liniowy / ryczałt.

    Оценка: zdrowotna считается от дохода до её вычета (без итераций),
    вычеты zdrowotnej для liniówki (лимит/год) и ryczałt (50%) учтены.
    """
    z = RATES["zdrowotna"]
    spol = spoleczne_monthly(stage, chorobowe)["total"] * 12
    out = {}

    dochod = max(annual_revenue - annual_costs - spol, 0.0)

    zdr_sk = zdrowotna_monthly("skala", dochod) * 12
    tax_sk = pit_skala(dochod)
    out["skala"] = {"tax": tax_sk, "zdrowotna": _r2(zdr_sk),
                    "net": _r2(annual_revenue - annual_costs - spol - zdr_sk - tax_sk)}

    zdr_li = zdrowotna_monthly("liniowy", dochod) * 12
    base_li = max(dochod - min(zdr_li, z["liniowy_deduct_limit_year"]), 0.0)
    tax_li = _r2(base_li * RATES["pit"]["liniowy_rate"])
    out["liniowy"] = {"tax": tax_li, "zdrowotna": _r2(zdr_li),
                      "net": _r2(annual_revenue - annual_costs - spol - zdr_li - tax_li)}

    zdr_ry = zdrowotna_monthly("ryczalt", annual_revenue) * 12
    base_ry = max(annual_revenue - spol - zdr_ry * z["ryczalt_deduct_share"], 0.0)
    tax_ry = _r2(base_ry * ryczalt_rate)
    out["ryczalt"] = {"tax": tax_ry, "zdrowotna": _r2(zdr_ry),
                      "net": _r2(annual_revenue - annual_costs - spol - zdr_ry - tax_ry)}

    for v in out.values():
        v["spoleczne"] = _r2(spol)
    out["best"] = max(FORMS, key=lambda f: out[f]["net"])
    return out


def vat_limit(annual_revenue_ytd: float, started_this_year: str | None = None) -> dict:
    """Остаток лимита zwolnienia z VAT. started_this_year: 'YYYY-MM-DD' или None.

    При старте среди года лимит пропорционален дням до конца года.
    """
    from datetime import date
    limit = RATES["vat_exemption_limit"]
    if started_this_year:
        d = date.fromisoformat(started_this_year)
        year_days = (date(d.year, 12, 31) - date(d.year, 1, 1)).days + 1
        limit = limit * ((date(d.year, 12, 31) - d).days + 1) / year_days
    limit = _r2(limit)
    return {"limit": limit, "used": _r2(annual_revenue_ytd),
            "left": _r2(max(limit - annual_revenue_ytd, 0.0)),
            "exceeded": annual_revenue_ytd > limit}
