"""Шаг 1: 351 МБ экспорта Telegram → компактный JSONL + статистика.

Потоковый разбор (ijson), в память целиком ничего не грузится.
Ничего не выбрасываем молча: мусор помечается флагами, счётчики — в stats.json,
чтобы на следующем шаге можно было менять правила фильтрации без перепарсинга.

    python tools/chat_prep.py "C:/Users/user/Desktop/JDG/ChatExport_2026-07-22/result.json"

На выходе chat_analysis/messages.jsonl:
    {id, ts, date, from, uid, reply_to, text, n, links, kind}
kind: msg | short | link_only | media | service | bot
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import ijson

ROOT = Path(__file__).resolve().parent.parent
OUTDIR = ROOT / "chat_analysis"

URL_RE = re.compile(r"https?://\S+")
POSITIVE = {"👍", "❤️", "🔥", "🙏", "💯", "🏆", "⚡", "❤", "🤝", "👏"}
# «спасибо», «+», «ага», приветствия — шум, который забивает любую кластеризацию
NOISE_RE = re.compile(
    r"^(?:\+{1,3}|спасибо\w*|благодарю|пасибо|спс|thx|ок|окей|ok|ага|угу|да|нет|"
    r"привет\w*|здравствуйте|добрый\s+(?:день|вечер|утро)|доброе\s+утро|"
    r"хорошо|понял\w*|поняла|ясно|точно|верно|согласен|согласна|"
    r"[)\(!.,\s]*|👍+|🙏+)$", re.I)


def flatten(text) -> str:
    if isinstance(text, str):
        return text
    if isinstance(text, list):
        return "".join(x if isinstance(x, str) else x.get("text", "") for x in text)
    return ""


def uid_of(raw) -> str:
    return str(raw or "").replace("user", "").replace("channel", "")


def classify(text: str, msg: dict) -> str:
    if msg.get("type") == "service":
        return "service"
    stripped = URL_RE.sub("", text).strip()
    if not text.strip():
        return "media"
    if not stripped and URL_RE.search(text):
        return "link_only"
    if NOISE_RE.match(text.strip()) or len(stripped) < 12:
        return "short"
    return "msg"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("result_json")
    ap.add_argument("--out", default=str(OUTDIR / "messages.jsonl"))
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    kinds, authors, years = Counter(), Counter(), Counter()
    names: dict[str, str] = {}
    total = 0

    with open(args.result_json, "rb") as f, out.open("w", encoding="utf-8") as w:
        for m in ijson.items(f, "messages.item", use_float=True):
            total += 1
            if total % 50_000 == 0:
                print(f"  …{total:,} сообщений")
            text = flatten(m.get("text", ""))
            kind = classify(text, m)
            uid = uid_of(m.get("from_id") or m.get("actor_id"))
            name = m.get("from") or m.get("actor") or ""
            if kind == "service":
                kinds["service"] += 1
                continue
            if name and name.lower().endswith("bot"):
                kind = "bot"
            date = str(m.get("date", ""))
            rec = {
                "id": m.get("id"), "date": date,
                "ts": int(m.get("date_unixtime", 0) or 0),
                "from": name, "uid": uid,
                "reply_to": m.get("reply_to_message_id"),
                "text": text.strip(),
                "n": len(text.strip()),
                "links": len(URL_RE.findall(text)),
                "kind": kind,
            }
            # реакции — единственный явный сигнал «сообщество согласилось»
            reacts = m.get("reactions") or []
            if reacts:
                rec["react"] = sum(int(r.get("count", 0)) for r in reacts)
                rec["react_up"] = sum(int(r.get("count", 0)) for r in reacts
                                      if (r.get("emoji") or "") in POSITIVE)
            w.write(json.dumps(rec, ensure_ascii=False) + "\n")
            kinds[kind] += 1
            years[date[:4]] += 1
            if kind == "msg":
                authors[uid] += 1
                if name:
                    names[uid] = name

    stats = {
        "total_records": total,
        "kinds": dict(kinds.most_common()),
        "by_year": dict(sorted(years.items())),
        "unique_authors_meaningful": len(authors),
        "top_authors": [{"uid": u, "name": names.get(u, ""), "msgs": c}
                        for u, c in authors.most_common(40)],
    }
    (out.parent / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\nВсего записей: {total:,}")
    for k, c in kinds.most_common():
        print(f"  {k:10} {c:>8,}  ({c / total:.1%})")
    print(f"\nАвторов с содержательными сообщениями: {len(authors):,}")
    print("По годам:", ", ".join(f"{y}: {c:,}" for y, c in sorted(years.items())))
    print(f"\n-> {out}  ({out.stat().st_size // 1024 // 1024} МБ)")


if __name__ == "__main__":
    main()
