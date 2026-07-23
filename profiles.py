"""Серверный профиль предпринимателя + расчёт персональных дедлайнов.

Единая таблица profiles: поля профиля (дата регистрации, форма, VAT, ulga)
и флаги подписок (news_sub — новости мониторинга, dl_sub — напоминания
о дедлайнах). Логика этапов ZUS — зеркало plan.html (менять синхронно!).
"""
import os
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(os.environ.get("JDG_DB")
                or Path(__file__).parent / "news.db")

FORMS = ("skala", "liniowy", "ryczalt", "unknown")
PUSH_DAYS = (5, 1)  # за сколько дней напоминать


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS profiles(
        user_id INTEGER PRIMARY KEY, reg TEXT, form TEXT, vat INTEGER DEFAULT 0,
        ulga INTEGER DEFAULT 1, news_sub INTEGER DEFAULT 0,
        dl_sub INTEGER DEFAULT 0, updated INTEGER)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sent_pushes(
        user_id INTEGER, key TEXT, ts INTEGER, PRIMARY KEY(user_id, key))""")
    return conn


ALLOWED = {"reg", "form", "vat", "ulga", "news_sub", "dl_sub"}


def upsert(user_id: int, **fields):
    """Частичное обновление: пишем только переданные поля."""
    fields = {k: v for k, v in fields.items() if k in ALLOWED}
    with _db() as c:
        c.execute("INSERT OR IGNORE INTO profiles(user_id, updated) VALUES(?,?)",
                  (user_id, int(time.time())))
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            c.execute(f"UPDATE profiles SET {sets}, updated=? WHERE user_id=?",
                      (*fields.values(), int(time.time()), user_id))


def get(user_id: int) -> dict | None:
    with _db() as c:
        row = c.execute("SELECT user_id,reg,form,vat,ulga,news_sub,dl_sub "
                        "FROM profiles WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return None
    keys = ("user_id", "reg", "form", "vat", "ulga", "news_sub", "dl_sub")
    return dict(zip(keys, row))


def with_flag(flag: str) -> list[dict]:
    assert flag in ("news_sub", "dl_sub")
    with _db() as c:
        rows = c.execute(f"SELECT user_id,reg,form,vat,ulga FROM profiles "
                         f"WHERE {flag}=1").fetchall()
    return [dict(zip(("user_id", "reg", "form", "vat", "ulga"), r)) for r in rows]


# ── этапы ZUS (зеркало window.stageDates из webapp/app.js) ───────────────────
# Совпадение зеркал на опорных датах проверяет
# test_webapp_browser.test_zus_stages_match_python_mirror — правишь здесь,
# правь и app.js, иначе приложение и пуши назовут разные сроки перехода.
def _last_day(y: int, m: int) -> date:
    if m > 12:
        y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    nm = date(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1)
    return nm - timedelta(days=1)


def stage_dates(reg: date, use_ulga: bool):
    """ulga: 6 полных месяцев (месяц старта считается только с 1-го числа);
    preferencyjne: 24 полных месяца следом."""
    if use_ulga:
        extra = 5 if reg.day == 1 else 6
        ulga_end = _last_day(reg.year, reg.month + extra)
        base = ulga_end + timedelta(days=1)
    else:
        ulga_end, base = None, reg
    pref_end = _last_day(base.year, base.month + (23 if base.day == 1 else 24))
    return ulga_end, pref_end


def upcoming(profile: dict, today: date, horizon: int = 40) -> list[dict]:
    """Дедлайны в ближайшие horizon дней: ZUS/JPK/PIT + переходы этапов."""
    evs = []
    for k in range(3):
        m = today.month + k
        y = today.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        evs.append({"d": date(y, m, 20), "key": f"dra-{y}-{m:02d}",
                    "title": "ZUS DRA + składki",
                    "desc": "и аванс налога за прошлый месяц"})
        if profile.get("vat"):
            evs.append({"d": date(y, m, 25), "key": f"jpk-{y}-{m:02d}",
                        "title": "JPK_V7M (VAT)",
                        "desc": "декларация за прошлый месяц"})
    pit_y = today.year if (today.month, today.day) <= (4, 30) else today.year + 1
    evs.append({"d": date(pit_y, 4, 30), "key": f"pit-{pit_y}",
                "title": "Годовая декларация PIT", "desc": "за прошлый год"})
    if profile.get("reg"):
        try:
            reg = date.fromisoformat(profile["reg"])
            ulga_end, pref_end = stage_dates(reg, bool(profile.get("ulga", 1)))
            if ulga_end and ulga_end >= today:
                evs.append({"d": ulga_end, "key": f"ulga-end-{ulga_end}",
                            "title": "🔥 Конец Ulga na Start",
                            "desc": "подай ZUS ZUA — переход на preferencyjne"})
            if pref_end >= today:
                evs.append({"d": pref_end, "key": f"pref-end-{pref_end}",
                            "title": "🔥 Конец Preferencyjnych",
                            "desc": "переход на Duży ZUS"})
        except ValueError:
            pass
    evs = [e for e in evs if today <= e["d"] <= today + timedelta(days=horizon)]
    evs.sort(key=lambda e: e["d"])
    return evs


def due_pushes(today: date) -> list[dict]:
    """Кому что напомнить сегодня: [{user_id, key, days, event}], без дублей."""
    out = []
    with _db() as c:
        sent = {(r[0], r[1]) for r in c.execute("SELECT user_id,key FROM sent_pushes")}
    for p in with_flag("dl_sub"):
        for e in upcoming(p, today):
            days = (e["d"] - today).days
            if days in PUSH_DAYS:
                key = f"{e['key']}-d{days}"
                if (p["user_id"], key) not in sent:
                    out.append({"user_id": p["user_id"], "key": key,
                                "days": days, "event": e, "profile": p})
    return out


def mark_sent(user_id: int, key: str):
    with _db() as c:
        c.execute("INSERT OR IGNORE INTO sent_pushes VALUES(?,?,?)",
                  (user_id, key, int(time.time())))
        # чистка старше 90 дней
        c.execute("DELETE FROM sent_pushes WHERE ts < ?",
                  (int(time.time()) - 90 * 86400,))
