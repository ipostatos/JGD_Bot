"""Выбор следующего вопроса: одна незакрытая развилка, а не анкета.

Что этот слой умеет и чего НЕ умеет — важно не перепутать. Он не знает
ни одного кода PKD и не считает, как ответ разделит множество кандидатов:
кандидатов ещё нет. Он выбирает минимальную незакрытую доменную развилку,
без которой продолжать нельзя. Настоящий выигрыш информации по кандидатам
появится позже, вместе с правилами выбора кодов, и будет отдельным
механизмом — смешивать их нельзя.

Главное свойство: вопрос не задаётся, если ответ уже известен — неважно,
пришёл он ответом человека или прямо из текста запроса. «Собираю отдельно
стоящие шкафы» не должно приводить к вопросу «встроенная или отдельно
стоящая»: человек уже сказал.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import IntEnum
from functools import lru_cache
from pathlib import Path

from . import contracts
from .models import Answer, DialogIntent, Question
from .resolved import FeatureState, ResolvedFeatureSet

POLICIES = Path(__file__).resolve().parent / "question_policies.json"


class QuestionPriority(IntEnum):
    """Меньше — раньше. Числа не веса, а порядок: сперва развязать
    противоречие, потом понять действие, потом объект, потом материал,
    и только в самом конце — про выручку, когда деятельности уже известны."""

    CONFLICT_RESOLUTION = 10
    ACTIVITY_MODE = 20
    DOMAIN_DISCRIMINATOR = 30
    MATERIAL_DISCRIMINATOR = 40
    PRIMARY_REVENUE = 50


# Причина решения — плоская строка: она уходит в трассу и в тесты как есть,
# и отдельный тип здесь только мешал бы читать вывод.
ASKED = "question_selected"
NO_POLICY_APPLIES = "no_policy_applies"
ALL_RESOLVED = "nothing_left_to_ask"


@dataclass(frozen=True)
class QuestionDecisionTrace:
    policy_id: str
    question_id: str
    eligible: bool
    reasons: tuple[str, ...]
    unknown_features: tuple[str, ...] = ()
    conflicting_features: tuple[str, ...] = ()


@dataclass(frozen=True)
class QuestionSelectionResult:
    """`question=None` НЕ значит «код найден». Значит только, что обязательных
    вопросов сейчас нет; решение про коды принимает следующий слой."""

    question: Question | None
    reason: str
    policy_id: str | None = None
    trace: tuple[QuestionDecisionTrace, ...] = ()


@lru_cache(maxsize=1)
def policies() -> list[dict]:
    raw = json.loads(POLICIES.read_text(encoding="utf-8"))
    known = contracts.questions()
    seen = set()
    for p in raw["policies"]:
        if p["id"] in seen:
            raise contracts.ContractError(f"дубль политики: {p['id']}")
        seen.add(p["id"])
        q = known.get(p["question_id"])
        if q is None:
            raise contracts.ContractError(f"{p['id']}: нет вопроса {p['question_id']}")
        if q.version != p["question_version"]:
            raise contracts.ContractError(
                f"{p['id']}: политика на версию {p['question_version']}, "
                f"вопрос сейчас {q.version} — условия могли устареть вместе с вариантами")
        QuestionPriority[p["priority"].upper()]
        for key in p["resolves"]:
            if key not in q.resolves:
                raise contracts.ContractError(
                    f"{p['id']}: обещает закрыть {key}, но вопрос его не выставляет")
        if not p.get("why"):
            raise contracts.ContractError(f"{p['id']}: политика без объяснения")
    return raw["policies"]


# ── условия ───────────────────────────────────────────────────────────────

def _check(cond: dict, fs: ResolvedFeatureSet,
           intent: DialogIntent = DialogIntent.FIND_CODES) -> tuple[bool, list[str]]:
    """Условие политики -> (выполнено, человеческие причины)."""
    if "intent_is" in cond:
        ok = intent.value == cond["intent_is"]
        return ok, [f"намерение: {intent.value}"] if ok else [
            f"вопрос нужен только при намерении {cond['intent_is']}"]
    if "all" in cond:
        reasons = []
        for sub in cond["all"]:
            ok, why = _check(sub, fs, intent)
            reasons += why
            if not ok:
                return False, reasons
        return True, reasons
    if "any" in cond:
        reasons = []
        for sub in cond["any"]:
            ok, why = _check(sub, fs, intent)
            if ok:
                return True, why
            reasons += why
        return False, reasons
    # причина нужна и у отказа: без неё отладка слепая — непонятно, почему
    # движок промолчал вместо того, чтобы спросить
    if "any_true" in cond:
        hit = [k for k in cond["any_true"] if fs.is_true(k)]
        return bool(hit), ([f"известно: {k}" for k in hit] if hit else
                           [f"ничего не подтверждено из: {', '.join(cond['any_true'])}"])
    if "any_known" in cond:
        hit = [k for k in cond["any_known"] if fs.is_known(k)]
        return bool(hit), ([f"уже известно: {k}" for k in hit] if hit else
                           [f"неизвестно ничего из: {', '.join(cond['any_known'])}"])
    if "none_known" in cond:
        known = [k for k in cond["none_known"] if fs.is_known(k)]
        return not known, ([f"уже известно: {k}" for k in known] if known else
                           [f"не известно ничего из: {', '.join(cond['none_known'])}"])
    if "any_conflicted" in cond:
        hit = [k for k in cond["any_conflicted"]
               if fs.state(k) is FeatureState.CONFLICTED]
        return bool(hit), ([f"противоречие: {k}" for k in hit] if hit else
                           ["противоречий нет"])
    if "feature" in cond:
        return fs.value(cond["feature"]) == cond.get("equals"), \
            [f"{cond['feature']} = {cond.get('equals')}"]
    raise contracts.ContractError(f"неизвестное условие: {cond}")


def select_next_question(*, features: ResolvedFeatureSet,
                         answers: tuple[Answer, ...] = (),
                         intent: DialogIntent = DialogIntent.FIND_CODES
                         ) -> QuestionSelectionResult:
    """Чистая функция: одни и те же признаки и ответы -> один и тот же вопрос."""
    catalog = contracts.questions()
    answered = {(a.question_id, a.question_version) for a in answers}
    trace: list[QuestionDecisionTrace] = []
    eligible: list[tuple[tuple, dict]] = []

    for p in policies():
        reasons: list[str] = []
        q = catalog[p["question_id"]]
        unknown = features.unknown_of(p["resolves"])
        conflicted = tuple(k for k in p["resolves"]
                           if features.state(k) is FeatureState.CONFLICTED)

        ok, why = _check(p["when"], features, intent)
        reasons += why
        if ok and "unless" in p:
            blocked, why_not = _check(p["unless"], features, intent)
            if blocked:
                ok = False
                reasons += why_not
        if ok and (q.id, q.version) in answered:
            ok, reasons = False, reasons + ["на этот вопрос уже отвечено"]
        if ok and not unknown and not conflicted:
            # всё, ради чего вопрос существует, уже известно — из ответа
            # или прямо из текста запроса
            ok, reasons = False, reasons + ["все признаки вопроса уже известны"]
        if ok and _useful_options(q, features) < 2:
            ok, reasons = False, reasons + ["меньше двух вариантов что-то меняют"]

        trace.append(QuestionDecisionTrace(p["id"], q.id, ok, tuple(reasons),
                                           unknown, conflicted))
        if ok:
            eligible.append((_rank(p, unknown, reasons), p))

    if not eligible:
        return QuestionSelectionResult(None, NO_POLICY_APPLIES, None, tuple(trace))
    eligible.sort(key=lambda x: x[0])
    winner = eligible[0][1]
    return QuestionSelectionResult(catalog[winner["question_id"]], ASKED,
                                   winner["id"], tuple(trace))


def _useful_options(q: Question, fs: ResolvedFeatureSet) -> int:
    """Сколько вариантов ещё способны что-то изменить.

    Если остался один, спрашивать нечего: ответ уже предопределён известными
    фактами, а значит вопрос был бы издевательством, а политика — неверной.
    """
    n = 0
    for o in q.options:
        if any(not fs.is_known(f.key) or fs.value(f.key) != f.value
               for f in o.effects):
            n += 1
    return n


def _rank(policy: dict, unknown: tuple[str, ...], reasons: list[str]) -> tuple:
    """Порядок фиксирован: приоритет, затем больше закрываемых неизвестных,
    затем больше подтверждающих свидетельств, затем id — чтобы при равенстве
    результат не зависел от порядка чтения файла."""
    return (QuestionPriority[policy["priority"].upper()].value,
            -len(unknown), -len(reasons), policy["id"])
