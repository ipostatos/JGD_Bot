"""Шаг 5: профиль эксперта чата — стиль, темы, свежесть, корпус пар Q&A.

Отвечает на вопрос «можно ли сделать бота, который отвечает как N»: сколько
пригодных пар вопрос-ответ, насколько ответы самодостаточны (а не «см. выше»),
какая доля знаний протухает, как звучит голос.

    python tools/expert_profile.py --name "Оленька Корнева"
    python tools/expert_profile.py --uid 123456789 --dump

Выход: chat_analysis/experts/<uid>/{profile.json, qa.jsonl, best.md}
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "chat_analysis"
SRC = DIR / "messages.jsonl"

URL = re.compile(r"https?://\S+")
NUM = re.compile(r"\d")
# «сам по себе непонятен»: ответ-указатель без контекста вопроса
DEICTIC = re.compile(r"^(да|нет|ага|именно|верно|наоборот|там же|см\.|выше|ниже|как писал)", re.I)
PL_TERM = re.compile(r"(zus|vat|ksef|pit|ceidg|dra|jdg|ryczał|liniow|ulga|składk|"
                     r"faktur|urząd|skarbow|zaliczk|pobyt|wniosek)", re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", help="имя как в экспорте (подстрока)")
    ap.add_argument("--uid")
    ap.add_argument("--min-answer", type=int, default=40)
    ap.add_argument("--dump", action="store_true", help="выгрузить полный корпус qa.jsonl")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not args.name and not args.uid:
        sys.exit("Нужен --name или --uid")

    labeled = {c["id"]: c for c in json.loads(
        (DIR / "clusters_labeled.json").read_text(encoding="utf-8"))}
    assign = {}
    with (DIR / "assignments.jsonl").open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            assign[r["qid"]] = r["cluster"]

    # 1) находим uid и собираем id вопросов, на которые он отвечал
    uid, name = args.uid, ""
    texts: dict[int, dict] = {}          # id → сообщение (вопросы-родители)
    mine: list[dict] = []
    if not uid:
        # тёзок в чате хватает — берём самого активного, остальных показываем
        cand, names = Counter(), {}
        with SRC.open(encoding="utf-8") as f:
            for line in f:
                m = json.loads(line)
                if args.name.lower() in (m["from"] or "").lower():
                    cand[m["uid"]] += 1
                    names[m["uid"]] = m["from"]
        if not cand:
            sys.exit("Не нашёл такого автора")
        if len(cand) > 1:
            print("Совпало несколько, беру самого активного:")
            for u, n in cand.most_common(5):
                print(f"  uid {u:>12} | {names[u][:30]:30} | сообщений {n:,}")
        uid = cand.most_common(1)[0][0]
    name = ""
    with SRC.open(encoding="utf-8") as f:
        for line in f:
            m = json.loads(line)
            if m["uid"] == uid and m["from"]:
                name = m["from"]

    parents = set()
    with SRC.open(encoding="utf-8") as f:
        for line in f:
            m = json.loads(line)
            if m["uid"] == uid and m.get("reply_to"):
                parents.add(m["reply_to"])
    with SRC.open(encoding="utf-8") as f:
        for line in f:
            m = json.loads(line)
            if m["id"] in parents:
                texts[m["id"]] = m
    with SRC.open(encoding="utf-8") as f:
        for line in f:
            m = json.loads(line)
            if m["uid"] == uid and m["kind"] == "msg":
                mine.append(m)

    answers = [m for m in mine if m.get("reply_to") in texts
               and m["n"] >= args.min_answer
               and texts[m["reply_to"]]["uid"] != uid]
    pairs = [{"q": texts[a["reply_to"]]["text"], "a": a["text"],
              "date": a["date"][:10], "react": a.get("react", 0),
              "cluster": assign.get(a["reply_to"]),
              "topic": (labeled.get(assign.get(a["reply_to"]), {}) or {}).get("label"),
              "volatile": (labeled.get(assign.get(a["reply_to"]), {}) or {}).get("volatile")}
             for a in answers]
    good = [p for p in pairs if len(p["a"]) >= 120 and not DEICTIC.match(p["a"])
            and len(p["q"]) >= 40]

    lens = sorted(len(p["a"]) for p in pairs) or [0]
    years = Counter(p["date"][:4] for p in pairs)
    topics = Counter(p["topic"] for p in pairs if p["topic"])
    # знаем тему не у всех ответов — долю «протухающих» считаем от известных
    known = [p for p in pairs if p["volatile"] is not None]
    volatile = sum(1 for p in known if p["volatile"])
    prof = {
        "uid": uid, "name": name,
        "messages_total": len(mine), "answers": len(pairs),
        "pairs_usable": len(good),
        "usable_share": round(len(good) / max(len(pairs), 1), 2),
        "len_median": lens[len(lens) // 2], "len_p90": lens[int(len(lens) * 0.9)],
        "short_pointer_share": round(sum(1 for p in pairs if len(p["a"]) < 80)
                                     / max(len(pairs), 1), 2),
        "long_answer_share": round(sum(1 for p in pairs if len(p["a"]) > 300)
                                   / max(len(pairs), 1), 2),
        "with_link": round(sum(1 for p in pairs if URL.search(p["a"]))
                           / max(len(pairs), 1), 2),
        "with_numbers": round(sum(1 for p in pairs if NUM.search(p["a"]))
                              / max(len(pairs), 1), 2),
        "with_pl_terms": round(sum(1 for p in pairs if PL_TERM.search(p["a"]))
                               / max(len(pairs), 1), 2),
        "volatile_share": round(volatile / max(len(known), 1), 2),
        "topic_known_share": round(len(known) / max(len(pairs), 1), 2),
        "by_year": dict(sorted(years.items())),
        "top_topics": topics.most_common(8),
    }

    outdir = DIR / "experts" / str(uid)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "profile.json").write_text(
        json.dumps(prof, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.dump:
        with (outdir / "qa.jsonl").open("w", encoding="utf-8") as w:
            for p in good:
                w.write(json.dumps(p, ensure_ascii=False) + "\n")
    best = sorted(good, key=lambda p: -(p["react"] * 10 + len(p["a"])))[:20]
    (outdir / "best.md").write_text(
        f"# {name} — 20 показательных ответов\n\n" + "\n\n---\n\n".join(
            f"**{p['date']} · {p['topic']}**\n\nВ: {p['q'][:500]}\n\nО: {p['a'][:900]}"
            for p in best), encoding="utf-8")

    print(f"{name} (uid {uid})")
    for k, v in prof.items():
        if k not in ("uid", "name", "by_year", "top_topics"):
            print(f"  {k:22} {v}")
    print("  по годам:", ", ".join(f"{y}: {n}" for y, n in prof["by_year"].items()))
    print("  темы:", ", ".join(f"{t} ({n})" for t, n in prof["top_topics"][:5]))
    print(f"-> {outdir}")


if __name__ == "__main__":
    main()
