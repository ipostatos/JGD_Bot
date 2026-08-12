"""Сквозной прогон движка: текст и ответы -> статус, деятельности, коды.

Здесь впервые проверяются коды. Если тест падает, слои уже разделены так,
что видно, где именно: разбор, деятельности, выбор вопроса или правила.
"""
import json
import re
from pathlib import Path

import pytest

import pkd
from pkd_dialog import Answer, DialogIntent, PrimaryStatus, Status
from pkd_dialog.code_rules import (CandidateState, ReasonType, apply_rules,
                                   rules)
from pkd_dialog.contracts import ContractError
from pkd_dialog.models import Feature, FeatureSource
from pkd_dialog.resolved import resolve_features, FeatureState
from pkd_dialog.resolution import (OUTSIDE, RESOLVED, _with_derived,
                                   resolve_furniture_dialog)
from pkd_dialog.activity_units import build_units
from pkd_dialog import features as feat

ROOT = Path(__file__).resolve().parent
SCENARIOS = json.loads(
    (ROOT / "tests" / "fixtures" / "pkd" / "furniture_scenarios.json")
    .read_text(encoding="utf-8"))["scenarios"]


def run(query, answers=(), intent=DialogIntent.FIND_CODES):
    return resolve_furniture_dialog(
        query=query, answers=tuple(answers), intent=intent)


# ── одна деятельность ─────────────────────────────────────────────────────

@pytest.mark.parametrize("query,codes", [
    ("делаю мебель на заказ", ["31.00.Z"]),
    ("реставрирую старые шкафы и перетягиваю диваны", ["95.24.Z"]),
    ("устанавливаю встроенные кухни", ["43.32.Z"]),
    ("собираю отдельно стоящие шкафы", ["95.24.Z"]),
    ("произвожу деревянные окна", ["16.25.Z"]),
    ("изготавливаю пластиковые двери", ["22.23.Z"]),
    ("производим металлические окна", ["25.12.Z"]),
    ("перепродаю мебель через свой магазин", ["47.55.Z"]),
    ("закупаю мебель оптом и продаю магазинам", ["46.47.Z"]),
])
def test_single_activity_resolves(query, codes):
    r = run(query)
    assert r.status is Status.RESOLVED_CANDIDATES
    assert list(r.codes) == codes


def test_installation_of_windows_needs_no_material():
    """Монтаж столярки идёт в один код при любом материале — ни вопроса,
    ни зависимости от материала быть не должно."""
    a = (Answer("activity.mode", 1, ("install_or_assemble",)),
         Answer("furniture.object_context", 2, ("windows_or_doors",)))
    r = run("столярные работы", a)
    assert r.status is Status.RESOLVED_CANDIDATES and list(r.codes) == ["43.32.Z"]


# ── развилки ──────────────────────────────────────────────────────────────

def test_kitchen_assembly_stops_until_answered():
    r = run("собираю кухни")
    assert r.status is Status.NEEDS_CLARIFICATION
    assert r.next_question is not None and r.codes == ()


def test_same_query_different_answers_give_different_codes():
    built_in = run("собираю кухни",
                   (Answer("furniture.object_context", 2, ("built_in_furniture",)),))
    freestanding = run("собираю кухни",
                       (Answer("furniture.object_context", 2, ("freestanding_furniture",)),))
    assert list(built_in.codes) == ["43.32.Z"]
    assert list(freestanding.codes) == ["95.24.Z"]


def test_whole_room_answer_leaves_the_pack():
    r = run("ремонтирую кухни",
            (Answer("furniture.object_context", 2, ("whole_room",)),))
    assert r.status is Status.OUTSIDE_CURRENT_PACK
    assert r.codes == ()
    assert r.units[0].suggested_pack == "construction"


def test_windows_manufacture_without_material_asks():
    r = run("производим окна")
    assert r.status is Status.NEEDS_CLARIFICATION
    assert r.next_question.id == "joinery.windows_doors_material"


# ── пакеты ────────────────────────────────────────────────────────────────

def test_repair_and_manufacture_is_a_package():
    r = run("ремонтирую и иногда делаю мебель")
    assert r.status is Status.RESOLVED_PACKAGE
    assert set(r.codes) == {"31.00.Z", "95.24.Z"}


