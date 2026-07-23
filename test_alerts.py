"""Поведение при исчерпанных кредитах: честный отказ + сигнал владельцу.

До этого пользователь видел «Не получилось получить ответ, попробуй ещё раз» и
жал кнопку по кругу, а простой был виден только в логах.
"""
import asyncio
import time

import httpx
import pytest

import ai
import server


class FakeResp:
    status_code = 400
    text = '{"error":{"message":"Your credit balance is too low to access the API"}}'

    def raise_for_status(self):
        raise httpx.HTTPStatusError("400", request=None, response=None)


class FakeClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **kw):
        return FakeResp()


def test_no_credits_is_named_not_generic(monkeypatch):
    monkeypatch.setattr(ai, "ANTHROPIC_KEY", "test-key")
    monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **kw: FakeClient())
    monkeypatch.setattr(ai, "_quota_ok", lambda uid: True)
    res = asyncio.run(ai.ask(1, "как платить ZUS?", None))
    assert res.get("reason") == "no_credits"
    assert "попробуй ещё раз" not in res["error"], "повтор здесь не поможет"
    assert "@JDG_PBH" in res["error"], "человека надо отправить туда, где помогут"


def test_admin_alert_is_throttled(monkeypatch):
    sent = []

    class FakeBot:
        async def send_message(self, chat_id, text, **kw):
            sent.append(text)

    monkeypatch.setattr(server, "bot", FakeBot())
    monkeypatch.setattr(server, "ADMIN_CHAT_ID", "42")
    server._alerts.clear()
    asyncio.run(server._alert_admin("🔴 тест-сигнал про кредиты"))
    asyncio.run(server._alert_admin("🔴 тест-сигнал про кредиты"))
    assert len(sent) == 1, "напоминание нужно, спам — нет"


def test_alert_silent_without_admin(monkeypatch):
    monkeypatch.setattr(server, "ADMIN_CHAT_ID", "")
    server._alerts.clear()
    asyncio.run(server._alert_admin("что-то сломалось"))  # не должно падать


def test_cooldown_expires(monkeypatch):
    sent = []

    class FakeBot:
        async def send_message(self, chat_id, text, **kw):
            sent.append(text)

    monkeypatch.setattr(server, "bot", FakeBot())
    monkeypatch.setattr(server, "ADMIN_CHAT_ID", "42")
    server._alerts.clear()
    asyncio.run(server._alert_admin("сигнал"))
    server._alerts["сигнал"[:40]] = time.time() - server.ALERT_COOLDOWN - 1
    asyncio.run(server._alert_admin("сигнал"))
    assert len(sent) == 2
