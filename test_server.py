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


def test_stars_donation_is_gone():
    """Свой Stars-донат убран: денег приложение не собирает."""
    good = make_init_data(os.environ["BOT_TOKEN"])
    with client() as c:
        r = c.post("/api/tips", json={"amount": 50, "initData": good})
        # роут удалён; POST добирается до StaticFiles и получает 405
        assert r.status_code in (404, 405)
        about = c.get("/about.html").text
        assert "openInvoice" not in about and "/api/tips" not in about
        # вместо звёзд — поддержка авторов гайда и ссылки на проекты автора
        assert "t.me/JDG_PBH/234948" in about
        assert "github.com/justandrei/jdg-tools" in about
        assert "github.com/sobolevbel/jdg" in about


def test_support_links_mirrored_under_cut():
    """Закреп чата продублирован в приложении и спрятан под <details>."""
    with client() as c:
        about = c.get("/about.html").text
        assert "<details class=\"support\">" in about
        for link in ("buymeacoffee.com/verunko", "buycoffee.to/olga.winnik",
                     "buymeacoffee.com/devsobolev", "buymeacoffee.com/welcome2pl",
                     "justandrei.github.io/coffee", "revolut.me/pointlesshenry"):
            assert link in about, link
        # имена — как в оригинале, без транслитерации
        assert "Jaŭhien S." in about and "Olga Winnik" in about
        # у одного из активистов ссылки нет — это должно быть видно
        assert "ссылки нет" in about


def test_utility_pages_and_docs():
    with client() as c:
        for page in ("/reader.html", "/merge.html", "/photo.html"):
            assert c.get(page).status_code == 200, page
        assert c.get("/vendor/pdfjs/pdf.min.js").status_code == 200
        assert c.get("/vendor/pdf-lib.min.js").status_code == 200
        r = c.get("/docs/anketa-przedsiebiorcy.pdf")
        assert r.status_code == 200 and r.content[:4] == b"%PDF"


def test_tmpfile_flow():
    good = make_init_data(os.environ["BOT_TOKEN"])
    with client() as c:
        # без авторизации
        r = c.post("/api/tmpfile?ext=pdf", content=b"%PDF-1.4 x")
        assert r.status_code == 401
        # не PDF под видом PDF
        r = c.post("/api/tmpfile?ext=pdf", content=b"MZ garbage",
                   headers={"x-init-data": good})
        assert r.status_code == 400
        # валидный цикл: залил -> скачал
        r = c.post("/api/tmpfile?ext=pdf", content=b"%PDF-1.4 test",
                   headers={"x-init-data": good})
        assert r.status_code == 200
        url = r.json()["url"]
        got = c.get(url)
        assert got.status_code == 200 and got.content == b"%PDF-1.4 test"
        # мусорное имя не проходит
        assert c.get("/tmpf/../server.py").status_code in (400, 404)


def test_news_and_subs_api():
    good = make_init_data(os.environ["BOT_TOKEN"])
    with client() as c:
        r = c.get("/api/news")
        assert r.status_code == 200 and "items" in r.json()
        assert c.get("/news.html").status_code == 200
        # профиль: авторизация и валидация
        r = c.post("/api/profile", json={"initData": "bad", "form": "ryczalt"})
        assert r.status_code == 401
        r = c.post("/api/profile", json={"initData": good, "form": "hacker"})
        assert r.status_code == 400
        r = c.post("/api/profile", json={"initData": good, "reg": "15-03-2026"})
        assert r.status_code == 400
        r = c.post("/api/profile", json={"initData": good, "form": "ryczalt",
                                         "reg": "2026-03-15", "vat": False,
                                         "news_sub": True, "dl_sub": True})
        assert r.status_code == 200
        p = r.json()["profile"]
        assert p["news_sub"] == 1 and p["dl_sub"] == 1 and p["reg"] == "2026-03-15"
        # частичное отключение подписки не трёт профиль
        r = c.post("/api/profile", json={"initData": good, "news_sub": False})
        p = r.json()["profile"]
        assert p["news_sub"] == 0 and p["reg"] == "2026-03-15"


def test_ask_api_auth_and_pages():
    good = make_init_data(os.environ["BOT_TOKEN"])
    with client() as c:
        assert c.get("/ai.html").status_code == 200
        r = c.post("/api/ask", json={"initData": "bad", "question": "test"})
        assert r.status_code == 401
        # короткий вопрос отклоняется до похода в API
        r = c.post("/api/ask", json={"initData": good, "question": "??"})
        assert r.status_code == 400


def test_verify_init_data_roundtrip():
    token = os.environ["BOT_TOKEN"]
    user = server.verify_init_data(make_init_data(token, 777))
    assert user and user["id"] == 777
    assert server.verify_init_data("garbage") is None
