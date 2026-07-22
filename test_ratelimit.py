"""Лимиты на проверку контрагента: окно на юзера и общий потолок."""
import os

os.environ["DISABLE_BOT"] = "1"
os.environ.setdefault("BOT_TOKEN", "12345:TESTTOKEN")

import ratelimit  # noqa: E402


def _tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(ratelimit, "DB_PATH", tmp_path / "t.db")


def test_minute_window_per_user(tmp_path, monkeypatch):
    _tmp(monkeypatch, tmp_path)
    monkeypatch.setattr(ratelimit, "LIMITS", {"nip": (3, 100, 1000)})
    assert all(ratelimit.check("nip", 1) is None for _ in range(3))
    why = ratelimit.check("nip", 1)
    assert why and "минуту" in why
    assert ratelimit.check("nip", 2) is None      # сосед не наказан


def test_rejected_request_does_not_extend_ban(tmp_path, monkeypatch):
    """Отказ не записывается: иначе юзер продлевал бы себе блокировку сам."""
    _tmp(monkeypatch, tmp_path)
    monkeypatch.setattr(ratelimit, "LIMITS", {"nip": (2, 100, 1000)})
    ratelimit.check("nip", 7); ratelimit.check("nip", 7)
    for _ in range(5):
        assert ratelimit.check("nip", 7) is not None
    with ratelimit._db() as c:
        n = c.execute("SELECT COUNT(*) FROM rate_hits WHERE user_id=7").fetchone()[0]
    assert n == 2


def test_daily_cap(tmp_path, monkeypatch):
    _tmp(monkeypatch, tmp_path)
    monkeypatch.setattr(ratelimit, "LIMITS", {"nip": (1000, 2, 1000)})
    ratelimit.check("nip", 5); ratelimit.check("nip", 5)
    assert "сутки" in ratelimit.check("nip", 5)


def test_global_cap_protects_registries(tmp_path, monkeypatch):
    """Потолок на всех: пятеро юзеров по чуть-чуть — реестрам всё равно много."""
    _tmp(monkeypatch, tmp_path)
    monkeypatch.setattr(ratelimit, "LIMITS", {"nip": (1000, 1000, 3)})
    for uid in (11, 12, 13):
        assert ratelimit.check("nip", uid) is None
    assert "много проверок" in ratelimit.check("nip", 14)


def test_api_returns_429(tmp_path, monkeypatch):
    """Эндпоинт отдаёт 429 и текст лимита, а не молча падает."""
    from fastapi.testclient import TestClient

    import server
    from test_server import make_init_data

    monkeypatch.setattr(ratelimit, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(ratelimit, "LIMITS", {"nip": (0, 0, 0)})
    good = make_init_data(os.environ["BOT_TOKEN"])
    with TestClient(server.app) as c:
        r = c.post("/api/nip", json={"nip": "5252248481", "initData": good})
        assert r.status_code == 429
        assert "подожди" in r.json()["detail"]
