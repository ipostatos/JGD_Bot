"""Лимиты ключа GUS BIR и переиспользование сессии.

Официальные лимиты на один ключ (stat.gov.pl): 08:00-16:59 — 3/сек, 120/мин,
6000/час; 06:00-07:59 и 17:00-21:59 — 3/сек, 150/мин, 8000/час; 22:00-05:59 —
4/сек, 200/мин, 10000/час. Превышение не блокирует мгновенно, но GUS обещает
«информировать» нарушителей — то есть это путь к отзыву ключа.
"""
import time

import registries


def setup_function():
    registries._gus_calls.clear()
    registries._gus_session.update(sid=None, born=0.0)


def test_windows_match_official_table():
    assert registries._gus_window(9) == (3, 120, 6000)     # рабочий день
    assert registries._gus_window(16) == (3, 120, 6000)    # 16:59 ещё день
    assert registries._gus_window(17) == (3, 150, 8000)    # вечернее окно
    assert registries._gus_window(7) == (3, 150, 8000)     # утреннее окно
    assert registries._gus_window(23) == (4, 200, 10000)   # ночь
    assert registries._gus_window(3) == (4, 200, 10000)


def test_per_second_limit_holds(monkeypatch):
    monkeypatch.setattr(time, "localtime", lambda *a: time.struct_time(
        (2026, 7, 23, 10, 0, 0, 3, 204, 0)))          # 10 утра — 3 запроса в секунду
    assert [registries._gus_allow() for _ in range(4)] == [True, True, True, False]


def test_hourly_limit_holds(monkeypatch):
    monkeypatch.setattr(time, "localtime", lambda *a: time.struct_time(
        (2026, 7, 23, 10, 0, 0, 3, 204, 0)))
    now = time.time()
    registries._gus_calls.extend([now - 30] * 6000)     # час уже выбран
    assert registries._gus_allow() is False


def test_old_calls_fall_out_of_the_window():
    registries._gus_calls.extend([time.time() - 4000] * 9000)  # старше часа
    assert registries._gus_allow() is True
    assert len(registries._gus_calls) == 1


def test_session_is_reused(monkeypatch):
    """Логин на каждый запрос — прямой путь к превышению лимитов."""
    calls = []

    class FakeResp:
        text = "<ZalogujResult>SID20SYMBOLOWDLUGI</ZalogujResult>"

        def raise_for_status(self):
            pass

    class FakeClient:
        async def post(self, *a, **kw):
            calls.append(1)
            return FakeResp()

    import asyncio
    cl, h = FakeClient(), {}
    sid1 = asyncio.run(registries._gus_sid(cl, "key", h))
    sid2 = asyncio.run(registries._gus_sid(cl, "key", h))
    assert sid1 == sid2 == "SID20SYMBOLOWDLUGI"
    assert len(calls) == 1, "второй вызов должен взять сессию из кэша"


def test_expired_session_triggers_relogin(monkeypatch):
    registries._gus_session.update(sid="OLD", born=time.time() - 60 * 60)

    class FakeResp:
        text = "<ZalogujResult>NOWYSID</ZalogujResult>"

        def raise_for_status(self):
            pass

    class FakeClient:
        async def post(self, *a, **kw):
            return FakeResp()

    import asyncio
    assert asyncio.run(registries._gus_sid(FakeClient(), "key", {})) == "NOWYSID"
