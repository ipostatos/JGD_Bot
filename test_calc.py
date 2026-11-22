"""Сверка расчётного ядра с опорными числами гайда (таблица ZUS 2026)."""
import calc


def test_spoleczne_pref_matches_guide():
    p = calc.spoleczne_monthly("pref")
    assert p["emerytalna"] == 281.44
    assert p["rentowa"] == 115.34
    assert p["wypadkowa"] == 24.08
    assert p["fp_fs"] == 0.0
    assert p["total"] == 420.86


def test_spoleczne_duzy_matches_guide():
    p = calc.spoleczne_monthly("duzy")
    assert p["emerytalna"] == 1103.27
    assert p["rentowa"] == 452.16  # 5652.00 * 8%
    assert p["wypadkowa"] == 94.39
    assert p["fp_fs"] == 138.47
    assert p["chorobowa"] == 0.0


def test_spoleczne_ulga_is_zero():
    assert calc.spoleczne_monthly("ulga", chorobowe=True)["total"] == 0.0


def test_chorobowa_addon():
    assert calc.spoleczne_monthly("pref", chorobowe=True)["chorobowa"] == 35.32
    assert calc.spoleczne_monthly("duzy", chorobowe=True)["chorobowa"] == 138.47


def test_zdrowotna_ryczalt_tiers():
    assert calc.zdrowotna_monthly("ryczalt", 50_000) == 498.35
    assert calc.zdrowotna_monthly("ryczalt", 60_000) == 498.35
    assert calc.zdrowotna_monthly("ryczalt", 100_000) == 830.58
    assert calc.zdrowotna_monthly("ryczalt", 400_000) == 1495.04


def test_zdrowotna_minimum():
    # низкий доход -> минимальная складка 432.54 (9% минималки 4806)
    assert calc.zdrowotna_monthly("skala", 10_000) == 432.54
    assert calc.zdrowotna_monthly("liniowy", 50_000) == 432.54


def test_zdrowotna_above_min():
    assert calc.zdrowotna_monthly("skala", 120_000) == 900.0
    assert calc.zdrowotna_monthly("liniowy", 240_000) == 980.0


def test_pit_skala_brackets():
    assert calc.pit_skala(20_000) == 0.0
    assert calc.pit_skala(100_000) == 8_400.0  # (100k-30k)*12%
    assert calc.pit_skala(150_000) == 20_400.0  # 10.8k + 30k*32%


def test_compare_it_contractor():
    # типовой айтишник: 20k/мес выручки, мало расходов, preferencyjne, ryczałt 12%
    r = calc.compare_forms(240_000, 12_000, "pref", ryczalt_rate=0.12)
    assert r["best"] == "ryczalt"
    assert r["ryczalt"]["zdrowotna"] == 9966.96  # 830.58 * 12
    # у всех форм вычтены те же społeczne
    assert r["skala"]["spoleczne"] == r["ryczalt"]["spoleczne"] == 5050.32


def test_compare_high_costs_prefers_skala_or_liniowy():
    # большие расходы делают ryczałt (налог с выручки) невыгодным
    r = calc.compare_forms(300_000, 200_000, "duzy", ryczalt_rate=0.12)
    assert r["best"] in ("skala", "liniowy")
    assert r[r["best"]]["net"] > r["ryczalt"]["net"]


def test_vat_limit_full_year():
    v = calc.vat_limit(100_000)
    assert v["limit"] == 240_000.0
    assert v["left"] == 140_000.0
    assert not v["exceeded"]


def test_vat_limit_prorated_midyear():
    v = calc.vat_limit(0, started_this_year="2026-07-02")
    # 183 дня из 365
    assert v["limit"] == round(240_000 * 183 / 365, 2)


def test_vat_limit_exceeded():
    assert calc.vat_limit(250_000)["exceeded"]


def test_year_2025_spoleczne():
    p = calc.spoleczne_monthly("duzy", year=2025)
    assert p["emerytalna"] == 1015.78  # 5203.80 * 19.52%
    assert p["rentowa"] == 416.3
    pref = calc.spoleczne_monthly("pref", year=2025)
    assert pref["emerytalna"] == 273.24  # 1399.80 * 19.52%


