"""Телеметрия диалога: что считаем и, главное, чего не храним.

Первый тест здесь — не про метрики, а про приватность: в таблице не должно
оказаться свободного текста ни при каком входе. Это свойство должно ломаться
громко, потому что чинить утёкшие данные поздно.
"""
import importlib
import sqlite3

import pytest


@pytest.fixture
def tele(tmp_path, monkeypatch):
    monkeypatch.setenv("JDG_DB", str(tmp_path / "t.db"))
    import dialog_telemetry
    importlib.reload(dialog_telemetry)
    return dialog_telemetry


def rows(tele):
    tele._db().close()          # неизвестное событие не создаёт даже таблицу
    with sqlite3.connect(tele.DB_PATH) as c:
        return c.execute("SELECT session,event,status,question_id,routing_hint,"
                         "answers,http,ms,rules_version,schema_version "
                         "FROM dialog_events").fetchall()


SID = "0123456789abcdef"


def test_free_text_cannot_reach_the_table(tele):
    """Даже если позвать с текстом человека, в базу он не попадёт."""
    tele.record(event="ask", session="Собираю кухни на заказ",
                status="делаю мебель, NIP 5252248481",
                question_id="Иван Петров, ООО «Мебель»",
                routing_hint="ул. Kwiatowa 1, Gdańsk",
                rules_version="описание деятельности", answers="три",
                http="двести", ms="быстро", schema_version="один")
    (session, event, status, qid, hint, answers, http, ms, rules, ver), = rows(tele)
    assert event == "ask"                     # само событие из белого списка
    assert (session, status, qid, hint, rules) == (None, None, None, None, None)
    assert (answers, http, ms, ver) == (None, None, None, None)


def test_unknown_event_is_not_recorded_at_all(tele):
    tele.record(event="произвольная строка", session=SID)
    tele.record(event="query", session=SID)
    assert rows(tele) == []


def test_known_fields_are_kept(tele):
    tele.record(event="ask", session=SID, status="needs_clarification",
                question_id="furniture.object_context", answers=0, http=200,
                ms=12, rules_version="furniture-v1", schema_version=1)
    (session, event, status, qid, hint, answers, http, ms, rules, ver), = rows(tele)
    assert (session, event, status, qid) == (
        SID, "ask", "needs_clarification", "furniture.object_context")
    assert (answers, http, ms, rules, ver) == (0, 200, 12, "furniture-v1", 1)
    assert hint is None


def test_bad_session_is_dropped_but_event_survives(tele):
    """Сессия — необязательная деталь: без неё событие всё равно полезно."""
    tele.record(event="start", session="not-a-session")
    tele.record(event="start", session="0123456789ABCDEF")     # верхний регистр
    assert [r[0] for r in rows(tele)] == [None, None]


def test_storage_failure_never_breaks_the_caller(tele, monkeypatch):
    def boom(*a, **kw):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(tele, "_db", boom)
    tele.record(event="ask", session=SID, http=200)            # не должно упасть


def test_stats_counts_sessions_not_requests(tele):
    # один разговор: вопрос -> ответ -> результат
    tele.record(event="start", session=SID)
    tele.record(event="ask", session=SID, status="needs_clarification",
                question_id="furniture.object_context", answers=0, http=200, ms=10)
    tele.record(event="ask", session=SID, status="resolved_candidates",
                answers=1, http=200, ms=20)
    # второй разговор: ушёл на вопросе
    other = "fedcba9876543210"
    tele.record(event="start", session=other)
    tele.record(event="ask", session=other, status="needs_clarification",
                question_id="activity.mode", answers=0, http=200, ms=30)
    tele.record(event="back", session=other)
    # третий: не распознали, ушёл в обычный поиск
    third = "aaaabbbbccccdddd"
    tele.record(event="ask", session=third, status="unrecognized_activity",
                routing_hint="general_search", answers=0, http=200, ms=15)
    tele.record(event="legacy", session=third)

    s = tele.stats(days=1)
    assert s["sessions_started"] == 3
    assert s["sessions_finished"] == 2          # брошенный на вопросе не считается
    assert s["questions_before_result_avg"] == 0.5
    assert s["dropped_at_question"] == {"activity.mode": 1}
    assert s["final_statuses"]["resolved_candidates"] == 1
    assert s["routing_hints"] == {"general_search": 1}
    assert s["went_to_legacy"] == 1 and s["went_back"] == 1
    assert s["http"] == {200: 4}
    assert s["ms_p50"] and s["ms_p95"]


def test_stats_window_excludes_old_events(tele):
    tele.record(event="ask", session=SID, status="resolved_candidates", http=200)
    with sqlite3.connect(tele.DB_PATH) as c:                   # состарим запись
        c.execute("UPDATE dialog_events SET ts = ts - ?", (40 * 86400,))
    assert tele.stats(days=14)["sessions_started"] == 0
    assert tele.stats(days=60)["sessions_started"] == 1


def test_prune_removes_only_what_outlived_the_retention(tele):
    tele.record(event="ask", session=SID, http=200)
    tele.record(event="ask", session=SID, http=200)
    with sqlite3.connect(tele.DB_PATH) as c:      # одна запись старше срока
        c.execute("UPDATE dialog_events SET ts = ts - ? WHERE rowid = 1",
                  ((tele.KEEP_DAYS + 1) * 86400,))
    assert tele.prune() == 1
    assert len(rows(tele)) == 1
    assert tele.prune() == 0                      # повторный вызов безвреден


def test_retention_runs_on_schedule_not_on_traffic(tele):
    """Срок хранения соблюдает фоновый цикл сервера, а не удачное совпадение
    при записи: на малом потоке «иногда при вставке» не сработало бы месяцами.

    Зовём ровно ту функцию, которую вызывает `monitor_loop`, — иначе тест
    проверял бы уборку, но не то, что её кто-то запускает.
    """
    import server
    tele.record(event="ask", session=SID, http=200)
    with sqlite3.connect(tele.DB_PATH) as c:
        c.execute("UPDATE dialog_events SET ts = ts - ?",
                  ((tele.KEEP_DAYS + 1) * 86400,))
    server._housekeeping()
    assert rows(tele) == []
