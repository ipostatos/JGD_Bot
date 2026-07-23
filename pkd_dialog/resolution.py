"""Чистая функция диалога: текст и ответы -> статус, деятельности, коды.

Ручки и UI здесь нет намеренно: движок должен быть полностью зелёным до того,
как его увидит хоть один пользователь. Состояние не хранится — каждый вызов
пересчитывает всё заново, поэтому одинаковый вход обязан давать одинаковый
выход, и это проверяется тестом.

Главные обещания:

* `needs_clarification` не возвращается без вопроса. Статус без вопроса —
  это тупик, в котором человек не понимает, чего от него хотят;
* деятельность из чужого пакета не отменяет полезный результат: «ремонтирую
  мебель и делаю ремонт кухни как комнаты» обязано вернуть код ремонта мебели
  и честно сказать, что отделка — не сюда;
* главный код не назначается движком. Пока не разрешены все деятельности,
  участвующие в выборе, честный ответ — «сначала нужно разобраться с ними».
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import contracts, features as feat
from .activity_units import (ActivityIndependence, ActivityUnit, build_units,
                             derived_features, standalone)
from .code_rules import (CandidateDecision, CandidateState, apply_rules,
                         eligible_codes, rules)
from .extraction import RuleBasedFeatureExtractor
from .models import (Answer, DialogIntent, Feature, FeatureSource, PrimaryStatus,
                     Question, Status, fingerprint)
from .resolved import ResolvedFeatureSet, resolve_features
from .selection import select_next_question

EXTRACTOR = RuleBasedFeatureExtractor()


class UncoveredActivityUnitError(RuntimeError):
    """Деятельность распознана, вопросов больше нет, а правил под неё нет.

    Это дыра в покрытии, а не «наверное, чужой пакет». Молчаливый fallback
    здесь означал бы, что движок тихо теряет людей целыми отраслями.
    """


@dataclass(frozen=True)
class UnitResolution:
    activity_unit_id: str
    kind: str
    state: str                      # resolved | unresolved | outside_current_pack
    candidate_decisions: tuple[CandidateDecision, ...] = ()
    suggested_pack: str | None = None
    explanation: tuple[str, ...] = ()

    @property
    def codes(self) -> tuple[str, ...]:
        return eligible_codes(self.candidate_decisions)


RESOLVED = "resolved"
UNRESOLVED = "unresolved"
OUTSIDE = "outside_current_pack"


@dataclass(frozen=True)
class DialogResolution:
    status: Status
    next_question: Question | None = None
    units: tuple[UnitResolution, ...] = ()
    primary_code: str | None = None
    primary_code_status: PrimaryStatus = PrimaryStatus.NOT_REQUESTED
    reason: str | None = None
    routing_hint: str | None = None
    input_fingerprint: str = ""
    versions: dict = field(default_factory=dict)
    trace: dict = field(default_factory=dict)

    @property
    def codes(self) -> tuple[str, ...]:
        seen: list[str] = []
        for u in self.units:
            seen += [c for c in u.codes if c not in seen]
        return tuple(seen)


def resolve_furniture_dialog(*, query: str, answers: tuple[Answer, ...] = (),
                             intent: DialogIntent = DialogIntent.FIND_CODES
                             ) -> DialogResolution:
    extraction = EXTRACTOR.extract(query)
    extracted = [Feature(f.key, f.value, f.source,
                         f.evidence[0].text if f.evidence else None)
                 for f in extraction.features]
    fs = resolve_features(extracted, contracts.features_from_answers(list(answers)))

    units = build_units(fs)
    fs = _with_derived(fs, units)

    selection = select_next_question(features=fs, answers=answers, intent=intent)
    versions = {"corpus": "pkd-2025", "rules": rules()["rules_version"],
                "dialog_schema": 1}
    base = {"input_fingerprint": fingerprint(query, list(answers), versions),
            "versions": versions}

    if selection.question is not None and _question_changes_anything(
            selection.question, fs, units):
        return DialogResolution(
            status=Status.NEEDS_CLARIFICATION, next_question=selection.question,
            units=(), trace={"selection": selection.policy_id,
                             "features": sorted(fs.features)}, **base)

    resolutions = tuple(_resolve_unit(u, fs) for u in units
                        if u.independence is ActivityIndependence.STANDALONE)
    in_pack = [r for r in resolutions if r.state != OUTSIDE]
    outside = [r for r in resolutions if r.state == OUTSIDE]

    status = _top_status(in_pack, outside)
    primary, primary_status = _primary(in_pack, outside, fs, intent)
    reason, hint = _routing(status, outside)
    return DialogResolution(status=status, units=resolutions,
                            primary_code=primary, primary_code_status=primary_status,
                            reason=reason, routing_hint=hint,
                            trace={"selection": selection.reason,
                                   "features": sorted(fs.features)}, **base)


def _with_derived(fs: ResolvedFeatureSet, units) -> ResolvedFeatureSet:
    """Признаки о самом диалоге выводятся из состава деятельностей."""
    derived = [Feature(k, v, FeatureSource.INFERRED)
               for k, v in derived_features(units).items()]
    return resolve_features([f for f in _as_features(fs)], derived)


def _as_features(fs: ResolvedFeatureSet) -> list[Feature]:
    return [Feature(f.key, f.value, f.source or FeatureSource.INFERRED, f.evidence)
            for f in fs.features.values() if f.value is not None]


def _resolve_unit(unit: ActivityUnit, fs: ResolvedFeatureSet) -> UnitResolution:
    if unit.outside_current_pack:
        return UnitResolution(unit.id, unit.kind, OUTSIDE, (), unit.pack,
                              (unit.why,))
    decisions = apply_rules(unit, fs)
    if not decisions:
        raise UncoveredActivityUnitError(
            f"{unit.kind}: деятельность распознана, вопросов больше нет, "
            f"а правила под неё нет — это дыра в покрытии, а не чужой пакет")
    codes = eligible_codes(decisions)
    state = RESOLVED if codes else UNRESOLVED
    return UnitResolution(unit.id, unit.kind, state, decisions, None,
                          tuple(d.explanation for d in decisions
                                if d.state is CandidateState.ELIGIBLE))


def _top_status(in_pack, outside) -> Status:
    if in_pack and outside:
        return Status.RESOLVED_PACKAGE
    if outside and not in_pack:
        return Status.OUTSIDE_CURRENT_PACK
    if len(in_pack) >= 2:
        return Status.RESOLVED_PACKAGE
    if len(in_pack) == 1:
        return Status.RESOLVED_CANDIDATES
    # Деятельностей не сформировалось вовсе. Это НЕ «понятая деятельность вне
    # пакета»: мы не знаем, чужой это пакет или разбор не понял формулировку,
    # и врать про распознавание нельзя — маршрут отсюда другой.
    return Status.UNRECOGNIZED_ACTIVITY


def _routing(status: Status, outside):
    """Куда девать запрос дальше. Подсказка оркестратору, не пользователю."""
    if status is Status.UNRECOGNIZED_ACTIVITY:
        return "no_activity_units", "general_search"
    if outside:
        return "activity_outside_pack", outside[0].suggested_pack
    return None, None


def _primary(in_pack, outside, fs: ResolvedFeatureSet, intent: DialogIntent):
    if intent is not DialogIntent.FIND_PRIMARY_CODE:
        return None, PrimaryStatus.NOT_REQUESTED
    if outside:
        # выбирать главную деятельность рано: у одной из них кода ещё нет,
        # и человек может выбрать именно её
        return None, PrimaryStatus.ACTIVITY_RESOLUTION_REQUIRED
    chosen = fs.value(feat.PRIMARY_REVENUE)
    if chosen:
        unit = next((u for u in in_pack if _kind_matches(u.kind, chosen)), None)
        if unit and unit.codes:
            return unit.codes[0], PrimaryStatus.USER_SELECTED
    if len(in_pack) >= 2:
        return None, PrimaryStatus.REVENUE_INFORMATION_REQUIRED
    if (len(in_pack) == 1 and in_pack[0].state == RESOLVED
            and len(in_pack[0].codes) == 1):
        # выбирать не из чего: деятельность одна и код у неё один. Это не
        # решение движка — просто у альтернатив нет конкурентов
        return in_pack[0].codes[0], PrimaryStatus.SINGLE_ACTIVITY
    # у единственной деятельности осталось несколько допустимых кодов —
    # сначала надо разобраться с самим кодом, а не назначать главный
    return None, PrimaryStatus.ACTIVITY_RESOLUTION_REQUIRED if in_pack         else PrimaryStatus.NOT_REQUESTED


KIND_BY_ACTIVITY = {
    feat.ACTIVITY_MANUFACTURE: ("manufacture_",),
    feat.ACTIVITY_INSTALL: ("install_", "assemble_"),
    feat.ACTIVITY_REPAIR: ("repair_",),
    feat.ACTIVITY_DESIGN: ("design_",),
    feat.ACTIVITY_RESELL: ("resell_",),
}


def _kind_matches(kind: str, activity_key: str) -> bool:
    return any(kind.startswith(p) for p in KIND_BY_ACTIVITY.get(activity_key, ()))


def _question_changes_anything(question: Question, fs: ResolvedFeatureSet, units) -> bool:
    """Проверка вопроса кандидатами: меняет ли ответ итог правил.

    Базовый выбор вопроса кодов не знает и знать не должен. Этот слой —
    только предохранитель против бесполезного вопроса.

    Судить можно лишь о вопросе, после которого диалог заканчивается. Если
    хотя бы один ответ ведёт к следующему вопросу, одного шага вперёд мало:
    «столяр на заказ» после любого ответа про режим ещё спросит про объект,
    и коды разойдутся только на следующем шаге. Отбросить такой вопрос
    значило бы оборвать разговор в самом начале.
    """
    outcomes = set()
    for option in question.options:
        probe = resolve_features(_as_features(fs), list(option.effects))
        probe_units = build_units(probe)
        probe = _with_derived(probe, probe_units)
        if select_next_question(features=probe).question is not None:
            return True                      # разговор продолжается — вопрос нужен
        codes = tuple(sorted(
            c for u in standalone(probe_units) if not u.outside_current_pack
            for c in eligible_codes(apply_rules(u, probe))))
        outside = tuple(sorted(u.kind for u in probe_units if u.outside_current_pack))
        outcomes.add((codes, outside))
    return len(outcomes) > 1
