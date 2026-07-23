"""Подбор PKD: словарь профессий, ранжирование, переход 2007→2025, флаги VAT.

Логику гоняем на фикстуре (tests/fixtures/pkd) — полный справочник весит мегабайт,
генерируется из файлов GUS и в репозиторий не едет, так что на CI его нет.
Отдельный тест проверяет полный набор, если он собран локально.
"""
import json
from pathlib import Path

import pytest

import pkd

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "tests" / "fixtures" / "pkd"
# полный справочник лежит в git сжатым и гоняется в CI целиком: «облегчённого
# корпуса для CI» быть не должно — он разойдётся с тем, что работает у людей
FULL = ROOT / "data" / "pkd" / "pkd.json.gz"


@pytest.fixture(autouse=True)
def small_index(monkeypatch):
    """Индекс кэшируется на процесс — сбрасываем, чтобы подменить данные."""
    monkeypatch.setenv("JDG_PKD_DATA", str(FIXTURE))
    pkd.index.cache_clear()
    yield
    pkd.index.cache_clear()


def test_fixture_loads():
    idx = pkd.index()
    assert idx.codes["62.10.B"]["section"] == "K"
    assert idx.keys["62.01.Z"]["to"] == ["62.10.A", "62.10.B"]
    assert idx.syn and idx.rates["rates"], "словарь и ставки лежат в git, не в webapp/data"


def test_blogger_finds_official_wording():
    """«Блогер» есть в самом тексте GUS — подкласс должен всплыть первым."""
    top = pkd.lookup("я блогер")["results"][0]
    assert top["code"] == "90.11.Z"
    assert "bloger" in " ".join(top["includes"]).lower()


def test_specific_synonym_beats_general():
    """«Дизайн интерьера» не должен уступать общему «дизайнеру»."""
    assert pkd.lookup("я делаю дизайн интерьеров")["results"][0]["code"] == "74.13.Z"


def test_programming_query_without_dictionary_word():
    codes = [r["code"] for r in pkd.lookup("пишу код на питоне")["results"]]
    assert "62.10.B" in codes[:2]


def test_old_code_shows_migration():
    r = pkd.lookup("62.01.Z")
    assert r["kind"] == "migration"
    assert [x["code"] for x in r["migration"]["to"]] == ["62.10.A", "62.10.B"]
    assert "31.12.2026" in r["note"]


def test_search_hides_old_numbering():
    """В обычной выдаче «раньше это было 62.01.Z» — шум: спрашивают про сейчас."""
    assert all("was_pkd2007" not in r for r in pkd.lookup("пишу код")["results"])
    # но там, где старый код и есть предмет разговора, он остаётся
    assert "was_pkd2007" in pkd.index().analyse("62.10.B")
    assert pkd.audit(["62.01.Z"])["items"][0]["status"] == "outdated"


def test_vat_flag_only_when_activity_is_in_the_name():
    """Doradztwo в названии — предупреждение; упоминание в пояснениях — не более чем
    повод присмотреться, иначе графический дизайн ложно объявляется потерей льготы."""
    doradztwo = pkd.index().analyse("70.20.Z")
    assert any(f["level"] == "warn" for f in doradztwo["flags"])
    assert "113" in doradztwo["flags"][0]["text"]

    design = pkd.index().analyse("74.12.Z")
    assert all(f["level"] != "warn" for f in design["flags"])


def test_rate_hints_are_a_hint_not_a_verdict():
    it = pkd.index().analyse("62.10.B")["rate_hints"]
    assert it and it[0]["rate"] == 12
    assert pkd.index().analyse("69.10.Z")["rate_hints"][0]["rate"] == 17
    # услуги без отдельного пункта закона идут по 8,5%
    assert pkd.index().analyse("96.21.Z")["rate_hints"][0]["rate"] == 8.5
    assert "PKWiU" in pkd.lookup("маникюр на дому")["note"]


