"""Серверный профиль: upsert, этапы ZUS (зеркало plan.html), дедлайн-пуши."""
from datetime import date

import profiles


def _tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(profiles, "DB_PATH", tmp_path / "t.db")


def test_upsert_partial(tmp_path, monkeypatch):
    _tmp(monkeypatch, tmp_path)
    profiles.upsert(1, reg="2026-03-15", form="ryczalt", vat=0)
    profiles.upsert(1, dl_sub=1)  # частичное — не трёт остальное
    p = profiles.get(1)
    assert p["reg"] == "2026-03-15" and p["form"] == "ryczalt"
    assert p["dl_sub"] == 1 and p["news_sub"] == 0
    profiles.upsert(1, hacker="x")  # неизвестные поля игнорируются
    assert "hacker" not in profiles.get(1)


def test_stage_dates_mirror_js():
    # рег. 15.03.2026 (не 1-е число) -> ulga до 30.09.2026, pref до 30.09.2028
    ulga_end, pref_end = profiles.stage_dates(date(2026, 3, 15), True)
    assert ulga_end == date(2026, 9, 30)
    assert pref_end == date(2028, 9, 30)
    # рег. 1.03 -> месяц старта считается: ulga до 31.08
    ulga_end, _ = profiles.stage_dates(date(2026, 3, 1), True)
    assert ulga_end == date(2026, 8, 31)
    # без ульги: pref от даты регистрации
    ulga_end, pref_end = profiles.stage_dates(date(2026, 3, 15), False)
    assert ulga_end is None
    assert pref_end == date(2028, 3, 31)


def test_upcoming_events():
    p = {"reg": "2026-03-15", "vat": 1, "ulga": 1}
    evs = profiles.upcoming(p, date(2026, 9, 14))
    keys = [e["key"] for e in evs]
    assert "dra-2026-09" in keys
    assert "jpk-2026-09" in keys
    assert "ulga-end-2026-09-30" in keys  # 🔥 переход в горизонте
    # без VAT нет JPK
    evs2 = profiles.upcoming({"reg": None, "vat": 0}, date(2026, 9, 14))
    assert not any(k.startswith("jpk") for k in (e["key"] for e in evs2))


def test_due_pushes_and_dedup(tmp_path, monkeypatch):
    _tmp(monkeypatch, tmp_path)
    profiles.upsert(5, reg="2026-03-15", vat=0, ulga=1, dl_sub=1)
    profiles.upsert(6, reg="2026-03-15", vat=0, ulga=1, dl_sub=0)  # не подписан
    today = date(2026, 9, 15)  # за 5 дней до 20-го
    due = profiles.due_pushes(today)
    assert [d["user_id"] for d in due] == [5]
    assert due[0]["days"] == 5 and due[0]["event"]["key"] == "dra-2026-09"
    profiles.mark_sent(5, due[0]["key"])
    assert profiles.due_pushes(today) == []  # дубль не шлём
    # за день до — новый ключ, снова в очереди
    due1 = profiles.due_pushes(date(2026, 9, 19))
    assert due1 and due1[0]["days"] == 1
