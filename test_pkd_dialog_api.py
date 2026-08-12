"""Ручка диалога: контракт входа, коды ответов, полный цикл без состояния.

Предметную логику здесь не проверяем — она закрыта тестами движка. Здесь
проверяем ровно то, за что отвечает HTTP: что ручки нет при выключенном
флаге, что ошибка клиента отличается от ошибки сервера, и что один и тот же
запрос даёт тот же ответ.
"""
import json
import os
from contextlib import contextmanager

# Бот в тестах выключен. Без этого TestClient поднимает боевого бота: полинг
# и SetChatMenuButton по проду, а через несколько прогонов — flood control.
# В общем прогоне спасал импорт test_server, но файл обязан быть изолирован сам.
os.environ["DISABLE_BOT"] = "1"
os.environ.setdefault("BOT_TOKEN", "12345:TESTTOKEN")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402

URL = "/api/pkd/dialog"


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    """Лимитер считает окно на всех клиентов, а тестов в файле сорок.
    Чистим счётчики перед каждым тестом: проверяем ручку, а не лимит."""
    import ratelimit
    with ratelimit._db() as db:
        db.execute("DELETE FROM rate_hits")
    yield


@contextmanager
def client(enabled=True):
    prev = os.environ.get("PKD_DIALOG_ENABLED")
    os.environ["PKD_DIALOG_ENABLED"] = "true" if enabled else "false"
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        if prev is None:
            os.environ.pop("PKD_DIALOG_ENABLED", None)
        else:
            os.environ["PKD_DIALOG_ENABLED"] = prev


def ask(c, **body):
    return c.post(URL, json={"dialog_schema_version": 1, **body})


# ── флаг ──────────────────────────────────────────────────────────────────

def test_disabled_flag_hides_the_endpoint():
    """Выключенный флаг не намекает на будущую функциональность: ни 503,
    ни «скоро будет» — ручки просто нет."""
    with client(enabled=False) as c:
        r = c.post(URL, json={"query": "собираю кухни"})
        assert r.status_code == 404


def test_old_endpoint_is_untouched():
    """Старый поиск работает как работал — и с выключенным флагом тоже.

    Запрос взят такой, который он умеет: «собираю кухни» легаси-поиск
    не находит вовсе, и это как раз то, ради чего затевался диалог.
    """
    with client(enabled=False) as c:
        r = c.post("/api/pkd", json={"q": "производство мебели", "limit": 2})
        assert r.status_code == 200
        assert [x["code"] for x in r.json()["results"]][0] == "31.00.Z"


@pytest.mark.parametrize("limit", ["abc", None, [], 999999, -3])
def test_pkd_limit_is_validated_not_500(limit):
    """Нечисловой/огромный/отрицательный limit не должен ронять ручку 500."""
    with client(enabled=False) as c:
        r = c.post("/api/pkd", json={"q": "мебель", "limit": limit})
        assert r.status_code == 200
        assert len(r.json()["results"]) <= 50


# ── полный цикл без состояния ─────────────────────────────────────────────

def test_stateless_cycle():
    with client() as c:
        first = ask(c, query="Собираю кухни", intent="find_codes", answers=[])
        assert first.status_code == 200
        one = first.json()
        assert one["status"] == "needs_clarification"
        assert one["next_question"]["id"] == "furniture.object_context"
        assert one["next_question"]["version"] == 2
        assert one["activity_units"] == [] and one["primary_code"] is None

        answer = {"question_id": "furniture.object_context", "question_version": 2,
                  "option_ids": ["built_in_furniture"]}
        second = ask(c, query="Собираю кухни", intent="find_codes", answers=[answer])
        assert second.status_code == 200
        two = second.json()
        assert two["status"] == "resolved_candidates"
        assert two["next_question"] is None
        assert [x["code"] for u in two["activity_units"] for x in u["codes"]] == ["43.32.Z"]

        # тот же запрос — тот же ответ, включая отпечаток входа
        again = ask(c, query="Собираю кухни", intent="find_codes", answers=[answer])
        assert again.json() == two


