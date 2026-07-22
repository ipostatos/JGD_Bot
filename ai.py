"""AI-ассистент: ответы по базе гайда (RAG на поисковом индексе).

Ретривер: простой скоринг пересечения слов вопроса с текстом статей
(search.json собирает build_content.py). Топ-3 статьи идут контекстом
в Claude Haiku. Лимиты и суточный кэш — в SQLite (news.db, чтобы не плодить БД).
"""
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path

import httpx

log = logging.getLogger("jdg.ai")

ROOT = Path(__file__).parent
SEARCH_JSON = ROOT / "webapp" / "data" / "search.json"
DB_PATH = ROOT / "news.db"

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-haiku-4-5-20251001"
DAILY_USER_LIMIT = 10
DAILY_GLOBAL_LIMIT = 300
CONTEXT_ARTICLES = 3
CONTEXT_CHARS = 5000

FAQ_JSON = ROOT / "webapp" / "data" / "faq_data.json"
ZUS_ERRORS_JSON = ROOT / "webapp" / "zus_errors.json"

_index = None


def _load_index():
    """Статьи гайда + KSeF + база ошибок ZUS + (если собран faq_miner-ом) FAQ чата."""
    global _index
    if _index is None:
        _index = json.loads(SEARCH_JSON.read_text(encoding="utf-8"))
        try:
            import ksef
            entries = ksef.index_entries()
            _index = _index + entries
            log.info("ksef: +%d записей в индекс", len(entries))
        except Exception as e:
            log.warning("ksef не прочитан: %s", e)
        if ZUS_ERRORS_JSON.is_file():
            try:
                errs = json.loads(ZUS_ERRORS_JSON.read_text(encoding="utf-8"))["errors"]
                _index = _index + [
                    {"id": f"zuserr-{e.get('code') or i}",
                     "title": f"Ошибка ZUS {e.get('code') or ''}: {e['title_ru']}",
                     "text": " ".join([e.get("code") or "", e["title_ru"], e["msg_pl"],
                                       e["why_ru"], *e["fix_ru"], *e.get("tags", [])]).lower()}
                    for i, e in enumerate(errs)]
                log.info("zus_errors: +%d записей в индекс", len(errs))
            except Exception as e:
                log.warning("zus_errors не прочитан: %s", e)
        if FAQ_JSON.is_file():
            try:
                faq = json.loads(FAQ_JSON.read_text(encoding="utf-8"))
                _index = _index + [
                    {"id": f"chatfaq-{i}", "title": f"FAQ чата: {x['q'][:60]}",
                     "text": (x["q"] + " " + x["a"]).lower()}
                    for i, x in enumerate(faq)]
                log.info("faq_data: +%d записей в индекс", len(faq))
            except Exception as e:
                log.warning("faq_data не прочитан: %s", e)
    return _index


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS ai_usage(
        day TEXT, user_id INTEGER, n INTEGER, PRIMARY KEY(day, user_id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS ai_cache(
        qhash TEXT PRIMARY KEY, answer TEXT, sources TEXT, ts INTEGER)""")
    return conn


STOP = {"как", "что", "это", "для", "или", "нужно", "можно", "если", "меня",
        "мне", "при", "надо", "быть", "есть", "por", "jak", "czy", "мой",
        "моя", "буду", "сколько", "когда", "какой", "какая", "почему"}


def retrieve(question: str, k: int = CONTEXT_ARTICLES):
    """Топ-k статей: TF с потолком × IDF (редкие слова весят больше) + заголовок."""
    import math
    raw = [w for w in re.findall(r"[a-zа-яё]{3,}", question.lower())
           if w not in STOP]
    # грубый стемминг: длинные слова матчим по префиксу (банка/банком → банк)
    words = list({w if len(w) <= 6 else w[:6] for w in raw})
    if not words:
        return []
    idx = _load_index()
    n_docs = len(idx)
    idf = {}
    for w in words:
        df = sum(1 for a in idx if w in a["text"] or w in a["title"].lower())
        idf[w] = math.log((n_docs + 1) / (df + 1)) + 0.1
    scored = []
    for art in idx:
        title = art["title"].lower()
        score = sum(idf[w] * (min(art["text"].count(w), 8) + 8 * title.count(w))
                    for w in words)
        if score > 0:
            scored.append((score, art))
    scored.sort(key=lambda x: -x[0])
    return [a for _, a in scored[:k]]


SYSTEM = """Ты — ассистент «JDG HUB» для русскоязычных ИП (JDG) в Польше.
Отвечай ТОЛЬКО на основе приложенных фрагментов гайда сообщества. Правила:
- Отвечай по-русски, коротко и по делу, простым языком; суммы и термины — точно как в гайде.
- Если в фрагментах нет ответа — честно скажи об этом и посоветуй спросить в чате @JDG_PBH или у бухгалтера. Не выдумывай.
- Учитывай профиль пользователя, если он передан.
- Это справочная информация, не налоговая и не юридическая консультация — при сложных кейсах советуй специалиста.
- Не используй markdown-заголовки; можно короткие абзацы и списки через «—»."""


def _quota_ok(user_id: int) -> bool:
    day = time.strftime("%Y-%m-%d")
    with _db() as c:
        total = c.execute("SELECT COALESCE(SUM(n),0) FROM ai_usage WHERE day=?",
                          (day,)).fetchone()[0]
        mine = c.execute("SELECT COALESCE(n,0) FROM ai_usage WHERE day=? AND user_id=?",
                         (day, user_id)).fetchone()
        mine = mine[0] if mine else 0
    return total < DAILY_GLOBAL_LIMIT and mine < DAILY_USER_LIMIT


def _quota_bump(user_id: int):
    day = time.strftime("%Y-%m-%d")
    with _db() as c:
        c.execute("""INSERT INTO ai_usage VALUES(?,?,1)
            ON CONFLICT(day,user_id) DO UPDATE SET n=n+1""", (day, user_id))


def quota_left(user_id: int) -> int:
    day = time.strftime("%Y-%m-%d")
    with _db() as c:
        mine = c.execute("SELECT COALESCE(n,0) FROM ai_usage WHERE day=? AND user_id=?",
                         (day, user_id)).fetchone()
    return max(0, DAILY_USER_LIMIT - (mine[0] if mine else 0))


async def ask(user_id: int, question: str, profile: dict | None = None) -> dict:
    """Возвращает {answer, sources[], cached, left} или {error}."""
    question = question.strip()[:500]
    if len(question) < 5:
        return {"error": "Сформулируй вопрос подробнее"}

    qhash = hashlib.sha256(question.lower().encode()).hexdigest()
    with _db() as c:
        row = c.execute("SELECT answer, sources, ts FROM ai_cache WHERE qhash=?",
                        (qhash,)).fetchone()
    if row and time.time() - row[2] < 86400:
        return {"answer": row[0], "sources": json.loads(row[1]),
                "cached": True, "left": quota_left(user_id)}

    if not ANTHROPIC_KEY:
        return {"error": "Ассистент временно недоступен"}
    if not _quota_ok(user_id):
        return {"error": f"Лимит {DAILY_USER_LIMIT} вопросов в день исчерпан — "
                         "спроси в чате @JDG_PBH или возвращайся завтра"}

    arts = retrieve(question)
    if not arts:
        return {"answer": "В гайде я не нашёл ничего близкого к вопросу. "
                          "Попробуй переформулировать или спроси живых людей в чате @JDG_PBH.",
                "sources": [], "cached": False, "left": quota_left(user_id)}

    ctx = "\n\n".join(
        f"### Статья «{a['title']}» (id: {a['id']})\n{a['text'][:CONTEXT_CHARS]}"
        for a in arts)
    prof = ""
    if profile:
        bits = []
        if profile.get("form"):
            bits.append(f"форма налогообложения: {profile['form']}")
        bits.append("плательщик VAT" if profile.get("vat") else "без VAT (zwolnienie)")
        prof = "Профиль пользователя: " + ", ".join(bits) + "\n\n"

    try:
        async with httpx.AsyncClient(timeout=60) as cl:
            r = await cl.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_KEY,
                         "anthropic-version": "2023-06-01"},
                json={"model": MODEL, "max_tokens": 1000, "system": SYSTEM,
                      "messages": [{"role": "user", "content":
                          f"{prof}Фрагменты гайда:\n\n{ctx}\n\nВопрос: {question}"}]})
            r.raise_for_status()
            answer = r.json()["content"][0]["text"].strip()
    except Exception as e:
        log.warning("ask failed: %s", e)
        return {"error": "Не получилось получить ответ, попробуй ещё раз"}

    _quota_bump(user_id)
    sources = [{"id": a["id"], "title": a["title"]} for a in arts]
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO ai_cache VALUES(?,?,?,?)",
                  (qhash, answer, json.dumps(sources, ensure_ascii=False),
                   int(time.time())))
    return {"answer": answer, "sources": sources, "cached": False,
            "left": quota_left(user_id)}
