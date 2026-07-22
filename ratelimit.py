"""Лимиты на запросы, которые уходят во внешние сервисы.

Проверка контрагента ходит в четыре чужих реестра (White List, VIES, GUS,
CEIDG). Суточный кэш спасает только от повторов одного NIP: перебор разных
номеров упирался бы в чужие сервисы от нашего имени. Поэтому — окно на юзера
и общий потолок на всех.

Хранилище — та же news.db (плодить БД незачем), окно скользящее: считаем
попадания за последние N секунд и чистим старое при записи.
"""
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "news.db"

# bucket -> (за минуту на юзера, за сутки на юзера, за минуту на всех)
LIMITS = {
    "nip": (10, 100, 60),
}
KEEP = 86400


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS rate_hits(
        bucket TEXT, user_id INTEGER, ts REAL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS rate_hits_ts ON rate_hits(bucket, ts)")
    return conn


def check(bucket: str, user_id: int) -> str | None:
    """None — можно; строка — человекочитаемая причина отказа.

    Попадание засчитывается только если запрос разрешён: отказ не должен
    удлинять собственную блокировку.
    """
    per_min, per_day, global_min = LIMITS[bucket]
    now = time.time()
    with _db() as c:
        c.execute("DELETE FROM rate_hits WHERE ts < ?", (now - KEEP,))
        mine_min = c.execute(
            "SELECT COUNT(*) FROM rate_hits WHERE bucket=? AND user_id=? AND ts > ?",
            (bucket, user_id, now - 60)).fetchone()[0]
        mine_day = c.execute(
            "SELECT COUNT(*) FROM rate_hits WHERE bucket=? AND user_id=? AND ts > ?",
            (bucket, user_id, now - 86400)).fetchone()[0]
        all_min = c.execute(
            "SELECT COUNT(*) FROM rate_hits WHERE bucket=? AND ts > ?",
            (bucket, now - 60)).fetchone()[0]

        if mine_min >= per_min:
            return "Слишком часто — подожди минуту. Реестры отвечают медленно, и мы их бережём."
        if mine_day >= per_day:
            return (f"Лимит {per_day} проверок в сутки исчерпан. "
                    "Если нужно больше — напиши @ipostatos.")
        if all_min >= global_min:
            return "Сейчас много проверок сразу — попробуй через минуту."

        c.execute("INSERT INTO rate_hits VALUES(?,?,?)", (bucket, user_id, now))
    return None
