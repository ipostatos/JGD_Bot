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
OUT = ROOT / "webapp" / "data"
SRC = ROOT / "sources" / "pkd"

SUBCLASS = re.compile(r"^\d{2}\.\d{2}\.[A-Z]$")
CODE_LINE = re.compile(r"^(\d{2}\.\d{2}\.[A-Z])\s*$")
INCL = re.compile(r"^Podklasa ta obejmuje.*$", re.I)
EXCL = re.compile(r"^Podklasa ta nie obejmuje.*$", re.I)
NOISE = re.compile(r"^(Wyjaśnienia do PKD 2025|Struktura klasyfikacji|SEKCJA [A-U]|\d+)\s*$")


SECTION_ROW = re.compile(r"^SEKCJA\s+([A-U])$", re.I)


def from_structure(path: Path):
    """Коды, названия, секции и разделы — из официальной структуры PKD 2025.

    Секции ОБЯЗАНЫ приходить отсюда, а не из файла ключей: там одной секции
    PKD 2007 соответствует несколько секций PKD 2025 (у C их три: A, C, S),
    и «текущая секция» при потоковом чтении застревает на последней строке.
    Из-за этого электромонтаж 43.21.Z оказывался в секции «культура и спорт».
    """
    df = pd.read_excel(path, sheet_name=0, header=0, dtype=str)
    df.columns = ["dzial", "grupa", "klasa", "podklasa", "name"]

    names, sections, div_names = {}, {}, {}
    section, section_name = None, None
    for r in df.itertuples(index=False):
        col0 = str(r.dzial).strip() if r.dzial and str(r.dzial) != "nan" else ""
        m = SECTION_ROW.match(col0)
        if m:                                        # «SEKCJA F» + название в колонке класса
            section = m.group(1).upper()
            section_name = str(r.klasa).strip()
            continue
        name = str(r.name).strip() if r.name and str(r.name) != "nan" else ""
        code = str(r.podklasa).strip() if r.podklasa and str(r.podklasa) != "nan" else ""
        # у разделов с единственным классом (31 «Produkcja mebli», 75 «weterynaryjna»)
        # GUS кладёт раздел, группу, класс и подкласс в ОДНУ строку: если после
        # раздела делать continue, такие подклассы теряются — их было девять
        if re.fullmatch(r"\d{2}", col0):             # дział
            div_names[col0] = name
            if not SUBCLASS.match(code):
                continue
        if SUBCLASS.match(code):
            names[code] = name
            sections[code] = (section, section_name)
    return names, sections, div_names


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

    print("XLS: структура — коды, названия, секции…")
    names, sections, divs = from_structure(Path(args.structure))
    keys = keys_from_xls(Path(args.keys))
    print(f"  подклассов PKD 2025: {len(names)}, секций: "
          f"{len({s for s, _ in sections.values()})}, старых кодов в ключах: {len(keys)}")

    print("PDF: пояснения…")
    expl = from_pdf(Path(args.pdf), set(names))
    print(f"  с пояснениями: {sum(1 for v in expl.values() if v['includes'])}")

    validity = bir_validity()
    if validity:
        print(f"  даты действия из словаря REGON: {len(validity)} кодов")

    records = []
    for code in sorted(names):
        sec, sec_name = sections.get(code, (None, None))
        e = expl.get(code, {})
        v = validity.get(code) or {}
        records.append({
            "valid_from": v.get("from"),
            "valid_to": v.get("to"),
            "code": code,
            "name": names[code],
            "section": sec,
            "section_name": sec_name,
            "division": code[:2],
            "division_name": divs.get(code[:2]),
            "includes": merge_wrapped(e.get("includes", [])),
            "excludes": merge_wrapped(e.get("excludes", [])),
        })

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "pkd.json").write_text(json.dumps(
        {"version": "PKD 2025", "source": "GUS, rozporządzenie RM z 18.12.2024 (Dz. U. poz. 1936)",
         "transition_until": "2026-12-31", "codes": records},
        ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "pkd_keys.json").write_text(json.dumps(
        {"note": "PKD 2007 -> PKD 2025, официальные ключи GUS", "map": keys},
        ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\nЗаписей: {len(records)}, из них с описанием: "
          f"{sum(1 for r in records if r['includes'])}")
    print(f"-> {OUT / 'pkd.json'} ({(OUT / 'pkd.json').stat().st_size // 1024} КБ)")
    print(f"-> {OUT / 'pkd_keys.json'}")


if __name__ == "__main__":
    main()
