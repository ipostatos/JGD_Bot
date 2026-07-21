"""Смоук API и вебаппа. Бот в тестах выключен (DISABLE_BOT=1)."""
import hashlib
import hmac
import json
import os
from urllib.parse import urlencode

os.environ["DISABLE_BOT"] = "1"
os.environ.setdefault("BOT_TOKEN", "12345:TESTTOKEN")

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402


def make_init_data(token: str, user_id: int = 42) -> str:
    pairs = {"user": json.dumps({"id": user_id, "first_name": "Test"}),
             "auth_date": "1700000000", "query_id": "AAA"}
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


def client():
    return TestClient(server.app)


def test_health():
    with client() as c:
        r = c.get("/api/health")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["bot"] is False


def test_webapp_pages_served():
    with client() as c:
        for page in ("/", "/index.html", "/guide.html", "/calc.html",
                     "/plan.html", "/article.html", "/about.html", "/tools.html"):
            r = c.get(page)
            assert r.status_code == 200, page
            assert "JDG" in r.text


def test_content_data_built():
    with client() as c:
        data = c.get("/data/content.json").json()
        assert len(data["sections"]) >= 10
        zus = c.get("/data/articles/zus.html")
        assert zus.status_code == 200
        assert "Zakład" in zus.text
        rates = c.get("/data/rates_2026.json").json()
        assert rates["year"] == 2026


def test_tips_rejects_bad_initdata():
    with client() as c:
        r = c.post("/api/tips", json={"amount": 50, "initData": "hash=deadbeef"})
        assert r.status_code == 401


def test_tips_validates_amount_and_bot_offline():
    good = make_init_data(os.environ["BOT_TOKEN"])
    with client() as c:
        r = c.post("/api/tips", json={"amount": 7, "initData": good})
        assert r.status_code == 400
        # валидная сумма, но бот в тестах выключен -> 503
        r = c.post("/api/tips", json={"amount": 50, "initData": good})
        assert r.status_code == 503


def test_verify_init_data_roundtrip():
    token = os.environ["BOT_TOKEN"]
    user = server.verify_init_data(make_init_data(token, 777))
    assert user and user["id"] == 777
    assert server.verify_init_data("garbage") is None
