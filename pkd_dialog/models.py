"""Типы и семантика контракта диалогового подбора PKD.

Здесь только форма данных и правила их слияния. Ни одного правила выбора
кода: их пишем следующим шагом, и они обязаны укладываться в эти типы,
а не диктовать себе удобную форму.

Ключевое, что задают эти типы:

* признак всегда знает, откуда он взялся (`FeatureSource`), и ответ человека
  перекрывает любую догадку движка — иначе диалог бессмыслен;
* главный код не может появиться «сам»: у `PrimaryStatus` намеренно нет
  значения `system_selected`, потому что преобладающую деятельность
  определяет доля выручки, а не поисковый score;
* ответ сервера воспроизводим: состояние не хранится, а пересчитывается
  из запроса и уже данных ответов, и `input_fingerprint` это доказывает.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    """Что движок может сказать про запрос целиком."""

    # признаков не хватает, чтобы безопасно выбрать кандидата
    NEEDS_CLARIFICATION = "needs_clarification"
    # одна деятельность и один или несколько допустимых кандидатов
    RESOLVED_CANDIDATES = "resolved_candidates"
    # несколько отдельных деятельностей и пакет кодов под них
    RESOLVED_PACKAGE = "resolved_package"
    # деятельность распознана, но за неё отвечает другой пакет правил.
    # Это не «кода нет» и не повод спрашивать дальше: ни один мебельный
    # вопрос не поможет тому, кто делает отделку комнаты
    OUTSIDE_CURRENT_PACK = "outside_current_pack"


class PrimaryStatus(str, Enum):
    """Что движок может сказать про главный код.

    `system_selected` тут нет сознательно: движок не назначает главный код
    по своим весам. Либо человек ответил про выручку, либо он не спрашивал,
    либо мы честно говорим, что данных нет.
    """

    NOT_REQUESTED = "not_requested"
    REVENUE_INFORMATION_REQUIRED = "revenue_information_required"
    USER_SELECTED = "user_selected"


class DialogIntent(str, Enum):
    """Зачем человек пришёл. Разные намерения — разные обязательные вопросы.

    Спрашивать про долю выручки уместно только тогда, когда человек хочет
    знать ГЛАВНЫЙ код. Если он просто выясняет, какие коды ему подходят,
    вопрос про выручку — лишняя работа для него и ничего не меняет в ответе.
    """

    FIND_CODES = "find_codes"
    FIND_PRIMARY_CODE = "find_primary_code"


class FeatureSource(str, Enum):
    """Откуда взялся признак. Порядок объявления = порядок доверия."""

    USER_ANSWER = "user_answer"        # ответ на заданный вопрос
    EXPLICIT_QUERY = "explicit_query"  # прямо сказано в тексте: «собираю кухни»
    ALIAS = "alias"                    # проверенная запись словаря профессий
    INFERRED = "inferred"              # вывод по косвенным признакам


# больший вес перекрывает меньший при слиянии признаков
TRUST = {s: i for i, s in enumerate(reversed(list(FeatureSource)))}


@dataclass(frozen=True)
class Feature:
    """Признак деятельности вместе с происхождением.

    `evidence_span` — кусок исходного текста, из которого признак взялся.
    Он нужен не для красоты: без него нельзя показать человеку, почему
    движок так решил, и нельзя отличить извлечение из текста от догадки.
    """

    key: str
    value: bool | str
    source: FeatureSource
    evidence_span: str | None = None

    @property
    def trust(self) -> int:
        return TRUST[self.source]


@dataclass(frozen=True)
class Answer:
    """Ответ человека на конкретную версию конкретного вопроса.

    Версия обязательна: если формулировка или набор вариантов изменились,
    старый ответ нельзя молча применить к новой логике — контракт вернёт
    `invalid_answers`, а не притворится, что понял.
    """

    question_id: str
    question_version: int
    option_ids: tuple[str, ...]

    @classmethod
    def from_dict(cls, d: dict) -> "Answer":
        return cls(str(d["question_id"]), int(d["question_version"]),
                   tuple(str(o) for o in d["option_ids"]))

    def as_dict(self) -> dict:
        return {"question_id": self.question_id,
                "question_version": self.question_version,
                "option_ids": list(self.option_ids)}


@dataclass(frozen=True)
class QuestionOption:
    """Вариант ответа. Каждый обязан менять признаки, иначе вопрос холостой."""

    id: str
    label: str
    effects: tuple[Feature, ...]


@dataclass(frozen=True)
class Question:
    """Вопрос из questions.json.

    `resolves` — признаки, ради которых вопрос и задаётся. По ним движок
    выбирает следующий вопрос: спрашивать надо то, чего не хватает, а не
    следующий пункт анкеты.
    """

    id: str
    version: int
    type: str                  # single_select | multi_select
    text: str
    options: tuple[QuestionOption, ...]
    resolves: tuple[str, ...]
    help: str | None = None

    @property
    def versioned_id(self) -> str:
        return f"{self.id}.v{self.version}"

    def option(self, option_id: str) -> QuestionOption | None:
        return next((o for o in self.options if o.id == option_id), None)


class FeatureSet:
    """Признаки запроса с учётом происхождения.

    Слияние по одному правилу: побеждает более доверенный источник. Ответ
    человека перекрывает извлечение из текста, извлечение — словарь, словарь —
    вывод по косвенным признакам. При равном доверии остаётся первый: повторный
    прогон одних и тех же данных обязан дать тот же результат.
    """

    def __init__(self, features: list[Feature] | None = None):
        self._by_key: dict[str, Feature] = {}
        for f in features or []:
            self.add(f)

    def add(self, feature: Feature) -> None:
        cur = self._by_key.get(feature.key)
        if cur is None or feature.trust > cur.trust:
            self._by_key[feature.key] = feature

    def get(self, key: str) -> Feature | None:
        return self._by_key.get(key)

    def value(self, key: str, default=None):
        f = self._by_key.get(key)
        return default if f is None else f.value

    def is_true(self, key: str) -> bool:
        return self.value(key) is True

    def known(self) -> set[str]:
        return set(self._by_key)

    def missing(self, keys) -> set[str]:
        return {k for k in keys if k not in self._by_key}

    def as_list(self) -> list[Feature]:
        return [self._by_key[k] for k in sorted(self._by_key)]

    def __len__(self) -> int:
        return len(self._by_key)

    def __contains__(self, key: str) -> bool:
        return key in self._by_key


@dataclass(frozen=True)
class ActivityResolution:
    """Одна деятельность и коды, которые ей соответствуют."""

    activity_id: str
    label: str
    candidate_codes: tuple[str, ...]
    reasons: tuple[str, ...] = ()


@dataclass
class DialogState:
    """Ответ движка. Пересчитывается с нуля, на сервере не хранится."""

    status: Status
    questions: list[Question] = field(default_factory=list)
    activities: list[ActivityResolution] = field(default_factory=list)
    candidate_codes: list[str] = field(default_factory=list)
    primary_code: str | None = None
    primary_code_status: PrimaryStatus = PrimaryStatus.NOT_REQUESTED
    input_fingerprint: str = ""
    versions: dict = field(default_factory=dict)
    debug_trace: dict | None = None      # только при включённом флаге отладки


WS = re.compile(r"\s+")


def fingerprint(query: str, answers: list[Answer], versions: dict) -> str:
    """Отпечаток входа: одинаковый вход обязан давать одинаковый ответ.

    Считается от нормализованного текста, ОТСОРТИРОВАННЫХ ответов и версий
    корпуса, правил и схемы. Сортировка важна: порядок, в котором человек
    отвечал, на результат влиять не должен, а вот смена версии правил —
    должна, иначе старый отпечаток тихо подтвердит новый ответ.
    """
    payload = {
        "query": WS.sub(" ", query).strip().lower(),
        "answers": sorted((a.question_id, a.question_version, tuple(sorted(a.option_ids)))
                          for a in answers),
        "versions": {k: versions[k] for k in sorted(versions)},
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=list)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]
