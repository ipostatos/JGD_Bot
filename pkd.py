"""Подбор кода PKD по описанию деятельности + разбор последствий.

Человек пишет «я блогер» или «пишу код на питоне» — получает подклассы PKD 2025
с официальным текстом GUS, а к ним разбор: что этот код делает с правом на
освобождение от VAT, что PKD не определяет ставку ryczałtu, и не устарел ли
код (PKD 2007 живёт до 31.12.2026).

Поиск офлайновый: BM25 по официальным пояснениям + словарь русских синонимов.
Никаких обращений к платному API — данные локальные, ответ детерминированный.

Данные: webapp/data/{pkd.json, pkd_keys.json, pkd_synonyms.json} (tools/pkd_build.py).
"""
import json
import logging
import math
import os
import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
# канонический справочник лежит в git сжатым (~200 КБ) и едет в прод как есть:
# тесты, CI и прод обязаны работать с одним артефактом, иначе порча справочника
# всплывает у людей, а не на сборке. JDG_PKD_DATA подменяет каталог в тестах.
DATA = Path(os.environ.get("JDG_PKD_DATA") or ROOT / "data" / "pkd")

WORD = re.compile(r"[a-zа-яёąćęłńóśźż0-9]+", re.I)
CODE_IN_QUERY = re.compile(r"\b(\d{2})[.,](\d{2})(?:[.,\s]?([A-Za-z]))?\b")


def normalize_code(code: str) -> str:
    """Код к виду справочника (`62.10.B`).

    Реестры пишут коды по-разному: CEIDG отдаёт `9311Z`, GUS в отчётах REGON —
    тоже без точек, человек в поле ввода пишет `62.10.b` или через запятую.
    """
    c = re.sub(r"[^0-9A-Z]", "", (code or "").upper())
    if len(c) < 4:
        return c
    return ".".join(x for x in (c[:2], c[2:4], c[4:]) if x)
STEM = 6          # грубая нормализация: обе морфологии богатые, режем хвосты
K1, B = 1.4, 0.75  # BM25

# Виды деятельности, которые лишают права на zwolnienie podmiotowe z VAT
# (art. 113 ust. 13 ustawy o VAT) — определяем по официальному тексту, не по списку кодов
VAT_EXCLUDED = re.compile(r"doradzt|prawnicz|jubilers|inkas|ściągani[ae] długów|faktoring", re.I)
VAT_SOFT = re.compile(r"doradztwem w zakresie informatyki|doradztwo w zakresie sprzętu", re.I)


RU = re.compile(r"^[а-яё]")
# Обрезать кириллицу до пяти символов нельзя: «продажа» слипается с «продакт»,
# «фотосъёмка» с «фотостоком», и словарь срабатывает не на ту запись.
# Сначала снимаем окончание, потом уже режем.
RU_END = re.compile(r"(иями|ями|ами|ыми|ими|ого|ему|ому|ой|ей|ая|ое|ые|ий|ый|"
                    r"ам|ах|ов|ев|ии|ия|ию|ье|ья|ы|и|а|я|у|ю|е|о|ь|й)$")
# Основы, которые на шести символах слипаются в одно слово, а значат разное:
# «перевозка»/«переводчик» -> «перево», «маркетплейс»/«маркетолог» -> «маркет»,
# «монтажник»/«монтажёр» -> «монтаж». Из-за этого «перевозка мебели» находила
# бюро переводов. Общий STEM поднимать нельзя: «дизайн» и «дизайнер» тогда
# разъедутся и словарь потеряет их. Поэтому длиннее режем только эти корни.
RU_LONG = re.compile(r"^(перевоз|перевод|маркетп|маркето|маркети|"
                     r"монтажн|монтажё|монтаже)")


def _tok(text: str):
    """Грубая нормализация: у русского словоизменение богаче польского,
    «мебель» и «мебели» должны сойтись, а «продажа» и «продакт» — нет."""
    out = []
    for w in WORD.findall(text.lower()):
        # двухбуквенные русские («на», «по») — шум, а латинские и цифробуквенные
        # («hr», «ux», «qa», «3d») люди пишут как название профессии, и словарь
        # на них рассчитан: раньше такие запросы молча схлопывались в пустой
        if len(w) < 2 or (len(w) == 2 and RU.match(w)):
            continue
        if RU.match(w):
            base = RU_END.sub("", w) if len(w) > 4 else w
            long = RU_LONG.match(base)
            out.append(long.group(0) if long else base[:STEM])
        else:
            out.append(w[:STEM])
    return out


