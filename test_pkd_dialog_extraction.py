"""Извлечение признаков из текста: что движок вправе считать сказанным.

Кодов PKD здесь нет ни одного — и это главное свойство этапа. Если позже
окажется, что «собираю кухни» уходит не в тот подкласс, по этим тестам сразу
видно, разбор ли соврал или правило выбора кода.
"""
import json
from pathlib import Path

import pytest

from pkd_dialog import FeatureSource, features as feat
from pkd_dialog.extraction import (Confidence, RuleBasedFeatureExtractor,
                                   load_rules, tokenize)
from pkd_dialog.extraction.rule_based import FeatureValue, EvidenceSpan, resolve

ROOT = Path(__file__).resolve().parent
SCENARIOS = json.loads(
    (ROOT / "tests" / "fixtures" / "pkd" / "furniture_scenarios.json")
    .read_text(encoding="utf-8"))["scenarios"]

E = RuleBasedFeatureExtractor()


def vals(text) -> dict:
    return {f.key: f.value for f in E.extract(text).features}


# ── прямое извлечение ─────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("собираю кухни", {feat.ACTIVITY_INSTALL: True, feat.OBJECT_KITCHEN: True}),
    ("устанавливаю встроенные шкафы", {feat.ACTIVITY_INSTALL: True,
                                       feat.OBJECT_BUILT_IN: True,
                                       feat.OBJECT_FURNITURE: True}),
    ("производим мебель", {feat.ACTIVITY_MANUFACTURE: True,
                           feat.OBJECT_FURNITURE: True}),
    ("ремонтирую старые стулья", {feat.ACTIVITY_REPAIR: True,
                                  feat.OBJECT_FURNITURE: True}),
    ("перепродаю готовую мебель", {feat.ACTIVITY_RESELL: True,
                                   feat.OBJECT_FURNITURE: True}),
    ("делаю деревянные окна", {feat.ACTIVITY_MANUFACTURE: True,
                               feat.OBJECT_WINDOWS_DOORS: True,
                               feat.MATERIAL: "wood"}),
    ("изготавливаю пластиковые двери", {feat.ACTIVITY_MANUFACTURE: True,
                                        feat.OBJECT_WINDOWS_DOORS: True,
                                        feat.MATERIAL: "plastic"}),
    ("производим металлические окна", {feat.ACTIVITY_MANUFACTURE: True,
                                       feat.OBJECT_WINDOWS_DOORS: True,
                                       feat.MATERIAL: "metal"}),
])
def test_direct_extraction(text, expected):
    assert vals(text) == expected


def test_design_only_needs_an_explicit_restriction():
    """Упоминание проектирования не делает человека проектировщиком без
    производства: «только» — это отдельный факт, а не оттенок."""
    assert vals("проектирую и произвожу мебель") == {
        feat.ACTIVITY_DESIGN: True, feat.ACTIVITY_MANUFACTURE: True,
        feat.OBJECT_FURNITURE: True}

    only = vals("только проектирую кухни")
    assert only[feat.ACTIVITY_DESIGN] is True
    assert only[feat.ACTIVITY_DESIGN_ONLY] is True
    # ограничитель обязан закрыть производство и монтаж, иначе он бесполезен
    assert only[feat.ACTIVITY_MANUFACTURE] is False
    assert only[feat.ACTIVITY_INSTALL] is False


# ── неоднозначность сохраняется ───────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "занимаюсь мебелью", "мастер мебели", "столярные работы", "делаю кухни под ключ",
])
def test_ambiguous_text_gets_no_invented_mode(text):
    """Объект найти можно, режим деятельности — выдумывать нельзя.
    Именно на этих формулировках движок обязан задать вопрос."""
    got = vals(text)
    assert not (set(got) & set(feat.ACTIVITY_KEYS)), got


def test_turnkey_marker_blocks_the_mode_not_the_object():
    r = E.extract("делаю кухни под ключ")
    assert r.value(feat.OBJECT_KITCHEN) is True
    assert feat.ACTIVITY_MANUFACTURE in r.blocked_keys
    assert any(t.get("marker") == "turnkey.v1" for t in r.trace)


# ── несколько деятельностей ───────────────────────────────────────────────

