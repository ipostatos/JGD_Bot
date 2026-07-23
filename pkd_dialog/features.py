"""Словарь признаков деятельности: что движок вообще умеет знать о запросе.

Признак — это не тег вроде «монтаж», а типизированный факт, на который можно
сослаться в правиле и который можно проверить тестом. Строковый тег «монтаж»
одинаково подходит и встроенной кухне (43.32.Z), и отдельно стоящему шкафу
(95.24.Z), а `object.built_in_furniture=true` разводит их однозначно.

Здесь только словарь и его семантика. Извлечение признаков из текста — шаг
следующий; правила выбора кодов — ещё один. Порядок такой намеренно: сначала
язык, на котором движок разговаривает сам с собой, потом всё остальное.
"""
from __future__ import annotations

from dataclasses import dataclass

# ── что человек делает ────────────────────────────────────────────────────
# Пять режимов повторятся почти в каждой отрасли: производство, монтаж,
# ремонт, проектирование и перепродажа встречаются и в еде, и в бьюти,
# и в транспорте. Поэтому имена доменно нейтральные, без слова furniture.
ACTIVITY_MANUFACTURE = "activity.manufacture_new"
ACTIVITY_INSTALL = "activity.install_or_assemble"
ACTIVITY_REPAIR = "activity.repair_or_restore"
# Проектирование и «только проектирование» — разные факты. Упоминание
# «проектирую и произвожу» не делает человека проектировщиком без производства,
# поэтому design_only выставляется лишь при явном ограничителе: «только
# проектирую», «изготовление отдаю на фабрику», «сам не произвожу».
ACTIVITY_DESIGN = "activity.design"
ACTIVITY_DESIGN_ONLY = "activity.design_only"
ACTIVITY_RESELL = "activity.resell"

# ── с каким объектом ──────────────────────────────────────────────────────
# Объект отраслевой: у мебели он разводит коды, у транспорта будет свой.
# Родовой объект: «занимаюсь мебелью» сообщает предмет, но не вид мебели,
# и подменять его встроенностью нельзя — это и есть повод задать вопрос.
OBJECT_FURNITURE = "object.furniture"
OBJECT_KITCHEN = "object.kitchen_furniture"
OBJECT_FREESTANDING = "object.freestanding_furniture"
OBJECT_BUILT_IN = "object.built_in_furniture"
OBJECT_WINDOWS_DOORS = "object.windows_or_doors"
OBJECT_BUILDING_ELEMENTS = "object.stairs_railings_or_building_elements"
# «Ремонт кухни» по-русски одинаково значит и починку мебели, и ремонт
# комнаты. Второе — строительная отделка, то есть другой пакет правил,
# и признак нужен, чтобы честно сказать «это не сюда», а не угадать.
OBJECT_WHOLE_ROOM = "object.whole_room"
# «Столярные работы» и «столяр» называют домен, но не объект: под ними
# и мебель, и окна, и монтаж на объекте. Это повод спросить, а не выбрать.
OBJECT_WOODWORK = "object.woodwork_generic"

# ── материал ──────────────────────────────────────────────────────────────
# Нужен ровно там, где классификация делит по нему коды: производство окон
# и дверей (16.25.Z дерево, 22.23.Z пластик, 25.12.Z металл). На монтаж
# той же столярки (43.32.Z) материал не влияет, и спрашивать его там нельзя.
MATERIAL = "material.windows_doors"

# ── выручка ───────────────────────────────────────────────────────────────
# Единственный источник главного кода. Значение — идентификатор деятельности,
# которую человек назвал преобладающей, а не код: код выводится из неё.
PRIMARY_REVENUE = "revenue.primary_activity"

# Ставит только слой разбора деятельностей, которого ещё нет. Считать
# деятельности самостоятельными по числу глаголов нельзя: «проектирую
# и произвожу мебель» — это может быть один процесс, а не два направления.
MULTIPLE_ACTIVITIES = "dialog.multiple_independent_activities"


