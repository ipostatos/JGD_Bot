"""Загрузка и проверка контракта: вопросы, ответы, версии.

Проверяем на входе, а не в момент использования. Вопрос с опечаткой в ключе
признака не сломается громко — он просто никогда не совпадёт с правилом,
и движок будет спрашивать одно и то же по кругу. Поэтому questions.json
разбирается строго и падает на сборке.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from . import features
from .models import Answer, Feature, FeatureSource, Question, QuestionOption

ROOT = Path(__file__).resolve().parent
QUESTIONS = ROOT / "questions.json"
TYPES = {"single_select", "multi_select"}


class ContractError(ValueError):
    """Данные контракта не соответствуют схеме."""


@lru_cache(maxsize=1)
def questions() -> dict[str, Question]:
    """Вопросы по id. Загружаются один раз: файл статический."""
    raw = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    out: dict[str, Question] = {}
    for q in raw["questions"]:
        qid = q["id"]
        if qid in out:
            raise ContractError(f"дубль вопроса: {qid}")
        if q["type"] not in TYPES:
            raise ContractError(f"{qid}: неизвестный тип {q['type']}")
        if not q.get("options"):
            raise ContractError(f"{qid}: вопрос без вариантов ответа")
        for key in q["resolves"]:
            if key not in features.REGISTRY:
                raise ContractError(f"{qid}: resolves ссылается на несуществующий "
                                    f"признак {key}")
        options, seen = [], set()
        for o in q["options"]:
            if o["id"] in seen:
                raise ContractError(f"{qid}: дубль варианта {o['id']}")
            seen.add(o["id"])
            if not o.get("effects"):
                raise ContractError(f"{qid}/{o['id']}: вариант ничего не меняет — "
                                    f"такой вопрос только тратит время человека")
            effects = []
            for e in o["effects"]:
                features.validate(e["key"], e["value"])
                if e["key"] not in q["resolves"]:
                    raise ContractError(f"{qid}/{o['id']}: меняет признак {e['key']}, "
                                        f"которого нет в resolves")
                effects.append(Feature(e["key"], e["value"], FeatureSource.USER_ANSWER))
            options.append(QuestionOption(o["id"], o["label"], tuple(effects)))
        out[qid] = Question(qid, int(q["version"]), q["type"], q["text"],
                            tuple(options), tuple(q["resolves"]), q.get("help"))
    return out


def question(qid: str) -> Question | None:
    return questions().get(qid)


class StaleAnswerError(ContractError):
    """Ответ дан на другую версию вопроса — применять его нельзя."""


class UnknownAnswerError(ContractError):
    """Ответ ссылается на несуществующий вопрос или вариант."""


def features_from_answers(answers: list[Answer]) -> list[Feature]:
    """Ответы -> признаки. Единственный способ получить USER_ANSWER-признак.

    Ошибки не глотаем: молча проигнорированный ответ выглядит для человека
    как «меня не услышали», и диалог зацикливается на том же вопросе.
    """
    out: list[Feature] = []
    for a in answers:
        q = question(a.question_id)
        if q is None:
            raise UnknownAnswerError(f"неизвестный вопрос: {a.question_id}")
        if q.version != a.question_version:
            raise StaleAnswerError(
                f"{a.question_id}: ответ на версию {a.question_version}, "
                f"сейчас {q.version}")
        if q.type == "single_select" and len(a.option_ids) != 1:
            raise UnknownAnswerError(f"{a.question_id}: ожидается ровно один вариант")
        for oid in a.option_ids:
            opt = q.option(oid)
            if opt is None:
                raise UnknownAnswerError(f"{a.question_id}: нет варианта {oid}")
            out += list(opt.effects)
    return out
