"""Официальный PKD 2025 -> data/pkd/pkd.json.gz (+ ключи PKD 2007→2025).

Источники (качаются в sources/pkd/, в git не идут):
  StrukturaPKD2025.xls     — иерархия: секция, раздел, группа, класс, подкласс
  Wyjasnienia_PKD_2025.xls — пояснения «obejmuje / nie obejmuje» по уровням
  KluczePKD_2007_2025.xls  — ключи перехода PKD 2007 -> PKD 2025

Всё берём из XLS. PDF классификации в сборке не участвует намеренно: в плоском
тексте заголовок следующей группы неотличим от строки исключения, и пояснения
затекали в чужие подклассы — 633 строки из 1401 у 244 кодов. В XLS код уровня
стоит в первой колонке, а его пояснение — в следующей строке, поэтому привязка
однозначна по построению.

PKD 2025 введена rozporządzeniem RM от 18.12.2024 (Dz. U. poz. 1936), действует
с 01.01.2025. Коды PKD 2007 остаются в силе до 31.12.2026; после этого GUS
перекодирует записи автоматически по общим ключам, без разбора реальной
деятельности предпринимателя.

    python tools/pkd_build.py
"""
import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
# канонический справочник лежит в git (сжатый ~200 КБ) и едет в прод как есть:
# так тесты, CI и прод работают с одним и тем же артефактом
OUT = ROOT / "data" / "pkd"
SRC = ROOT / "sources" / "pkd"
SCHEMA_VERSION = 2
BUILDER_VERSION = "2.0.0"

SUBCLASS = re.compile(r"^\d{2}\.\d{2}\.[A-Z]$")


SECTION_ROW = re.compile(r"^SEKCJA\s+([A-U])$", re.I)


# ссылки внутри «Podklasa ta nie obejmuje: …sklasyfikowanej w 13.91.Z»
CODE_REF = re.compile(r"\b(\d{2}\.\d{2}\.[A-Z])\b")
CLASS_REF = re.compile(r"\b(\d{2}\.\d{2})\b(?!\.[A-Z])")


def from_structure(path: Path):
    """Полная иерархия PKD 2025 из официальной структуры GUS.

    Секции ОБЯЗАНЫ приходить отсюда, а не из файла ключей: там одной секции
    PKD 2007 соответствует несколько секций PKD 2025 (у C их три: A, C, S),
    и «текущая секция» при потоковом чтении застревает на последней строке.
    Из-за этого электромонтаж 43.21.Z оказывался в секции «культура и спорт».

    Возвращает код -> вся цепочка секция → раздел → группа → класс → подкласс
    с официальными названиями уровней: по ней потом проверяется целостность.
    """
    df = pd.read_excel(path, sheet_name=0, header=0, dtype=str)
    df.columns = ["dzial", "grupa", "klasa", "podklasa", "name"]

    def cell(v):
        s = str(v).strip()
        return "" if s in ("nan", "") else s

    out: dict[str, dict] = {}
    section = section_name = None
    division = division_name = None
    group = group_name = None
    klass = class_name = None

    for r in df.itertuples(index=False):
        col0, grupa = cell(r.dzial), cell(r.grupa)
        klasa, code, name = cell(r.klasa), cell(r.podklasa), cell(r.name)

        m = SECTION_ROW.match(col0)
        if m:                                    # «SEKCJA F», название в колонке класса
            section, section_name = m.group(1).upper(), klasa
            continue
        if re.fullmatch(r"\d{2}", col0):         # дział
            division, division_name = col0, name
        if grupa:                                # grupa; в той же строке может быть класс
            group, group_name = grupa, name
        if re.fullmatch(r"\d{2}\.\d{2}", klasa):
            klass, class_name = klasa, name
        # у разделов с единственным классом (31 «Produkcja mebli», 75 «weterynaryjna»)
        # GUS кладёт раздел, группу, класс и подкласс в ОДНУ строку — девять
        # подклассов терялись, когда после строки раздела делался continue
        if SUBCLASS.match(code):
            out[code] = {
                "code": code, "name": name,
                "section": section, "section_name": section_name,
                "division": division or code[:2], "division_name": division_name,
                "group": group or code[:4], "group_name": group_name,
                "class": klass or code[:5], "class_name": class_name,
            }
    return out