@dataclass(frozen=True)
class FeatureKey:
    key: str
    kind: str            # bool | enum
    what: str            # зачем нужен: читается в отчётах и в объяснении
    values: tuple[str, ...] = ()


REGISTRY: dict[str, FeatureKey] = {f.key: f for f in (
    FeatureKey(ACTIVITY_MANUFACTURE, "bool", "производит новое изделие"),
    FeatureKey(ACTIVITY_INSTALL, "bool", "монтирует или собирает у клиента"),
    FeatureKey(ACTIVITY_REPAIR, "bool", "ремонтирует или реставрирует"),
    FeatureKey(ACTIVITY_DESIGN, "bool", "проектирует"),
    FeatureKey(ACTIVITY_DESIGN_ONLY, "bool",
               "проектирует и при этом не производит и не монтирует"),
    FeatureKey(ACTIVITY_RESELL, "bool", "перепродаёт готовое"),
    FeatureKey(OBJECT_FURNITURE, "bool", "мебель, вид не уточнён"),
    FeatureKey(OBJECT_KITCHEN, "bool", "кухня: встроенная или отдельно стоящая — неизвестно"),
    FeatureKey(OBJECT_FREESTANDING, "bool", "отдельно стоящая мебель"),
    FeatureKey(OBJECT_BUILT_IN, "bool", "встроенная мебель: кухни, шкафы"),
    FeatureKey(OBJECT_WINDOWS_DOORS, "bool", "окна и двери"),
    FeatureKey(OBJECT_BUILDING_ELEMENTS, "bool",
               "лестницы, ограждения и прочие строительные элементы"),
    FeatureKey(OBJECT_WHOLE_ROOM, "bool",
               "помещение целиком: отделка комнаты, а не мебель"),
    FeatureKey(OBJECT_WOODWORK, "bool",
               "столярка вообще: мебель, строительные изделия или монтаж — неизвестно"),
    FeatureKey(MATERIAL, "enum", "материал изделия; коды разводит только у окон и дверей",
               ("wood", "plastic", "metal")),
    FeatureKey(MULTIPLE_ACTIVITIES, "bool",
               "подтверждено, что деятельностей несколько и они самостоятельные"),
    FeatureKey(PRIMARY_REVENUE, "enum", "деятельность с наибольшей выручкой",
               (ACTIVITY_MANUFACTURE, ACTIVITY_INSTALL, ACTIVITY_REPAIR,
                ACTIVITY_DESIGN, ACTIVITY_RESELL)),
)}

ACTIVITY_KEYS = (ACTIVITY_MANUFACTURE, ACTIVITY_INSTALL, ACTIVITY_REPAIR,
                 ACTIVITY_DESIGN, ACTIVITY_RESELL)
# объекты, которые реально разводят коды; родовые (мебель, кухня) в набор
# не входят намеренно: они не решают развилку, а только называют предмет
OBJECT_KEYS = (OBJECT_FREESTANDING, OBJECT_BUILT_IN,
               OBJECT_WINDOWS_DOORS, OBJECT_BUILDING_ELEMENTS)
# объект назван, но развилку не решает: с ними и надо идти к вопросу
GENERIC_OBJECT_KEYS = (OBJECT_FURNITURE, OBJECT_KITCHEN, OBJECT_WOODWORK)


def validate(key: str, value) -> None:
    """Признака нет в словаре или значение не того типа — это ошибка сборки.

    Опечатка в ключе иначе просто никогда не совпадёт с правилом, и вопрос
    будет задаваться вечно: молчаливый отказ вместо громкой поломки.
    """
    spec = REGISTRY.get(key)
    if spec is None:
        raise ValueError(f"неизвестный признак: {key}")
    if spec.kind == "bool" and not isinstance(value, bool):
        raise ValueError(f"{key}: ожидается bool, пришло {value!r}")
    if spec.kind == "enum" and value not in spec.values:
        raise ValueError(f"{key}: {value!r} не из {spec.values}")
