"""Извлечение признаков из текста детерминированными правилами.

Задача одна: превратить «собираю кухни» в признаки со ссылкой на слово,
из которого они взялись. Ни кодов PKD, ни выбора вопроса, ни статусов —
иначе ошибки разбора языка смешаются с ошибками логики диалога, и станет
непонятно, что именно сломалось.

Три вещи, из-за которых этот модуль выглядит именно так:

* совпадение только по целому слову или последовательности слов. Обрезка
  до общего корня уже склеивала «перевозку» с «переводчиком» и «монтажника»
  с «монтажёром» — здесь этого нет и не будет;
* span указывает в ИСХОДНЫЙ текст, а не в нормализованный: иначе человеку
  нельзя показать, почему движок так решил, а тесту — доказать, что признак
  взялся из реального слова;
* отрицание даёт факт `value=false`, а не отсутствие признака. Явное
  «не произвожу» сильнее молчания и должно переживать слияние.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from pathlib import Path

from .. import features as feat
from ..models import TRUST, FeatureSource

RULES_PATH = Path(__file__).resolve().parent / "rules.json"
TOKEN = re.compile(r"[а-яёa-z0-9]+", re.I)


class Confidence(str, Enum):
    """Насколько буквально признак взялся из текста.

    Отдельное измерение от источника: «точное совпадение» и «догадка»
    различаются даже внутри одного источника. Числовой вероятности здесь
    намеренно нет — её было бы неоткуда взять честно.
    """

    EXACT = "exact"                       # слово или фраза найдены как есть
    NORMALIZED_EXACT = "normalized_exact"  # совпало после нормализации ё/регистра
    ALIAS = "alias"                       # пришло из словаря профессий
    INFERRED = "inferred"                 # вывод по косвенным признакам


@dataclass(frozen=True)
class EvidenceSpan:
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class FeatureValue:
    key: str
    value: bool | str
    source: FeatureSource
    confidence: Confidence
    rule_id: str
    evidence: tuple[EvidenceSpan, ...]
    negated: bool = False

    @property
    def span_len(self) -> int:
        return sum(e.end - e.start for e in self.evidence)


@dataclass(frozen=True)
class FeatureConflict:
    """Два правила дали разные значения одного признака.

    Конфликт нельзя молча потерять: «не произвожу мебель, хотя иногда делаю
    отдельные элементы» — это не ошибка пользователя, а повод переспросить.
    """

    key: str
    winning_value: bool | str | None
    losing_values: tuple
    reason: str


@dataclass(frozen=True)
class ExtractionResult:
    original_text: str
    normalized_text: str
    features: tuple[FeatureValue, ...]
    conflicts: tuple[FeatureConflict, ...] = ()
    unmatched_tokens: tuple[str, ...] = ()
    blocked_keys: tuple[str, ...] = ()
    trace: tuple[dict, ...] = ()

    def value(self, key: str):
        f = next((x for x in self.features if x.key == key), None)
        return None if f is None else f.value

    def keys(self) -> set[str]:
        return {f.key for f in self.features}


@dataclass(frozen=True)
class Token:
    norm: str
    start: int
    end: int


def tokenize(text: str) -> list[Token]:
    """Токены с координатами в ИСХОДНОМ тексте.

    Нормализуем ровно две вещи — регистр и «ё»: этого хватает, чтобы
    «Ё» и «Е» сошлись, и не хватает, чтобы склеить разные слова.
    """
    return [Token(m.group(0).lower().replace("ё", "е"), m.start(), m.end())
            for m in TOKEN.finditer(text)]


@lru_cache(maxsize=1)
def load_rules() -> dict:
    raw = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    seen = set()
    for r in raw["rules"]:
        if r["id"] in seen:
            raise ValueError(f"дубль правила извлечения: {r['id']}")
        seen.add(r["id"])
        feat.validate(r["feature"], r["value"])
        FeatureSource(r["source"])
        Confidence(r["confidence"])
        patterns = r["match"].get("any")
        if not patterns:
            raise ValueError(f"{r['id']}: правило без образцов")
        for p in patterns:
            if p["type"] not in ("token", "phrase", "near"):
                raise ValueError(f"{r['id']}: неизвестный тип образца {p['type']}")
            if not p["value"].strip():
                raise ValueError(f"{r['id']}: пустой образец")
            if p["type"] == "token" and " " in p["value"]:
                raise ValueError(f"{r['id']}: токен с пробелом — это фраза")
            if p["type"] == "near" and not p.get("with"):
                raise ValueError(f"{r['id']}: near без списка спутников — "
                                 f"это совпадение по одному слову")
        unknown = set(r) - {"id", "feature", "value", "source", "confidence",
                            "why", "match"}
        if unknown:
            raise ValueError(f"{r['id']}: лишние поля {unknown}")
    for m in raw["ambiguity_markers"]:
        for key in m["blocks"]:
            if key not in feat.REGISTRY:
                raise ValueError(f"{m['id']}: блокирует неизвестный признак {key}")
    return raw


class RuleBasedFeatureExtractor:
    """Единственная реализация на первом срезе. LLM-вариант появится позже
    и обязан отдавать тот же ExtractionResult, а не свой формат."""

    def __init__(self, rules: dict | None = None):
        self.rules = rules or load_rules()

    # ── совпадения ────────────────────────────────────────────────────────
    def _matches(self, pattern: dict, toks: list[Token]) -> list[tuple[int, int]]:
        """Индексы токенов [начало, конец) для каждого совпадения образца.

        Тип `near` нужен для «делаю деревянные окна»: глагол и предмет стоят
        рядом, но между ними прилагательное. Спутники перечислены поимённо —
        это не обрезка корня и не «любое слово поблизости».
        """
        norms = [t.norm for t in toks]
        if pattern.get("type") == "near":
            head = pattern["value"].lower().replace("ё", "е")
            near = {w.lower().replace("ё", "е") for w in pattern["with"]}
            window = int(pattern.get("window", 3))
            out = []
            for i, t in enumerate(norms):
                if t != head:
                    continue
                for j in range(i + 1, min(i + 1 + window, len(norms))):
                    if norms[j] in near:
                        out.append((i, j + 1))
                        break
            return out
        words = [w.lower().replace("ё", "е") for w in pattern["value"].split()]
        n = len(words)
        out = []
        for i in range(len(toks) - n + 1):
            if norms[i:i + n] == words:
                out.append((i, i + n))
        return out

    def _negated(self, text: str, toks: list[Token], start: int) -> bool:
        """Есть ли отрицание в окне перед совпадением.

        Две ловушки, на которых наивное окно ошибается:

        «Не произвожу, только собираю» — «не» относится к производству,
        а не к сборке, и запятая это разделяет. Поэтому отрицание не
        перепрыгивает границу оборота: запятую, тире, точку или союз.

        «Не только собираю, но и делаю» — подтверждение обеих деятельностей,
        поэтому «не только» из отрицаний исключено: иначе движок решил бы,
        что человек как раз не собирает.
        """
        neg = self.rules["negation"]
        window = toks[max(0, start - neg["window"]):start]
        chain = [t.norm for t in window]
        for exc in neg["affirmative_exceptions"]:
            ex = exc.split()
            for i in range(len(chain) - len(ex) + 1):
                if chain[i:i + len(ex)] == ex:
                    return False
        for k, t in enumerate(window):
            if t.norm not in neg["markers"]:
                continue
            between = text[t.end:toks[start].start]
            if any(sep in between for sep in neg["clause_breaks"]):
                continue          # отрицание осталось в предыдущем обороте
            return True
        return False

    def extract(self, text: str) -> ExtractionResult:
        toks = tokenize(text)
        found: list[FeatureValue] = []
        trace: list[dict] = []
        used: set[int] = set()

        # маркеры неоднозначности: «под ключ» не даёт признака, а запрещает
        # выводить режим деятельности из общих слов
        blocked: set[str] = set()
        for m in self.rules["ambiguity_markers"]:
            for phrase in m["phrases"]:
                if self._matches({"value": phrase}, toks):
                    blocked |= set(m["blocks"])
                    trace.append({"marker": m["id"], "phrase": phrase,
                                  "blocks": sorted(m["blocks"])})

        for rule in self.rules["rules"]:
            for pattern in rule["match"]["any"]:
                for i, j in self._matches(pattern, toks):
                    span = EvidenceSpan(text[toks[i].start:toks[j - 1].end],
                                        toks[i].start, toks[j - 1].end)
                    negated = self._negated(text, toks, i)
                    value = rule["value"]
                    if negated:
                        # отрицание применимо только к прямому «делаю X» → «не делаю X».
                        # value=false у правил — это импликация («отдаю на фабрику» ⇒
                        # сам не произвожу), и отрицание триггера обратного факта не
                        # даёт: «не отдаю на фабрику» ≠ «произвожу сам» (может и
                        # перепродавать). Такое правило под отрицанием не срабатывает.
                        # Небулевы (материал: «не из дерева») — тоже: конкретный
                        # материал этим не задан и отрицать нечего.
                        if value is not True:
                            continue
                        value = False
                    if rule["feature"] in blocked and not negated:
                        trace.append({"rule": rule["id"], "skipped": "ambiguity_marker",
                                      "evidence": span.text})
                        continue
                    used |= set(range(i, j))
                    found.append(FeatureValue(
                        key=rule["feature"], value=value,
                        source=FeatureSource(rule["source"]),
                        confidence=Confidence(rule["confidence"]),
                        rule_id=rule["id"], evidence=(span,), negated=negated))
                    trace.append({"rule": rule["id"], "feature": rule["feature"],
                                  "value": value, "evidence": span.text,
                                  "negated": negated})

        resolved, conflicts = resolve(found)
        # «только проектирую» означает, что производства и монтажа нет
        resolved = _apply_design_only(resolved)
        unmatched = tuple(t.norm for k, t in enumerate(toks) if k not in used)
        return ExtractionResult(
            original_text=text,
            normalized_text=" ".join(t.norm for t in toks),
            features=tuple(sorted(resolved, key=lambda f: f.key)),
            conflicts=tuple(conflicts), unmatched_tokens=unmatched,
            blocked_keys=tuple(sorted(blocked)), trace=tuple(trace))


def _apply_design_only(found: list[FeatureValue]) -> list[FeatureValue]:
    """«Только проектирую» — это ограничитель, а не просто ещё один признак.

    Без него design_only ничего не значил бы для правил: код проектировщика
    должен выбираться именно потому, что производства и монтажа нет.
    """
    only = next((f for f in found if f.key == feat.ACTIVITY_DESIGN_ONLY
                 and f.value is True), None)
    if only is None:
        return found
    out = {f.key: f for f in found}
    out.setdefault(feat.ACTIVITY_DESIGN, FeatureValue(
        feat.ACTIVITY_DESIGN, True, only.source, only.confidence,
        only.rule_id, only.evidence))
    for key in (feat.ACTIVITY_MANUFACTURE, feat.ACTIVITY_INSTALL):
        if key not in out:
            out[key] = FeatureValue(key, False, only.source, only.confidence,
                                    only.rule_id, only.evidence, negated=True)
    return list(out.values())


def resolve(found: list[FeatureValue]) -> tuple[list[FeatureValue], list[FeatureConflict]]:
    """Слияние с явным разрешением конфликтов.

    Порядок разбора спора зафиксирован, потому что «взять первый» — это
    молчаливая лотерея: при равных данных результат обязан быть одинаковым
    от прогона к прогону.
    """
    by_key: dict[str, list[FeatureValue]] = {}
    for f in found:
        by_key.setdefault(f.key, []).append(f)

    out, conflicts = [], []
    for key, items in by_key.items():
        values = {f.value for f in items}
        if len(values) == 1:
            out.append(_best(items))
            continue
        ranked = sorted(items, key=_rank)
        winner, runner = ranked[0], ranked[1]
        if _rank(winner) == _rank(runner) and winner.value != runner.value:
            # полное равенство: молча выбирать нельзя, признак остаётся
            # неизвестным и станет поводом для вопроса
            conflicts.append(FeatureConflict(
                key, None, tuple(sorted(map(str, values))), "tie_unresolved"))
            continue
        out.append(winner)
        conflicts.append(FeatureConflict(
            key, winner.value,
            tuple(sorted(str(f.value) for f in items if f.value != winner.value)),
            _reason(winner, runner)))
    return out, conflicts


def _rank(f: FeatureValue) -> tuple:
    """Меньше — сильнее. Порядок критериев зафиксирован спецификацией."""
    return (
        -TRUST[f.source],                               # доверие к источнику
        0 if f.negated else 1,                          # явное отрицание сильнее
        -f.span_len,                                    # длинная точная фраза сильнее
        f.evidence[0].start if f.evidence else 0,       # раньше в тексте
        f.rule_id,                                      # полное равенство — по id
    )


def _reason(winner: FeatureValue, runner: FeatureValue) -> str:
    if winner.negated and not runner.negated:
        return "explicit_negation_over_affirmation"
    if winner.span_len > runner.span_len:
        return "longer_exact_phrase"
    return "earlier_evidence_span"


def _best(items: list[FeatureValue]) -> FeatureValue:
    return sorted(items, key=_rank)[0]
