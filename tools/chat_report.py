"""Шаг 4: сводный отчёт по чату — паттерны, этапы, дыры гайда, эксперты.

    python tools/chat_report.py

Вход:  chat_analysis/{clusters_labeled.json, experts.json, assignments.jsonl,
       messages.jsonl, stats.json}
Выход: chat_analysis/report.md  (лежит вне git: внутри тексты участников)
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "chat_analysis"
NOISE_INTENTS = {"обсуждение-без-вопроса", "оффтоп"}
MIN_COHESION = 0.5  # ниже — «мешок» непохожих вопросов, а не паттерн (медиана 0.71)


def primary_intent(c):
    """Модель иногда отдаёт «процедура | личный-кейс» — берём первый."""
    return (c.get("intent") or "").split("|")[0].strip()


def load_all():
    clusters = json.loads((DIR / "clusters_labeled.json").read_text(encoding="utf-8"))
    for c in clusters:
        c["intent"] = primary_intent(c)
    experts = json.loads((DIR / "experts.json").read_text(encoding="utf-8"))
    stats = json.loads((DIR / "stats.json").read_text(encoding="utf-8"))
    assign = {}
    with (DIR / "assignments.jsonl").open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            assign[r["qid"]] = r["cluster"]
    return clusters, experts, stats, assign


def expert_topics(assign, clusters):
    """Кто в каких темах отвечает. Не «где больше ответов» — где эксперт
    представлен непропорционально своей общей активности (lift)."""
    by_id = {c["id"]: c for c in clusters}
    asker: dict[int, str] = {}
    spec: dict[str, Counter] = defaultdict(Counter)
    src = DIR / "messages.jsonl"
    with src.open(encoding="utf-8") as f:
        for line in f:
            m = json.loads(line)
            if m["id"] in assign:
                asker[m["id"]] = m["uid"]
    with src.open(encoding="utf-8") as f:
        for line in f:
            m = json.loads(line)
            rid = m.get("reply_to")
            if rid in assign and m["kind"] == "msg" and m["n"] >= 40 \
                    and m["uid"] != asker.get(rid):
                c = by_id.get(assign[rid])
                if c and c.get("label") and c.get("intent") not in NOISE_INTENTS \
                        and c.get("cohesion", 1) >= MIN_COHESION:
                    spec[m["uid"]][c["label"]] += 1

    topic_total = Counter()
    for cnt in spec.values():
        topic_total.update(cnt)
    grand = sum(topic_total.values()) or 1
    lift: dict[str, list] = {}
    for uid, cnt in spec.items():
        mine = sum(cnt.values()) or 1
        scored = [(t, n, (n / mine) / (topic_total[t] / grand))
                  for t, n in cnt.items() if n >= 8]
        scored.sort(key=lambda x: -x[2] * (x[1] ** 0.5))
        lift[uid] = scored[:3]
    return lift


def trend(c, base_recent):
    y = c["by_year"]
    total = sum(y.values()) or 1
    recent = (y.get("2025", 0) + y.get("2026", 0)) / total
    r = recent / base_recent if base_recent else 1
    return ("растёт" if r > 1.35 else "угасает" if r < 0.65 else "ровно"), r


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    clusters, experts, stats, assign = load_all()
    real = [c for c in clusters if c.get("intent") not in NOISE_INTENTS
            and c.get("stage") != "X-не-про-JDG"
            and c.get("cohesion", 1) >= MIN_COHESION]
    vague = [c for c in clusters if c.get("cohesion", 1) < MIN_COHESION]
    q_real = sum(c["size"] for c in real)
    q_all = sum(c["size"] for c in clusters)

    all_years = Counter()
    for c in clusters:
        for y, n in c["by_year"].items():
            all_years[y] += n
    base_recent = (all_years["2025"] + all_years["2026"]) / max(sum(all_years.values()), 1)

    spec = expert_topics(assign, clusters)
    L = []
    add = L.append

    add("# Чат «ИП в Польше» — анализ спроса\n")
    add(f"Экспорт от 2026-07-22, {stats['total_records']:,} записей, "
        f"{stats['unique_authors_meaningful']:,} авторов, "
        f"{min(stats['by_year'])}–{max(stats['by_year'])}.\n")
    add("Считаем **спрос**, а не истину: ответы участников в разметку не попадали, "
        "потому что ставки ZUS, лимит VAT и правила KSeF менялись — ответ 2022 года "
        "сегодня может быть вреден.\n")

    add("## 1. Воронка\n")
    add("| | шт. | доля |\n|---|---:|---:|")
    k = stats["kinds"]
    add(f"| Сообщений содержательных | {k['msg']:,} | 100% |")
    add(f"| Из них вопросов | {q_all:,} | {q_all / k['msg']:.0%} |")
    add(f"| Вопросов в размытых кластерах (не паттерн) | {sum(c['size'] for c in vague):,} | "
        f"{sum(c['size'] for c in vague) / q_all:.0%} от вопросов |")
    add(f"| Вопросов по делу (чистые паттерны) | {q_real:,} | {q_real / q_all:.0%} от вопросов |")
    answered = sum(c["size"] * c["answered_rate"] for c in real)
    add(f"| На вопрос кто-то ответил | {answered:,.0f} | {answered / q_real:.0%} |")
    add(f"\n**Каждый второй вопрос по делу остаётся без ответа** — {q_real - answered:,.0f} "
        f"штук за 5 лет. Это и есть ниша бота.\n")

    add("## 2. На каком этапе спрашивают\n")
    stages = Counter()
    for c in real:
        stages[c.get("stage") or "?"] += c["size"]
    add("| Этап | вопросов | доля |\n|---|---:|---:|")
    for s, n in sorted(stages.items()):
        add(f"| {s} | {n:,} | {n / q_real:.0%} |")

    add("\n## 3. Топ-25 паттернов\n")
    add("| Вопросов | Тема | Этап | Ответили | Гайд | Боль | Тренд |\n"
        "|---:|---|---|---:|---|---:|---|")
    for c in sorted(real, key=lambda c: -c["size"])[:25]:
        t, _ = trend(c, base_recent)
        add(f"| {c['size']:,} | {c['label']} | {(c.get('stage') or '')[:22]} | "
            f"{c['answered_rate']:.0%} | {c.get('covered')} | {c.get('pain')} | {t} |")

    add("\n## 4. Дыры в гайде (приоритет = объём × боль)\n")
    gaps = [c for c in real if c.get("covered") in ("no", "partial")]
    gaps.sort(key=lambda c: -(c["size"] * (int(c.get("pain") or 1))))
    add("| Приоритет | Вопросов | Боль | Тема | Чего не хватает |\n|---:|---:|---:|---|---|")
    for i, c in enumerate(gaps[:25], 1):
        add(f"| {i} | {c['size']:,} | {c.get('pain')} | {c['label']} | "
            f"{(c.get('gap') or '')[:120]} |")

    add("\n## 5. Что растёт и что умерло\n")
    scored = [(c, *trend(c, base_recent)) for c in real if c["size"] >= 120]
    up = sorted(scored, key=lambda x: -x[2])[:10]
    down = sorted(scored, key=lambda x: x[2])[:10]
    add("**Растущие темы (доля 2025-2026 выше средней):**\n")
    add("| Тема | Вопросов | Индекс | Гайд |\n|---|---:|---:|---|")
    for c, _, r in up:
        add(f"| {c['label']} | {c['size']:,} | ×{r:.1f} | {c.get('covered')} |")
    add("\n**Угасающие:**\n")
    add("| Тема | Вопросов | Индекс |\n|---|---:|---:|")
    for c, _, r in down:
        add(f"| {c['label']} | {c['size']:,} | ×{r:.1f} |")

    add("\n## 6. Где чат отвечает хуже всего\n")
    add("Большие темы с низкой долей ответов — там пользователь остаётся один.\n")
    add("| Тема | Вопросов | Ответили | Боль | Гайд |\n|---|---:|---:|---:|---|")
    for c in sorted([c for c in real if c["size"] >= 150],
                    key=lambda c: c["answered_rate"])[:12]:
        add(f"| {c['label']} | {c['size']:,} | {c['answered_rate']:.0%} | "
            f"{c.get('pain')} | {c.get('covered')} |")

    add("\n## 7. Устаревание ответов\n")
    vol = sum(c["size"] for c in real if c.get("volatile"))
    add(f"В **{vol / q_real:.0%}** вопросов по делу верный ответ зависит от года "
        f"и текущих правил ({vol:,} из {q_real:,}). Именно поэтому ответы из чата "
        f"нельзя переносить в FAQ как есть — только как указатель на тему.\n")

    add("## 8. Эксперты чата\n")
    add("Вес: помощь разным людям и явные «спасибо» важнее вала сообщений.\n")
    add("| # | Кто | Ответов | Людям помог | Спасибо | Реакций+ | Средняя длина | Активен | Темы |\n"
        "|---:|---|---:|---:|---:|---:|---:|---|---|")
    for i, e in enumerate(experts[:20], 1):
        topics = ", ".join(f"{t} (×{lf:.1f})" for t, _, lf in spec.get(e["uid"], []))
        add(f"| {i} | {e['name'][:26]} | {e['answers']:,} | {e['people_helped']:,} | "
            f"{e['thanks']} | {e['positive_reactions']} | {e['avg_len']} | "
            f"{e['active']} | {topics[:110]} |")

    add("\n## 9. Данные\n")
    add("- `clusters_labeled.json` — 159 кластеров с метками, примерами и вердиктом по гайду\n"
        "- `experts.json` — 251 автор с ≥30 ответами\n"
        "- `messages.jsonl` — очищенный корпус (166 МБ), `assignments.jsonl` — вопрос→кластер\n"
        "- Папка `chat_analysis/` в `.gitignore`: внутри тексты и имена участников\n")

    (DIR / "report.md").write_text("\n".join(L), encoding="utf-8")
    print(f"-> {DIR / 'report.md'}")
    print("\n".join(L[:40]))


if __name__ == "__main__":
    main()
