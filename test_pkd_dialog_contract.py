"""Контракт диалога: типы, вопросы, признаки, отпечаток входа.

Правил выбора кодов здесь ещё нет — и это намеренно. Сначала фиксируем язык,
на котором движок будет разговаривать сам с собой, и сверяем с ним золотые
сценарии. Иначе движок начнёт диктовать удобную ему форму данных, а сценарии
подгоняться под движок.
"""
import json
from pathlib import Path

import pytest

from pkd_dialog import (Answer, FeatureSet, PrimaryStatus, Status, fingerprint,
                        models)
from pkd_dialog import contracts, features

ROOT = Path(__file__).resolve().parent
SCENARIOS = json.loads(
    (ROOT / "tests" / "fixtures" / "pkd" / "furniture_scenarios.json")
    .read_text(encoding="utf-8"))["scenarios"]


# ── вопросы ───────────────────────────────────────────────────────────────

def test_questions_load_and_validate():
    qs = contracts.questions()
    assert set(qs) == {"activity.mode", "furniture.object_context",
                       "joinery.windows_doors_material", "activity.primary_revenue"}
    assert all(q.version >= 1 for q in qs.values())


def test_every_option_changes_something():
    """Вариант, ничего не меняющий, — это вопрос, заданный впустую."""
    for q in contracts.questions().values():
        for o in q.options:
            assert o.effects, f"{q.id}/{o.id}"
            for f in o.effects:
                features.validate(f.key, f.value)


def test_question_ids_say_what_they_are_about():
    """Имя вопроса переживает первый срез.

    Окна и двери — строительная столярка, а не мебель, поэтому вопрос про
    их материал не может называться furniture.*: имя стало бы неверным
    сразу после мебельного пакета.
    """
    qs = contracts.questions()
    assert "joinery." in "".join(k for k in qs if "material" in k)
    # режим деятельности и выручка повторятся во всех отраслях — они нейтральны
    assert qs["activity.mode"].id.startswith("activity.")
    assert qs["activity.primary_revenue"].id.startswith("activity.")


# ── ответы ────────────────────────────────────────────────────────────────

def test_answer_gives_user_features():
    fs = FeatureSet(contracts.features_from_answers(
        [Answer("furniture.object_context", 2, ("built_in_furniture",))]))
    assert fs.is_true(features.OBJECT_BUILT_IN)
    assert fs.get(features.OBJECT_BUILT_IN).source is models.FeatureSource.USER_ANSWER


def test_stale_answer_is_rejected_not_guessed():
    """Изменился набор вариантов — старый ответ применять нельзя."""
    with pytest.raises(contracts.StaleAnswerError):
        contracts.features_from_answers(
            [Answer("furniture.object_context", 99, ("built_in_furniture",))])


def test_unknown_answer_is_loud():
    with pytest.raises(contracts.UnknownAnswerError):
        contracts.features_from_answers([Answer("нет.такого", 1, ("x",))])
    with pytest.raises(contracts.UnknownAnswerError):
        contracts.features_from_answers([Answer("activity.mode", 1, ("нет-варианта",))])
    with pytest.raises(contracts.UnknownAnswerError):
        # single_select с двумя вариантами — ошибка клиента, а не «ну ладно»
        contracts.features_from_answers(
            [Answer("joinery.windows_doors_material", 1, ("wood", "metal"))])


# ── слияние признаков ─────────────────────────────────────────────────────

def test_user_answer_beats_extraction():
    """Человек сказал прямо — движок не спорит.

    Иначе диалог бессмыслен: зачем спрашивать, если ответ проигрывает
    догадке, сделанной по тексту.
    """
    fs = FeatureSet([
        models.Feature(features.OBJECT_BUILT_IN, True,
                       models.FeatureSource.INFERRED, "кухни"),
        models.Feature(features.OBJECT_BUILT_IN, False,
                       models.FeatureSource.USER_ANSWER),
    ])
    assert fs.value(features.OBJECT_BUILT_IN) is False

    # и в обратном порядке добавления результат тот же
    fs2 = FeatureSet([
        models.Feature(features.OBJECT_BUILT_IN, False,
                       models.FeatureSource.USER_ANSWER),
        models.Feature(features.OBJECT_BUILT_IN, True,
                       models.FeatureSource.INFERRED, "кухни"),
    ])
    assert fs2.value(features.OBJECT_BUILT_IN) is False


