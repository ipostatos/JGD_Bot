"""Золотая фикстура пояснений: эталон, сверенный с официальным XLS.

Проверяем не «как сейчас собралось», а два свойства, ради которых Phase 0.1
и затевалась:

  1. каждая строка пояснения дословно есть в ячейке ЭТОГО кода в файле GUS —
     значит текст не выдуман, не пересказан и не склеен из соседних;
  2. в пояснениях нет ни одной строки из ячеек соседних уровней — значит
     заголовок группы больше не приезжает в исключения подкласса.

Набор кодов подобран по типам вёрстки и по известным поломкам, а не по
отраслям: 01.19.Z раньше содержал заголовок следующей группы, 43.32.Z терял
собственные исключения от позиционной эвристики.

    python tools/pkd_golden.py          # пересобрать эталон
    python tools/pkd_golden.py --check  # только проверить, ничего не писать

Вход:  sources/pkd/Wyjasnienia_PKD_2025.xls, собранный справочник
Выход: tests/fixtures/pkd/golden_explanations.json
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SRC = ROOT / "sources" / "pkd" / "Wyjasnienia_PKD_2025.xls"
DST = ROOT / "tests" / "fixtures" / "pkd" / "golden_explanations.json"

CODES = {
    "01.19.Z": "раньше содержал заголовок следующей группы",
    "01.50.Z": "раньше содержал обрывок колонтитула «SEKCJA A»",
    "43.32.Z": "позиционная эвристика уничтожала его собственные исключения",
    "14.10.Z": "правила уровня раздела рядом с собственными исключениями",
    "31.00.Z": "раздел из одного класса: код стоит в одной строке с разделом",
    "75.00.Z": "то же самое, ветеринария",
    "62.10.B": "несколько ссылок на другие подклассы",
    "96.99.Z": "ссылки словами, без кода",
    "47.91.Z": "длинный список включений с вводной фразой",
    "52.10.B": "ссылка на класс, а не на подкласс",
    "10.71.Z": "первый подкласс своей группы",
    "95.31.A": "последний подкласс своего класса",
    "88.91.Z": "короткое пояснение",
    "25.53.Z": "термин совпадает с названием соседнего уровня",
    "86.95.Z": "несколько списков исключений подряд",
    "56.21.Z": "название подкласса длинное и переносится",
}


def source_cells() -> dict:
    """Код уровня -> его собственная ячейка пояснения из файла GUS."""
    import pandas as pd
    df = pd.read_excel(SRC, sheet_name=0, header=None, dtype=str)
    rows = [(str(a).strip() if a and str(a) != "nan" else "",
             str(b) if b and str(b) != "nan" else "")
            for a, b in df.itertuples(index=False)]
    out = {}
    for i, (code, _) in enumerate(rows):
        if not code:
            continue
        nxt = rows[i + 1] if i + 1 < len(rows) else ("", "")
        if not nxt[0]:
            out[re.sub(r"^(SEKCJA|DZIAŁ)\s+", "", code).upper()] = nxt[1]
    return out


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" ;.,:").lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import pkd
    pkd.index.cache_clear()
    idx = pkd.index()
    cells = source_cells()

    golden, problems = {}, []
    for code, why in CODES.items():
        rec = idx.codes[code]
        own = norm(cells.get(code, ""))
        foreign = {lvl: norm(cells.get(rec[key], ""))
                   for lvl, key in (("group", "group"), ("class", "class"),
                                    ("division", "division"), ("section", "section"))
                   if cells.get(rec[key])}
        includes = list(rec["includes"])
        excludes = [x["raw"] for x in rec["excludes"]]

        for line in includes + excludes:
            if norm(line) not in own:
                problems.append(f"{code}: строки нет в своей ячейке -> {line[:70]}")
            for lvl, text in foreign.items():
                if text and norm(line) in text and norm(line) not in own:
                    problems.append(f"{code}: строка из уровня {lvl} -> {line[:70]}")

        golden[code] = {
            "why": why,
            "expected_includes": includes,
            "expected_excludes": excludes,
            "expected_target_codes": sorted({t for x in rec["excludes"]
                                             for t in x["target_codes"]}),
            "review_status": "verified_against_own_source_cell",
        }

    print(f"Кодов в эталоне: {len(golden)}")
    print(f"Строк включений: {sum(len(v['expected_includes']) for v in golden.values())}, "
          f"исключений: {sum(len(v['expected_excludes']) for v in golden.values())}")
    if problems:
        print("\nПРОБЛЕМЫ:")
        for p in problems[:20]:
            print("  ", p)
        sys.exit(1)
    print("Все строки дословно найдены в ячейке своего кода, чужих строк нет.")

    if args.check:
        return
    DST.write_text(json.dumps(
        {"note": "Эталон пояснений, сверенный с Wyjasnienia_PKD_2025.xls. "
                 "Пересобирать только вместе с проверкой tools/pkd_golden.py --check.",
         "source": "GUS, Wyjasnienia_PKD_2025.xls",
         "codes": golden}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"-> {DST}")


if __name__ == "__main__":
    main()