def test_audit_marks_outdated_codes():
    r = pkd.audit(["62.01.Z", "70.20.Z", "99.99.Z"])
    by_code = {i["code"]: i["status"] for i in r["items"]}
    assert by_code == {"62.01.Z": "outdated", "70.20.Z": "ok", "99.99.Z": "unknown"}
    assert r["outdated"] == 1 and "31.12.2026" in r["summary"]
    assert r["vat_warning_codes"] == ["70.20.Z"]


def test_empty_query_is_safe():
    assert pkd.lookup("")["results"] == []
    assert pkd.lookup("qwertyuiop")["results"] == []


def test_russian_layer_works_without_profession_entry():
    """Русские слова ищут по переводу самой классификации, а не по списку
    профессий: словарь покрывает частные случаи, глоссарий — все 728 кодов."""
    # «юридические услуги» на фикстуре из 16 кодов неоднозначны: слово «услуги»
    # тянет за собой usługową. Проверяем на словах, где перевод решает
    assert pkd.lookup("адвокат право")["results"][0]["code"] == "69.10.Z"
    assert pkd.lookup("парикмахерская стрижка")["results"][0]["code"] == "96.21.Z"


def test_short_latin_words_survive_tokenizer():
    """«hr», «qa», «ux», «3d» — так люди называют профессию, и словарь на них
    рассчитан. Отбрасываем только двухбуквенные русские: «на», «по» — шум."""
    assert pkd._tok("hr qa ux 3d") == ["hr", "qa", "ux", "3d"]
    assert pkd._tok("на по за") == []


def test_stemming_does_not_glue_different_words():
    """«продажа» и «продакт», «фотосъёмка» и «фотосток» — разные слова.
    При обрезке до пяти символов они слипались, и срабатывал не тот синоним."""
    t = pkd._tok("продажа продакт фотосъёмка фотосток мебель мебели")
    assert t[0] != t[1] and t[2] != t[3]
    assert t[4] == t[5], "«мебель» и «мебели» должны сойтись"


def test_long_stems_split_colliding_professions():
    """На шести символах «перевозка» и «переводчик» — одно слово, поэтому
    «перевозка мебели» находила бюро переводов. То же у «маркетплейса»
    с «маркетологом» и у «монтажника» с «монтажёром»."""
    t = pkd._tok("перевозка переводчик маркетплейс маркетолог монтажник монтажёр")
    assert len({t[0], t[1]}) == 2 and len({t[2], t[3]}) == 2 and len({t[4], t[5]}) == 2
    # но своё словоизменение внутри корня по-прежнему сходится
    assert pkd._tok("перевозки")[0] == pkd._tok("перевозка")[0]
    assert pkd._tok("дизайн")[0] == pkd._tok("дизайна")[0]


def test_glossary_merges_colliding_stems():
    """У «produkcja» и «produktów» одна основа — значения обязаны слиться,
    иначе коды получают чужой перевод."""
    ru = pkd.index().ru_terms.get("produk", [])
    assert "производство" in ru and "продукты" in ru


# Официальные диапазоны разделов по секциям PKD 2025 (StrukturaPKD2025.xls).
# Буквы не совпадают с NACE Rev. 2: у GUS ICT разделён на J (58–60, издательская
# и вещательная) и K (61–63, телеком и IT), из-за чего всё дальше сдвинуто.
SECTION_RANGES = [
    ("A", 1, 3), ("B", 5, 9), ("C", 10, 33), ("D", 35, 35), ("E", 36, 39),
    ("F", 41, 43), ("G", 46, 47), ("H", 49, 53), ("I", 55, 56), ("J", 58, 60),
    ("K", 61, 63), ("L", 64, 66), ("M", 68, 68), ("N", 69, 75), ("O", 77, 82),
    ("P", 84, 84), ("Q", 85, 85), ("R", 86, 88), ("S", 90, 93), ("T", 94, 96),
    ("U", 97, 99),
]


