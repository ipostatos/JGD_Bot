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


# ── фоновый простой ИИ (ноябрь 2026: монитор стоял два месяца молча) ─────────

def _fake_bot(monkeypatch):
    sent = []

    class FakeBot:
        async def send_message(self, chat_id, text, **kw):
            sent.append(text)

    monkeypatch.setattr(server, "bot", FakeBot())
    monkeypatch.setattr(server, "ADMIN_CHAT_ID", "42")
    server._alerts.clear()
    return sent


def test_monitor_classify_marks_no_credits(monkeypatch):
    import monitor
    monkeypatch.setattr(monitor, "ANTHROPIC_KEY", "test-key")
    monkeypatch.setattr(monitor.httpx, "post", lambda *a, **kw: FakeResp())
    monkeypatch.setattr(monitor, "AI_STATUS", {"ok": None, "reason": None, "since": None})
    assert monitor.classify([("u", "s", "t")]) is None
    assert monitor.AI_STATUS["ok"] is False
    assert monitor.AI_STATUS["reason"] == "no_credits"


def test_background_outage_alerts_admin_without_any_user_question(monkeypatch):
    """Сигнал уходит из прохода монитора, а не только из /api/ask."""
    sent = _fake_bot(monkeypatch)
    asyncio.run(server._alert_ai_outage({"ok": False, "reason": "no_credits"}))
    assert len(sent) == 1 and "кредиты Anthropic" in sent[0]


def test_outage_alert_shares_cooldown_with_ask(monkeypatch):
    """Монитор и /api/ask шлют один и тот же текст — второй сигнал глушится."""
    sent = _fake_bot(monkeypatch)
    asyncio.run(server._alert_ai_outage({"ok": False, "reason": "no_credits"}))
    asyncio.run(server._alert_admin(server.NO_CREDITS_ALERT))
    assert len(sent) == 1


def test_no_alert_when_ai_fine_or_unknown(monkeypatch):
    sent = _fake_bot(monkeypatch)
    asyncio.run(server._alert_ai_outage({"ok": True, "reason": None}))
    asyncio.run(server._alert_ai_outage({"ok": None, "reason": None}))
    assert sent == []


def test_other_api_error_alerts_differently(monkeypatch):
    sent = _fake_bot(monkeypatch)
    asyncio.run(server._alert_ai_outage({"ok": False, "reason": "error"}))
    assert len(sent) == 1 and "кредиты" not in sent[0]


# ── Haiku 5.5: размышление первым блоком, отказ, обрыв ─────────────────────
class OkResp:
    status_code = 200
    text = ""

    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


def _thinking_first(text, stop="end_turn"):
    return {"stop_reason": stop, "content": [
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "text", "text": text}]}


def test_answer_text_skips_thinking_block():
    import monitor
    assert monitor.answer_text(_thinking_first(" ответ ")) == "ответ"


@pytest.mark.parametrize("body", [
    {"stop_reason": "refusal", "content": []},
    _thinking_first('[{"relevant": tr', stop="max_tokens"),
    {"stop_reason": "end_turn", "content": [{"type": "thinking", "thinking": ""}]},
])
def test_answer_text_rejects_non_answers(body):
    import monitor
    with pytest.raises(monitor.ModelAnswerError):
        monitor.answer_text(body)


def test_classify_reads_answer_after_thinking(monkeypatch):
    import monitor
    sent = {}
    item = ('[{"relevant": true, "importance": 2, "topics": ["ZUS"], '
            '"summary": "s", "who_vat": "any"}]')

    def post(*a, **kw):
        sent.update(kw["json"])
        return OkResp(_thinking_first(item))
    monkeypatch.setattr(monitor, "ANTHROPIC_KEY", "test-key")
    monkeypatch.setattr(monitor.httpx, "post", post)
    out = monitor.classify([("https://x", "ZUS", "Nowe stawki")])
    assert out and out[0]["topics"] == ["ZUS"]
    assert sent["model"] == "claude-haiku-5-5"
    # у Haiku 5.5 sampling-параметры дают 400, а размышление ест max_tokens
    assert not {"temperature", "top_p", "top_k"} & set(sent)
    assert sent["max_tokens"] >= 4000


def test_ask_reads_answer_after_thinking(monkeypatch):
    sent = {}
    with ai._db() as c:                 # тот же вопрос мог лечь в кэш в другом тесте
        c.execute("DELETE FROM ai_cache")

    class Client(FakeClient):
        async def post(self, *a, **kw):
            sent.update(kw["json"])
            return OkResp(_thinking_first("Платить до 20 числа."))
    monkeypatch.setattr(ai, "ANTHROPIC_KEY", "test-key")
    monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **kw: Client())
    monkeypatch.setattr(ai, "_quota_ok", lambda uid: True)
    monkeypatch.setattr(ai, "_quota_bump", lambda uid: None)
    res = asyncio.run(ai.ask(1, "до какого числа платить ZUS?", None))
    assert res.get("answer") == "Платить до 20 числа.", res
    assert sent["model"] == "claude-haiku-5-5"
    assert not {"temperature", "top_p", "top_k"} & set(sent)
