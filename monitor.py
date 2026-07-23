"""Мониторинг официальных источников для ленты «Что нового» и пушей.

Пайплайн: скрейп списков новостей (stdlib-regex, без ключей) → diff по URL в
SQLite → классификация новых записей Claude Haiku (касается ли JDG, темы,
важность, выжимка RU, кого касается) → лента /api/news + пуши подписчикам.

ZUS (zus.pl) рендерит новости JS-ом — источник добавим отдельно (TODO).
"""
import html as html_mod
import json
import logging
import os
import re
import sqlite3
import time
import urllib.request
from pathlib import Path

import httpx

log = logging.getLogger("jdg.monitor")

DB_PATH = Path(os.environ.get("JDG_DB")
                or Path(__file__).parent / "news.db")
UA = {"User-Agent": "Mozilla/5.0 (compatible; JDGGuideBot/1.0)"}
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
HAIKU = "claude-haiku-4-5-20251001"

TOPICS = ("ZUS", "VAT", "PIT", "ryczałt", "KSeF", "легализация", "другое")


# ── источники ────────────────────────────────────────────────────────────────
def _get(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "ignore")


def parse_podatki(html: str):
    """podatki.gov.pl/aktualnosci: <a href="/aktualnosci/slug">…Title…</a>"""
    out = []
    for m in re.finditer(
            r'<a[^>]+href="(/aktualnosci/[a-z0-9-]+)"[^>]*>(.*?)</a>', html, re.S):
        title = html_mod.unescape(
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(2))).strip())
        if len(title) >= 15:
            out.append(("https://www.podatki.gov.pl" + m.group(1), title))
    return out


def parse_govpl_mf(html: str):
    """gov.pl/web/finanse/wiadomosci: блок art-prev, <a href="/web/finanse/slug">"""
    seg = html[html.find("art-prev"):]
    end = seg.find("</article>")
    if end > 0:
        seg = seg[:end]
    out = []
    for m in re.finditer(r'<a href="(/web/finanse/[^"]+)"[^>]*>(.*?)</a>', seg, re.S):
        inner = re.sub(r"<picture.*?</picture>", "", m.group(2), flags=re.S)
        title = html_mod.unescape(
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", inner)).strip())
        if len(title) >= 15:
            out.append(("https://www.gov.pl" + m.group(1), title))
    return out


def parse_zus(html: str):
    """zus.pl: новости живут под /-/<slug> (Liferay friendly-URL) с ?redirect=."""
    out, seen = [], set()
    for m in re.finditer(
            r'href="(?:https://www\.zus\.pl)?(/-/[^"?]+)[^"]*"[^>]*>(.*?)</a>',
            html, re.S):
        url = "https://www.zus.pl" + m.group(1)
        title = html_mod.unescape(
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(2))).strip())
        if len(title) >= 15 and url not in seen:
            seen.add(url)
            out.append((url, title))
    return out


SOURCES = [
    {"id": "podatki", "label": "podatki.gov.pl",
     "url": "https://www.podatki.gov.pl/aktualnosci", "parse": parse_podatki},
    {"id": "mf", "label": "Min. Finansów",
     "url": "https://www.gov.pl/web/finanse/wiadomosci", "parse": parse_govpl_mf},
    {"id": "zus_prasa", "label": "ZUS",
     "url": "https://www.zus.pl/o-zus/aktualnosci/informacje-biura-prasowego",
     "parse": parse_zus},
    {"id": "zus_inne", "label": "ZUS",
     "url": "https://www.zus.pl/o-zus/aktualnosci/inne", "parse": parse_zus},
]