def exclusion_texts(code_record: dict) -> list[str]:
    """Сырые тексты исключений. В артефакте они лежат вместе с разобранными
    ссылками ({raw, target_codes}), а старые фикстуры хранят просто строки."""
    return [x["raw"] if isinstance(x, dict) else x
            for x in code_record.get("excludes", [])]


def own_chain(code_record: dict) -> set[str]:
    """Названия уровней, которым код принадлежит сам.

    Подкласс часто цитирует название своей же группы («górnictwo ropy naftowej»
    у 06.10.Z) — это его собственный текст. Загрязнением считается название
    ЧУЖОГО уровня, как «Uprawa roślin wieloletnich» в исключениях 01.19.Z.
    """
    return {(code_record.get(k) or "").strip().lower()
            for k in ("name", "class_name", "group_name",
                      "division_name", "section_name")} - {""}


def exclusion_targets(code_record: dict) -> set[str]:
    """Коды, на которые ссылаются исключения подкласса, — материал для правил:
    официальное «nie obejmuje» это единственное проверяемое основание запрета."""
    return {t for x in code_record.get("excludes", []) if isinstance(x, dict)
            for t in x.get("target_codes", [])}


def _load(path: Path) -> dict:
    """Артефакт справочника: сжатый в git и в проде, несжатый — у фикстур."""
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    import gzip
    with gzip.open(path.with_suffix(".json.gz"), "rt", encoding="utf-8") as f:
        return json.load(f)


