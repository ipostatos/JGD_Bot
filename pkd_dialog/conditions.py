"""Один язык условий для всех слоёв диалога.

Раньше вычислитель был скопирован в три файла (деятельности, правила кодов,
выбор вопроса) и успел разойтись: `none_true` понимали только деятельности,
`any_conflicted` и `intent_is` — только выбор вопроса, а правила кодов знали
лишь `all`/`any`/`feature`. Условие, переехавшее из одного JSON в другой,
падало с «неизвестное условие» или, хуже, проверялось по-другому.

Теперь операторы и их смысл описаны здесь один раз, а каждый слой проверяет
при загрузке своего файла, что условия написаны на этом языке.
"""
from __future__ import annotations

from .contracts import ContractError
from .models import DialogIntent
from .resolved import FeatureState, ResolvedFeatureSet

GROUP_OPS = ("all", "any")
LIST_OPS = ("any_true", "none_true", "any_known", "none_known", "any_conflicted")
OPS = GROUP_OPS + LIST_OPS + ("feature", "intent_is")


def validate(cond: dict, where: str) -> None:
    """Ловит опечатку в операторе при загрузке, а не на чьём-то запросе."""
    if not isinstance(cond, dict):
        raise ContractError(f"{where}: условие должно быть объектом: {cond!r}")
    ops = [k for k in cond if k in OPS]
    if len(ops) != 1:
        raise ContractError(f"{where}: в условии нужен ровно один оператор "
                            f"из {', '.join(OPS)}: {cond}")
    op = ops[0]
    extra = set(cond) - {op} - ({"equals"} if op == "feature" else set())
    if extra:
        raise ContractError(f"{where}: лишние поля {sorted(extra)} в условии {cond}")
    if op in GROUP_OPS:
        if not cond[op]:
            raise ContractError(f"{where}: пустой {op}")
        for sub in cond[op]:
            validate(sub, where)
    elif op in LIST_OPS and not cond[op]:
        raise ContractError(f"{where}: пустой список в {op}")


def keys_of(cond: dict) -> set[str]:
    """Признаки, на которые смотрит условие."""
    out: set[str] = set()
    for op in GROUP_OPS:
        for sub in cond.get(op, []):
            out |= keys_of(sub)
    for op in LIST_OPS:
        out |= set(cond.get(op, []))
    if "feature" in cond:
        out.add(cond["feature"])
    return out


def explain(cond: dict, fs: ResolvedFeatureSet,
            intent: DialogIntent = DialogIntent.FIND_CODES) -> tuple[bool, list[str]]:
    """Условие -> (выполнено, человеческие причины).

    Причина нужна и у отказа: без неё отладка слепая — непонятно, почему
    движок промолчал вместо того, чтобы спросить.
    """
    if "intent_is" in cond:
        ok = intent.value == cond["intent_is"]
        return ok, [f"намерение: {intent.value}"] if ok else [
            f"вопрос нужен только при намерении {cond['intent_is']}"]
    if "all" in cond:
        reasons: list[str] = []
        for sub in cond["all"]:
            ok, why = explain(sub, fs, intent)
            reasons += why
            if not ok:
                return False, reasons
        return True, reasons
    if "any" in cond:
        reasons = []
        for sub in cond["any"]:
            ok, why = explain(sub, fs, intent)
            if ok:
                return True, why
            reasons += why
        return False, reasons
    if "any_true" in cond:
        hit = [k for k in cond["any_true"] if fs.is_true(k)]
        return bool(hit), ([f"известно: {k}" for k in hit] if hit else
                           [f"ничего не подтверждено из: {', '.join(cond['any_true'])}"])
    if "none_true" in cond:
        hit = [k for k in cond["none_true"] if fs.is_true(k)]
        return not hit, ([f"подтверждено: {k}" for k in hit] if hit else
                         [f"не подтверждено ничего из: {', '.join(cond['none_true'])}"])
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
    raise ContractError(f"неизвестное условие: {cond}")


def check(cond: dict, fs: ResolvedFeatureSet,
          intent: DialogIntent = DialogIntent.FIND_CODES) -> bool:
    return explain(cond, fs, intent)[0]
