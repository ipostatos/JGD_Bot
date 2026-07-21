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