def test_answer_order_does_not_change_the_response():
    a1 = {"question_id": "activity.mode", "question_version": 1,
          "option_ids": ["manufacture_new"]}
    a2 = {"question_id": "furniture.object_context", "question_version": 2,
          "option_ids": ["windows_or_doors"]}
    a3 = {"question_id": "joinery.windows_doors_material", "question_version": 1,
          "option_ids": ["wood"]}
    with client() as c:
        one = ask(c, query="Столяр на заказ", answers=[a1, a2, a3]).json()
        two = ask(c, query="Столяр на заказ", answers=[a3, a2, a1]).json()
        assert one == two
        assert [x["code"] for u in one["activity_units"] for x in u["codes"]] == ["16.25.Z"]


# ── оболочка ответа ───────────────────────────────────────────────────────

ENVELOPE = {"status", "next_question", "activity_units", "primary_code",
            "primary_code_status", "reason", "routing_hint", "versions",
            "input_fingerprint"}


@pytest.mark.parametrize("query,answers", [
    ("Собираю кухни", []),
    ("делаю мебель на заказ", []),
    ("ремонт сайта", []),
    ("ремонтирую кухни", [{"question_id": "furniture.object_context",
                           "question_version": 2, "option_ids": ["whole_room"]}]),
])
def test_envelope_is_the_same_for_every_status(query, answers):
    """Поля не исчезают в зависимости от статуса: клиенту проще держать
    один контракт, чем пять несовместимых форм ответа."""
    with client() as c:
        body = ask(c, query=query, answers=answers).json()
        assert set(body) == ENVELOPE
        assert body["versions"] == {"dialog_schema": 1, "rules": "furniture-v1",
                                    "corpus": "pkd-2025"}
        assert (body["next_question"] is not None) == \
            (body["status"] == "needs_clarification")


def test_domain_statuses_are_not_http_errors():
    with client() as c:
        assert ask(c, query="ремонт сайта").json()["status"] == "unrecognized_activity"
        assert ask(c, query="ремонт сайта").status_code == 200
        outside = ask(c, query="ремонтирую кухни",
                      answers=[{"question_id": "furniture.object_context",
                                "question_version": 2, "option_ids": ["whole_room"]}])
        assert outside.status_code == 200
        assert outside.json()["status"] == "outside_current_pack"
        assert outside.json()["routing_hint"] == "construction"


def test_single_activity_primary():
    with client() as c:
        body = ask(c, query="делаю мебель на заказ",
                   intent="find_codes_and_primary").json()
        assert body["primary_code"] == "31.00.Z"
        assert body["primary_code_status"] == "single_activity"


def test_mixed_package_keeps_both_units():
    with client() as c:
        body = ask(c, query="Ремонтирую шкафы и делаю ремонт кухни",
                   intent="find_codes_and_primary",
                   answers=[{"question_id": "furniture.object_context",
                             "question_version": 2, "option_ids": ["whole_room"]}]).json()
        assert body["status"] == "resolved_package"
        states = {u["id"]: u["state"] for u in body["activity_units"]}
        assert states["repair_or_restore_furniture"] == "resolved"
        assert states["whole_room_renovation"] == "outside_current_pack"
        packs = {u["id"]: u["suggested_pack"] for u in body["activity_units"]}
        assert packs["whole_room_renovation"] == "construction"
        assert body["primary_code_status"] == "activity_resolution_required"


def test_units_carry_a_human_label_not_an_identifier():
    """Каждая деятельность приходит с именем для человека: в пакете иначе
    непонятно, какой код к чему относится, а внутренний `kind` показывать
    нельзя."""
    with client() as c:
        body = ask(c, query="Ремонтирую шкафы и делаю мебель на заказ").json()
        labels = {u["id"]: u["label"] for u in body["activity_units"]}
        assert labels["repair_or_restore_furniture"] == "Ремонт мебели"
        assert labels["manufacture_furniture"] == "Производство мебели"


def test_unit_outside_the_pack_is_also_named():
    """Чужой раздел тоже надо назвать: «этот раздел пока не поддерживается»
    без имени деятельности человек не соотнесёт со своей фразой."""
    with client() as c:
        body = ask(c, query="Ремонтирую шкафы и делаю ремонт кухни",
                   answers=[{"question_id": "furniture.object_context",
                             "question_version": 2, "option_ids": ["whole_room"]}]).json()
        outside = [u for u in body["activity_units"]
                   if u["state"] == "outside_current_pack"]
        assert [u["label"] for u in outside] == ["Ремонт помещения"]


