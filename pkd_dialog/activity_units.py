"""Признаки -> самостоятельные виды деятельности.

Слой нужен ровно потому, что набор признаков не равен набору деятельностей.
«Проектирую и произвожу мебель» — два действия, но чертёж у мебельщика это
этап изготовления, а не отдельная услуга. «Ремонтирую и иногда делаю новую» —
два самостоятельных направления, и слово «иногда» этого не отменяет.

Поэтому здесь нельзя написать `if положительных_действий >= 2`. Считать
деятельности самостоятельными разрешено только по объявленным условиям,
а не по количеству глаголов в предложении.

Кодов PKD в этом модуле нет ни одного: сопоставление деятельности и кода —
следующий слой и отдельный пакет правил.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from pathlib import Path

from . import features as feat
from .contracts import ContractError
from .resolved import ResolvedFeatureSet

POLICIES = Path(__file__).resolve().parent / "activity_unit_policies.json"
FURNITURE_PACK = "furniture"


class ActivityIndependence(str, Enum):
    """Самостоятельная деятельность или часть другой."""

    STANDALONE = "standalone"
    INTEGRATED = "integrated"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ActivityUnit:
    id: str
    kind: str
    title: str                      # человеческое имя деятельности для интерфейса
    object: str | None
    independence: ActivityIndependence
    qualifiers: tuple[str, ...] = ()
    source_features: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    pack: str = FURNITURE_PACK
    why: str = ""

    @property
    def outside_current_pack(self) -> bool:
        return self.pack != FURNITURE_PACK


@lru_cache(maxsize=1)
def policies() -> list[dict]:
    raw = json.loads(POLICIES.read_text(encoding="utf-8"))
    seen_ids, seen_kinds = set(), set()
    for p in raw["units"]:
        if p["id"] in seen_ids:
            raise ContractError(f"дубль политики деятельности: {p['id']}")
        seen_ids.add(p["id"])
        if p["kind"] in seen_kinds:
            raise ContractError(f"два правила дают одну деятельность: {p['kind']}")
        seen_kinds.add(p["kind"])
        ActivityIndependence(p["independence"])
        if not p.get("why"):
            raise ContractError(f"{p['id']}: политика без объяснения")
        if not p.get("title"):
            # без имени интерфейс покажет `kind` — внутренний идентификатор
            # вместо деятельности, и человек не поймёт, к чему относится код
            raise ContractError(f"{p['id']}: политика без имени деятельности")
        for key in _keys_of(p["when"]):
            if key not in feat.REGISTRY:
                raise ContractError(f"{p['id']}: неизвестный признак {key}")
        import re
        if re.search(r"\b\d{2}\.\d{2}\.[A-Z]\b",
                     json.dumps({k: v for k, v in p.items() if k != "why"},
                                ensure_ascii=False)):
            raise ContractError(f"{p['id']}: в слое деятельностей не может быть кода PKD")
    return raw["units"]


def _keys_of(cond: dict) -> set[str]:
    out: set[str] = set()
    for op in ("all", "any"):
        for sub in cond.get(op, []):
            out |= _keys_of(sub)
    for op in ("any_true", "none_true", "any_known", "none_known"):
        out |= set(cond.get(op, []))
    if "feature" in cond:
        out.add(cond["feature"])
    return out


def _check(cond: dict, fs: ResolvedFeatureSet) -> bool:
    if "all" in cond:
        return all(_check(sub, fs) for sub in cond["all"])
    if "any" in cond:
        return any(_check(sub, fs) for sub in cond["any"])
    if "any_true" in cond:
        return any(fs.is_true(k) for k in cond["any_true"])
    if "none_true" in cond:
        return not any(fs.is_true(k) for k in cond["none_true"])
    if "any_known" in cond:
        return any(fs.is_known(k) for k in cond["any_known"])
    if "none_known" in cond:
        return not any(fs.is_known(k) for k in cond["none_known"])
    if "feature" in cond:
        return fs.value(cond["feature"]) == cond.get("equals")
    raise ContractError(f"неизвестное условие: {cond}")


def build_units(fs: ResolvedFeatureSet) -> tuple[ActivityUnit, ...]:
    """Деятельности в порядке объявления политик.

    Порядок фиксирован файлом, а не порядком признаков: одинаковый набор
    фактов обязан давать одинаковый список, как бы они ни пришли.
    """
    units: list[ActivityUnit] = []
    for p in policies():
        if not _check(p["when"], fs):
            continue
        independence = ActivityIndependence(p["independence"])
        if p.get("standalone_when") and _check(p["standalone_when"], fs):
            independence = ActivityIndependence.STANDALONE
        elif p.get("integrated_when") and _check(p["integrated_when"], fs):
            independence = ActivityIndependence.INTEGRATED
        elif p["independence"] == ActivityIndependence.INTEGRATED.value:
            # по умолчанию интегрированная, но обосновать интеграцию нечем —
            # честнее сказать «неизвестно», чем молча растворить деятельность
            independence = ActivityIndependence.UNKNOWN

        keys = tuple(sorted(k for k in _keys_of(p["when"]) if fs.is_true(k)))
        units.append(ActivityUnit(
            id=p["id"], kind=p["kind"], title=p["title"], object=p.get("object"),
            independence=independence, qualifiers=tuple(p.get("qualifiers", ())),
            source_features=keys,
            evidence=tuple(e for e in (fs.features[k].evidence for k in keys) if e),
            pack=p.get("pack", FURNITURE_PACK), why=p["why"]))
    return tuple(units)


def standalone(units) -> tuple[ActivityUnit, ...]:
    return tuple(u for u in units
                 if u.independence is ActivityIndependence.STANDALONE)


def outside_pack(units) -> tuple[ActivityUnit, ...]:
    return tuple(u for u in units if u.outside_current_pack)


def derived_features(units) -> dict[str, bool]:
    """Факты о самом диалоге, выведенные из состава деятельностей.

    Единственный законный источник для вопроса о выручке: спрашивать про
    преобладающую деятельность можно, только когда доказано, что деятельностей
    несколько и они самостоятельные.
    """
    in_pack = [u for u in standalone(units) if not u.outside_current_pack]
    return {feat.MULTIPLE_ACTIVITIES: len(in_pack) >= 2}
