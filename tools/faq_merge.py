"""Влить очередной батч заготовок в faq_draft.json (дедуп по номеру кластера).

    python tools/faq_merge.py chat_analysis/faq_batch2.json

Существующая запись с тем же cluster перезаписывается — так можно переписать
неудачную заготовку, не трогая остальные.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DRAFT = ROOT / "chat_analysis" / "faq_draft.json"


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        sys.exit("Укажи файл батча")
    new = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    draft = json.loads(DRAFT.read_text(encoding="utf-8")) if DRAFT.exists() else []
    by_cluster = {r["cluster"]: r for r in draft}
    added = sum(1 for r in new if r["cluster"] not in by_cluster)
    for r in new:
        by_cluster[r["cluster"]] = r
    out = list(by_cluster.values())
    DRAFT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Добавлено {added}, перезаписано {len(new) - added}, всего в черновике {len(out)}")


if __name__ == "__main__":
    main()
