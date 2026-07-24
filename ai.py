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
DB_PATH = Path(os.environ.get("JDG_DB") or ROOT / "news.db")

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-haiku-4-5-20251001"
DAILY_USER_LIMIT = 10
DAILY_GLOBAL_LIMIT = 300
CONTEXT_ARTICLES = 3
CONTEXT_CHARS = 5000

FAQ_JSON = ROOT / "webapp" / "data" / "faq_data.json"
ZUS_ERRORS_JSON = ROOT / "webapp" / "zus_errors.json"

_index = None


def _rates_entries() -> list[dict]:
    """Ставки и лимиты по годам — из calc.py, а не переписанные руками.

    «Сколько платить в этом году» — самый частый вопрос, и до 2026-07-24
    ассистент отвечал на него «в гайде нет информации»: цифры жили в
    rates_2026.json и в калькуляторах, а в корпус не попадали.

    Текст собирается тем же расчётным ядром, что считает калькуляторы, —
    иначе появилась бы вторая копия ставок, которая разъедется с первой на
    следующей правке. Год в каждой записи назван явно: ответ, верный для
    2026-го, для 2025-го уже неверен.
    """
    import calc
    from datetime import date
    out = []
    for year in sorted(calc.YEARS["years"], reverse=True):
        # «действуют сейчас» сверяем с календарём, а не с тем, что это самый
        # свежий файл: если ставки на новый год ещё не завезли, объявлять
        # прошлогодние текущими — прямая дезинформация
        now = "действуют сейчас, текущий год. " if int(year) == date.today().year \
              else "прошлый год, для сравнения; сейчас не действуют. "
        y = calc.year_rates(int(year))
        z, r = y["zdrowotna"], calc.YEARS["spoleczne_rates"]
        pref = calc.spoleczne_monthly("pref", chorobowe=True, year=int(year))
        duzy = calc.spoleczne_monthly("duzy", chorobowe=True, year=int(year))
        tiers = ", ".join(
            f"до {t['max_revenue']:,} zł выручки — {t['monthly']} zł".replace(",", " ")
            if t["max_revenue"] else f"свыше — {t['monthly']} zł"
            for t in z["ryczalt_tiers"])
        out.append({
            "id": f"rates-zus-{year}",
            "title": f"Ставки ZUS {year} год",
            "text": (
                f"ставки взносы zus {year} год. {now}минимальная зарплата "
                f"{y['min_wage']} zł. база preferencyjny zus (pref, малый зус) "
                f"{y['pref_base']} zł, база duży zus (полный) {y['duzy_base']} zł. "
                f"składki społeczne в месяц с chorobowe: pref {pref['total']} zł, "
                f"duży {duzy['total']} zł. ставки: emerytalna {r['emerytalna']*100:.2f}%, "
                f"rentowa {r['rentowa']*100:.2f}%, wypadkowa {r['wypadkowa']*100:.2f}%, "
                f"chorobowa {r['chorobowa']*100:.2f}%, fp/fs {r['fp_fs']*100:.2f}%. "
                f"минимальная składka zdrowotna {z['min_monthly']} zł в месяц. "
                f"ulga na start: только zdrowotna, społeczne не платятся."
            ).lower()})
        out.append({
            "id": f"rates-zdrow-{year}",
            "title": f"Składka zdrowotna {year} по формам налога",
            "text": (
                f"składka zdrowotna здоровотна {year} год. {now}skala {z['skala_rate']*100:.0f}% "
                f"от dochodu, liniowy {z['liniowy_rate']*100:.1f}% "
                f"(вычет из налога не больше {z['liniowy_deduct_limit_year']} zł за год), "
                f"ryczałt фиксированная по порогам: {tiers}; из дохода вычитается "
                f"{z['ryczalt_deduct_share']*100:.0f}% уплаченной składki. "
                f"минимальная składka zdrowotna минимум {z['min_monthly']} zł в месяц."
            ).lower()})
    p = calc.RATES.get("pit") or {}
    out.append({
        "id": "rates-pit-2026",
        "title": f"Налоги и лимиты {calc.RATES['year']} год",
        "text": (
            f"налоги лимиты {calc.RATES['year']} год pit skala: свободная сумма "
            f"{p.get('free_amount')} zł, порог {p.get('skala_threshold')} zł, "
            f"ставки {p.get('skala_rate1', 0)*100:.0f}% и {p.get('skala_rate2', 0)*100:.0f}%. "
            f"podatek liniowy {p.get('liniowy_rate', 0)*100:.0f}%. "
            f"лимит зволнения с vat (zwolnienie podmiotowe) "
            f"{calc.RATES.get('vat_exemption_limit')} zł выручки за год."
        ).lower()})
    return out