def test_section_matches_the_code():
    """Секция обязана следовать из номера раздела.

    Секции собирались из файла ключей PKD 2007→2025, где одной старой секции
    отвечает несколько новых, и «текущая секция» застревала на последней строке:
    электромонтаж 43.21.Z уезжал в «культуру и спорт», а человек читал это
    в карточке. Теперь источник — официальная структура, а тест сторожит.
    """
    def expected(code):
        d = int(code[:2])
        return next((s for s, lo, hi in SECTION_RANGES if lo <= d <= hi), None)

    wrong = {c: (v["section"], expected(c)) for c, v in pkd.index().codes.items()
             if v["section"] != expected(c)}
    assert not wrong


def test_hint_codes_look_like_codes():
    """Подсказка с опечаткой молча игнорируется — профессия теряет буст."""
    import re
    for e in pkd.index().syn:
        for h in e.get("hint", []):
            assert re.fullmatch(r"\d{2}\.\d{2}\.[A-Z]", h), f"{e['ru'][0]}: {h}"


@pytest.fixture
def full_index(monkeypatch):
    """Настоящий справочник целиком — тот же артефакт, что работает в проде."""
    monkeypatch.delenv("JDG_PKD_DATA", raising=False)
    pkd.index.cache_clear()
    yield pkd.index()
    pkd.index.cache_clear()


class TestCorpusIntegrity:
    """Целостность официального корпуса: проверяем артефакт, а не поиск.

    Справочник собирается из трёх файлов GUS, и порча здесь не видна на глаз —
    секции уже один раз разъехались с кодами и доехали до людей в карточках.
    """

    def test_all_728_subclasses_unique(self, full_index):
        codes = list(full_index.codes)
        assert len(codes) == 728 and len(set(codes)) == 728

    def test_hierarchy_chain_is_consistent(self, full_index):
        """Подкласс → класс → группа → раздел → секция: каждый уровень обязан
        быть префиксом следующего, иначе запись собрана из разных строк файла."""
        broken = []
        for code, c in full_index.codes.items():
            if not (code.startswith(c["class"]) and c["class"].startswith(c["group"])
                    and c["group"].startswith(c["division"])
                    and c["section"] and len(c["section"]) == 1):
                broken.append((code, c["class"], c["group"], c["division"], c["section"]))
        assert not broken

    def test_every_level_has_official_name(self, full_index):
        nameless = [c["code"] for c in full_index.codes.values()
                    if not all(c.get(k) for k in
                               ("name", "section_name", "division_name",
                                "group_name", "class_name"))]
        assert not nameless

    def test_exclusion_links_point_to_existing_codes(self, full_index):
        """Разобранная ссылка обязана вести в существующий подкласс.
        Нераспознанный текст исключения — это нормально: GUS ссылается и на
        «odpowiednie podklasy działu 43», и на чужие классификации."""
        broken = {c["code"]: sorted(pkd.exclusion_targets(c) - set(full_index.codes))
                  for c in full_index.codes.values()}
        assert not {k: v for k, v in broken.items() if v}

    def test_exclusion_raw_text_is_never_lost(self, full_index):
        empty = [c["code"] for c in full_index.codes.values()
                 for x in c["excludes"] if not x.get("raw")]
        assert not empty

    def test_artifact_declares_sources_and_versions(self, full_index):
        meta = full_index.meta
        assert meta["schema_version"] == 2 and meta["pkd_version"] == "2025"
        assert meta["builder_version"]
        by_file = {s["filename"]: s["sha256"] for s in meta["sources"]}
        assert {"StrukturaPKD2025.xls", "KluczePKD_2007_2025.xls",
                "KlasyfikacjaPKD2025.pdf"} <= set(by_file)
        assert all(len(h) == 64 for h in by_file.values())
        # метки времени в артефакте быть не должно: одинаковый вход обязан
        # давать одинаковые байты, иначе хеши источников бессмысленны
        assert "generated_at" not in meta

    def test_official_wording_is_intact(self, full_index):
        """Официальный текст не пересказан и не переведён."""
        c = full_index.codes["43.32.Z"]
        assert c["name"] == "Zakładanie stolarki budowlanej"
        assert any("meble wbudowane" in x for x in c["includes"])


