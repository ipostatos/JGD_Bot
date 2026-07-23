"""Разбор на самостоятельные деятельности.

Кодов PKD здесь по-прежнему нет ни одного. Проверяем ровно то, ради чего
слой существует: набор признаков не равен набору деятельностей, и решать,
сколько их, нельзя пересчётом глаголов.
"""
import json
from pathlib import Path

import pytest

from pkd_dialog import Answer, contracts, features as feat
from pkd_dialog.activity_units import (ActivityIndependence, build_units,
                                       derived_features, outside_pack, policies,
                                       standalone)
from pkd_dialog.extraction import RuleBasedFeatureExtractor
from pkd_dialog.models import Feature
from pkd_dialog.resolved import resolve_features

ROOT = Path(__file__).resolve().parent
SCENARIOS = json.loads(
    (ROOT / "tests" / "fixtures" / "pkd" / "furniture_scenarios.json")
    .read_text(encoding="utf-8"))["scenarios"]

E = RuleBasedFeatureExtractor()


def units_for(text, answers=()):
    ex = E.extract(text)
    extracted = [Feature(f.key, f.value, f.source,
                         f.evidence[0].text if f.evidence else None)
                 for f in ex.features]
    fs = resolve_features(extracted, contracts.features_from_answers(list(answers)))
    return build_units(fs)


def kinds(text, answers=()):
    return [u.kind for u in units_for(text, answers)]


# ── единичные деятельности ────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("делаю мебель на заказ", ["manufacture_furniture"]),
    ("реставрирую старые шкафы", ["repair_or_restore_furniture"]),
    ("устанавливаю встроенные кухни", ["install_built_in_furniture"]),
    ("собираю отдельно стоящие шкафы", ["assemble_freestanding_furniture"]),
    ("произвожу деревянные окна", ["manufacture_wood_windows_doors"]),
    ("изготавливаю пластиковые двери", ["manufacture_plastic_windows_doors"]),
    ("производим металлические окна", ["manufacture_metal_windows_doors"]),
])
def test_single_activity(text, expected):
    assert kinds(text) == expected


def test_built_in_installation_does_not_also_create_generic_assembly():
    """Одна деятельность, а не две: «собираю встроенную кухню» — это монтаж
    встроенной мебели, и дублировать её общей сборкой незачем."""
    assert kinds("собираю встроенную кухню") == ["install_built_in_furniture"]


# ── объединение и самостоятельность ───────────────────────────────────────

def test_design_with_manufacture_is_integrated():
    """Чертёж у мебельщика — этап изготовления. Отдельный код проектирования
    из этого не следует, иначе человек получит лишнюю запись в CEIDG."""
    us = {u.kind: u for u in units_for("проектирую и произвожу мебель")}
    assert us["manufacture_furniture"].independence is ActivityIndependence.STANDALONE
    assert us["design_furniture"].independence is ActivityIndependence.INTEGRATED
    assert len(standalone(us.values())) == 1


def test_design_only_is_standalone():
    us = {u.kind: u for u in units_for("только проектирую мебель")}
    assert us["design_furniture"].independence is ActivityIndependence.STANDALONE


def test_design_offered_separately_is_standalone():
    us = {u.kind: u for u in units_for("произвожу мебель и отдельно проектирую")}
    assert us["design_furniture"].independence is ActivityIndependence.STANDALONE


def test_repair_and_manufacture_are_two_standalone_units():
    """«Иногда» не удаляет деятельность и не выбирает главную."""
    us = units_for("ремонтирую и иногда делаю мебель")
    assert {u.kind for u in us} == {"repair_or_restore_furniture", "manufacture_furniture"}
    assert len(standalone(us)) == 2


def test_manufacture_and_built_in_installation_are_two_units():
    us = units_for("делаю мебель и устанавливаю встроенные кухни")
    assert {u.kind for u in us} == {"manufacture_furniture", "install_built_in_furniture"}
    assert len(standalone(us)) == 2


def test_resale_and_separate_assembly_are_two_units():
    us = units_for("продаю готовую мебель и отдельно собираю отдельно стоящую")
    assert {u.kind for u in us} == {"resell_furniture_retail",
                                    "assemble_freestanding_furniture"}
    assert len(standalone(us)) == 2


def test_wholesale_and_retail_are_different_activities():
    assert kinds("закупаю мебель оптом и продаю магазинам") == \
        ["resell_furniture_wholesale"]
    assert kinds("перепродаю мебель через свой магазин") == ["resell_furniture_retail"]


# ── вне пакета ────────────────────────────────────────────────────────────