def _load_index():
    """Статьи гайда + ставки + KSeF + база ошибок ZUS + (если собран) FAQ чата."""
    global _index
    if _index is None:
        _index = json.loads(SEARCH_JSON.read_text(encoding="utf-8"))
        try:
            rates = _rates_entries()
            _index = _index + rates
            log.info("rates: +%d записей в индекс", len(rates))
        except Exception as e:
            log.warning("ставки не прочитаны: %s", e)
        try:
            import ksef
            entries = ksef.index_entries()
            _index = _index + entries
            log.info("ksef: +%d записей в индекс", len(entries))
        except Exception as e:
            log.warning("ksef не прочитан: %s", e)
        banks = ROOT / "webapp" / "banks.json"
        if banks.is_file():
            try:
                b = json.loads(banks.read_text(encoding="utf-8"))
                _index = _index + [
                    {"id": f"banks-{x['id']}",
                     "title": f"Счета и расчёты: {x['title']}",
                     "text": " ".join([x["title"], x["short"], x["text"],
                                       *(f["text"] for f in x.get("flags", []))]).lower()}
                    for x in b["blocks"]]
                log.info("banks: +%d записей в индекс", len(b["blocks"]))
            except Exception as e:
                log.warning("banks не прочитан: %s", e)
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


BM25_K1 = 1.5      # насыщение частоты: десятое вхождение слова уже мало что добавляет
BM25_B = 0.6       # насколько сильно штрафуем длину документа
TITLE_BOOST = 8.0


def retrieve(question: str, k: int = CONTEXT_ARTICLES, with_scores: bool = False):
    """Топ-k статей: BM25 (частота с насыщением, нормировка по длине) + заголовок.

    Нормировка по длине здесь не украшение. С простым потолком частоты статья
    гайда на 20 000 знаков упиралась в него по каждому слову запроса, а
    короткая справка со ставками — нет, и длинный текст выигрывал у точного
    ответа просто размером: вопрос «база duży ZUS в 2025» приводил к обзорной
    статье про бухгалтерию, а не к таблице ставок 2025 года.
    """
    import math
    # год — значащее слово, а не мусор: «база duży ZUS в 2025» и тот же вопрос
    # про 2026 обязаны находить разные записи. Токенизатор резал цифры целиком,
    # и год молча исчезал из запроса
    raw = [w for w in re.findall(r"[a-zа-яё]{3,}|(?:19|20)\d{2}", question.lower())
           if w not in STOP]
    # грубый стемминг: длинные слова матчим по префиксу (банка/банком → банк)
    words = list({w if len(w) <= 6 else w[:6] for w in raw})
    if not words:
        return ([], 0.0) if with_scores else []
    idx = _load_index()
    n_docs = len(idx)
    idf = {}
    for w in words:
        df = sum(1 for a in idx if w in a["text"] or w in a["title"].lower())
        idf[w] = math.log((n_docs + 1) / (df + 1)) + 0.1
    avg_len = sum(len(a["text"]) for a in idx) / max(n_docs, 1)
    scored = []
    for art in idx:
        title = art["title"].lower()
        norm = BM25_K1 * (1 - BM25_B + BM25_B * len(art["text"]) / max(avg_len, 1))
        score = 0.0
        for w in words:
            tf = art["text"].count(w)
            if tf:
                score += idf[w] * tf * (BM25_K1 + 1) / (tf + norm)
            score += idf[w] * TITLE_BOOST * title.count(w)
        if score > 0:
            scored.append((score, art))
    scored.sort(key=lambda x: -x[0])
    top = [a for _, a in scored[:k]]
    return (top, scored[0][0] if scored else 0.0) if with_scores else top


# ── ворота: что вообще пускаем к модели ──────────────────────────────────────
# Отказ должен быть бесплатным. Всё, что ниже, проверяется ДО обращения к API:
# запрос не по теме не должен стоить ни одного токена.

# Признаки нашей темы. Достаточно одного, чтобы вопрос считался профильным.
# Короткие аббревиатуры — только целым словом: без границ «пит» находится
# внутри «питона», и запрос «напиши код на питоне» притворяется профильным.
DOMAIN = re.compile(
    r"jdg|\bип\b|\bзус\b|\bzus\b|\bvat\b|\bват\b|\bpit\b|\bпит\b|ksef|ксеф|"
    r"ceidg|цеидг|regon|регон|\bnip\b|\bнип\b|"
    r"\bpkd\b|\bпкд\b|pkwiu|pesel|песел|ryczał|рычал|linio|линей|skala|скал|składk|складк|"
    r"взнос|налог|деклараци|фактур|faktur|jpk|ulga|ульг|льгот|zdrowot|здровот|"
    r"chorobow|больничн|декрет|emerytur|пенси|urząd|ужонд|skarbow|скарбов|"
    r"biznes\.gov|ceidg|внж|побыт|pobyt|karta|карт[аы] побыт|мельдун|zameldow|"
    r"фирм|działaln|бухгалт|księgow|infakt|инфакт|wfirma|ифирма|"
    r"счёт|счет|rachun|банк|перевод|валют|курс nbp|инвойс|invoice|"
    r"limit|лимит|порог|срок|termin|штраф|kara|пеня|odsetk|"
    r"zawiesz|приостанов|likwidac|закрыт|zmień dane|регистрац|rejestrac", re.I)

