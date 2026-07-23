"""Телеметрия диалогового подбора PKD: сколько диалогов, чем кончились, где ушли.

Зачем: следующий пакет правил выбираем по тому, чего людям не хватило, а не
по ощущению. Значит надо знать долю нераспознанного, повторяющиеся
`routing_hint` и вопросы, на которых разговор обрывается.

Главное свойство модуля — **в базу физически не может попасть свободный
текст**. Не «мы стараемся не писать запрос», а: каждое строковое поле сверяется
с белым списком (статусы, идентификаторы вопросов, имена событий), всё
остальное превращается в NULL. Поэтому исходное описание деятельности, тексты
ответов, названия фирм, NIP и evidence spans записать сюда нельзя, даже если
кто-то позже передаст их по ошибке — тест на это стоит отдельно.

Идентификатор сессии — случайные 16 hex-символов, которые клиент держит в
sessionStorage. Он нужен ровно для одного: не считать один разговор за пять.
Связи с пользователем, аккаунтом Telegram или IP у него нет; вкладка
закрылась — идентификатор исчез. IP не пишем ни в каком виде, даже хэшем.
"""
import os
import re
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(os.environ.get("JDG_DB") or Path(__file__).parent / "news.db")

# Хранение: полгода. Данные агрегатные и обезличенные, но бессрочно держать
# даже такие незачем — окно наблюдения за релизом измеряется неделями.
KEEP_DAYS = 180

SESSION_RE = re.compile(r"^[0-9a-f]{16}$")

# Белые списки. Всё, чего здесь нет, пишется как NULL.
EVENTS = {
    "start",            # человек открыл режим точного подбора
    "ask",              # запрос к движку (ставит сервер, не клиент)
    "back",             # «Назад» — снял последний ответ
    "reset",            # «Начать заново»
    "legacy",           # ушёл в обычный поиск
}
STATUSES = {
    "needs_clarification", "resolved_candidates", "resolved_package",
    "outside_current_pack", "unrecognized_activity",
}
ROUTING_HINTS = {"general_search", "construction"}
# идентификаторы вопросов — из контракта, а не из запроса
QUESTION_ID_RE = re.compile(r"^[a-z_]+\.[a-z_]+$")


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS dialog_events(
        ts INTEGER, session TEXT, event TEXT, status TEXT, question_id TEXT,
        routing_hint TEXT, answers INTEGER, http INTEGER, ms INTEGER,
        rules_version TEXT, schema_version INTEGER)""")
    conn.execute("CREATE INDEX IF NOT EXISTS dialog_events_ts "
                 "ON dialog_events(ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS dialog_events_session "
                 "ON dialog_events(session, ts)")
    return conn


def _clean_session(value) -> str | None:
    if isinstance(value, str) and SESSION_RE.match(value):
        return value
    return None


def _one_of(value, allowed) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def record(*, event: str, session=None, status=None, question_id=None,
           routing_hint=None, answers=None, http=None, ms=None,
           rules_version=None, schema_version=None) -> None:
    """Записать событие. Молча игнорирует всё, что не в белом списке.

    Телеметрия не имеет права уронить ответ человеку: любая ошибка записи
    гасится. Полезность метрик всегда ниже полезности работающей ручки.
    """
    ev = _one_of(event, EVENTS)
    if ev is None:
        return
    q = question_id if isinstance(question_id, str) and QUESTION_ID_RE.match(
        question_id or "") else None
    row = (int(time.time()), _clean_session(session), ev,
           _one_of(status, STATUSES), q, _one_of(routing_hint, ROUTING_HINTS),
           _int(answers), _int(http), _int(ms),
           # версия правил — короткий идентификатор вида "furniture-v1"
           rules_version if isinstance(rules_version, str)
           and re.match(r"^[a-z0-9._-]{1,32}$", rules_version) else None,
           _int(schema_version))
    try:
        with _db() as c:
            c.execute(
                "INSERT INTO dialog_events(ts,session,event,status,question_id,"
                "routing_hint,answers,http,ms,rules_version,schema_version) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)", row)
            if row[0] % 97 == 0:          # чистим изредка, а не каждый запрос
                c.execute("DELETE FROM dialog_events WHERE ts < ?",
                          (row[0] - KEEP_DAYS * 86400,))
    except sqlite3.Error:
        pass


def stats(days: int = 14) -> dict:
    """Сводка за окно наблюдения. Считает по сессиям, а не по запросам.

    «Начатый диалог» — сессия, в которой был хотя бы один запрос к движку:
    открытая и брошенная вкладка разговором не является.
    """
    since = int(time.time()) - days * 86400
    with _db() as c:
        rows = c.execute(
            "SELECT session,event,status,question_id,routing_hint,answers,http,ms "
            "FROM dialog_events WHERE ts >= ? ORDER BY ts", (since,)).fetchall()

    TERMINAL = STATUSES - {"needs_clarification"}
    sessions: dict[str, dict] = {}
    anon = {"asks": 0, "terminal": 0}      # события без session — считаем грубо
    http_codes: dict[int, int] = {}
    statuses: dict[str, int] = {}
    hints: dict[str, int] = {}
    latency: list[int] = []
    backs = legacy = 0

    for sess, event, status, qid, hint, answers, http, ms in rows:
        if http:
            http_codes[http] = http_codes.get(http, 0) + 1
        if ms is not None:
            latency.append(ms)
        if event == "back":
            backs += 1
        if event == "legacy":
            legacy += 1
        if status:
            statuses[status] = statuses.get(status, 0) + 1
        if hint:
            hints[hint] = hints.get(hint, 0) + 1
        if not sess:
            if event == "ask":
                anon["asks"] += 1
                if status in TERMINAL:
                    anon["terminal"] += 1
            continue
        s = sessions.setdefault(sess, {"asks": 0, "questions": set(),
                                       "last_question": None, "final": None,
                                       "answers": None, "legacy": False})
        if event == "ask":
            s["asks"] += 1
            if status == "needs_clarification" and qid:
                s["questions"].add(qid)
                s["last_question"] = qid
            elif status in TERMINAL:
                s["final"] = status
                s["answers"] = answers
        elif event == "legacy":
            s["legacy"] = True

    started = [s for s in sessions.values() if s["asks"]]
    finished = [s for s in started if s["final"]]
    dropped: dict[str, int] = {}
    for s in started:
        if not s["final"] and s["last_question"]:
            dropped[s["last_question"]] = dropped.get(s["last_question"], 0) + 1
    q_counts = [len(s["questions"]) for s in finished]
    latency.sort()
    return {
        "days": days,
        "sessions_started": len(started),
        "sessions_finished": len(finished),
        "anonymous_asks": anon["asks"],
        "questions_before_result_avg": round(sum(q_counts) / len(q_counts), 2)
                                       if q_counts else None,
        "final_statuses": dict(sorted(statuses.items(), key=lambda kv: -kv[1])),
        "routing_hints": dict(sorted(hints.items(), key=lambda kv: -kv[1])),
        "dropped_at_question": dict(sorted(dropped.items(), key=lambda kv: -kv[1])),
        "went_to_legacy": legacy,
        "went_back": backs,
        "http": dict(sorted(http_codes.items())),
        "ms_p50": latency[len(latency) // 2] if latency else None,
        "ms_p95": latency[int(len(latency) * 0.95)] if latency else None,
    }