def test_codes_carry_official_name_and_human_reason():
    with client() as c:
        body = ask(c, query="устанавливаю встроенные кухни").json()
        code = body["activity_units"][0]["codes"][0]
        assert code["code"] == "43.32.Z"
        assert code["name"] == "Zakładanie stolarki budowlanej"
        assert code["why"] and all(isinstance(w, str) for w in code["why"])


# ── ошибки клиента ────────────────────────────────────────────────────────

def test_malformed_json_is_400():
    with client() as c:
        r = c.post(URL, content=b"{not json", headers={"Content-Type": "application/json"})
        assert r.status_code == 400


@pytest.mark.parametrize("body,code", [
    ({}, 400),                                             # нет query
    ({"query": 42}, 400),                                  # не строка
    ({"query": "   "}, 400),                               # пусто после trim
    ({"query": "мебель", "debug": True}, 400),             # неизвестное поле
    ({"query": "мебель", "dialog_schema_version": 99}, 400),
    ({"query": "мебель", "answers": "нет"}, 400),
    ({"query": "мебель", "intent": "give_me_everything"}, 422),
])
def test_bad_requests(body, code):
    with client() as c:
        assert c.post(URL, json=body).status_code == code


def test_user_debug_flag_is_rejected_not_honoured():
    """`debug: true` от клиента — не переключатель, а неизвестное поле.
    Трасса включается только настройкой сервера."""
    with client() as c:
        r = c.post(URL, json={"query": "собираю кухни", "debug": True})
        assert r.status_code == 400
        assert "debug" in r.json()["detail"]["message"]


def test_too_long_query_is_413():
    with client() as c:
        r = ask(c, query="мебель " * 400)
        assert r.status_code == 413


def test_oversized_body_is_413():
    with client() as c:
        r = c.post(URL, content=json.dumps({"query": "м" * 40000}).encode(),
                   headers={"Content-Type": "application/json"})
        assert r.status_code == 413


@pytest.mark.parametrize("answer,code", [
    ({"question_id": "нет.такого", "question_version": 1, "option_ids": ["x"]}, 422),
    ({"question_id": "furniture.object_context", "question_version": 2,
      "option_ids": ["нет-варианта"]}, 422),
    ({"question_id": "joinery.windows_doors_material", "question_version": 1,
      "option_ids": ["wood", "metal"]}, 422),
    ({"question_id": "furniture.object_context", "question_version": 2,
      "option_ids": []}, 400),
    ({"question_id": "furniture.object_context", "question_version": 2,
      "option_ids": ["built_in_furniture"], "note": "лишнее"}, 400),
])
def test_bad_answers(answer, code):
    with client() as c:
        assert ask(c, query="собираю кухни", answers=[answer]).status_code == code


def test_stale_question_version_is_409():
    """Форма ответа верная, но варианты вопроса с тех пор поменялись —
    применять его молча нельзя."""
    with client() as c:
        r = ask(c, query="собираю кухни",
                answers=[{"question_id": "furniture.object_context",
                          "question_version": 1, "option_ids": ["built_in_furniture"]}])
        assert r.status_code == 409
        err = r.json()["detail"]
        assert err["code"] == "stale_question_version"
        assert err["received_version"] == 1 and err["current_version"] == 2


def test_conflicting_answers_for_one_question():
    with client() as c:
        r = ask(c, query="собираю кухни", answers=[
            {"question_id": "furniture.object_context", "question_version": 2,
             "option_ids": ["built_in_furniture"]},
            {"question_id": "furniture.object_context", "question_version": 2,
             "option_ids": ["freestanding_furniture"]}])
        assert r.status_code == 422


# ── что наружу не уходит ──────────────────────────────────────────────────

def test_response_hides_everything_internal():
    """Клиент не должен видеть ни id правил, ни ключи признаков, ни цитаты:
    это внутренняя кухня, а не часть контракта."""
    with client() as c:
        raw = ask(c, query="Ремонтирую шкафы и делаю ремонт кухни",
                  answers=[{"question_id": "furniture.object_context",
                            "question_version": 2, "option_ids": ["whole_room"]}]).text
        for leak in ("rule_id", "furniture.repair.v1", "official_basis", "activity.",
                     "object.", "obejmuje", "pkd_dialog", "Traceback"):
            assert leak not in raw, leak