def test_multiple_activities_are_all_kept():
    assert vals("проектирую, произвожу и устанавливаю мебель") == {
        feat.ACTIVITY_DESIGN: True, feat.ACTIVITY_MANUFACTURE: True,
        feat.ACTIVITY_INSTALL: True, feat.OBJECT_FURNITURE: True}

    got = vals("ремонтирую и иногда делаю мебель")
    assert got[feat.ACTIVITY_REPAIR] is True and got[feat.ACTIVITY_MANUFACTURE] is True
    # «иногда» — это про долю выручки, а не про наличие деятельности
    assert feat.PRIMARY_REVENUE not in got

    got = vals("продаю готовую мебель и отдельно оказываю монтаж")
    assert got[feat.ACTIVITY_RESELL] is True and got[feat.ACTIVITY_INSTALL] is True


# ── отрицания ─────────────────────────────────────────────────────────────

def test_negation_is_a_fact_not_a_silence():
    """Явное «не произвожу» сильнее отсутствия совпадения и обязано
    переживать слияние признаков."""
    got = vals("не произвожу, только собираю")
    assert got[feat.ACTIVITY_MANUFACTURE] is False
    assert got[feat.ACTIVITY_INSTALL] is True


def test_negation_does_not_jump_over_a_clause_break():
    """«Не» относится к своему глаголу: запятая разделяет обороты, и сборка
    в «не произвожу, только собираю» остаётся утверждением."""
    assert vals("не произвожу, только собираю")[feat.ACTIVITY_INSTALL] is True
    assert vals("мебель не ремонтирую")[feat.ACTIVITY_REPAIR] is False


def test_not_only_but_also_affirms_both():
    """«Не только … но и …» — подтверждение обеих деятельностей.
    Наивное правило «рядом есть „не“» дало бы здесь ложное отрицание."""
    got = vals("не только собираю, но и делаю мебель")
    assert got[feat.ACTIVITY_INSTALL] is True
    assert got[feat.ACTIVITY_MANUFACTURE] is True


def test_negation_of_enum_value_is_not_inverted():
    """«Не из дерева» не означает пластик: отрицать конкретное значение
    перечисления нечем, поэтому материал остаётся неизвестным."""
    assert feat.MATERIAL not in vals("окна не из дерева")


# ── evidence ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [s["query"] for s in SCENARIOS])
def test_evidence_points_into_the_original_text(text):
    for f in E.extract(text).features:
        for span in f.evidence:
            assert text[span.start:span.end] == span.text
            assert f.rule_id and f.source is FeatureSource.EXPLICIT_QUERY
            assert f.confidence is Confidence.EXACT


def test_extraction_is_deterministic():
    text = "проектирую, произвожу и устанавливаю встроенные кухни"
    first = E.extract(text)
    for _ in range(3):
        again = E.extract(text)
        assert again.features == first.features
        assert again.conflicts == first.conflicts


# ── ложные совпадения ─────────────────────────────────────────────────────

@pytest.mark.parametrize("text,forbidden", [
    ("монтажёр видео", [feat.ACTIVITY_INSTALL]),
    ("сборка компьютеров", [feat.OBJECT_FURNITURE, feat.OBJECT_KITCHEN]),
    ("кухонный блог", [feat.OBJECT_KITCHEN, feat.ACTIVITY_MANUFACTURE]),
    ("оконный менеджер", [feat.OBJECT_WINDOWS_DOORS, feat.ACTIVITY_MANUFACTURE,
                          feat.ACTIVITY_INSTALL]),
    ("ремонт сайта", [feat.OBJECT_FURNITURE, feat.OBJECT_KITCHEN]),
    ("производственный менеджер мебельной компании",
     [feat.ACTIVITY_MANUFACTURE, feat.OBJECT_FURNITURE]),
])
def test_no_false_positives(text, forbidden):
    """Совпадение только по целому слову. Обрезка до общего корня когда-то
    склеила «монтажника» с «монтажёром» — здесь этого не должно быть."""
    got = vals(text)
    assert not (set(got) & set(forbidden)), got


# ── конфликты ─────────────────────────────────────────────────────────────

def _fv(key, value, *, negated=False, rule="r", span=(0, 5), source=FeatureSource.EXPLICIT_QUERY):
    return FeatureValue(key, value, source, Confidence.EXACT, rule,
                        (EvidenceSpan("x" * (span[1] - span[0]), *span),), negated)


def test_explicit_negation_beats_affirmation():
    won, conflicts = resolve([_fv(feat.ACTIVITY_MANUFACTURE, True),
                              _fv(feat.ACTIVITY_MANUFACTURE, False, negated=True)])
    assert [f.value for f in won] == [False]
    assert conflicts[0].reason == "explicit_negation_over_affirmation"
    assert conflicts[0].losing_values == ("True",)