# Попытки переназначить роль или вытащить инструкции. Отбиваем всегда, даже
# если рядом стоит профильное слово: «представь, что ты бухгалтер из США» —
# это не вопрос про JDG, а просьба стать другим ассистентом.
INJECTION = re.compile(
    r"ignore (all )?previous|disregard (all )?(previous|instructions)|system prompt|"
    r"твой промпт|твои инструкции|покажи инструкц|act as|представь,? что ты|"
    r"веди себя как|ты теперь|забудь (всё|все|предыдущ)", re.I)

# Просьбы, ради которых ассистента не заводили: творчество, код, переводы,
# бытовые вопросы. Пропускаем, только если рядом есть профильное слово.
GENERIC = re.compile(
    r"\b(напиши|сочини|придума\w*|переведи|перепиши|сгенерируй|нарисуй|"
    r"реши задач\w*|расскажи анекдот|стих\w*|сценарий|эссе|реферат|"
    r"код на|программу на|скрипт на|regex|sql|python|javascript|питон\w*|"
    r"рецепт\w*|погод\w*|биткоин\w*|акци[ияй]\w*)", re.I)

# Профильные вопросы дают ≥ 20 (медиана 28), мусор ≤ 20 (медиана 13):
# порог измерен на наборах из test_ai_gate.py, а не выдуман.
# Порог измерен на наборах из test_ai_gate, а не выдуман. После перехода
# ретривера на BM25 шкала изменилась (нормировка по длине убрала перекос
# в пользу длинных статей): профильные вопросы дают 13.7…60.6, мусор — до 10.7.
# 12.0 стоит между ними; тест сторожит и порядок, и запас с обеих сторон.
MIN_RELEVANCE = 12.0


def gate(question: str, top_score: float) -> str | None:
    """Причина отказа или None. Считается локально, без обращения к API."""
    if INJECTION.search(question):
        return ("Я отвечаю только про JDG в Польше — налоги, ZUS, VAT, KSeF, "
                "регистрацию и документы, и только по материалам гайда.")
    domain = bool(DOMAIN.search(question))
    if GENERIC.search(question) and not domain:
        return ("Я отвечаю только про JDG в Польше — налоги, ZUS, VAT, KSeF, "
                "регистрацию и документы. С остальным помогут другие сервисы.")
    if not domain and top_score < MIN_RELEVANCE:
        return ("Не нашёл в гайде ничего близкого. Спроси конкретнее про JDG "
                "(взносы, налоги, фактуры, регистрацию) или загляни в чат @JDG_PBH — "
                "там живые люди.")
    return None


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
    # 300 символов хватает на любой вопрос про JDG; всё длиннее — это или
    # вставленный документ, или попытка залить контекст за наш счёт
    question = question.strip()[:300]
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

    arts, top_score = retrieve(question, with_scores=True)
    # ворота стоят до вызова API и до списания квоты: мусорный запрос не должен
    # ни стоить денег, ни съедать лимит того, кто спросит по делу
    refusal = gate(question, top_score) if arts else (
        "В гайде я не нашёл ничего близкого к вопросу. Попробуй переформулировать "
        "или спроси живых людей в чате @JDG_PBH.")
    if refusal:
        log.info("ai: отказ до модели (score=%.1f) на %r", top_score, question[:60])
        return {"answer": refusal, "sources": [], "cached": False,
                "off_topic": True, "left": quota_left(user_id)}

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
            if r.status_code == 400 and "credit balance" in r.text.lower():
                # деньги на ключе кончились: чинится оплатой, а не повтором —
                # честнее сказать прямо, чем гонять человека по кнопке
                log.error("ANTHROPIC: закончились кредиты, ассистент отключён")
                return {"error": "Ассистент временно недоступен по техническим "
                                 "причинам. Ответ можно поискать в гайде или "
                                 "спросить людей в чате @JDG_PBH.",
                        "reason": "no_credits"}
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
        # ответы живут сутки, статистика — месяц: держать их вечно незачем
        c.execute("DELETE FROM ai_cache WHERE ts < ?", (int(time.time()) - 7 * 86400,))
        c.execute("DELETE FROM ai_usage WHERE day < ?",
                  (time.strftime("%Y-%m-%d",
                                 time.localtime(time.time() - 30 * 86400)),))
    return {"answer": answer, "sources": sources, "cached": False,
            "left": quota_left(user_id)}