def test_internal_failure_does_not_leak(monkeypatch):
    import pkd_dialog.resolution as resolution
    monkeypatch.setattr(resolution, "resolve_furniture_dialog",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("секретный путь")))
    with client() as c:
        r = ask(c, query="собираю кухни")
        assert r.status_code == 500
        assert "секретный путь" not in r.text
        assert r.json()["detail"]["code"] == "internal_error"


# ── лимиты ────────────────────────────────────────────────────────────────

def test_normal_dialog_is_not_rate_limited():
    """Диалог — это несколько запросов подряд. Лимит, обрывающий разговор
    на третьем вопросе, хуже отсутствия лимита."""
    with client() as c:
        for _ in range(5):
            assert ask(c, query="собираю кухни").status_code == 200


def test_rate_limit_uses_the_test_database():
    """Иначе прогон тестов копил бы лимиты в рабочей базе — на этом уже
    один раз падал тест ассистента."""
    assert os.environ.get("JDG_DB"), "conftest обязан подменить базу"
    import ratelimit
    assert str(ratelimit.DB_PATH) == os.environ["JDG_DB"]


# ── телеметрия ────────────────────────────────────────────────────────────

def _events():
    import sqlite3

    import dialog_telemetry
    dialog_telemetry._db().close()
    with sqlite3.connect(dialog_telemetry.DB_PATH) as db:
        return db.execute("SELECT event,session,status,question_id,routing_hint,"
                          "answers,http,rules_version FROM dialog_events").fetchall()


@pytest.fixture(autouse=True)
def fresh_telemetry():
    import sqlite3

    import dialog_telemetry
    dialog_telemetry._db().close()
    with sqlite3.connect(dialog_telemetry.DB_PATH) as db:
        db.execute("DELETE FROM dialog_events")
    yield


SID = "0123456789abcdef"


def test_request_is_measured_without_storing_the_query():
    """Мерим статус, вопрос и версию правил — но не то, что человек написал."""
    with client() as c:
        r = c.post(URL, json={"dialog_schema_version": 1, "query": "Собираю кухни"},
                   headers={"X-Dialog-Session": SID})
        assert r.status_code == 200
    (event, session, status, qid, hint, answers, http, rules), = _events()
    assert (event, session, http) == ("ask", SID, 200)
    assert status == "needs_clarification"
    assert qid == "furniture.object_context" and answers == 0
    assert rules and hint is None
    # ни одного поля с текстом человека — проверяем всю строку целиком
    import sqlite3

    import dialog_telemetry
    with sqlite3.connect(dialog_telemetry.DB_PATH) as db:
        raw = str(db.execute("SELECT * FROM dialog_events").fetchall())
    assert "кухн" not in raw.lower()


def test_client_errors_are_measured_too():
    """Иначе 429 и 400 не видно в статистике: молчащий отказ выглядит
    как «люди просто не пользуются»."""
    with client() as c:
        c.post(URL, json={"query": "x", "unknown": 1}, headers={"X-Dialog-Session": SID})
        c.post(URL, content=b"{", headers={"X-Dialog-Session": SID,
                                    "Content-Type": "application/json"})
    codes = sorted(e[6] for e in _events())
    assert codes == [400, 400]


def test_ui_event_endpoint_accepts_only_client_events():
    with client() as c:
        assert c.post(URL + "/event", json={"event": "start"},
                      headers={"X-Dialog-Session": SID}).status_code == 204
        # серверное «ask» от клиента — подделка статистики, отклоняем
        assert c.post(URL + "/event", json={"event": "ask"},
                      headers={"X-Dialog-Session": SID}).status_code == 400
        # неизвестное имя — тоже 400, а не тихий приём
        assert c.post(URL + "/event", json={"event": "собираю кухни"},
                      headers={"X-Dialog-Session": SID}).status_code == 400
        # лишнее поле — ошибка контракта, а не тихий приём текста
        assert c.post(URL + "/event", json={"event": "start", "query": "кухни"},
                      headers={"X-Dialog-Session": SID}).status_code == 400
    assert [(e[0], e[1]) for e in _events()] == [("start", SID)]


def test_ui_event_endpoint_hidden_behind_the_flag():
    with client(enabled=False) as c:
        assert c.post(URL + "/event", json={"event": "start"}).status_code == 404
