"""Официальный PKD 2025 -> webapp/data/pkd.json (+ ключи PKD 2007→2025).

Источники (качаются в sources/pkd/, в git не идут):
  KluczePKD_2007_2025.xls  — таблица GUS: коды и названия обеих классификаций,
                             уровень группировки и текст соответствия
  KlasyfikacjaPKD2025.pdf  — пояснения «Podklasa ta obejmuje / nie obejmuje»

Названия и иерархию берём из XLS: в PDF таблица структуры свёрстана колонками,
и текстовый слой рвёт названия на куски. Из PDF берём только пояснения.

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

import fitz
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
# канонический справочник лежит в git (сжатый ~200 КБ) и едет в прод как есть:
# так тесты, CI и прод работают с одним и тем же артефактом
OUT = ROOT / "data" / "pkd"
SRC = ROOT / "sources" / "pkd"
SCHEMA_VERSION = 2
BUILDER_VERSION = "2.0.0"

SUBCLASS = re.compile(r"^\d{2}\.\d{2}\.[A-Z]$")
CODE_LINE = re.compile(r"^(\d{2}\.\d{2}\.[A-Z])\s*$")
INCL = re.compile(r"^Podklasa ta obejmuje.*$", re.I)
EXCL = re.compile(r"^Podklasa ta nie obejmuje.*$", re.I)
NOISE = re.compile(r"^(Wyjaśnienia do PKD 2025|Struktura klasyfikacji|SEKCJA [A-U]|\d+)\s*$")


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


def from_pdf(path: Path, valid: set):
    """Пояснения по подклассам: что входит и что явно не входит."""
    doc = fitz.open(path)
    text = "\n".join(doc[i].get_text() for i in range(doc.page_count)
                     if "Wyjaśnienia do PKD 2025" in doc[i].get_text())
    out: dict[str, dict] = {}
    cur, mode = None, None
    for ln in (l.strip() for l in text.split("\n")):
        m = CODE_LINE.match(ln)
        if m and m.group(1) in valid:
            cur, mode = m.group(1), None
            out.setdefault(cur, {"includes": [], "excludes": []})
            continue
        if cur is None or not ln or NOISE.match(ln):
            continue
        if INCL.match(ln):
            mode = "includes"
            continue
        if EXCL.match(ln):
            mode = "excludes"
            continue
        if mode:
            item = ln.lstrip("-–— ").strip()
            if item:
                out[cur][mode].append(item)
    return out


def merge_wrapped(items):
    """Строки PDF рвутся посреди предложения — склеиваем продолжения.

    Обрывок «…taką jak:» — это начало перечисления, а не отдельный пункт:
    без склейки первым описанием подкласса оказывается голое «jak:».
    """
    merged = []
    for it in items:
        prev = merged[-1] if merged else ""
        cont = prev and (prev.endswith(":") or
                         (not prev.endswith((".", ";")) and not it[:1].isupper()))
        if cont:
            merged[-1] += " " + it
        else:
            merged.append(it)
    return [m.strip(" ;.") for m in merged if len(m.strip()) > 12]


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
    for name in ("KlasyfikacjaPKD2025.pdf", "KluczePKD_2007_2025.xls",
                 "StrukturaPKD2025.xls"):
        dst = SRC / name
        if dst.exists():
            print(f"  {name}: уже есть")
            continue
        print(f"  качаю {name}…")
        urllib.request.urlretrieve(GUS + name, dst)
        print(f"  {name}: {dst.stat().st_size // 1024} КБ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", default=str(SRC / "KlasyfikacjaPKD2025.pdf"))
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

    print("PDF: пояснения…")
    expl = from_pdf(Path(args.pdf), set(hier))
    print(f"  с пояснениями: {sum(1 for v in expl.values() if v['includes'])}")

    validity = bir_validity()
    if validity:
        print(f"  даты действия из словаря REGON: {len(validity)} кодов")

    valid = set(hier)
    records, unresolved = [], 0
    for code in sorted(hier):
        e = expl.get(code, {})
        v = validity.get(code) or {}
        excludes = link_exclusions(merge_wrapped(e.get("excludes", [])), valid)
        unresolved += sum(1 for x in excludes if not x["target_codes"])
        records.append({
            "valid_from": v.get("from"),
            "valid_to": v.get("to"),
            **hier[code],
            "includes": merge_wrapped(e.get("includes", [])),
            "excludes": excludes,
        })

    linked = sum(len(x["target_codes"]) for r in records for x in r["excludes"])
    print(f"  ссылок из исключений разобрано: {linked}, "
          f"без распознанного кода: {unresolved} (текст сохранён)")

    sources = [{"filename": p.name, "sha256": sha256(p)}
               for p in (Path(args.structure), Path(args.keys), Path(args.pdf))
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
    write_artifact(OUT / "pkd.json", {**meta, "codes": records})
    write_artifact(OUT / "pkd_keys.json",
                   {"note": "PKD 2007 -> PKD 2025, официальные ключи GUS",
                    "schema_version": SCHEMA_VERSION, "map": keys})

    print(f"\nЗаписей: {len(records)}, из них с описанием: "
          f"{sum(1 for r in records if r['includes'])}")
    for name in ("pkd.json.gz", "pkd_keys.json.gz"):
        print(f"-> {OUT / name} ({(OUT / name).stat().st_size // 1024} КБ)")


if __name__ == "__main__":
    main()