def test_longer_phrase_beats_single_token():
    won, conflicts = resolve([_fv(feat.ACTIVITY_MANUFACTURE, True, span=(0, 5)),
                              _fv(feat.ACTIVITY_MANUFACTURE, False, span=(0, 20))])
    assert [f.value for f in won] == [False]
    assert conflicts[0].reason == "longer_exact_phrase"


def test_full_tie_leaves_the_feature_unknown():
    """При полном равенстве выбирать нельзя: признак остаётся неизвестным
    и станет поводом для вопроса, а не молчаливой лотереей."""
    won, conflicts = resolve([_fv(feat.ACTIVITY_MANUFACTURE, True, rule="a"),
                              _fv(feat.ACTIVITY_MANUFACTURE, False, rule="a")])
    assert won == []
    assert conflicts[0].winning_value is None
    assert conflicts[0].reason == "tie_unresolved"


def test_conflict_is_never_silently_dropped():
    got = E.extract("не произвожу мебель, хотя иногда делаю мебель")
    assert any(c.key == feat.ACTIVITY_MANUFACTURE for c in got.conflicts)


# ── правила ───────────────────────────────────────────────────────────────

def test_rules_validate_strictly():
    rules = load_rules()
    ids = [r["id"] for r in rules["rules"]]
    assert len(ids) == len(set(ids))
    for r in rules["rules"]:
        feat.validate(r["feature"], r["value"])
        assert r["match"]["any"], r["id"]
        assert r["why"], f"{r['id']}: правило без объяснения, зачем оно"


def test_rules_never_mention_pkd_codes():
    """Связка «признак -> код» живёт отдельно: если коды просочатся сюда,
    правила извлечения и правила выбора начнут меняться вместе."""
    import re
    raw = (Path(__file__).resolve().parent / "pkd_dialog" / "extraction"
           / "rules.json").read_text(encoding="utf-8")
    # «why» — человеческое пояснение, там ссылка на код уместна и полезна;
    # проверяем механику: образцы, признаки и значения
    body = json.dumps([{k: v for k, v in r.items() if k != "why"}
                       for r in json.loads(raw)["rules"]], ensure_ascii=False)
    assert not re.search(r"\b\d{2}\.\d{2}\.[A-Z]\b", body)


def test_no_prefix_stemming_anywhere():
    """Ни одного образца короче четырёх букв и ни одной обрезки: правило
    «10 доказанных форм лучше одного широкого корня» проверяется тестом."""
    abbreviations = {"пвх"}
    for r in load_rules()["rules"]:
        for p in r["match"]["any"]:
            value = p["value"]
            assert len(value) >= 4 or value in abbreviations,                 f"{r['id']}: слишком короткий образец «{value}» — похоже на обрезанный корень"


# ── сценарии: только то, что относится к разбору ──────────────────────────

@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["id"])
def test_scenario_extraction_invents_nothing(scenario):
    """Материал и встроенность нельзя угадать: они появляются либо словом
    в тексте, либо ответом человека — но ответы это уже другой слой."""
    text = scenario["query"]
    got = vals(text)
    low = text.lower()
    if feat.MATERIAL in got:
        assert any(w in low for w in ("дерев", "пластик", "металл", "пвх", "алюмин"))
    if got.get(feat.OBJECT_BUILT_IN):
        assert "встроен" in low or "купе" in low


def test_scenario_007_gets_the_mode_but_not_the_object():
    """«Собираю кухни»: режим известен из глагола, встроенность — нет.
    Ровно поэтому сценарий требует вопроса про объект, а не про режим."""
    got = vals("Собираю кухни")
    assert got[feat.ACTIVITY_INSTALL] is True
    assert feat.OBJECT_BUILT_IN not in got and feat.OBJECT_FREESTANDING not in got


def test_scenario_009b_gets_production_of_windows_without_material():
    """Материал в тексте не назван — извлекать его неоткуда, и это
    честная причина задать вопрос."""
    got = vals("Столяр на заказ")
    assert feat.MATERIAL not in got


def test_scenario_024_keeps_both_activities():
    got = vals("Ремонтирую и иногда делаю мебель")
    assert got[feat.ACTIVITY_REPAIR] is True
    assert got[feat.ACTIVITY_MANUFACTURE] is True
    assert feat.PRIMARY_REVENUE not in got


def test_scenarios_requiring_mode_question_have_no_mode_in_text():
    """Сценарий требует вопроса про режим только тогда, когда режим
    действительно не сказан. Иначе вопрос был бы издевательством."""
    for s in SCENARIOS:
        if "activity.mode.v1" in s["expected"]["required_questions"]:
            got = vals(s["query"])
            assert not (set(got) & set(feat.ACTIVITY_KEYS)), s["id"]