class Index:
    def __init__(self, data: Path | None = None):
        data = data or Path(os.environ.get("JDG_PKD_DATA") or DATA)
        self.dir = data
        raw = _load(data / "pkd.json")
        self.meta = {k: v for k, v in raw.items() if k != "codes"}
        self.codes = {c["code"]: c for c in raw["codes"]}
        try:
            self.keys = _load(data / "pkd_keys.json")["map"]
        except Exception:
            self.keys = {}
        try:
            # словарь лежит рядом с ksef.json, а не в webapp/data: тот каталог
            # генерируемый и целиком в .gitignore, а синонимы писались руками
            self.syn = json.loads(
                (ROOT / "webapp" / "pkd_synonyms.json").read_text(encoding="utf-8"))["entries"]
        except Exception:
            self.syn = []
        try:
            self.rates = json.loads(
                (ROOT / "webapp" / "pkd_rates.json").read_text(encoding="utf-8"))
        except Exception:
            self.rates = {"rates": [], "note": ""}
        # русский слой поверх польских названий: словарь профессий покрывает
        # частные случаи, а это — саму классификацию, все 728 подклассов
        try:
            terms = json.loads(
                (ROOT / "webapp" / "pkd_ru_terms.json").read_text(encoding="utf-8"))["terms"]
            # у «produkcja» и «produktów» одна основа: значения сливаем, иначе
            # последний ключ молча затирает первый и коды получают чужой перевод
            self.ru_terms = {}
            for pl, ru in terms.items():
                t = _tok(pl)
                if t:
                    self.ru_terms.setdefault(t[0], [])
                    self.ru_terms[t[0]] += [w for w in ru if w not in self.ru_terms[t[0]]]
        except Exception:
            self.ru_terms = {}

        self.docs: dict[str, Counter] = {}
        self.names: dict[str, set] = {}
        self.df: Counter = Counter()
        for code, c in self.codes.items():
            # название весит больше пояснений: оно и есть суть подкласса
            name_toks = _tok(c["name"])
            ru = [t for w in name_toks for t in _tok(" ".join(self.ru_terms.get(w, [])))]
            # пояснения обрезаем: у «Produkcja mebli» название короткое, а список
            # «obejmuje» длинный, и BM25 штрафует документ за длину — короткий
            # точный подкласс проигрывал соседям с пухлым описанием
            toks = name_toks * 3 + ru * 2 + _tok(" ".join(c["includes"]))[:120]
            self.docs[code] = Counter(toks)
            self.names[code] = set(name_toks) | set(ru)
            self.df.update(set(toks))
        self.avg_len = sum(sum(d.values()) for d in self.docs.values()) / max(len(self.docs), 1)
        self.n = len(self.docs)

        # сопоставляем по основам слов, а не по подстроке: «интерьеров» должно
        # находить «дизайн интерьера», «консультирую» — «консультант»
        self.by_ru: list[tuple[list[list[str]], dict]] = []
        for e in self.syn:
            self.by_ru.append(([_tok(r) for r in e["ru"] if _tok(r)], e))
        log.info("pkd: %d подклассов, %d синонимов", self.n, len(self.syn))

    # ── поиск ──────────────────────────────────────────────────────────────
    def expand(self, query: str):
        """Русский запрос -> польские слова + коды-подсказки из словаря."""
        qs = set(_tok(query))
        found = []
        for variants, e in self.by_ru:
            # многословный синоним («дизайн интерьера») требует всех основ
            hit = max((v for v in variants if set(v) <= qs), key=len, default=None)
            if hit:
                found.append((len(hit), e))
        # специфичное совпадение важнее общего: «дизайн интерьера» перебивает «дизайнера»
        found.sort(key=lambda x: -x[0])
        pl, matched = [], []
        hints: dict[str, float] = {}
        for i, (spec, e) in enumerate(found):
            # слова самого точного совпадения весят втрое: иначе общий «дизайнер»
            # своим словарём перебивает узкий «дизайн интерьера»
            if i and found[0][0] >= 2:
                pass          # нашлось точное многословное совпадение — общие
                              # записи в польские слова не добавляем, только в hint
            else:
                pl += e.get("pl", []) * (3 if i == 0 else 1)
            # многословный синоним («дизайн интерьера») — это уже прямое указание,
            # а не намёк, поэтому его коды получают вес, перебивающий общие слова
            base = (20.0 if spec >= 2 else 9.0) - 2.0 * i
            for j, h in enumerate(e.get("hint", [])):
                hints[h] = max(hints.get(h, 0), max(base - 2.0 * j, 2.0))
            matched.append(e["ru"][0])
        return pl, hints, matched

    def search(self, query: str, limit: int = 5):
        pl, hints, matched = self.expand(query)
        toks = _tok(query + " " + " ".join(pl))
        if not toks:
            return [], matched
        scores: dict[str, float] = defaultdict(float)
        for t in set(toks):
            df = self.df.get(t, 0)
            if not df:
                continue
            idf = math.log(1 + (self.n - df + 0.5) / (df + 0.5))
            for code, doc in self.docs.items():
                f = doc.get(t, 0)
                if not f:
                    continue
                dl = sum(doc.values())
                scores[code] += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * dl / self.avg_len))
        # попадание прямо в название весомее совпадения в пояснениях: BM25 иначе
        # предпочитает короткий «Naprawa mebli» точному «Produkcja mebli»
        qset = {t for t in set(_tok(query)) if self.df.get(t)}
        if qset:
            for code, hit in ((c, len(qset & n)) for c, n in self.names.items()):
                if hit:
                    scores[code] += 9.0 * (hit / len(qset))

        # подсказка словаря поднимает код, но не назначает его: официальный текст
        # всё равно показывается, и решение остаётся за человеком
        for h, w in hints.items():
            if h in self.codes:
                scores[h] += w
        top = sorted(scores.items(), key=lambda kv: -kv[1])[:limit]
        return [(code, round(s, 1)) for code, s in top], matched

    def context_rules(self, code: str) -> list[dict]:
        """Правила уровней над подклассом: класс → группа → раздел → секция.

        GUS выносит туда общее («Dział ten nie obejmuje naprawy odzieży»
        над всем производством одежды). К самому подклассу этот текст
        не относится, но правилам движка он понадобится, поэтому отдаём
        отдельным списком с указанием уровня.
        """
        c = self.codes.get(code)
        if not c:
            return []
        levels = self.meta.get("levels") or {}
        out = []
        for level, key in (("класс", "class"), ("группа", "group"),
                           ("раздел", "division"), ("секция", "section")):
            rules = levels.get(c.get(key) or "")
            if rules:
                out.append({"level": level, "code": c[key], **rules})
        return out

    # ── разбор последствий ────────────────────────────────────────────────
    def analyse(self, code: str) -> dict:
        c = self.codes[code]
        flags = []
        # жёсткий флаг — только если исключённый вид услуг стоит в САМОМ названии
        # подкласса; упоминание внутри пояснений даёт лишь повод присмотреться,
        # иначе «графический дизайн» ошибочно объявляется потерей права на VAT
        if VAT_EXCLUDED.search(c["name"]):
            flags.append({
                "level": "warn",
                "title": "Лишает права на освобождение от VAT",
                "text": "Doradztwo, юридические и ювелирные услуги, взыскание долгов "
                        "перечислены в art. 113 ust. 13 ustawy o VAT: при них "
                        "плательщиком VAT нужно стать сразу, независимо от оборота "
                        "и лимита 240 000 zł."})
        elif VAT_EXCLUDED.search(" ".join(c["includes"])):
            flags.append({
                "level": "note",
                "title": "Проверить формулировку услуг",
                "text": "Сам подкласс под art. 113 ust. 13 не подпадает, но в его "
                        "официальном описании упоминается doradztwo или смежные "
                        "услуги из списка исключений. Если консультации станут "
                        "заметной частью работы, право на освобождение от VAT "
                        "теряется — формулировки в договоре и на фактуре стоит "
                        "согласовать с бухгалтером."})
        # Старый код полезен ровно в двух местах: когда человек сам его ввёл и
        # когда он стоит у него в реестре. В обычной выдаче «раньше это было
        # 62.01.Z» — шум: спрашивают, какой код нужен сейчас, а не каким он был.
        old = [o for o, v in self.keys.items() if code in v["to"]]
        return {
            "code": code,
            "name": c["name"],
            "section": c["section"],
            "section_name": (c["section_name"] or "").capitalize(),
            "includes": c["includes"][:6],
            # наружу отдаём тот же плоский текст, что и раньше: ссылки на коды
            # разобраны внутри артефакта и нужны правилам, а не карточке
            "excludes": exclusion_texts(c)[:4],
            "was_pkd2007": old[:4],
            "flags": flags,
            "rate_hints": self.rate_hints(code),
        }

    def rate_hints(self, code: str) -> list[dict]:
        """Вероятные ставки ryczałtu — подсказка, а не расчёт.

        Ставку задаёт вид услуги по PKWiU (art. 12 ust. 1), поэтому даём
        максимум два кандидата и всегда с оговоркой: код PKD ничего не решает.
        """
        c = self.codes.get(code)
        if not c:
            return []
        blob = (c["name"] + " " + " ".join(c["includes"][:4])).lower()
        out = []
        for r in self.rates.get("rates", []):
            by_code = any(code.startswith(p) for p in r.get("codes", []))
            by_word = any(w.lower() in blob for w in r.get("words", []))
            if by_code or by_word:
                out.append({"rate": r["rate"], "who": r["who"],
                            "match": "код" if by_code else "описание"})
        if not out:                       # услуги по умолчанию идут по 8,5%
            default = next((r for r in self.rates.get("rates", [])
                            if r.get("default_for_services")), None)
            if default:
                out.append({"rate": default["rate"], "who": default["who"],
                            "match": "по умолчанию для услуг"})
        return out[:2]

    def migrate(self, old_code: str):
        """Старый код PKD 2007 -> актуальные подклассы PKD 2025."""
        v = self.keys.get(old_code)
        if not v:
            return None
        return {"old": old_code, "old_name": v["name"],
                "to": [{"code": c, "name": self.codes[c]["name"]}
                       for c in v["to"] if c in self.codes]}