def test_manufacture_and_built_in_installation_is_a_package():
    r = run("делаю мебель и устанавливаю встроенные кухни")
    assert r.status is Status.RESOLVED_PACKAGE
    assert set(r.codes) == {"31.00.Z", "43.32.Z"}


def test_integrated_design_adds_no_code():
    """Чертёж у того, кто сам изготавливает мебель, — этап работы.
    Отдельный код проектирования здесь был бы лишней записью в CEIDG."""
    r = run("проектирую и произвожу мебель")
    assert r.status is Status.RESOLVED_CANDIDATES
    assert list(r.codes) == ["31.00.Z"] and "74.11.Z" not in r.codes


def test_standalone_design_gets_its_code():
    r = run("только проектирую мебель, изготовление отдаю на фабрику")
    assert list(r.codes) == ["74.11.Z"]


def test_mixed_pack_keeps_the_useful_half():
    """Деятельность из чужого пакета не отменяет полезный результат."""
    r = run("Ремонтирую шкафы и делаю ремонт кухни",
            (Answer("furniture.object_context", 2, ("whole_room",)),),
            DialogIntent.FIND_PRIMARY_CODE)
    assert r.status is Status.RESOLVED_PACKAGE
    assert list(r.codes) == ["95.24.Z"]
    states = {u.kind: u.state for u in r.units}
    assert states["repair_or_restore_furniture"] == RESOLVED
    assert states["whole_room_renovation"] == OUTSIDE
    # выбирать главный код рано: у второй деятельности его ещё нет
    assert r.primary_code_status is PrimaryStatus.ACTIVITY_RESOLUTION_REQUIRED
    assert r.primary_code is None


# ── главный код ───────────────────────────────────────────────────────────

def test_primary_is_not_requested_by_default():
    r = run("ремонтирую и иногда делаю мебель")
    assert r.primary_code_status is PrimaryStatus.NOT_REQUESTED
    assert r.primary_code is None


def test_primary_requires_revenue_answer():
    r = run("ремонтирую и иногда делаю мебель", intent=DialogIntent.FIND_PRIMARY_CODE)
    assert r.primary_code_status is PrimaryStatus.REVENUE_INFORMATION_REQUIRED
    assert r.primary_code is None


def test_primary_comes_from_the_person_not_the_engine():
    a = (Answer("activity.primary_revenue", 1, ("manufacture_new",)),)
    r = run("ремонтирую и иногда делаю мебель", a, DialogIntent.FIND_PRIMARY_CODE)
    assert r.primary_code_status is PrimaryStatus.USER_SELECTED
    assert r.primary_code == "31.00.Z"


def test_answer_order_does_not_change_the_result():
    a = (Answer("activity.mode", 1, ("manufacture_new",)),
         Answer("furniture.object_context", 2, ("windows_or_doors",)),
         Answer("joinery.windows_doors_material", 1, ("wood",)))
    assert run("столяр на заказ", a).codes == run("столяр на заказ",
                                                  tuple(reversed(a))).codes


# ── негативные домены ─────────────────────────────────────────────────────

@pytest.mark.parametrize("query", [
    "монтажёр видео", "ремонт сайта", "кухонный блог",
    "производственный менеджер мебельной компании",
])
def test_foreign_domains_are_unrecognized_not_routed(query):
    """«Ремонт сайта» — это не «понятая деятельность вне мебельного пакета»,
    а «мы не поняли, чем человек занимается». Разные статусы, потому что
    маршрут разный: одно передаётся строительному пакету, другое уходит
    в общий поиск или к переформулировке."""
    r = run(query)
    assert r.codes == () and r.next_question is None
    assert r.status is Status.UNRECOGNIZED_ACTIVITY
    assert r.reason == "no_activity_units" and r.routing_hint == "general_search"


def test_understood_but_foreign_activity_keeps_its_route():
    r = run("ремонтирую кухни",
            (Answer("furniture.object_context", 2, ("whole_room",)),))
    assert r.status is Status.OUTSIDE_CURRENT_PACK
    assert r.routing_hint == "construction"


def test_single_activity_answers_the_primary_question():
    """Человек просил главный код, деятельность одна и код у неё один —
    выбирать не из чего, но запрос выполнен. Это не выбор движка."""
    r = run("делаю мебель на заказ", intent=DialogIntent.FIND_PRIMARY_CODE)
    assert r.primary_code == "31.00.Z"
    assert r.primary_code_status is PrimaryStatus.SINGLE_ACTIVITY
    # без запроса главного кода статус остаётся прежним
    assert run("делаю мебель на заказ").primary_code_status is PrimaryStatus.NOT_REQUESTED


