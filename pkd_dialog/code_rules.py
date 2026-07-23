"""Деятельность -> подкласс PKD. Первый слой, который вообще знает про коды.

Два правила, которые здесь важнее всего:

* правило принимает ActivityUnit и подтверждённые признаки — и всё. Никакого
  поиска слов в исходном тексте, никакого BM25, никакого словаря профессий:
  если правило начнёт само разбирать язык, слои снова смешаются, и станет
  непонятно, что сломалось — разбор или классификация;

* цитата из официального текста проверяется при загрузке. Не «мы уверены, что
  GUS так пишет», а дословное совпадение с каноническим справочником. Перепишет
  GUS формулировку — упадёт сборка, а не разъедется тихо смысл правил.

Числовых весов здесь нет намеренно. `confidence: 0.87` у юридического решения
означал бы точность, которой взять неоткуда.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path

import pkd

from . import features as feat
from .activity_units import ActivityUnit
from .contracts import ContractError
from .resolved import ResolvedFeatureSet

RULES = Path(__file__).resolve().parent / "rules" / "furniture.json"
WS = re.compile(r"\s+")


class CandidateState(str, Enum):
    ELIGIBLE = "eligible"
    UNRESOLVED = "unresolved"
    EXCLUDED = "excluded"


class ReasonType(str, Enum):
    """Почему кандидат исключён. Других оснований на этом срезе нет.

    «Другой код показался вероятнее», «alias не совпал», «низкий score» —
    это НЕ основания: кандидат тогда просто не создаётся или остаётся
    нерешённым, но не объявляется запрещённым.
    """

    OFFICIAL_EXCLUSION = "official_exclusion"
    CONTRADICTED = "contradicted_by_confirmed_feature"


@dataclass(frozen=True)
class OfficialEvidence:
    code: str
    field: str
    quote: str


@dataclass(frozen=True)
class CandidateDecision:
    code: str
    state: CandidateState
    activity_unit_id: str
    rule_id: str
    explanation: str
    reason_type: ReasonType | None = None
    official_evidence: tuple[OfficialEvidence, ...] = ()


def _norm(text: str) -> str:
    return WS.sub(" ", text).strip().lower()


@lru_cache(maxsize=1)
def rules() -> dict:
    """Загрузка с проверкой против канонического справочника."""
    raw = json.loads(RULES.read_text(encoding="utf-8"))
    idx = pkd.index()
    seen = set()
    for r in raw["rules"]:
        rid = r["id"]
        if rid in seen:
            raise ContractError(f"дубль правила: {rid}")
        seen.add(rid)
        if r["candidate_code"] not in idx.codes:
            raise ContractError(f"{rid}: кода {r['candidate_code']} нет в справочнике")
        CandidateState(r["effect"])
        if r["effect"] == CandidateState.EXCLUDED.value:
            ReasonType(r["reason_type"])
        elif r.get("reason_type"):
            raise ContractError(f"{rid}: причина исключения у разрешающего правила")
        if not r.get("explanation"):
            raise ContractError(f"{rid}: правило без объяснения")
        for key in _keys_of(r["when"]):
            if key not in feat.REGISTRY:
                raise ContractError(f"{rid}: неизвестный признак {key}")
        for basis in r["official_basis"]:
            _verify_quote(rid, basis, idx)
    _check_no_contradictory_pairs(raw["rules"])
    return raw


def _verify_quote(rid: str, basis: dict, idx) -> None:
    """Цитата обязана дословно быть в указанном поле указанного кода.

    Проверяем и место, и текст. Ссылка на текст соседнего уровня не проходит:
    правило, опирающееся на чужое пояснение, — это то самое загрязнение,
    которое мы вычищали из справочника.
    """
    code = basis["code"]
    if code not in idx.codes:
        raise ContractError(f"{rid}: официальная ссылка на несуществующий код {code}")
    record = idx.codes[code]
    if basis["field"] == "includes":
        haystack = record["includes"]
    elif basis["field"] == "excludes":
        haystack = pkd.exclusion_texts(record)
    elif basis["field"] == "context":
        haystack = [x["raw"] for c in idx.context_rules(code) for x in c["excludes"]]
        haystack += [t for c in idx.context_rules(code) for t in c["includes"]]
    else:
        raise ContractError(f"{rid}: неизвестное поле {basis['field']}")

    quote = _norm(basis["quote"])
    if not quote:
        raise ContractError(f"{rid}: пустая цитата")
    if not any(quote in _norm(text) for text in haystack):
        raise ContractError(
            f"{rid}: цитаты нет в {basis['field']} кода {code}. "
            f"Либо цитата придумана, либо GUS переписал текст — "
            f"в обоих случаях правило нельзя применять молча")


def _check_no_contradictory_pairs(items: list[dict]) -> None:
    """Два правила с одинаковым условием и разным эффектом — тихая лотерея."""
    seen: dict[tuple, str] = {}
    for r in items:
        key = (r["activity_kind"], r["candidate_code"],
               json.dumps(r["when"], sort_keys=True, ensure_ascii=False))
        if key in seen and seen[key] != r["effect"]:
            raise ContractError(f"{r['id']}: то же условие, что у другого правила, "
                                f"но противоположный эффект")
        seen[key] = r["effect"]


def _keys_of(cond: dict) -> set[str]:
    out: set[str] = set()
    for op in ("all", "any"):
        for sub in cond.get(op, []):
            out |= _keys_of(sub)
    if "feature" in cond:
        out.add(cond["feature"])
    return out


def _check(cond: dict, fs: ResolvedFeatureSet) -> bool:
    if "all" in cond:
        return all(_check(sub, fs) for sub in cond["all"])
    if "any" in cond:
        return any(_check(sub, fs) for sub in cond["any"])
    if "feature" in cond:
        return fs.value(cond["feature"]) == cond.get("equals")
    raise ContractError(f"неизвестное условие: {cond}")


def apply_rules(unit: ActivityUnit, fs: ResolvedFeatureSet) -> tuple[CandidateDecision, ...]:
    """Решения по кандидатам для одной деятельности.

    Порядок правил на результат не влияет: решения сортируются по коду,
    а исключение всегда сильнее разрешения — кандидат, удалённый доказанным
    правилом, не может вернуться из-за другого правила.
    """
    by_code: dict[str, CandidateDecision] = {}
    for r in rules()["rules"]:
        if r["activity_kind"] != unit.kind or not _check(r["when"], fs):
            continue
        decision = CandidateDecision(
            code=r["candidate_code"], state=CandidateState(r["effect"]),
            activity_unit_id=unit.id, rule_id=r["id"], explanation=r["explanation"],
            reason_type=ReasonType(r["reason_type"]) if r.get("reason_type") else None,
            official_evidence=tuple(OfficialEvidence(b["code"], b["field"], b["quote"])
                                    for b in r["official_basis"]))
        current = by_code.get(decision.code)
        if current is None or (current.state is not CandidateState.EXCLUDED
                               and decision.state is CandidateState.EXCLUDED):
            by_code[decision.code] = decision
    return tuple(sorted(by_code.values(), key=lambda d: d.code))


def eligible_codes(decisions) -> tuple[str, ...]:
    return tuple(d.code for d in decisions if d.state is CandidateState.ELIGIBLE)
