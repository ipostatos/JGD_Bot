"""Выбор следующего вопроса: минимальная незакрытая развилка.

Кодов PKD здесь по-прежнему нет. Проверяем ровно одно: движок спрашивает
то, чего действительно не знает, и молчит, когда знает.
"""
import json
from pathlib import Path

import pytest

from pkd_dialog import Answer, contracts, features as feat
from pkd_dialog.extraction import RuleBasedFeatureExtractor
from pkd_dialog.models import Feature, FeatureSource
from pkd_dialog.resolved import FeatureState, resolve_features
from pkd_dialog.selection import (ASKED, NO_POLICY_APPLIES, QuestionPriority,
                                  policies, select_next_question)

ROOT = Path(__file__).resolve().parent
SCENARIOS = json.loads(
    (ROOT / "tests" / "fixtures" / "pkd" / "furniture_scenarios.json")
    .read_text(encoding="utf-8"))["scenarios"]

E = RuleBasedFeatureExtractor()


def run(text, answers=()):
    """Полный путь этапа: текст -> признаки -> ответы -> следующий вопрос."""
    ex = E.extract(text)
    extracted = [Feature(f.key, f.value, f.source,
                         f.evidence[0].text if f.evidence else None)
                 for f in ex.features]
    ans = list(answers)
    resolved = resolve_features(extracted, contracts.features_from_answers(ans))
    return select_next_question(features=resolved, answers=tuple(ans))


def asked(text, answers=()):
    r = run(text, answers)
    return f"{r.question.id}.v{r.question.version}" if r.question else None


# ── правильный вопрос ─────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("собираю кухни", "furniture.object_context.v2"),
    ("занимаюсь мебелью", "activity.mode.v1"),
    ("производим окна", "joinery.windows_doors_material.v1"),
    ("ремонтирую кухни", "furniture.object_context.v2"),
    ("столярные работы", "activity.mode.v1"),
    ("делаю изделия из дерева", "furniture.object_context.v2"),
])
def test_asks_the_right_question(text, expected):
    assert asked(text) == expected


# ── вопрос не повторяется ─────────────────────────────────────────────────

def test_answered_question_is_not_asked_again():
    a = (Answer("furniture.object_context", 2, ("built_in_furniture",)),)
    assert asked("собираю кухни") == "furniture.object_context.v2"
    assert asked("собираю кухни", a) is None


def test_fact_from_the_query_counts_as_an_answer():
    """Человек уже сказал текстом — переспрашивать нельзя, даже если
    формального ответа в массиве нет."""
    assert asked("собираю отдельно стоящие шкафы") is None
    assert asked("собираю встроенные кухни") is None
    # а вот без уточнения объекта вопрос нужен
    assert asked("собираю шкафы") == "furniture.object_context.v2"


def test_material_is_not_asked_when_named_in_the_query():
    assert asked("произвожу деревянные окна") is None
    assert asked("производим окна") == "joinery.windows_doors_material.v1"


def test_answer_order_does_not_matter():
    a1 = (Answer("activity.mode", 1, ("manufacture_new",)),
          Answer("furniture.object_context", 2, ("windows_or_doors",)))
    assert asked("столяр на заказ", a1) == asked("столяр на заказ", tuple(reversed(a1)))


# ── нерелевантные вопросы ─────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "монтажёр видео", "ремонт сайта", "кухонный блог", "сборка компьютеров",
])
def test_foreign_domain_gets_no_furniture_question(text):
    """Ни одного мебельного вопроса там, где мебели нет: иначе движок
    начнёт допрашивать человека о встроенных кухнях из-за слова «ремонт»."""
    r = run(text)
    assert r.question is None and r.reason == NO_POLICY_APPLIES


def test_material_is_not_asked_for_installation():
    """Монтаж окон идёт в один код независимо от материала — вопрос был бы
    вопросом ради вопроса."""
    assert asked("устанавливаю пластиковые окна") is None
    assert asked("устанавливаю окна") is None


def test_repair_of_plain_furniture_needs_no_object_question():
    """Починка шкафа — это починка мебели при любом объекте. Развилка есть
    только у кухни, где по-русски то же слово значит и отделку комнаты."""
    assert asked("реставрирую старые шкафы и перетягиваю диваны") is None


def test_manufacturing_named_furniture_needs_no_object_question():
    """Производство мебели идёт в один код и для встроенной, и для отдельно
    стоящей — уточнять нечего."""
    assert asked("делаю мебель на заказ") is None


# ── минимальность ─────────────────────────────────────────────────────────

def test_known_mode_is_not_re_asked():
    """«Собираю» уже сообщило режим: спрашивать про него — издевательство."""
    r = run("собираю кухни")
    assert r.question.id == "furniture.object_context"


def test_only_one_question_at_a_time():
    r = run("занимаюсь мебелью")
    assert r.question is not None
    assert isinstance(r.question.id, str)          # ровно один, не список


def test_revenue_question_is_not_asked_from_verb_count():
    """Два глагола не доказывают, что деятельности самостоятельные.
    Пока это не подтверждено разбором деятельностей, вопрос молчит."""
    assert asked("ремонтирую и иногда делаю мебель") is None
    assert asked("проектирую, произвожу и устанавливаю встроенные кухни") is None