def link_exclusions(items: list[str], valid: set) -> list[dict]:
    """Текст исключения + коды, на которые оно ссылается.

    Сырой текст сохраняем всегда: свободные формулировки («sklasyfikowanej
    w odpowiednich podklasach działu 43») кодом не выражаются, и терять их
    нельзя. Ссылки на классы (13.91) разворачиваем в подклассы: в тексте GUS
    уровень ссылки не всегда совпадает с уровнем кода.
    """
    out = []
    for raw in items:
        codes = {c for c in CODE_REF.findall(raw) if c in valid}
        for cl in CLASS_REF.findall(raw):
            codes |= {c for c in valid if c.startswith(cl + ".")}
        out.append({"raw": raw, "target_codes": sorted(codes)})
    return out


def write_artifact(path: Path, payload: dict) -> None:
    """Канонический артефакт пишем сжатым — он же едет в git и в прод.

    Один файл вместо «полного справочника на VPS и урезанной фикстуры на CI»:
    гонять тесты на другом наборе, чем работает прод, — способ не заметить,
    что справочник испортился. Gzip с mtime=0, иначе один и тот же вход даёт
    разные байты и коммит шумит на каждой сборке.
    """
    import gzip
    raw = json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8")
    with gzip.GzipFile(path.with_suffix(".json.gz"), "wb", mtime=0) as f:
        f.write(raw)
    path.unlink(missing_ok=True)      # старый несжатый артефакт не оставляем


def sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def keys_from_xls(path: Path):
    """Только карта старый код PKD 2007 -> новые подклассы PKD 2025."""
    df = pd.read_excel(path, sheet_name=0, header=2, dtype=str)
    df.columns = ["lp", "level", "old", "old_name", "code", "name", "scope", "n", "rel"]
    df = df.dropna(subset=["code"])

    keys = {}
    for r in df.itertuples(index=False):
        code = str(r.code).strip()
        old = str(r.old).strip() if r.old else ""
        if SUBCLASS.match(code) and SUBCLASS.match(old):
            keys.setdefault(old, {"name": str(r.old_name).strip(), "to": []})
            if code not in keys[old]["to"]:
                keys[old]["to"].append(code)
    return keys


LEVEL_ROW = re.compile(r"^(SEKCJA\s+([A-U])|DZIAŁ\s+(\d{2})|(\d{2}\.\d)|(\d{2}\.\d{2})|"
                       r"(\d{2}\.\d{2}\.[A-Z]))$", re.I)
# «Podklasa ta obejmuje… / …obejmuje również… / …nie obejmuje…» и то же самое
# для группы, класса, раздела и секции. Уровень маркера = уровень текста.
MARKER = re.compile(r"^(Podklasa|Klasa|Grupa|Dział|Sekcja)\s+(?:ta|ten)\s+"
                    r"(nie\s+obejmuje|obejmuje\s+również|obejmuje|zawiera)", re.I)


