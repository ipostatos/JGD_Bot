"""Дашборд: расчёты на фейковых данных inFakt (без сети).

Формы записей взяты с живого аккаунта: суммы в грошах, period='YYYY-MM-01',
у ZUS/налога бывают статусы draft/printed/not_applicable, а не только paid.
"""
from datetime import date

import calc
import dashboard


def test_vat_limit_has_single_source():
    """Лимит zwolnienia берётся из файла ставок, а не из второй константы."""
    assert dashboard.VAT_LIMIT == calc.RATES["vat_exemption_limit"]


def gr(zl_value: float) -> int:
    return int(round(zl_value * 100))


class FakeInfakt:
    def __init__(self, invoices=None, costs=None, fees=None, taxes=None, vat="zwolniony"):
        self._i, self._c, self._f, self._t, self._vat = (
            invoices or [], costs or [], fees or [], taxes or [], vat)

    def invoices(self, month=None):
        return self._i

    def costs(self, month=None):
        return self._c

    def insurance_fees(self):
        return self._f

    def income_taxes(self):
        return self._t

    def account(self):
        return {"accounting_settings": {"vat": {"value": self._vat}}}


TODAY = date(2026, 7, 22)


def inv(month_day, net, paid=True, ksef=True, due="2026-08-01", number="1/07/2026"):
    return {"invoice_date": month_day, "net_price": gr(net), "gross_price": gr(net),
            "status": "paid" if paid else "sent", "paid_date": "2026-07-03" if paid else None,
            "payment_date": due, "ksef_number": "KS-1" if ksef else None, "number": number}


def base_client(**kw):
    return FakeInfakt(
        invoices=[inv("2026-07-02", 7000), inv("2026-06-10", 5000)],
        costs=[{"issue_date": "2026-07-03", "net_price": gr(500),
                "statuses": [{"symbol": "cost_accounted"}]}],
        fees=[{"period": "2026-07-01", "sum_amount_price": gr(2286.64),
               "status": "draft", "payment_date": "2026-08-20"}],
        taxes=[{"period": "2026-07-01", "to_pay_price": gr(462),
                "status": "draft", "payment_date": "2026-08-20"}], **kw)


def test_month_and_profit():
    d = dashboard.build(client=base_client(), today=TODAY)
    m = d["month"]
    assert m["month"] == "2026-07"
    assert m["income"] == 7000 and m["costs"] == 500
    assert m["zus"] == 2286.64 and m["tax"] == 462
    assert m["profit"] == round(7000 - 500 - 2286.64 - 462, 2)


def test_series_is_12_months_oldest_first():
    d = dashboard.build(client=base_client(), today=TODAY)
    assert len(d["series"]) == 12
    assert d["series"][-1]["month"] == "2026-07"
    assert d["series"][0]["month"] == "2025-08"


def test_ytd_and_effective_burden():
    d = dashboard.build(client=base_client(), today=TODAY)
    ytd = d["ytd"]
    assert ytd["income"] == 12000
    assert ytd["burden_pct"] == round((2286.64 + 462) / 12000 * 100, 1)


def test_vat_forecast_for_zwolniony():
    d = dashboard.build(client=base_client(), today=TODAY)
    v = d["vat"]
    assert v["limit"] == 240000 and v["used"] == 12000
    assert v["left"] == 228000
    assert v["used_pct"] == 5.0


def test_vat_block_absent_for_payer():
    d = dashboard.build(client=base_client(vat="czynny"), today=TODAY)
    assert d["vat"] is None and d["vat_payer"] is True


def test_health_clean_account():
    d = dashboard.build(client=base_client(), today=TODAY)
    assert d["health"]["score"] == 100
    assert d["health"]["items"][0]["level"] == "ok"


def test_printed_and_not_applicable_are_not_overdue():
    """Живая грабля: 'printed' значит «распечатано», 'not_applicable' и нулевые
    суммы — вовсе не обязательства. Обвинять в просрочке по ним нельзя."""
    c = base_client()
    c._f = c._f + [
        {"period": "2026-05-01", "sum_amount_price": 0,
         "status": "not_applicable", "payment_date": "2026-06-20"},
        {"period": "2026-04-01", "sum_amount_price": 0,
         "status": "printed", "payment_date": "2026-05-20"},
    ]
    d = dashboard.build(client=c, today=TODAY)
    assert d["health"]["score"] == 100, d["health"]["items"]


def test_real_unpaid_obligation_lowers_health_softly():
    c = base_client()
    c._t = [{"period": "2026-05-01", "to_pay_price": gr(598),
             "status": "printed", "payment_date": "2026-06-22"}]
    d = dashboard.build(client=c, today=TODAY)
    item = next(i for i in d["health"]["items"] if "налога" in i["text"])
    assert item["cut"] == 10 and item["level"] == "warn"
    assert "не отмечена" in item["text"]


def test_old_obligations_outside_window_ignored():
    c = base_client()
    c._t = [{"period": "2023-11-01", "to_pay_price": gr(300),
             "status": "printed", "payment_date": "2023-12-20"}]
    d = dashboard.build(client=c, today=TODAY)
    assert d["health"]["score"] == 100


def test_overdue_invoice_and_missing_ksef_hit_health():
    c = base_client()
    c._i = [inv("2026-06-01", 5000, paid=False, ksef=False, due="2026-06-30")]
    d = dashboard.build(client=c, today=TODAY)
    h = d["health"]
    assert h["score"] == 100 - 10 - 5
    assert any("Просроченных" in i["text"] for i in h["items"])
    assert d["unpaid_total"] == 5000


def test_vat_eta_from_recent_pace():
    """Прогноз даты считается по трём полным месяцам, без текущего."""
    c = base_client()
    c._i = [inv(f"2026-0{m}-05", 20000, number=f"{m}") for m in (4, 5, 6)]
    d = dashboard.build(client=c, today=TODAY)
    v = d["vat"]
    assert v["pace"] == 20000
    assert v["months_left"] == round((240000 - 60000) / 20000, 1)
    assert v["eta"] is None or v["eta"] > "2026-07"