# ── исключения кандидатов ─────────────────────────────────────────────────

def test_exclusions_have_a_provable_reason():
    """Кандидат удаляется только по официальному исключению или по факту,
    подтверждённому человеком. «Другой код показался вероятнее» — не причина."""
    r = run("собираю кухни",
            (Answer("furniture.object_context", 2, ("built_in_furniture",)),))
    excluded = [d for u in r.units for d in u.candidate_decisions
                if d.state is CandidateState.EXCLUDED]
    assert excluded
    for d in excluded:
        assert d.reason_type in (ReasonType.OFFICIAL_EXCLUSION, ReasonType.CONTRADICTED)
        assert d.rule_id and d.explanation and d.official_evidence


def test_official_exclusion_is_backed_by_the_corpus():
    r = run("делаю мебель на заказ")
    joinery = [d for u in r.units for d in u.candidate_decisions
               if d.code == "16.23.Z"]
    assert joinery and joinery[0].state is CandidateState.EXCLUDED
    assert joinery[0].reason_type is ReasonType.OFFICIAL_EXCLUSION


# ── правила ───────────────────────────────────────────────────────────────

def test_every_quote_exists_in_the_corpus():
    """Цитата проверяется при загрузке: если GUS перепишет текст, сборка
    упадёт, а не разойдётся с официальным справочником молча."""
    idx = pkd.index()
    for r in rules()["rules"]:
        assert r["candidate_code"] in idx.codes
        for b in r["official_basis"]:
            texts = (idx.codes[b["code"]]["includes"] if b["field"] == "includes"
                     else pkd.exclusion_texts(idx.codes[b["code"]]))
            norm = lambda s: re.sub(r"\s+", " ", s).strip().lower()
            assert any(norm(b["quote"]) in norm(t) for t in texts), r["id"]


def test_invented_quote_does_not_load(monkeypatch):
    import pkd_dialog.code_rules as cr
    cr.rules.cache_clear()
    bad = {"rules_version": "test", "rules": [{
        "id": "bad.v1", "activity_kind": "manufacture_furniture",
        "candidate_code": "31.00.Z", "effect": "eligible",
        "when": {"all": [{"feature": "activity.manufacture_new", "equals": True}]},
        "explanation": "…",
        "official_basis": [{"code": "31.00.Z", "field": "includes",
                            "quote": "тут написано то, чего в справочнике нет"}]}]}
    monkeypatch.setattr(cr.json, "loads", lambda *a, **k: bad)
    with pytest.raises(ContractError):
        cr.rules()
    cr.rules.cache_clear()


def test_unknown_code_does_not_load(monkeypatch):
    import pkd_dialog.code_rules as cr
    cr.rules.cache_clear()
    bad = {"rules_version": "test", "rules": [{
        "id": "bad.v2", "activity_kind": "manufacture_furniture",
        "candidate_code": "99.99.Z", "effect": "eligible",
        "when": {"all": []}, "explanation": "…", "official_basis": []}]}
    monkeypatch.setattr(cr.json, "loads", lambda *a, **k: bad)
    with pytest.raises(ContractError):
        cr.rules()
    cr.rules.cache_clear()


def test_rule_order_does_not_matter():
    """Исключение сильнее разрешения независимо от порядка в файле."""
    import pkd_dialog.code_rules as cr
    from pkd_dialog.activity_units import build_units
    from pkd_dialog.resolved import resolve_features
    from pkd_dialog.models import Feature, FeatureSource
    from pkd_dialog import features as feat

    fs = resolve_features([
        Feature(feat.ACTIVITY_INSTALL, True, FeatureSource.USER_ANSWER),
        Feature(feat.OBJECT_BUILT_IN, True, FeatureSource.USER_ANSWER)], [])
    unit = build_units(fs)[0]
    straight = apply_rules(unit, fs)

    original = cr.rules()
    cr.rules.cache_clear()
    flipped = dict(original, rules=list(reversed(original["rules"])))
    cr.rules.__wrapped__ = lambda: flipped
    try:
        assert apply_rules(unit, fs) == straight
    finally:
        cr.rules.cache_clear()


