"""Шаг 6a: подготовка заданий на FAQ — без обращений к API.

Собирает по каждому кластеру источники (гайд + ставки + KSeF/ошибки ZUS) и
раскладывает батчами в chat_analysis/faq_tasks/. Дальше заготовки пишет
ассистент прямо в сессии (подписка), а не платный вызов из скрипта:
API-ключ в этом проекте один на прод, и его баланс лучше не жечь оффлайн-задачами.

    python tools/faq_prepare.py [--limit 60] [--batch 10] [--excerpt 1200]

Проверка написанного — tools/faq_validate.py, публикация — faq_build.py --publish.
"""
import argparse
import json
import sys
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "chat_analysis"
WEB = ROOT / "webapp"
TASKS = DIR / "faq_tasks"
YEAR = 2026


def select(clusters, limit, min_size):
    real = [c for c in clusters
            if c.get("cohesion", 1) >= 0.5 and c["size"] >= min_size
            and (c.get("intent") or "").split("|")[0].strip() not in
            ("обсуждение-без-вопроса", "оффтоп")
            and c.get("stage") != "X-не-про-JDG"]
    real.sort(key=lambda c: -(c["size"] * int(c.get("pain") or 1)))
    return real[:limit]


def build_sources(clusters, excerpt):
    docs = json.loads((WEB / "data" / "search.json").read_text(encoding="utf-8"))
    vec = TfidfVectorizer(ngram_range=(1, 2), min_df=1, max_features=80000)
    M = vec.fit_transform([d["title"] + " " + d["text"] for d in docs])
    rates = (WEB / "data" / "rates_2026.json").read_text(encoding="utf-8")
    years = (WEB / "data" / "rates_years.json").read_text(encoding="utf-8")
    ksef = (WEB / "ksef.json").read_text(encoding="utf-8")
    zus = (WEB / "zus_errors.json").read_text(encoding="utf-8")
    out = []
    for c in clusters:
        q = " ".join(c["terms"]) + " " + " ".join(e["text"][:200] for e in c["examples"][:3])
        sims = cosine_similarity(vec.transform([q]), M)[0]
        srcs = [{"id": docs[i]["id"], "title": docs[i]["title"],
                 "text": docs[i]["text"][:excerpt]} for i in sims.argsort()[::-1][:3]]
        srcs.append({"id": "rates", "title": f"Ставки и лимиты {YEAR}", "text": rates})
        srcs.append({"id": "rates_years", "title": "Ставки прошлых лет", "text": years})
        topic = (c.get("label", "") + " " + (c.get("stage") or "")).lower()
        if any(k in topic for k in ("vat", "ksef", "фактур", "налог", "pit")):
            srcs.append({"id": "ksef", "title": "KSeF: проверенные факты",
                         "text": ksef[:2500]})
        if any(k in topic for k in ("zus", "взнос", "деклараци")):
            srcs.append({"id": "zus_errors", "title": "Коды ошибок ZUS",
                         "text": zus[:2000]})
        out.append(srcs)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--min-size", type=int, default=150)
    ap.add_argument("--batch", type=int, default=10)
    ap.add_argument("--excerpt", type=int, default=1200)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    clusters = json.loads((DIR / "clusters_labeled.json").read_text(encoding="utf-8"))
    sel = select(clusters, args.limit, args.min_size)
    srcs = build_sources(sel, args.excerpt)

    TASKS.mkdir(parents=True, exist_ok=True)
    for f in TASKS.glob("*"):
        f.unlink()
    # для валидации чисел храним ПОЛНЫЕ тексты источников: в батчи идут выдержки,
    # и цифра из конца статьи иначе выглядела бы выдуманной
    full = {d["id"]: d["title"] + " " + d["text"] for d in json.loads(
        (WEB / "data" / "search.json").read_text(encoding="utf-8"))}
    json.dump([{"cluster": c["id"],
                "sources": [{"id": s["id"], "title": s["title"],
                             "text": full.get(s["id"], s["text"])} for s in ss]}
               for c, ss in zip(sel, srcs)],
              (DIR / "faq_sources.json").open("w", encoding="utf-8"),
              ensure_ascii=False)

    # общие для всех кластеров источники печатаем один раз на батч, а не 10
    shared_ids = {"rates", "rates_years", "ksef", "zus_errors"}
    shared = {s["id"]: s for ss in srcs for s in ss if s["id"] in shared_ids}

    S = ["# Общие источники для всех батчей\n"]
    for s in shared.values():
        S.append(f"\n**[{s['id']}] {s['title']}**\n{s['text']}")
    (TASKS / "_shared.md").write_text("\n".join(S), encoding="utf-8")

    for b in range(0, len(sel), args.batch):
        part = list(zip(sel, srcs))[b:b + args.batch]
        L = [f"# Батч {b // args.batch + 1}: кластеры {b + 1}-{b + len(part)}\n",
             "Ответ пишем ТОЛЬКО по источникам ниже. Нет данных — пустой ответ "
             "и запись в missing, а не догадка.\n",
             "Общие источники (ставки, KSeF, коды ошибок ZUS) — в `_shared.md`, "
             "они одни на все батчи.\n"]
        for c, ss in part:
            ex = "\n".join(f"  - {e['text'][:200]}" for e in c["examples"][:4])
            L.append(f"\n---\n\n## cluster {c['id']} · {c['label']}\n")
            L.append(f"спрос {c['size']} вопросов · этап {c.get('stage')} · "
                     f"боль {c.get('pain')}/5 · покрытие гайдом {c.get('covered')} · "
                     f"зависит от года: {'да' if c.get('volatile') else 'нет'}")
            L.append(f"\nнаш вердикт по дырам: {c.get('gap') or '—'}")
            L.append(f"\nживые формулировки из чата:\n{ex}")
            extra = [s["id"] for s in ss if s["id"] in shared_ids]
            L.append(f"\nИСТОЧНИКИ (плюс общие: {', '.join(extra) or '—'}):")
            for s in ss:
                if s["id"] not in shared_ids:
                    L.append(f"\n**[{s['id']}] {s['title']}**\n{s['text']}")
        (TASKS / f"batch_{b // args.batch + 1:02d}.md").write_text(
            "\n".join(L), encoding="utf-8")

    total = sum((TASKS / f.name).stat().st_size for f in TASKS.glob("*.md"))
    print(f"Кластеров: {len(sel)} (спрос {sum(c['size'] for c in sel):,} вопросов)")
    print(f"Батчей: {len(list(TASKS.glob('*.md')))}, всего {total // 1024} КБ")
    print(f"-> {TASKS}")


if __name__ == "__main__":
    main()