def test_year_2025_zdrowotna():
    assert calc.zdrowotna_monthly("ryczalt", 50_000, year=2025) == 461.66
    assert calc.zdrowotna_monthly("ryczalt", 200_000, year=2025) == 769.43
    assert calc.zdrowotna_monthly("ryczalt", 400_000, year=2025) == 1384.97
    # минималка 2025 = 9% от 75% минималки 4666
    assert calc.zdrowotna_monthly("skala", 10_000, year=2025) == 314.96


def test_year_2026_matches_default():
    assert calc.spoleczne_monthly("duzy") == calc.spoleczne_monthly("duzy", year=2026)
    assert calc.zdrowotna_monthly("ryczalt", 100_000) == \
        calc.zdrowotna_monthly("ryczalt", 100_000, year=2026)


# ── 2027: что уже объявлено считаем, чего нет — честно говорим ─────────────

def test_year_2027_spoleczne_from_announced_bases():
    pref = calc.spoleczne_monthly("pref", year=2027)
    assert pref["emerytalna"] == 289.87        # 1485.00 × 19.52%
    duzy = calc.spoleczne_monthly("duzy", year=2027)
    assert duzy["emerytalna"] == 1175.06       # 6019.80 × 19.52% = 1175.065 → вниз
    # сверка с публикациями (rcponline, infor): duży 2027 с chorobowym = 2052.15
    assert calc.spoleczne_monthly("duzy", chorobowe=True, year=2027)["total"] == 2052.15
    assert calc.is_forecast(2027, "duzy_base")
    assert not calc.is_forecast(2026, "duzy_base")


def test_year_2027_zdrowotna_min_and_unknown_ryczalt():
    assert calc.zdrowotna_monthly("skala", 10_000, year=2027) == 445.5   # 9% × 4950
    assert calc.zdrowotna_monthly("ryczalt", 100_000, year=2027) is None, \
        "ставки ryczałtu 2027 ещё не объявлены — не подставлять прошлогодние"


def test_compare_2027_refuses_until_data_exists():
    r = calc.compare_forms(240_000, 12_000, "pref", year=2027)
    assert set(r["unavailable"]) == {"pit", "zdrowotna.ryczalt_tiers",
                                     "zdrowotna.liniowy_deduct_limit_year"}
    assert "best" not in r


def test_compare_default_year_unchanged_for_2026():
    assert calc.compare_forms(240_000, 12_000, "pref") == \
        calc.compare_forms(240_000, 12_000, "pref", year=2026)


def test_current_year_follows_calendar_with_fallback():
    from datetime import date
    assert calc.current_year(date(2026, 11, 21)) == 2026
    assert calc.current_year(date(2027, 1, 5)) == 2027
    assert calc.current_year(date(2029, 3, 1)) == max(map(int, calc.YEARS["years"]))
    assert calc.current_year(date(2020, 1, 1)) == min(map(int, calc.YEARS["years"]))


def test_pit_scale_supports_three_brackets(monkeypatch):
    """Реформа 2027 (если её подпишут): 12% до 130k, 24% до 150k, 32% выше."""
    y = dict(calc.YEARS["years"]["2026"])
    y["pit"] = {"free_amount": 30000.0, "liniowy_rate": 0.19, "brackets": [
        {"upto": 130000.0, "rate": 0.12}, {"upto": 150000.0, "rate": 0.24},
        {"upto": None, "rate": 0.32}]}
    monkeypatch.setitem(calc.YEARS["years"], "2099", y)
    assert calc.pit_skala(100_000, year=2099) == 8_400.0              # 70k × 12%
    assert calc.pit_skala(140_000, year=2099) == 12_000 + 2_400       # 100k×12% + 10k×24%
    assert calc.pit_skala(200_000, year=2099) == 12_000 + 4_800 + 16_000


def test_vat_limit_by_year_in_data():
    ys = calc.YEARS["years"]
    assert ys["2025"]["vat_exemption_limit"] == 200_000
    assert ys["2026"]["vat_exemption_limit"] == ys["2027"]["vat_exemption_limit"] == 240_000


def test_rates_years_has_fresh_check_date():
    """Экран расчётов пишет «данные проверены <дата>» по этому полю:
    после добавления 2027 там оставалась июльская дата."""
    import json
    y = json.loads(open("rates_years.json", encoding="utf-8").read())
    assert y["updated"] >= "2026-11-21"
    assert max(y["years"]) in y["note"] or "2027" in y["note"]
