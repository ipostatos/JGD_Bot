"""Кокпит: подбор профиля из данных inFakt (без сети)."""
from decimal import Decimal

import cockpit


class FakeClient:
    """Минимальный inFakt-дубль под _derive_profile."""

    def __init__(self, zus_label, social, work, rate="8.5"):
        self._zus_label = zus_label
        self._social = social
        self._work = work
        self._rate = rate

    def account(self):
        return {"accounting_settings": {
            "pit_type": {"value": "flat_rate"},
            "zus": {"value": self._zus_label},
        }}

    def invoices(self, month=None):
        # реальные фактуры inFakt всегда с invoice_date; _derive_profile
        # берёт ставки из фактур закрываемого года
        return [{"invoice_date": "2026-05-10",
                 "services": [{"flat_rate_tax_symbol": self._rate}]}]

    def insurance_fee(self, month):
        return {"social_amount_price": self._social, "work_amount_price": self._work}


def test_profile_duzy_match():
    """Суммы Duży ZUS 2026: social 1649.82, FP 138.47 -> режим duzy, chorobowe False."""
    prof, notes = cockpit._derive_profile(FakeClient("standard_zus", 164982, 13847), "2026-07")
    assert prof.zus_regime == "duzy"
    assert prof.chorobowe is False
    assert prof.ryczalt_rate == "8.5"
    assert notes == []


def test_profile_pref_match():
    """Pref ZUS 2026: social 420.86, FP 0 -> режим pref."""
    prof, notes = cockpit._derive_profile(FakeClient("prefer", 42086, 0), "2026-07")
    assert prof.zus_regime == "pref"
    assert notes == []


def test_profile_label_mismatch_noted():
    """inFakt пишет 'prefer', но суммы = Duży -> берём duzy, оставляем заметку."""
    prof, notes = cockpit._derive_profile(FakeClient("prefer", 164982, 13847), "2026-07")
    assert prof.zus_regime == "duzy"
    assert any("по суммам" in n for n in notes)


def test_profile_mixed_rates_flagged():
    c = FakeClient("standard_zus", 164982, 13847)
    c.invoices = lambda month=None: [
        {"invoice_date": "2026-05-10", "services": [{"flat_rate_tax_symbol": "8.5"}]},
        {"invoice_date": "2026-06-11", "services": [{"flat_rate_tax_symbol": "12"}]},
    ]
    prof, notes = cockpit._derive_profile(c, "2026-07")
    assert prof.tax_form == "mixed"
    assert any("смешанные" in n for n in notes)


def test_profile_rate_symbol_with_comma_does_not_crash():
    """Символ ставки вида «8,5» из inFakt не должен ронять закрытие в 502."""
    c = FakeClient("standard_zus", 164982, 13847)
    c.invoices = lambda month=None: [
        {"invoice_date": "2026-05-10", "services": [{"flat_rate_tax_symbol": "8,5"}]},
    ]
    prof, notes = cockpit._derive_profile(c, "2026-07")
    assert prof.ryczalt_rate == "8.5"       # запятая нормализована в точку
    assert prof.tax_form == "ryczalt"


def test_profile_garbage_rate_symbol_ignored():
    """Нечисловой символ ставки не проходит в Decimal и не роняет расчёт."""
    c = FakeClient("standard_zus", 164982, 13847)
    c.invoices = lambda month=None: [
        {"invoice_date": "2026-05-10", "services": [{"flat_rate_tax_symbol": "brak"}]},
    ]
    prof, notes = cockpit._derive_profile(c, "2026-07")
    assert prof.ryczalt_rate == "8.5"       # мусор отброшен, дефолт цел
