"""KSeF: даты этапов, персональный статус, порог 10 000 zł, страница и RAG."""
import os
from datetime import date

os.environ["DISABLE_BOT"] = "1"
os.environ.setdefault("BOT_TOKEN", "12345:TESTTOKEN")

import ksef  # noqa: E402
import server  # noqa: E402

TODAY = date(2026, 7, 22)


def test_facts_file_is_complete():
    f = ksef.facts()
    assert f["updated"] >= "2026-07-01"
    for key in ("stages", "relief", "excluded", "access", "steps", "faq", "sources"):
        assert f[key], key
    assert all(s["url"].startswith("https://") for s in f["sources"])
    assert len(f["faq"]) >= 8


def test_constants_mirror_stage_dates():
    """Даты в коде и в ksef.json должны совпадать (иначе разъедутся)."""
    dates = [s["date"] for s in ksef.facts()["stages"]]
    assert ksef.BIG_SINCE.isoformat() in dates
    assert ksef.ALL_SINCE.isoformat() in dates
    assert ksef.NO_RELIEF_SINCE.isoformat() in dates


def test_status_before_april_2026():
    st = ksef.status({"reg": "2024-05-10", "vat": False}, today=date(2026, 3, 1))
    assert st["must_receive"] is True      # принимать обязаны с февраля
    assert st["must_issue"] is False
    assert "01.04.2026" in st["headline"]


def test_status_now_relief_still_open():
    st = ksef.status({"reg": "2024-05-10", "vat": False}, today=TODAY)
    assert st["must_issue"] is True and st["relief_open"] is True
    assert st["relief_lost"] is None
    assert "порогом" in st["headline"]
    texts = " ".join(l["text"] for l in st["lines"])
    assert "штрафов" in texts and "входящие" in texts.lower()


def test_zwolnienie_z_vat_does_not_exempt():
    st = ksef.status({"vat": False}, today=TODAY)
    assert any("Zwolnienie z VAT" in l["text"] for l in st["lines"])


def test_registered_after_obligation_gets_own_line():
    st = ksef.status({"reg": "2026-06-15", "vat": True}, today=TODAY)
    assert any("с первой фактуры" in l["text"] for l in st["lines"])


def test_status_after_2027_has_no_relief():
    st = ksef.status({"reg": "2024-01-01"}, today=date(2027, 2, 1))
    assert st["relief_open"] is False
    assert st["days_to_full"] == 0
    assert "только через KSeF" in st["headline"]


def test_threshold_uses_gross_and_only_months_since_april():
    invoices = [
        {"invoice_date": "2026-02-10", "gross_price": 5_000_00},   # до обязанности
        {"invoice_date": "2026-02-20", "gross_price": 9_000_00},   # 14k, но февраль
        {"invoice_date": "2026-05-05", "gross_price": 4_000_00},
        {"invoice_date": "2026-05-25", "gross_price": 3_000_00},   # май 7k — порог цел
    ]
    sales = ksef.sales_from_invoices(invoices, today=TODAY)
    assert sales["months"]["2026-02"] == 14000.0
    assert sales["months"]["2026-05"] == 7000.0
    st = ksef.status({"vat": False}, today=TODAY, sales=sales)
    assert st["relief_lost"] is None


def test_threshold_lost_is_reported_with_month_and_sum():
    invoices = [{"invoice_date": "2026-05-05", "gross_price": 12_500_00},
                {"invoice_date": "2026-06-05", "gross_price": 11_000_00}]
    sales = ksef.sales_from_invoices(invoices, today=TODAY)
    st = ksef.status({"vat": False}, today=TODAY, sales=sales)
    assert st["relief_lost"]["month"] == "2026-05"      # первый превышенный
    assert st["relief_lost"]["gross"] == 12500.0
    assert st["level"] == "bad"
    assert any("12500.00 zł брутто" in l["text"] for l in st["lines"])


def test_ksef_numbers_counted_for_current_year():
    invoices = [{"invoice_date": "2026-05-05", "gross_price": 100_00, "ksef_number": "123"},
                {"invoice_date": "2026-05-06", "gross_price": 100_00},
                {"invoice_date": "2025-05-06", "gross_price": 100_00}]  # прошлый год
    sales = ksef.sales_from_invoices(invoices, today=TODAY)
    assert sales["with_ksef"] == 1 and sales["no_ksef"] == 1
    st = ksef.status({}, today=TODAY, sales=sales)
    assert any("1 фактур с номером KSeF" in l["text"] for l in st["lines"])


def test_api_and_page_served():
    from fastapi.testclient import TestClient
    with TestClient(server.app) as c:
        assert c.get("/ksef.html").status_code == 200
        assert c.get("/ksef.json").json()["stages"]
        # без initData эндпоинт всё равно отвечает — по присланному профилю
        r = c.post("/api/ksef", json={"profile": {"reg": "2024-01-01", "vat": False}})
        assert r.status_code == 200
        body = r.json()
        assert body["infakt"] is False
        assert body["status"]["headline"]
        assert body["status"]["lines"]


def test_bot_reply_is_short_and_actionable():
    text = server._ksef_reply({"reg": "2024-01-01", "vat": False})
    assert "KSeF" in text and "2027" in text
    assert text.count("\n") < 20


def test_ai_index_includes_ksef():
    import ai
    ai._index = None
    idx = ai._load_index()
    assert any(d["id"].startswith("ksef-") for d in idx)
    hit = ai.retrieve("с какого числа обязателен ksef для zwolnionego z vat")
    assert any(d["id"].startswith("ksef-") for d in hit), [d["title"] for d in hit]
