"""wFirma API2: клиент, разбор ответов, обзор, хранение ключей, ручки.

Живого ключа нет, поэтому ответы собраны по документации doc.wfirma.pl:
записи пронумерованы ({"invoices": {"0": {"invoice": {...}}}}), ошибка —
в status.code, суммы — строки в злотых.
"""
import asyncio
import json
from datetime import date
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet

import infakt
import wfirma


@pytest.fixture(autouse=True)
def keys_and_db(tmp_path, monkeypatch):
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    infakt._fernet = None
    monkeypatch.setattr(wfirma, "DB_PATH", tmp_path / "w.db")
    monkeypatch.setattr(wfirma, "APP_KEY", "app-key-of-jdg-hub")
    yield
    infakt._fernet = None


def numbered(module, single, rows, total=None):
    block = {str(i): {single: r} for i, r in enumerate(rows)}
    block["parameters"] = {"limit": "100", "page": "1",
                           "total": str(total if total is not None else len(rows))}
    return {module: block, "status": {"code": "OK"}}


def inv(n, d, netto, paid=True, total=None, ksef=None, typ="normal", due=None, already=None):
    total = total if total is not None else round(netto * 1.23, 2)
    return {"id": str(n), "fullnumber": f"FV {n}/2026", "date": d, "netto": f"{netto:.2f}",
            "tax": f"{total - netto:.2f}", "total": f"{total:.2f}",
            "total_composed": f"{total:.2f}",
            "alreadypaid": f"{(total if paid else (already or 0)):.2f}",
            "paymentstate": "paid" if paid else "unpaid", "paymentdate": due or d,
            "type": typ, "ksef_reference_number": ksef or "", "currency": "PLN"}


