"""Маленькая фикстура PKD для тестов и CI.

Полный справочник (data/pkd/pkd.json.gz) лежит в git и гоняется в CI целиком.
Фикстура нужна не вместо него, а для быстрых unit-тестов: маленький индекс
строится за миллисекунды, а поведение проверяется на настоящих записях GUS.

    python tools/pkd_fixture.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "pkd"
DST = ROOT / "tests" / "fixtures" / "pkd"

# по одному представителю на проверяемый случай: IT, doradztwo (жёсткий флаг VAT),
# графический дизайн (ложный флаг, который мы гасим), интерьеры, блогеры, юристы
KEEP = ["62.10.A", "62.10.B", "62.20.B", "62.90.Z", "69.10.Z", "70.20.Z",
        "73.11.Z", "74.11.Z", "74.12.Z", "74.13.Z", "90.11.Z", "90.20.C",
        "96.21.Z", "96.22.Z", "60.39.Z", "85.59.B"]


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import pkd as pkd_module                    # тот же загрузчик, что и в проде
    full = pkd_module._load(SRC / "pkd.json")
    keys = pkd_module._load(SRC / "pkd_keys.json")

    codes = [c for c in full["codes"] if c["code"] in KEEP]
    missing = set(KEEP) - {c["code"] for c in codes}
    if missing:
        sys.exit(f"В справочнике нет: {', '.join(sorted(missing))}")

    kept = {c["code"] for c in codes}
    submap = {old: v for old, v in keys["map"].items()
              if any(c in kept for c in v["to"])}

    DST.mkdir(parents=True, exist_ok=True)
    (DST / "pkd.json").write_text(json.dumps(
        {**{k: v for k, v in full.items() if k != "codes"},
         "note": "Фикстура для тестов: подмножество официального справочника, "
                 "сгенерировано tools/pkd_fixture.py",
         "codes": codes}, ensure_ascii=False, indent=1), encoding="utf-8")
    (DST / "pkd_keys.json").write_text(json.dumps(
        {**{k: v for k, v in keys.items() if k != "map"}, "map": submap},
        ensure_ascii=False, indent=1), encoding="utf-8")

    size = sum(f.stat().st_size for f in DST.glob("*.json")) // 1024
    print(f"Подклассов: {len(codes)}, ключей: {len(submap)}, всего {size} КБ")
    print(f"-> {DST}")


if __name__ == "__main__":
    main()