def test_trust_order_is_explicit():
    order = [models.FeatureSource.USER_ANSWER, models.FeatureSource.EXPLICIT_QUERY,
             models.FeatureSource.ALIAS, models.FeatureSource.INFERRED]
    trust = [models.TRUST[s] for s in order]
    assert trust == sorted(trust, reverse=True)


def test_unknown_feature_key_is_an_error():
    """Опечатка в ключе иначе даёт молчаливый отказ: правило никогда
    не совпадёт, и движок будет спрашивать одно и то же по кругу."""
    with pytest.raises(ValueError):
        features.validate("object.built_in_furnitur", True)
    with pytest.raises(ValueError):
        features.validate(features.MATERIAL, "стекло")
    with pytest.raises(ValueError):
        features.validate(features.ACTIVITY_INSTALL, "да")


# ── отпечаток входа ───────────────────────────────────────────────────────

VERSIONS = {"corpus": "pkd-2025", "rules": "furniture-v1", "dialog_schema": 1}


def test_fingerprint_ignores_answer_order_and_whitespace():
    a = [Answer("activity.mode", 1, ("manufacture_new",)),
         Answer("furniture.object_context", 1, ("built_in_furniture",))]
    assert fingerprint("Собираю кухни", a, VERSIONS) == \
        fingerprint("  собираю   кухни ", list(reversed(a)), VERSIONS)


def test_fingerprint_changes_with_rules_version():
    """Иначе старый отпечаток тихо подтвердит результат новой логики."""
    a = [Answer("activity.mode", 1, ("manufacture_new",))]
    assert fingerprint("столяр", a, VERSIONS) != \
        fingerprint("столяр", a, {**VERSIONS, "rules": "furniture-v2"})


# ── сценарии против контракта ─────────────────────────────────────────────

def test_scenarios_use_only_known_statuses():
    ok = {s.value for s in Status}
    ok_primary = {s.value for s in PrimaryStatus}
    for s in SCENARIOS:
        assert s["expected"]["status"] in ok, s["id"]
        assert s["expected"]["primary_code_status"] in ok_primary, s["id"]


def test_scenarios_never_expect_system_selected_primary():
    """Главный код не назначается по весам движка — только человеком."""
    assert all(s["expected"]["primary_code_status"] != "system_selected"
               for s in SCENARIOS)
    for s in SCENARIOS:
        if s["expected"]["primary_code"]:
            assert s["expected"]["primary_code_status"] == "user_selected", s["id"]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["id"])
def test_scenario_answers_are_valid_contract(scenario):
    """Ответы сценария обязаны разбираться контрактом и давать ровно те
    признаки, что объявлены в вариантах: сценарии и вопросы расходятся
    молча, если это не проверять."""
    answers = [Answer.from_dict(a) for a in scenario["answers"]]
    fs = FeatureSet(contracts.features_from_answers(answers))

    expected = {f.key: f.value for a in answers
                for oid in a.option_ids
                for f in contracts.question(a.question_id).option(oid).effects}
    assert {f.key: f.value for f in fs.as_list()} == expected, scenario["id"]
    assert all(f.source is models.FeatureSource.USER_ANSWER for f in fs.as_list())


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["id"])
def test_scenario_required_questions_exist(scenario):
    for versioned in scenario["expected"]["required_questions"]:
        qid, _, ver = versioned.rpartition(".v")
        q = contracts.question(qid)
        assert q is not None, f"{scenario['id']}: нет вопроса {qid}"
        assert q.version == int(ver), f"{scenario['id']}: версия {versioned} устарела"


def test_scenario_set_covers_the_required_shape():
    """Набор обязан оставаться тем, о чём договорились: 24 бизнес-ситуации
    в пяти группах, а не разросшийся список удобных случаев."""
    base = [s for s in SCENARIOS if not s["id"][-1].isalpha()]
    groups = {}
    for s in base:
        groups[s["group"]] = groups.get(s["group"], 0) + 1
    assert len(base) == 24
    assert groups == {"однозначные": 6, "требуют вопроса": 6,
                      "несколько деятельностей": 4, "негативные": 4,
                      "пограничные": 4}
    # хотя бы один сценарий обязан требовать вопроса и хотя бы один — молчать
    # про главный код: это два поведения, ради которых движок и затевается
    assert any(s["expected"]["status"] == Status.NEEDS_CLARIFICATION.value
               for s in SCENARIOS)
    assert any(s["expected"]["primary_code_status"]
               == PrimaryStatus.REVENUE_INFORMATION_REQUIRED.value for s in SCENARIOS)