@pytest.mark.skipif(not FULL.is_file(),
                    reason="полный справочник собирается tools/pkd_build.py и не в git")
def test_hints_exist_in_catalogue(monkeypatch):
    """Пять записей словаря ссылались на коды, которых в PKD 2025 нет
    (62.30.Z, 86.90.E, 69.20.Z, 96.02.Z, 43.39.Z) — ловим это тестом."""
    monkeypatch.delenv("JDG_PKD_DATA", raising=False)
    pkd.index.cache_clear()
    idx = pkd.index()
    broken = {e["ru"][0]: [h for h in e.get("hint", []) if h not in idx.codes]
              for e in idx.syn}
    assert not {k: v for k, v in broken.items() if v}


@pytest.mark.skipif(not FULL.is_file(),
                    reason="полный справочник собирается tools/pkd_build.py и не в git")
def test_full_catalogue_when_built(monkeypatch):
    monkeypatch.delenv("JDG_PKD_DATA", raising=False)
    pkd.index.cache_clear()
    data = pkd._load(FULL.with_suffix("").with_suffix(".json"))
    assert len(data["codes"]) == 728, "в PKD 2025 ровно 728 подклассов"
    assert data["version"] == "PKD 2025"
    assert pkd.lookup("я блогер")["results"][0]["code"] == "90.11.Z"
    # разделы с единственным классом (31 «Produkcja mebli», 75 «weterynaryjna»)
    # GUS кладёт в одну строку с разделом — на них сборка спотыкалась
    for code in ("31.00.Z", "75.00.Z", "12.00.Z", "97.00.Z", "99.00.Z"):
        assert code in pkd.index().codes, code
    test_section_matches_the_code()   # тот же инвариант, но на всех 728


@pytest.mark.skipif(not FULL.is_file(),
                    reason="полный справочник собирается tools/pkd_build.py и не в git")
@pytest.mark.parametrize("query,code", [
    ("производство мебели", "31.00.Z"),
    # словарь профессий добавил «автомеханика»: общий ремонт авто — это
    # механика (95.31.A), а не кузовной цех (95.31.B), который идёт вторым
    ("ремонт автомобилей", "95.31.A"),
    ("уборка квартир", "81.21.Z"),
    ("перевозка грузов", "49.41.Z"),
    ("выращивание овощей", "01.13.Z"),
    ("детский сад няня", "88.91.Z"),
    ("туристическое агентство", "79.11.Z"),
    # пекарь печёт, а не только продаёт: производство (10.71.Z) впереди
    # розничной торговли выпечкой (47.24.Z), она остаётся второй строкой
    ("пекарня хлеб", "10.71.Z"),
    ("ветеринар", "75.00.Z"),
    ("переводчик текстов", "74.30.Z"),
    ("хостинг серверов", "63.10.D"),
    ("фотосъёмка свадеб", "74.20.Z"),
    ("курьерская доставка еды", "53.20.Z"),
    ("стоматолог", "86.23.Z"),
    ("ремонт телефонов", "95.10.Z"),
    ("аренда квартир посуточно", "68.20.Z"),
])
def test_russian_queries_across_sectors(monkeypatch, query, code):
    """Отрасли, которых нет в словаре профессий: работает перевод классификации."""
    monkeypatch.delenv("JDG_PKD_DATA", raising=False)
    pkd.index.cache_clear()
    assert pkd.lookup(query)["results"][0]["code"] == code


