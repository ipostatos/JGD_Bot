"""Состояние признака после слияния текста и ответов человека.

Почему четыре состояния, а не «есть значение / нет значения»:

* `false` — человек явно отрицает деятельность, переспрашивать её незачем;
* `unknown` — данных нет, и это законный повод задать вопрос;
* `conflicted` — свидетельства несовместимы, и молча считать это отсутствием
  признака нельзя: вопрос нужен, но другой — разрешающий противоречие.

Проигравшее свидетельство не выбрасывается. Иначе движок, увидев в тексте
«встроенные», будет снова и снова спрашивать про объект, хотя человек уже
ответил «отдельно стоящая», и его ответ победил.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .models import TRUST, Answer, Feature, FeatureSource


class FeatureState(str, Enum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"
    CONFLICTED = "conflicted"


@dataclass(frozen=True)
class ResolvedFeature:
    key: str
    state: FeatureState
    value: bool | str | None
    source: FeatureSource | None
    evidence: str | None = None
    overruled: tuple[str, ...] = ()      # что проиграло и почему


@dataclass(frozen=True)
class ResolvedFeatureSet:
    """Итоговая картина признаков. Строится заново на каждый запрос."""

    features: dict[str, ResolvedFeature] = field(default_factory=dict)

    def state(self, key: str) -> FeatureState:
        f = self.features.get(key)
        return FeatureState.UNKNOWN if f is None else f.state

    def value(self, key: str):
        f = self.features.get(key)
        return None if f is None else f.value

    def is_true(self, key: str) -> bool:
        return self.state(key) is FeatureState.TRUE

    def is_known(self, key: str) -> bool:
        return self.state(key) in (FeatureState.TRUE, FeatureState.FALSE)

    def unknown_of(self, keys) -> tuple[str, ...]:
        return tuple(k for k in keys if not self.is_known(k))

    def conflicted(self) -> tuple[str, ...]:
        return tuple(sorted(k for k, f in self.features.items()
                            if f.state is FeatureState.CONFLICTED))

    def known_true(self, keys) -> tuple[str, ...]:
        return tuple(k for k in keys if self.is_true(k))


def resolve_features(extracted: list[Feature], answers_features: list[Feature]
                     ) -> ResolvedFeatureSet:
    """Текст + ответы -> состояние каждого признака.

    Ответ человека перекрывает извлечение из текста: иначе диалог
    бессмыслен. Конфликт внутри одного уровня доверия не разрешается
    произвольно — признак становится `conflicted` и порождает вопрос.
    """
    by_key: dict[str, list[Feature]] = {}
    for f in list(extracted) + list(answers_features):
        by_key.setdefault(f.key, []).append(f)

    out: dict[str, ResolvedFeature] = {}
    for key, items in by_key.items():
        best_trust = max(TRUST[f.source] for f in items)
        top = [f for f in items if TRUST[f.source] == best_trust]
        values = {f.value for f in top}
        losers = tuple(sorted(
            f"{f.value} ({f.source.value}"
            + (f": «{f.evidence_span}»" if f.evidence_span else "") + ")"
            for f in items if f not in top or f.value != top[0].value))

        if len(values) > 1:
            out[key] = ResolvedFeature(key, FeatureState.CONFLICTED, None,
                                       None, None, losers)
            continue
        winner = top[0]
        state = (FeatureState.TRUE if winner.value is True else
                 FeatureState.FALSE if winner.value is False else
                 FeatureState.TRUE)          # enum-значение = признак известен
        out[key] = ResolvedFeature(key, state, winner.value, winner.source,
                                   winner.evidence_span, losers)
    return ResolvedFeatureSet(out)