def from_explanations(path: Path):
    """Пояснения из официального XLS: код уровня -> что входит и что не входит.

    Раньше пояснения вынимались из PDF, и это было источником целого класса
    ошибок: в плоском тексте заголовок следующей группы неотличим от строки
    исключения, и 633 строки из 1401 (244 подкласса) показывали людям чужой
    текст. Здесь привязка однозначна по построению: в первой колонке стоит код
    уровня, в следующей строке — его собственное пояснение. Никакой геометрии,
    пустых строк и догадок.
    """
    df = pd.read_excel(path, sheet_name=0, header=None, dtype=str)
    rows = [(str(a).strip() if a and str(a) != "nan" else "",
             str(b) if b and str(b) != "nan" else "")
            for a, b in df.itertuples(index=False)]

    out: dict[str, dict] = {}
    for i, (code, _) in enumerate(rows):
        m = LEVEL_ROW.match(code)
        if not m:
            continue
        key = (m.group(2) or m.group(3) or m.group(4) or m.group(5) or m.group(6)).upper()
        nxt = rows[i + 1] if i + 1 < len(rows) else ("", "")
        if nxt[0]:                      # следом сразу другой код — пояснения нет
            continue
        out[key] = split_explanation(nxt[1])
    return out


def split_explanation(text: str) -> dict:
    """Текст пояснения -> {includes, excludes}.

    Пункты идут маркированным списком, вводная фраза маркера («…na przykład:»)
    остаётся отдельной строкой: она несёт смысл и в старом артефакте тоже была.
    """
    res = {"includes": [], "excludes": []}
    mode = None
    for line in (l.strip() for l in text.split("\n")):
        if not line:
            continue
        m = MARKER.match(line)
        if m:
            mode = "excludes" if m.group(2).lower().startswith("nie") else "includes"
            rest = line[m.end():].strip(" :,")
            # «obejmuje pozostałe uprawy rolne…, na przykład» — это содержание,
            # а «obejmuje:» — только заголовок списка
            if len(rest) > 12:
                res[mode].append(rest)
            continue
        if mode:
            item = line.lstrip("-–— ").strip().rstrip(",")
            if item:
                res[mode].append(item)
    return res




GUS = "https://klasyfikacje.stat.gov.pl/static/pkd_25/pdf/"
# Словарь REGON (из пакета документации BIR): даёт даты действия кодов —
# по ним видно, что код закрыт, а не просто «нет в новой классификации»
BIR_DICT = ROOT / "GUS-Regon-UslugaBIRver1.2-dokumentacjaVer1.4" / "BIR12_SlownikPKD2025.xlsx"


def bir_validity() -> dict:
    """Код -> {from, to} из словаря REGON. Коды там без точек: 6210B."""
    if not BIR_DICT.is_file():
        return {}
    try:
        df = pd.read_excel(BIR_DICT, dtype=str)
    except Exception as e:
        print(f"  словарь REGON не прочитан: {e}")
        return {}
    out = {}
    for r in df.itertuples(index=False):
        raw = str(getattr(r, "Kod", "") or "").strip()
        if len(raw) != 5:
            continue
        code = f"{raw[:2]}.{raw[2:4]}.{raw[4]}"
        rec = {"from": str(r.DataOd)[:10] if r.DataOd else None,
               "to": str(r.DataDo)[:10] if r.DataDo and str(r.DataDo) != "nan" else None}
        # у кода бывает две записи (PKD 2007 и 2025) — берём более позднюю
        if code not in out or (rec["from"] or "") > (out[code]["from"] or ""):
            out[code] = rec
    return out