def test_whole_room_is_recognised_as_another_pack():
    """Отделка комнаты — распознанная деятельность, просто отвечает за неё
    другой пакет правил. Это не «нет кода» и не повод спрашивать дальше."""
    a = (Answer("furniture.object_context", 2, ("whole_room",)),)
    us = units_for("ремонтирую кухни", a)
    room = [u for u in us if u.kind == "whole_room_renovation"]
    assert room and room[0].outside_current_pack and room[0].pack == "construction"
    assert outside_pack(us)


# ── производный признак для вопроса о выручке ─────────────────────────────

def test_derived_flag_needs_two_standalone_units():
    assert derived_features(units_for("ремонтирую и иногда делаю мебель"))[
        feat.MULTIPLE_ACTIVITIES] is True
    assert derived_features(units_for("проектирую и произвожу мебель"))[
        feat.MULTIPLE_ACTIVITIES] is False
    assert derived_features(units_for("делаю мебель на заказ"))[
        feat.MULTIPLE_ACTIVITIES] is False


def test_activity_outside_pack_does_not_count_as_second_activity():
    a = (Answer("furniture.object_context", 2, ("whole_room",)),)
    us = units_for("ремонтирую кухни", a)
    assert derived_features(us)[feat.MULTIPLE_ACTIVITIES] is False


# ── свойства слоя ─────────────────────────────────────────────────────────

def test_units_keep_evidence():
    us = units_for("собираю встроенные кухни")
    assert us[0].evidence and all(isinstance(e, str) for e in us[0].evidence)
    assert us[0].source_features


def test_feature_order_does_not_change_units():
    a = [Feature(feat.ACTIVITY_MANUFACTURE, True, contracts.FeatureSource.EXPLICIT_QUERY),
         Feature(feat.OBJECT_FURNITURE, True, contracts.FeatureSource.EXPLICIT_QUERY)]
    one = build_units(resolve_features(a, []))
    two = build_units(resolve_features(list(reversed(a)), []))
    assert [u.kind for u in one] == [u.kind for u in two]


def test_units_are_deterministic():
    first = kinds("делаю мебель и устанавливаю встроенные кухни")
    for _ in range(3):
        assert kinds("делаю мебель и устанавливаю встроенные кухни") == first


def test_no_units_for_foreign_domains():
    for text in ("монтажёр видео", "ремонт сайта", "кухонный блог",
                 "производственный менеджер мебельной компании"):
        assert kinds(text) == [], text


def test_unknown_object_gives_no_unit():
    """«Собираю кухни» — деятельность ещё не определена: встроенная кухня
    и отдельно стоящая ведут в разные коды, и до ответа unit не создаётся."""
    assert kinds("собираю кухни") == []


# ── политики ──────────────────────────────────────────────────────────────

def test_policies_validate():
    for p in policies():
        assert p["why"], p["id"]
        ActivityIndependence(p["independence"])


def test_every_activity_has_a_human_name():
    """Имя деятельности — часть контракта, а не украшение интерфейса.

    Без него страница покажет `kind` («repair_or_restore_furniture») или
    начнёт переводить идентификаторы сама — и правила разъедутся между
    сервером и браузером.
    """
    titles = [p["title"] for p in policies()]
    for p in policies():
        assert p["title"] and p["title"] != p["kind"], p["id"]
        assert not p["title"].isascii(), f"{p['id']}: имя не на языке пользователя"
    assert len(set(titles)) == len(titles), "две деятельности с одним именем"


def test_units_layer_knows_no_pkd_codes():
    import re
    raw = (ROOT / "pkd_dialog" / "activity_unit_policies.json").read_text(encoding="utf-8")
    body = json.dumps([{k: v for k, v in p.items() if k != "why"}
                       for p in json.loads(raw)["units"]], ensure_ascii=False)
    assert not re.search(r"\b\d{2}\.\d{2}\.[A-Z]\b", body)


# ── сценарии ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["id"])
def test_scenario_units_match_expected_shape(scenario):
    """Там, где сценарий ждёт пакет кодов, деятельностей обязано быть две;
    где ждёт одну — одна. Коды здесь ни при чём, проверяем структуру."""
    answers = tuple(Answer.from_dict(a) for a in scenario["answers"])
    us = units_for(scenario["query"], answers)
    n = len(standalone([u for u in us if not u.outside_current_pack]))
    outside = len(outside_pack(us))
    expected = scenario["expected"]["status"]
    if expected == "resolved_package":
        # пакет — это либо две деятельности внутри пакета, либо смесь:
        # одна решается мебельными правилами, вторая относится к чужому
        assert n >= 2 or (n == 1 and outside), \
            f"{scenario['id']}: ожидался пакет, а деятельностей {n} (+{outside} чужих)"
    if expected == "resolved_candidates":
        assert n == 1, f"{scenario['id']}: ожидалась одна деятельность, а их {n}"