# Как человек называет свою работу -> подкласс, который обязан встать первым.
# Список ведём по дырам из чата: до расширения словаря половина этих запросов
# либо не находила ничего, либо уводила в чужую отрасль («перевозка мебели» —
# в бюро переводов, «монтажник окон» — в видеомонтаж).
PROFESSIONS = [
    ("электрик", "43.21.Z"), ("сантехник", "43.22.Z"), ("плиточник", "43.33.Z"),
    ("штукатур", "43.31.Z"), ("каменщик", "43.91.Z"), ("кровельщик", "43.41.Z"),
    ("монтажник окон", "43.32.Z"), ("утепление", "43.23.Z"), ("демонтаж", "43.11.Z"),
    ("сварщик", "25.53.Z"), ("плотник", "16.23.Z"), ("строительство домов", "41.00.A"),
    ("сборка мебели", "95.24.Z"),
    ("таксист", "49.33.Z"), ("дальнобойщик", "49.41.Z"), ("логист", "52.25.Z"),
    ("эвакуатор", "52.21.A"), ("перевозка мебели", "49.42.Z"),
    ("автомеханик", "95.31.A"), ("кузовной ремонт", "95.31.B"),
    ("автомойка", "95.31.C"), ("шиномонтаж", "95.31.A"), ("ремонт мотоциклов", "95.32.Z"),
    ("маникюр", "96.22.Z"), ("бровист", "96.22.Z"), ("татуировщик", "96.99.Z"),
    ("парикмахер", "96.21.Z"), ("физиотерапевт", "86.95.Z"), ("диетолог", "86.99.B"),
    ("сиделка", "88.10.Z"), ("няня", "88.91.Z"), ("груминг", "96.99.Z"),
    ("ветеринар", "75.00.Z"),
    ("повар", "56.21.Z"), ("кондитер", "10.71.Z"), ("кейтеринг", "56.21.Z"),
    ("фудтрак", "56.12.Z"), ("бариста", "56.30.Z"), ("ресторан", "56.11.Z"),
    ("нотариус", "69.10.Z"), ("аудитор", "69.20.C"), ("страховой агент", "66.22.Z"),
    ("кредитный брокер", "66.19.Z"), ("оценщик недвижимости", "68.32.A"),
    ("охранник", "80.01.Z"), ("колл-центр", "82.20.Z"),
    ("организация мероприятий", "82.30.Z"), ("hr", "78.10.Z"),
    ("художник", "90.12.Z"), ("актёр", "90.20.C"), ("диджей", "90.20.C"),
    ("аниматор", "93.29.A"), ("преподаватель музыки", "85.52.Z"),
    ("автошкола", "85.53.Z"), ("учитель английского", "85.59.A"),
    ("садовник", "81.30.Z"), ("флорист", "47.76.A"), ("портной", "95.29.Z"),
    ("сапожник", "95.23.Z"), ("ювелир", "32.12.Z"), ("часовщик", "95.25.Z"),
    ("вёрстка сайтов", "62.10.B"), ("техподдержка", "62.90.Z"),
    ("розничная торговля", "47.12.Z"), ("оптовая торговля", "46.90.Z"),
    ("маркетплейс", "47.91.Z"), ("торговый представитель", "46.19.Z"),
]


@pytest.mark.skipif(not FULL.is_file(),
                    reason="полный справочник собирается tools/pkd_build.py и не в git")
@pytest.mark.parametrize("query,code", PROFESSIONS)
def test_dictionary_covers_common_professions(monkeypatch, query, code):
    monkeypatch.delenv("JDG_PKD_DATA", raising=False)
    pkd.index.cache_clear()
    assert pkd.lookup(query)["results"][0]["code"] == code


def test_every_dictionary_word_is_findable():
    """Слово из словаря, которое токенизатор выбрасывает (короткое или из одних
    служебных символов), — мёртвая запись: человек его пишет, а поиск молчит."""
    dead = [w for e in pkd.index().syn for w in e["ru"] if not pkd._tok(w)]
    assert not dead