# ── база ─────────────────────────────────────────────────────────────────────
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS news(
        url TEXT PRIMARY KEY, source TEXT, title TEXT, found_at INTEGER,
        relevant INTEGER, importance INTEGER, topics TEXT, summary TEXT,
        who_vat TEXT, pushed INTEGER DEFAULT 0)""")
    return conn


def get_feed(limit: int = 30):
    with db() as c:
        rows = c.execute(
            "SELECT url,source,title,found_at,importance,topics,summary FROM news "
            "WHERE relevant=1 ORDER BY found_at DESC LIMIT ?", (limit,)).fetchall()
    return [{"url": r[0], "source": r[1], "title": r[2], "found_at": r[3],
             "importance": r[4], "topics": json.loads(r[5] or "[]"),
             "summary": r[6]} for r in rows]


# ── классификация ────────────────────────────────────────────────────────────
CLASSIFY_PROMPT = """Ты — фильтр новостей для русскоязычных ИП (JDG) в Польше.
Для каждой новости (заголовок польский) верни объект:
- relevant: касается ли обычного мелкого предпринимателя JDG (true/false).
  Служебные окна сервисов, бюджетная статистика, тендеры, конкурсы — false.
- importance: 1 (просто знать) / 2 (стоит прочитать) / 3 (требует действий).
- topics: подмножество из {topics}.
- summary: 1-2 предложения по-русски, что случилось и что делать.
- who_vat: "any" | "vat_only" | "nonvat_only" — кого касается по статусу VAT.
Ответ строго JSON-массив в том же порядке, без пояснений."""


def classify(items):
    """items: [(url, source_label, title)] → список dict или None при ошибке."""
    if not ANTHROPIC_KEY or not items:
        return None
    listing = "\n".join(f"{i+1}. [{s}] {t}" for i, (_, s, t) in enumerate(items))
    try:
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY,
                     "anthropic-version": "2023-06-01"},
            json={"model": HAIKU, "max_tokens": 2000,
                  "system": CLASSIFY_PROMPT.format(topics=", ".join(TOPICS)),
                  "messages": [{"role": "user", "content": listing}]},
            timeout=60)
        r.raise_for_status()
        text = r.json()["content"][0]["text"]
        m = re.search(r"\[.*\]", text, re.S)
        data = json.loads(m.group(0)) if m else None
        if isinstance(data, list) and len(data) == len(items):
            return data
    except Exception as e:
        log.warning("classify failed: %s", e)
    return None


# ── основной проход ──────────────────────────────────────────────────────────
def run_once():
    """Скрейп → diff → классификация → сохранение. Возвращает новые важные."""
    fresh = []
    with db() as c:
        known = {r[0] for r in c.execute("SELECT url FROM news")}
    for src in SOURCES:
        try:
            for url, title in src["parse"](_get(src["url"]))[:25]:
                if url not in known:
                    fresh.append((url, src["label"], title))
        except Exception as e:
            log.warning("fetch %s failed: %s", src["id"], e)
    if not fresh:
        return []

    verdicts = classify(fresh)
    now = int(time.time())
    to_push = []
    with db() as c:
        for i, (url, src_label, title) in enumerate(fresh):
            v = verdicts[i] if verdicts else {}
            relevant = bool(v.get("relevant", False))
            row = (url, src_label, title, now, int(relevant),
                   int(v.get("importance", 1)),
                   json.dumps(v.get("topics", []), ensure_ascii=False),
                   v.get("summary", ""), v.get("who_vat", "any"), 0)
            c.execute("INSERT OR IGNORE INTO news VALUES(?,?,?,?,?,?,?,?,?,?)", row)
            if relevant and int(v.get("importance", 1)) >= 2:
                to_push.append({"url": url, "source": src_label, "title": title,
                                "summary": v.get("summary", ""),
                                "importance": int(v.get("importance", 1)),
                                "who_vat": v.get("who_vat", "any")})
    log.info("monitor: %d new, %d push-worthy", len(fresh), len(to_push))
    return to_push


def subs_for(item):
    """Подписчики на новости (profiles.news_sub), фильтр по VAT-статусу."""
    import profiles
    who = item.get("who_vat", "any")
    out = []
    for p in profiles.with_flag("news_sub"):
        if who == "vat_only" and not p.get("vat"):
            continue
        if who == "nonvat_only" and p.get("vat"):
            continue
        out.append(p["user_id"])
    return out