# ── прогон золотых сценариев ──────────────────────────────────────────────

@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["id"])
def test_gold_scenario(scenario):
    intent = DialogIntent(scenario.get("intent", "find_codes"))
    answers = tuple(Answer.from_dict(a) for a in scenario["answers"])
    r = run(scenario["query"], answers, intent)
    exp = scenario["expected"]

    assert r.status.value == exp["status"], f"{scenario['id']}: статус"
    if exp["status"] == "needs_clarification":
        assert r.next_question is not None, f"{scenario['id']}: статус без вопроса"
        assert f"{r.next_question.id}.v{r.next_question.version}" \
            == exp["required_questions"][0]
    else:
        assert r.next_question is None, f"{scenario['id']}: лишний вопрос"

    assert set(r.codes) == set(exp["acceptable_codes"]), f"{scenario['id']}: коды"
    assert not (set(r.codes) & set(exp["forbidden_codes"])), \
        f"{scenario['id']}: запрещённый код в выдаче"
    assert r.primary_code == exp["primary_code"], f"{scenario['id']}: главный код"
    assert r.primary_code_status.value == exp["primary_code_status"], \
        f"{scenario['id']}: статус главного кода"


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["id"])
def test_gold_scenario_is_reproducible(scenario):
    intent = DialogIntent(scenario.get("intent", "find_codes"))
    answers = tuple(Answer.from_dict(a) for a in scenario["answers"])
    first = run(scenario["query"], answers, intent)
    again = run(scenario["query"], answers, intent)
    assert first.codes == again.codes and first.status is again.status
    assert first.input_fingerprint == again.input_fingerprint


def test_rule_coverage_report():
    """Правило, которое не срабатывает ни разу, — мёртвый груз: либо оно
    лишнее, либо нет случая, ради которого его писали.

    Считаем и золотые сценарии, и прямые запросы этого файла: производство
    металлических окон в золотой набор не входит (24 ситуации подбирались
    по типам развилок), но правило для него следует прямо из официального
    текста 25.12.Z и обязано быть проверено.
    """
    probes = [(s["query"], tuple(Answer.from_dict(a) for a in s["answers"]),
               DialogIntent(s.get("intent", "find_codes"))) for s in SCENARIOS]
    probes += [(q, (), DialogIntent.FIND_CODES) for q in (
        "производим металлические окна", "изготавливаю пластиковые двери",
        "закупаю мебель оптом и продаю магазинам")]

    fired: set[str] = set()
    for query, answers, intent in probes:
        r = run(query, answers, intent)
        fired |= {d.rule_id for u in r.units for d in u.candidate_decisions}
    declared = {r["id"] for r in rules()["rules"]}
    assert not declared - fired, f"правила без единого срабатывания: {declared - fired}"


# ── регрессии по аудиту (PR3) ─────────────────────────────────────────────
def test_conflicted_feature_survives_derivation():
    """CONFLICTED-признак (value=None) не должен схлопываться в UNKNOWN при
    выводе диалоговых признаков: иначе вопрос-развязка конфликта не задаётся."""
    fs = resolve_features([
        Feature(feat.ACTIVITY_MANUFACTURE, True, FeatureSource.EXPLICIT_QUERY),
        Feature(feat.ACTIVITY_MANUFACTURE, False, FeatureSource.EXPLICIT_QUERY),
    ], [])
    assert fs.conflicted() == (feat.ACTIVITY_MANUFACTURE,)
    after = _with_derived(fs, build_units(fs))
    assert after.state(feat.ACTIVITY_MANUFACTURE) is FeatureState.CONFLICTED


def test_not_outsourcing_is_not_manufacturing():
    """«не отдаю на фабрику» ≠ «произвожу сам»: отрицание value=false-правила
    (импликации аутсорса) не должно давать ACTIVITY_MANUFACTURE=True."""
    from pkd_dialog.extraction import RuleBasedFeatureExtractor
    ex = RuleBasedFeatureExtractor().extract("не отдаю на фабрику, только продаю")
    manuf = [f for f in ex.features if f.key == feat.ACTIVITY_MANUFACTURE]
    assert not any(f.value is True for f in manuf), \
        "отрицание аутсорса ошибочно записало в производители"
