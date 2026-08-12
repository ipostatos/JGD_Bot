"""Границы HTTP: разбор запроса, преобразование ошибок, сериализация ответа.

Предметной логики здесь нет и быть не должно. Ручка обязана оставаться
тонкой: провалидировать вход, позвать движок, сериализовать результат.
Если сюда просочится выбор кода или правка признаков, начнётся вторая,
неявная реализация правил — та самая, из-за которой потом невозможно
понять, почему у человека другой ответ.

Наружу не уходит ничего внутреннего: ни id правил, ни цитаты целиком,
ни ключи признаков, ни свидетельства проигравших кандидатов. Клиенту —
код, официальное название и человеческое объяснение.
"""
from __future__ import annotations

from dataclasses import dataclass

import pkd

from .contracts import StaleAnswerError, UnknownAnswerError, question
from .models import Answer, DialogIntent

SCHEMA_VERSION = 1
MAX_QUERY_CHARS = 2000
MAX_ANSWERS = 10
MAX_OPTIONS = 10
MAX_BODY_BYTES = 16 * 1024

# Наружное имя намерения отличается от внутреннего намеренно: в API читается
# как задача пользователя, внутри — как режим движка.
INTENTS = {"find_codes": DialogIntent.FIND_CODES,
           "find_codes_and_primary": DialogIntent.FIND_PRIMARY_CODE}

TOP_FIELDS = {"query", "intent", "answers", "dialog_schema_version"}
ANSWER_FIELDS = {"question_id", "question_version", "option_ids"}


@dataclass(frozen=True)
class ApiError(Exception):
    """Ошибка входа с готовым HTTP-кодом. Текст — для человека, код — для клиента."""

    http_status: int
    code: str
    message: str
    extra: dict | None = None

    def payload(self) -> dict:
        return {"error": {"code": self.code, "message": self.message,
                          **(self.extra or {})}}


@dataclass(frozen=True)
class DialogRequest:
    query: str
    intent: DialogIntent
    answers: tuple[Answer, ...]


def parse_request(body) -> DialogRequest:
    """Строгий разбор. Неизвестное поле — ошибка, а не молчаливый пропуск:
    опечатка клиента иначе выглядит как «сервер меня не услышал»."""
    if not isinstance(body, dict):
        raise ApiError(400, "bad_request", "Тело запроса должно быть JSON-объектом.")
    unknown = set(body) - TOP_FIELDS
    if unknown:
        raise ApiError(400, "unknown_field",
                       f"Неизвестные поля запроса: {', '.join(sorted(unknown))}.")

    version = body.get("dialog_schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise ApiError(400, "unsupported_schema_version",
                       f"Поддерживается версия схемы {SCHEMA_VERSION}.",
                       {"received_version": version})

    query = body.get("query")
    if not isinstance(query, str):
        raise ApiError(400, "bad_request", "Поле query обязательно и должно быть строкой.")
    if len(query) > MAX_QUERY_CHARS:
        raise ApiError(413, "query_too_long",
                       f"Опиши деятельность короче: не длиннее {MAX_QUERY_CHARS} символов.",
                       {"limit": MAX_QUERY_CHARS})
    query = query.strip()
    if not query:
        raise ApiError(400, "empty_query", "Опиши, чем занимаешься.")

    raw_intent = body.get("intent", "find_codes")
    if raw_intent not in INTENTS:
        raise ApiError(422, "unknown_intent",
                       f"Неизвестная задача: {raw_intent}.",
                       {"allowed": sorted(INTENTS)})

    raw_answers = body.get("answers", [])
    if not isinstance(raw_answers, list):
        raise ApiError(400, "bad_request", "Поле answers должно быть массивом.")
    if len(raw_answers) > MAX_ANSWERS:
        raise ApiError(413, "too_many_answers",
                       f"Слишком много ответов: не больше {MAX_ANSWERS}.")

    answers, by_question = [], {}
    for item in raw_answers:
        answers.append(_parse_answer(item, by_question))
    return DialogRequest(query, INTENTS[raw_intent], tuple(answers))