class Api:
    """Подменный wFirma: отвечает по пути, запоминает запросы."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def handler(self, request: httpx.Request):
        path = request.url.path.strip("/")
        self.calls.append(request)
        for prefix, fn in self.routes.items():
            if path.startswith(prefix):
                res = fn(request)
                if isinstance(res, str):
                    return httpx.Response(200, text=res)
                return httpx.Response(200, json=res)
        return httpx.Response(200, json={"status": {"code": "ACTION NOT FOUND"}})

    def client(self, company_id=None):
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return wfirma.Client("acc-key-123", "sec-key-456", company_id, http=http)


def run(coro):
    return asyncio.run(coro)


# ── запросы ────────────────────────────────────────────────────────────────
def test_request_carries_keys_formats_and_company():
    api = Api({"companies/get": lambda r: numbered("companies", "company", [{"id": "1"}])})
    run(api.client("555").company())
    req = api.calls[0]
    assert req.headers["accessKey"] == "acc-key-123"
    assert req.headers["secretKey"] == "sec-key-456"
    assert req.headers["appKey"] == "app-key-of-jdg-hub"
    q = parse_qs(urlparse(str(req.url)).query)
    assert q["inputFormat"] == ["xml"] and q["outputFormat"] == ["json"]
    assert q["company_id"] == ["555"]
    assert str(req.url).startswith("https://api2.wfirma.pl/companies/get")


def test_find_body_is_escaped_xml():
    body = wfirma.find_body("invoices", [("date", "ge", "2026-01-01"),
                                          ("fullnumber", "like", "<x>&")], page=2)
    assert "<invoices><parameters><conditions>" in body
    assert "<field>date</field><operator>ge</operator><value>2026-01-01</value>" in body
    assert "&lt;x&gt;&amp;" in body and "<page>2</page><limit>100</limit>" in body


@pytest.mark.parametrize("code,human", [
    ("AUTH", "не приняла ключи"),
    ("TOTAL REQUESTS LIMIT EXCEEDED", "паузу"),
    ("SOMETHING NEW", "SOMETHING NEW"),
])
def test_error_codes_become_human_text(code, human):
    api = Api({"companies/get": lambda r: {"status": {"code": code}}})
    with pytest.raises(wfirma.WfirmaError) as e:
        run(api.client().company())
    assert human in e.value.human


def test_find_all_follows_pages():
    pages = []

    def invoices(req):
        body = req.content.decode()
        page = int(body.split("<page>")[1].split("<")[0])
        pages.append(page)
        rows = [inv(page * 1000 + i, "2026-05-01", 100) for i in range(100 if page < 3 else 7)]
        return numbered("invoices", "invoice", rows, total=207)
    api = Api({"invoices/find": invoices})
    got = run(api.client().find_all("invoices", "invoice"))
    assert pages == [1, 2, 3] and len(got) == 207


# ── разбор ─────────────────────────────────────────────────────────────────
def test_norm_invoice_amounts_and_payment():
    i = wfirma.norm_invoice(inv(1, "2026-11-03", 1000, paid=False, total=1230,
                                already=230, due="2026-11-17", ksef="KSEF-1"))
    assert i["netto"] == 1000 and i["total"] == 1230 and i["left"] == 1000
    assert i["paid"] is False and i["due"] == "2026-11-17" and i["ksef_number"] == "KSEF-1"


def test_parse_jpk_reads_vat_due_and_carry():
    xml = ("<JPK><Deklaracja><PozycjeSzczegolowe><P_51>1234.00</P_51>"
           "<P_62>0</P_62></PozycjeSzczegolowe></Deklaracja></JPK>")
    assert wfirma.parse_jpk(xml) == {"to_pay": 1234.0, "carry": 0.0}
    assert wfirma.parse_jpk("<JPK><tns:P_62>500</tns:P_62></JPK>") == {"to_pay": 0.0, "carry": 500.0}
    assert wfirma.parse_jpk("<JPK></JPK>") is None


# ── обзор ──────────────────────────────────────────────────────────────────
def _api_for_overview(vat_payer="0"):
    invoices = [inv(1, "2026-11-03", 5000), inv(2, "2026-10-10", 4000),
                inv(3, "2026-10-20", 1000, paid=False, due="2026-10-30"),
                inv(4, "2026-09-01", 9999, typ="proforma"),          # не продажа
                inv(5, "2026-05-05", 2000, ksef="KSEF-5")]
    expenses = [{"date": "2026-11-05", "netto": "300.00", "paid": "1", "payment_date": "2026-11-05"},
                {"date": "2026-10-01", "netto": "200.00", "paid": "0", "payment_date": "2026-10-10"},
                {"date": "2026-10-02", "netto": "999.00", "paid": "0", "draft": "1"}]
    return Api({
        "companies/get": lambda r: numbered("companies", "company", [
            {"id": "9", "name": "Jan Kowalski", "altname": "JK Studio",
             "vat_payer": vat_payer, "tax": "lumpregister"}]),
        "invoices/find": lambda r: numbered("invoices", "invoice", invoices),
        "expenses/find": lambda r: numbered("expenses", "expense", expenses),
        "declaration_body_jpkvat/get": lambda r: "<JPK><P_51>2300.00</P_51></JPK>",
    })


def test_overview_shape_matches_infakt_dashboard():
    today = date(2026, 11, 22)
    raw = run(wfirma.fetch_all(_api_for_overview().client(), today))
    o = wfirma.build_overview(raw, today)
    assert o["taxes_available"] is False and o["provider"] == "wfirma"
    assert o["company_name"] == "JK Studio" and o["tax_form"] == "ryczałt"
    assert len(o["series"]) == 12 and o["series"][-1]["month"] == "2026-11"
    assert o["month"]["income"] == 5000 and o["month"]["costs"] == 300
    assert o["prev"]["income"] == 5000          # 4000 + 1000, проформа не считается
    assert o["prev"]["costs"] == 200             # черновик расхода не считается
    assert o["ytd"]["income"] == 12000 and o["ytd"]["profit"] == 11500
    assert o["unpaid"][0]["number"] == "FV 3/2026" and o["unpaid_total"] == 1230
    assert o["vat"] is not None and o["jpk"] is None      # не VAT-плательщик
    texts = " ".join(i["text"] for i in o["health"]["items"])
    assert "просрочили оплату: 1 фактура" in texts and "Без номера KSeF с апреля 2026: 3 фактуры" in texts
    assert o["ksef"] == {"with": 1, "without": 3}


def test_vat_payer_gets_jpk_for_previous_month():
    today = date(2026, 11, 22)
    api = _api_for_overview(vat_payer="1")
    raw = run(wfirma.fetch_all(api.client(), today))
    assert raw["jpk"] == {"to_pay": 2300.0, "carry": 0.0, "period": "2026-10"}
    assert any("declaration_body_jpkvat/get/2026/10" in str(c.url) for c in api.calls)
    assert wfirma.build_overview(raw, today)["vat"] is None


def test_ksef_invoices_feed_the_ksef_section():
    raw = {"invoices": [wfirma.norm_invoice(inv(1, "2026-05-05", 1000, total=1230, ksef="K1"))]}
    assert wfirma.ksef_invoices(raw) == [
        {"invoice_date": "2026-05-05", "gross_price": 123000, "ksef_number": "K1"}]


# ── ключи ──────────────────────────────────────────────────────────────────
def test_keys_encrypted_at_rest(tmp_path):
    wfirma.save_keys(7, "plain-access-xyz", "plain-secret-xyz", "555",
                     "plain-appkey-xyz", "JK Studio")
    k = wfirma.load_keys(7)
    assert (k.access, k.secret, k.company_id, k.app_key, k.company_name) == (
        "plain-access-xyz", "plain-secret-xyz", "555", "plain-appkey-xyz", "JK Studio")
    raw = (tmp_path / "w.db").read_bytes()
    for secret in (b"plain-access-xyz", b"plain-secret-xyz", b"plain-appkey-xyz", b"JK Studio"):
        assert secret not in raw
    wfirma.delete_keys(7)
    assert wfirma.load_keys(7) is None


def test_old_rows_without_app_key_still_load():
    # строки, сохранённые до окна ключей: {"a","s"} без appKey и имени фирмы
    import json as _json
    blob = infakt.fernet().encrypt(_json.dumps({"a": "old-access-1", "s": "old-secret-1"}).encode())
    with wfirma._db() as c:
        c.execute("INSERT INTO wfirma_keys VALUES(?,?,?,?)", (9, blob, None, 1))
    k = wfirma.load_keys(9)
    assert (k.access, k.secret, k.app_key, k.company_name) == ("old-access-1", "old-secret-1", None, "")


def test_status_never_returns_keys():
    wfirma.save_keys(7, "plain-access-xyz", "plain-secret-xyz", None, "plain-appkey-xyz", "JK")
    s = wfirma.status(7)
    assert s["connected"] and s["own_app_key"] and s["company_name"] == "JK"
    assert s["access_tail"] == "-xyz"
    dumped = str(s)
    assert "plain-access" not in dumped and "plain-secret" not in dumped and "appkey" not in dumped
    assert wfirma.status(8) == {"server_app_key": True, "connected": False}


def test_users_app_key_wins_over_server_one():
    api = Api({"companies/get": lambda r: numbered("companies", "company", [{"id": "1"}])})
    http = httpx.AsyncClient(transport=httpx.MockTransport(api.handler))
    run(wfirma.Client("acc-key-123", "sec-key-456", http=http, app_key="users-own-app").company())
    assert api.calls[0].headers["appKey"] == "users-own-app"


def test_connect_without_any_app_key_is_refused_before_network(monkeypatch):
    monkeypatch.setattr(wfirma, "APP_KEY", "")
    monkeypatch.setattr(wfirma, "Client", lambda *a, **k: pytest.fail("в сеть идти нельзя"))
    with pytest.raises(wfirma.WfirmaError) as e:
        run(wfirma.connect(1, "acc-key-123", "sec-key-456", None))
    assert e.value.code == "NO APP KEY" and "appKey" in e.value.human


def test_connect_saves_users_app_key(monkeypatch):
    monkeypatch.setattr(wfirma, "APP_KEY", "")
    api = Api({"companies/get": lambda r: numbered("companies", "company",
                                                   [{"id": "1", "name": "JK Studio"}])})
    real = wfirma.Client
    monkeypatch.setattr(wfirma, "Client", lambda a, s, c=None, **kw: real(
        a, s, c, http=httpx.AsyncClient(transport=httpx.MockTransport(api.handler)), **kw))
    assert run(wfirma.connect(1, "acc-key-123", "sec-key-456", None, "users-own-app")) == {
        "ok": True, "company_name": "JK Studio"}
    assert api.calls[0].headers["appKey"] == "users-own-app"
    k = wfirma.load_keys(1)
    assert k.app_key == "users-own-app" and k.company_name == "JK Studio"


def test_connect_with_many_companies_returns_choice(monkeypatch):
    api = Api({
        "companies/get": lambda r: {"status": {"code": "COMPANY ID REQUIRED"}},
        "user_companies/find": lambda r: numbered("user_companies", "user_company", [
            {"id": "1", "company": {"id": "11", "name": "Firma A"}},
            {"id": "2", "company": {"id": "22", "name": "Firma B"}}]),
    })
    real = wfirma.Client
    monkeypatch.setattr(wfirma, "Client", lambda a, s, c=None, **kw: real(
        a, s, c, http=httpx.AsyncClient(transport=httpx.MockTransport(api.handler)), **kw))
    with pytest.raises(wfirma.WfirmaError) as e:
        run(wfirma.connect(1, "acc-key-123", "sec-key-456", None))
    assert e.value.companies == [{"id": "11", "name": "Firma A"},
                                 {"id": "22", "name": "Firma B"}]
    assert wfirma.load_keys(1) is None, "не сохраняем, пока фирма не выбрана"


# ── ручки ──────────────────────────────────────────────────────────────────
def _server():
    import os
    os.environ.setdefault("BOT_TOKEN", "12345:TESTTOKEN")
    from fastapi.testclient import TestClient
    import server
    from test_server import make_init_data
    return TestClient(server.app), make_init_data(server.BOT_TOKEN, user_id=4242)


def test_without_server_app_key_user_brings_own(monkeypatch):
    monkeypatch.setattr(wfirma, "APP_KEY", "")
    c, good = _server()
    with c:
        h = c.get("/api/health").json()
        assert h["wfirma"] is True and h["wfirma_app_key"] is False
        r = c.post("/api/wfirma/connect", json={"initData": good, "access_key": "a" * 20,
                                                "secret_key": "b" * 20})
        assert r.status_code == 400 and "appKey" in r.json()["detail"]
        r = c.post("/api/wfirma/connect", json={"initData": good, "access_key": "a" * 20,
                                                "secret_key": "b" * 20, "app_key": "short"})
        assert r.status_code == 400
        assert c.post("/api/wfirma/status", json={"initData": good}).json() == {
            "server_app_key": False, "connected": False}
        assert c.post("/api/wfirma/status", json={"initData": "bad"}).status_code == 401


def test_connect_endpoint_flow(monkeypatch):
    async def fake_connect(uid, a, s, comp, app=None):
        if not comp:
            raise wfirma.WfirmaError("COMPANY ID REQUIRED", [{"id": "11", "name": "A"}])
        return {"ok": True, "company_name": "A"}
    monkeypatch.setattr(wfirma, "connect", fake_connect)
    c, good = _server()
    with c:
        assert c.get("/api/health").json()["wfirma"] is True
        assert c.post("/api/wfirma/connect", json={"initData": "bad"}).status_code == 401
        r = c.post("/api/wfirma/connect", json={"initData": good, "access_key": "x",
                                                "secret_key": "y"})
        assert r.status_code == 400
        r = c.post("/api/wfirma/connect", json={"initData": good, "access_key": "a" * 20,
                                                "secret_key": "b" * 20})
        assert r.status_code == 409 and r.json()["companies"][0]["id"] == "11"
        r = c.post("/api/wfirma/connect", json={"initData": good, "access_key": "a" * 20,
                                                "secret_key": "b" * 20, "company_id": "11"})
        assert r.status_code == 200 and r.json()["company_name"] == "A"


def test_api_error_is_502_with_human_text(monkeypatch):
    async def boom(uid):
        raise wfirma.WfirmaError("AUTH")
    monkeypatch.setattr(wfirma, "overview", boom)
    c, good = _server()
    with c:
        r = c.post("/api/wfirma/overview", json={"initData": good})
        assert r.status_code == 502 and "не приняла ключи" in r.json()["detail"]