@lru_cache(maxsize=1)
def index() -> Index:
    return Index()


def lookup(query: str, limit: int = 5) -> dict:
    """Точка входа: описание деятельности или код -> подборка с разбором."""
    idx = index()
    query = (query or "").strip()
    if not query:
        return {"query": query, "results": [], "matched": []}

    m = CODE_IN_QUERY.search(query)
    if m:                                  # спросили про конкретный код
        a, b, letter = m.groups()
        code = f"{a}.{b}" + (f".{letter.upper()}" if letter else "")
        if code in idx.codes:
            return {"query": query, "kind": "code",
                    "results": [idx.analyse(code)], "matched": [],
                    "note": _rate_note()}
        mig = idx.migrate(code)
        if mig:                            # код из PKD 2007 — показываем замену
            return {"query": query, "kind": "migration", "migration": mig,
                    "results": [idx.analyse(c["code"]) for c in mig["to"]],
                    "matched": [],
                    "note": "Это код старой классификации PKD 2007. Она действует "
                            "до 31.12.2026: после этой даты GUS перекодирует записи "
                            "автоматически по общим ключам, без разбора того, чем вы "
                            "занимаетесь на самом деле. Лучше обновить самому."}

    hits, matched = idx.search(query, limit)
    results = []
    for code, score in hits:
        r = dict(idx.analyse(code), score=score)
        r.pop("was_pkd2007", None)     # в поиске старый номер только отвлекает
        results.append(r)
    return {"query": query, "kind": "search", "matched": matched,
            "results": results, "note": _rate_note()}