def _parse_answer(item, by_question: dict) -> Answer:
    if not isinstance(item, dict):
        raise ApiError(400, "bad_request", "Ответ должен быть JSON-объектом.")
    unknown = set(item) - ANSWER_FIELDS
    if unknown:
        raise ApiError(400, "unknown_field",
                       f"Неизвестные поля ответа: {', '.join(sorted(unknown))}.")
    qid, version = item.get("question_id"), item.get("question_version")
    options = item.get("option_ids")
    if not isinstance(qid, str) or not isinstance(version, int) \
            or not isinstance(options, list) or not options:
        raise ApiError(400, "bad_request",
                       "Ответ должен содержать question_id, question_version "
                       "и непустой список option_ids.")
    if len(options) > MAX_OPTIONS or any(not isinstance(o, str) for o in options):
        raise ApiError(422, "bad_options", "Варианты ответа заданы неверно.")
    if len(set(options)) != len(options):
        raise ApiError(422, "duplicate_options",
                       "Один и тот же вариант повторяется в ответе.")

    q = question(qid)
    if q is None:
        raise ApiError(422, "unknown_question", f"Неизвестный вопрос: {qid}.")
    if q.version != version:
        # форма верная, но ответ относится к другой версии вопроса: варианты
        # могли поменяться, и применять его молча нельзя
        raise ApiError(409, "stale_question_version",
                       "Ответ относится к устаревшей версии вопроса.",
                       {"question_id": qid, "received_version": version,
                        "current_version": q.version})
    if q.type == "single_select" and len(options) > 1:
        raise ApiError(422, "too_many_options",
                       "На этот вопрос можно выбрать только один вариант.",
                       {"question_id": qid})
    for oid in options:
        if q.option(oid) is None:
            raise ApiError(422, "unknown_option",
                           f"У вопроса {qid} нет варианта {oid}.")

    prev = by_question.get(qid)
    if prev is not None and prev != tuple(options):
        raise ApiError(422, "conflicting_answers",
                       f"На вопрос {qid} прислано два разных ответа.")
    by_question[qid] = tuple(options)
    return Answer(qid, version, tuple(options))


def to_api_error(exc: Exception) -> ApiError:
    """Доменные исключения -> HTTP. Движок про HTTP ничего не знает."""
    if isinstance(exc, ApiError):
        return exc
    if isinstance(exc, StaleAnswerError):
        return ApiError(409, "stale_question_version",
                        "Ответ относится к устаревшей версии вопроса.")
    if isinstance(exc, UnknownAnswerError):
        return ApiError(422, "unknown_answer", "Ответ не подходит к вопросу.")
    return ApiError(500, "internal_error", "Не удалось обработать запрос.")


def serialize(result) -> dict:
    """Доменный результат -> ответ API.

    Оболочка одна и та же при любом статусе: клиенту проще держать один
    контракт, чем пять несовместимых форм. Поля не исчезают, а становятся
    null или пустым списком.
    """
    idx = pkd.index()
    return {
        "status": result.status.value,
        "next_question": _question(result.next_question),
        "activity_units": [_unit(u, idx) for u in result.units],
        "primary_code": result.primary_code,
        "primary_code_status": result.primary_code_status.value,
        "reason": result.reason,
        "routing_hint": result.routing_hint,
        "versions": {"dialog_schema": SCHEMA_VERSION,
                     "rules": result.versions.get("rules"),
                     "corpus": result.versions.get("corpus")},
        "input_fingerprint": result.input_fingerprint,
    }


def _question(q) -> dict | None:
    if q is None:
        return None
    return {"id": q.id, "version": q.version, "type": q.type, "text": q.text,
            "help": q.help,
            "options": [{"id": o.id, "label": o.label} for o in q.options]}


def _unit(unit, idx) -> dict:
    # обоснование у каждого кода своё: раньше на каждый код вешался общий кортеж
    # объяснений всего юнита, и при двух подходящих кодах карточка кода A
    # показывала обоснование, написанное для кода B
    why_by_code = {d.code: d.explanation for d in unit.candidate_decisions}
    return {
        "id": unit.kind,
        # имя для человека приходит с сервера намеренно: иначе интерфейс начнёт
        # переводить `kind` сам, и правила разъедутся между слоями
        "label": unit.title,
        "state": unit.state,
        "codes": [{"code": code,
                   "name": idx.codes[code]["name"],
                   "why": [why_by_code[code]] if code in why_by_code
                          else list(unit.explanation)}
                  for code in unit.codes],
        "suggested_pack": unit.suggested_pack,
    }
