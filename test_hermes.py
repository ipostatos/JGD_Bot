"""Движок Hermes: пагинация inFakt и гейт проверок по форме налога (без сети)."""
from decimal import Decimal

import pytest

from hermes.checklist import close_month
from hermes.config import Profile
from hermes.infakt import Infakt, InfaktError


# ── _list_all: пагинация не должна молча обрезать список ──────────────────────
class _PagedHttp:
    """Дубль httpx.Client: отдаёт заранее нарезанные страницы entities."""

    def __init__(self, pages, total=None):
        self._pages = pages          # список списков entity
        self._total = total          # что класть в metainfo.total_count (или None)

    def get(self, path, params=None):
        offset = (params or {}).get("offset", 0)
        limit = (params or {}).get("limit", 100)
        idx = offset // limit
        ents = self._pages[idx] if idx < len(self._pages) else []
        meta = {} if self._total is None else {"total_count": self._total}

        class _R:
            status_code = 200
            def json(self_inner):
                return {"entities": ents, "metainfo": meta}
        return _R()


def _client_with(pages, total=None):
    c = Infakt.__new__(Infakt)          # без __init__ (не нужен ключ/сеть)
    c._http = _PagedHttp(pages, total)
    return c


def test_list_all_without_metainfo_reads_every_page():
    """Без metainfo total_count=0 делал `len>=0` истинным и возвращал одну
    страницу — оборот занижался. Теперь идём до неполной страницы."""
    pages = [[{"i": n} for n in range(100)],      # полная — есть ещё
             [{"i": n} for n in range(100, 130)]]  # неполная — конец
    out = _client_with(pages, total=None)._list_all("/x")
    assert len(out) == 130


def test_list_all_raises_instead_of_silent_truncation():
    """Если страницы кончились раньше данных — ошибка, а не обрезанный список."""
    pages = [[{"i": n} for n in range(100)] for _ in range(60)]  # всё полные
    with pytest.raises(InfaktError, match="список не кончился"):
        _client_with(pages, total=None)._list_all("/x", max_pages=3)


def test_list_all_stops_at_total_count():
    pages = [[{"i": n} for n in range(100)], [{"i": 100}]]
    out = _client_with(pages, total=101)._list_all("/x")
    assert len(out) == 101


# ── close_month: проверки налога и zdrowotnej только для ryczałtu ─────────────
class _FakeClient:
    """inFakt-дубль под close_month: суммы подобраны так, что у ryczałt-профиля
    сверка сходится, а у не-ryczałt zdrowotna заведомо разошлась бы."""

    def __init__(self, health_price):
        self._health = health_price

    def invoices(self, month=None):
        return [{"number": "1/2026", "net_price": 1000000,   # 10 000 zł netto
                 "invoice_date": "2026-05-10", "ksef_number": "KSEF-1",
                 "status": "paid", "paid_date": "2026-05-20",
                 "services": [{"flat_rate_tax_symbol": "8.5"}]}]

    def costs(self, month=None):
        return []

    def insurance_fee(self, month):
        # duzy 2026: social 1649.82, work 138.47; health — параметр теста
        if month == "2026-05":
            return {"social_amount_price": 164982, "work_amount_price": 13847,
                    "health_amount_price": self._health, "sum_amount_price": 178829 + self._health,
                    "payment_date": "2026-06-20", "status": "paid"}
        if month == "2026-04":     # prev_fee для расчёта ryczałtu
            return {"social_amount_price": 164982, "work_amount_price": 13847,
                    "health_amount_price": 49835, "status": "paid"}
        return None

    def income_tax(self, month):
        return {"to_pay_price": 78000, "payment_date": "2026-06-20", "status": "paid"}


def _run(tax_form, health_price=49835):
    prof = Profile(tax_form=tax_form, ryczalt_rate="8.5", chorobowe=False, zus_regime="duzy")
    return close_month("2026-05", _FakeClient(health_price), prof)


def test_non_ryczalt_skips_tax_and_zdrowotna_checks():
    """skala/liniowy: zdrowotna идёт от дохода, налог — по КПиР; движок их не
    считает, поэтому обе проверки в сверке не участвуют, ложного BLOCK нет."""
    rep = _run("liniowy", health_price=99999)      # health заведомо «неверный» для тиров
    names = [c.name for c in rep.reconciliation.checks]
    assert "ZUS zdrowotna" not in names
    assert not any("Ryczałt" in n for n in names)
    assert rep.reconciliation.verdict == "PASS"    # społeczne+FP сходятся
    assert any("не ryczałt" in ln for ln in rep.lines)


def test_ryczalt_still_checks_zdrowotna_and_tax():
    rep = _run("ryczalt", health_price=49835)      # тир 1 = 498.35
    checks = {c.name: c for c in rep.reconciliation.checks}
    assert any("Ryczałt" in n for n in checks)     # налоговая проверка на месте
    assert checks["ZUS zdrowotna"].ok              # 498.35 сошлась грош-в-грош


def test_revenue_ytd_bounded_by_closed_month():
    """revenue_ytd не должен включать месяцы после закрываемого: иначе тир
    zdrowotnej берётся не тот. Проверяем на клиенте с будущей фактурой."""
    from hermes import checklist

    class _FutureClient(_FakeClient):
        def invoices(self, month=None):
            base = super().invoices(month)
            if month is None:        # весь список для revenue_ytd
                return base + [{"net_price": 9900000, "invoice_date": "2026-12-31"}]
            return base

    captured = {}
    orig = checklist.rules_zus.compute
    def spy(year, regime, revenue_ytd, chorobowe):
        captured["revenue"] = revenue_ytd
        return orig(year, regime, revenue_ytd, chorobowe)
    checklist.rules_zus.compute = spy
    try:
        close_month("2026-05", _FutureClient(49835),
                    Profile(tax_form="ryczalt", ryczalt_rate="8.5", zus_regime="duzy"))
    finally:
        checklist.rules_zus.compute = orig
    # декабрьская фактура (99 000 zł) в май-YTD попасть не должна
    assert captured["revenue"] == Decimal("10000")