def download():
    """Скачать исходники GUS — нужно на чистой машине (VPS, CI)."""
    import urllib.request
    SRC.mkdir(parents=True, exist_ok=True)
    for name in ("StrukturaPKD2025.xls", "Wyjasnienia_PKD_2025.xls",
                 "KluczePKD_2007_2025.xls"):
        dst = SRC / name
        if dst.exists():
            print(f"  {name}: уже есть")
            continue
        print(f"  качаю {name}…")
        urllib.request.urlretrieve(GUS + name, dst)
        print(f"  {name}: {dst.stat().st_size // 1024} КБ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--explanations",
                    default=str(SRC / "Wyjasnienia_PKD_2025.xls"))
    ap.add_argument("--keys", default=str(SRC / "KluczePKD_2007_2025.xls"))
    ap.add_argument("--structure", default=str(SRC / "StrukturaPKD2025.xls"))
    ap.add_argument("--download", action="store_true",
                    help="скачать исходники GUS, если их нет локально")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.download:
        download()

    print("XLS: структура — коды, названия, вся иерархия…")
    hier = from_structure(Path(args.structure))
    keys = keys_from_xls(Path(args.keys))
    print(f"  подклассов PKD 2025: {len(hier)}, секций: "
          f"{len({h['section'] for h in hier.values()})}, старых кодов в ключах: {len(keys)}")

    print("XLS: пояснения…")
    expl = from_explanations(Path(args.explanations))
    subs = {k: v for k, v in expl.items() if SUBCLASS.match(k)}
    print(f"  уровней с пояснениями: {len(expl)}, из них подклассов: {len(subs)}")

    validity = bir_validity()
    if validity:
        print(f"  даты действия из словаря REGON: {len(validity)} кодов")

    valid = set(hier)
    records, unresolved = [], 0
    for code in sorted(hier):
        e = expl.get(code, {})
        v = validity.get(code) or {}
        excludes = link_exclusions(e.get("excludes", []), valid)
        unresolved += sum(1 for x in excludes if not x["target_codes"])
        records.append({
            "valid_from": v.get("from"),
            "valid_to": v.get("to"),
            **hier[code],
            "includes": e.get("includes", []),
            "excludes": excludes,
        })

    linked = sum(len(x["target_codes"]) for r in records for x in r["excludes"])
    print(f"  ссылок из исключений разобрано: {linked}, "
          f"без распознанного кода: {unresolved} (текст сохранён)")

    # Пояснения уровней (секция, раздел, группа, класс) храним один раз рядом
    # с кодами, а не копией в каждом подклассе: у GUS там лежит общее правило
    # («Dział ten nie obejmuje naprawy odzieży»), и оно относится к уровню,
    # а не к конкретному подклассу. Выдавать его за текст подкласса нельзя,
    # терять — тоже: правила движка будут на него опираться.
    levels = {code: {"includes": v["includes"],
                     "excludes": link_exclusions(v["excludes"], valid)}
              for code, v in expl.items()
              if not SUBCLASS.match(code) and (v["includes"] or v["excludes"])}
    print(f"  пояснений уровней (секция/раздел/группа/класс): {len(levels)}")

    sources = [{"filename": p.name, "sha256": sha256(p)}
               for p in (Path(args.structure), Path(args.explanations),
                         Path(args.keys))
               if p.is_file()]
    meta = {
        "pkd_version": "2025",
        "schema_version": SCHEMA_VERSION,
        "builder_version": BUILDER_VERSION,
        # version/source оставлены для совместимости со старыми потребителями
        "version": "PKD 2025",
        "source": "GUS, rozporządzenie RM z 18.12.2024 (Dz. U. poz. 1936)",
        "transition_until": "2026-12-31",
        "sources": sources,
        "counts": {"codes": len(records),
                   "with_includes": sum(1 for r in records if r["includes"]),
                   "with_excludes": sum(1 for r in records if r["excludes"])},
    }
    # Метки времени в артефакте нет намеренно: сборка из одних и тех же файлов
    # обязана давать байт в байт то же самое, иначе теряется смысл хеша
    OUT.mkdir(parents=True, exist_ok=True)
    write_artifact(OUT / "pkd.json", {**meta, "levels": levels, "codes": records})
    write_artifact(OUT / "pkd_keys.json",
                   {"note": "PKD 2007 -> PKD 2025, официальные ключи GUS",
                    "schema_version": SCHEMA_VERSION, "map": keys})

    print(f"\nЗаписей: {len(records)}, из них с описанием: "
          f"{sum(1 for r in records if r['includes'])}")
    for name in ("pkd.json.gz", "pkd_keys.json.gz"):
        print(f"-> {OUT / name} ({(OUT / name).stat().st_size // 1024} КБ)")


if __name__ == "__main__":
    main()