def audit(codes: list[str], regon: dict | None = None, source: str = "ceidg") -> dict:
    """Разбор кодов, которые уже стоят в реестре: актуальны ли и чем грозят.

    Главное, что ищем, — коды старой классификации: PKD 2007 действует до
    31.12.2026, дальше GUS перекодирует записи автоматически по общим ключам.

    `regon` — ответ GUS BIR (`{"version", "codes"}`). Только он знает, в какой
    классификации лежит сама запись: по одним кодам это не определить, потому что
    большинство номеров в PKD 2007 и PKD 2025 совпадает.
    """
    idx = index()
    items, outdated = [], 0
    for raw in codes[:20]:
        # 🔴 коды из CEIDG приходят без точек (`9311Z`), и без нормализации
        # аудит объявлял «такого кода нет» про каждый реальный код записи
        code = normalize_code(raw)
        if not code:
            continue
        if code in idx.codes:
            items.append(dict(idx.analyse(code), status="ok"))
            continue
        mig = idx.migrate(code)
        if mig:
            outdated += 1
            items.append({"code": code, "name": mig["old_name"], "status": "outdated",
                          "replace_with": [dict(idx.analyse(x["code"]), status="new")
                                           for x in mig["to"]]})
        else:
            items.append({"code": code, "name": "", "status": "unknown"})
    summary = ("Все коды уже в новой классификации." if not outdated else
               f"Кодов старой классификации: {outdated}. PKD 2007 действует "
               f"до 31.12.2026 — после этой даты GUS перекодирует запись сам, "
               f"по общим ключам и без разбора того, чем вы занимаетесь. "
               f"Обновить можно самому: biznes.gov.pl → «zmień dane w CEIDG».")
    vat = [i["code"] for i in items
           if any(f["level"] == "warn" for f in i.get("flags", []))]
    out = {"items": items, "outdated": outdated, "summary": summary,
           "vat_warning_codes": vat, "note": _rate_note(), "source": source}
    if regon:
        out["regon"] = _regon_block(items, outdated, regon, source)
    return out


REGON_NOTE = {
    "2025": "REGON: запись уже переведена на новую классификацию PKD 2025.",
    "2007": "REGON: запись всё ещё в классификации PKD 2007. Она действует "
            "до 31.12.2026 — потом GUS перекодирует запись сам, по общим ключам "
            "и без разбора того, чем вы занимаетесь. Выбрать коды осознанно "
            "можно только пока это не случилось: biznes.gov.pl → «zmień dane w CEIDG».",
}


def _regon_block(items: list[dict], outdated: int, regon: dict, source: str) -> dict:
    """Что о записи говорит REGON и совпадает ли она с CEIDG.

    Сравнивать списки кодов имеет смысл только внутри одной классификации:
    иначе «реестры разошлись» покажет разницу между PKD 2007 и PKD 2025,
    то есть нормальный ход перекодировки, а не ошибку в записи.
    """
    version = regon.get("version")
    block = {"version": version,
             "codes": [c for c in (regon.get("codes") or [])],
             "note": REGON_NOTE.get(version or "") or (
                 f"REGON: у записи коды сразу двух классификаций "
                 f"({(version or '').replace('+', ' и ')})." if version else
                 "REGON не сказал, в какой классификации записаны коды.")}
    if source != "ceidg" or version != "2025" or outdated:
        # сравнивать нечего: либо коды и так пришли из REGON, либо классификации
        # у реестров разные — расхождение в этом случае ничего не значит
        return block
    mine = {i["code"] for i in items}
    theirs = {c["code"] for c in block["codes"]}
    block["only_in_ceidg"] = sorted(mine - theirs)
    block["only_in_regon"] = sorted(theirs - mine)
    if block["only_in_ceidg"] or block["only_in_regon"]:
        block["diff_note"] = (
            "Списки кодов в CEIDG и REGON разошлись. Обычно это значит, что "
            "правка в CEIDG ещё не доехала до REGON (пара дней) — если прошло "
            "больше недели, стоит проверить запись.")
    return block


def _rate_note() -> str:
    return ("Код PKD не определяет ставку ryczałtu и не решает за налоговую: "
            "ставка зависит от фактического вида услуги (классификация PKWiU), "
            "а PKD — это запись в реестре. Совпадать они могут, но проверять "
            "нужно именно вид деятельности.")