def test_revenue_question_appears_only_with_confirmed_independence():
    fs = resolve_features(
        [Feature(feat.MULTIPLE_ACTIVITIES, True, FeatureSource.INFERRED)], [])
    r = select_next_question(features=fs)
    assert r.question is not None and r.question.id == "activity.primary_revenue"


# ── конфликты ─────────────────────────────────────────────────────────────

def test_user_answer_overrules_the_query_and_ends_the_question():
    """Запрос говорит «встроенные», ответ — «отдельно стоящая». Побеждает
    ответ, и тот же вопрос второй раз не задаётся."""
    a = (Answer("furniture.object_context", 2, ("freestanding_furniture",)),)
    r = run("собираю встроенные кухни", a)
    assert r.question is None


def test_losing_evidence_stays_visible():
    fs = resolve_features(
        [Feature(feat.OBJECT_BUILT_IN, True, FeatureSource.EXPLICIT_QUERY, "встроенные")],
        [Feature(feat.OBJECT_BUILT_IN, False, FeatureSource.USER_ANSWER)])
    assert fs.state(feat.OBJECT_BUILT_IN) is FeatureState.FALSE
    assert fs.features[feat.OBJECT_BUILT_IN].overruled


def test_conflict_is_a_separate_state_not_silence():
    """Противоречие внутри одного уровня доверия не разрешается само:
    признак становится conflicted, и это повод спросить."""
    fs = resolve_features([
        Feature(feat.ACTIVITY_MANUFACTURE, True, FeatureSource.EXPLICIT_QUERY, "делаю"),
        Feature(feat.ACTIVITY_MANUFACTURE, False, FeatureSource.EXPLICIT_QUERY, "не делаю"),
    ], [])
    assert fs.state(feat.ACTIVITY_MANUFACTURE) is FeatureState.CONFLICTED
    r = select_next_question(features=fs)
    assert r.question is not None and r.question.id == "activity.mode"
    assert r.policy_id == "policy.conflict.activity_mode.v1"


def test_conflict_resolution_goes_first():
    assert (QuestionPriority.CONFLICT_RESOLUTION < QuestionPriority.ACTIVITY_MODE
            < QuestionPriority.DOMAIN_DISCRIMINATOR
            < QuestionPriority.MATERIAL_DISCRIMINATOR
            < QuestionPriority.PRIMARY_REVENUE)


# ── детерминированность и трасса ──────────────────────────────────────────

def test_same_input_same_question():
    for _ in range(3):
        assert asked("собираю кухни") == "furniture.object_context.v2"


def test_feature_order_does_not_change_the_result():
    fs_a = resolve_features([
        Feature(feat.ACTIVITY_INSTALL, True, FeatureSource.EXPLICIT_QUERY),
        Feature(feat.OBJECT_KITCHEN, True, FeatureSource.EXPLICIT_QUERY)], [])
    fs_b = resolve_features([
        Feature(feat.OBJECT_KITCHEN, True, FeatureSource.EXPLICIT_QUERY),
        Feature(feat.ACTIVITY_INSTALL, True, FeatureSource.EXPLICIT_QUERY)], [])
    assert (select_next_question(features=fs_a).policy_id
            == select_next_question(features=fs_b).policy_id)


def test_every_decision_is_traced():
    r = run("собираю кухни")
    assert r.reason == ASKED and r.policy_id
    assert {t.policy_id for t in r.trace} == {p["id"] for p in policies()}
    chosen = next(t for t in r.trace if t.policy_id == r.policy_id)
    assert chosen.eligible and chosen.reasons and chosen.unknown_features
    # у отклонённых политик тоже должна быть причина, иначе отладка слепая
    assert all(t.reasons for t in r.trace if not t.eligible)


# ── политики ──────────────────────────────────────────────────────────────

def test_policies_validate_against_questions():
    for p in policies():
        q = contracts.question(p["question_id"])
        assert q.version == p["question_version"], p["id"]
        assert set(p["resolves"]) <= set(q.resolves), p["id"]
        assert p["why"], p["id"]


def test_policies_know_nothing_about_pkd_codes():
    """Связка «признак -> код» появится в следующем слое. Если коды
    просочатся сюда, выбор вопроса и выбор кода начнут меняться вместе."""
    import re
    raw = (ROOT / "pkd_dialog" / "question_policies.json").read_text(encoding="utf-8")
    body = json.dumps([{k: v for k, v in p.items() if k != "why"}
                       for p in json.loads(raw)["policies"]], ensure_ascii=False)
    assert not re.search(r"\b\d{2}\.\d{2}\.[A-Z]\b", body)


# ── прогон по сценариям ───────────────────────────────────────────────────

@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["id"])
def test_scenario_question_expectations(scenario):
    """Сценарий требует ровно того вопроса, который движок и обязан задать —
    или молчания, если спрашивать нечего."""
    answers = tuple(Answer.from_dict(a) for a in scenario["answers"])
    got = asked(scenario["query"], answers)
    expected = scenario["expected"]["required_questions"]
    assert got == (expected[0] if expected else None), scenario["id"]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["id"])
def test_selector_never_produces_codes_or_status(scenario):
    """Слой отвечает только за вопрос: коды и статусы — следующий этап."""
    r = run(scenario["query"], tuple(Answer.from_dict(a) for a in scenario["answers"]))
    assert not hasattr(r, "codes") and not hasattr(r, "status")
    assert r.reason in (ASKED, NO_POLICY_APPLIES)
