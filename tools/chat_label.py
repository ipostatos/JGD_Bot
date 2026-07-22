"""Шаг 3: разметка кластеров вопросов через Haiku — тема, этап, покрытие гайдом.

Словарь этапов из chat_analyze.py — грубая эвристика (путает «карту побыта»
с выбором формы). Здесь каждый кластер смотрит модель: даёт человеческую метку,
этап пути, тип запроса и вердикт по покрытию — с выдержками из гайда на руках.

Ответы участников модели НЕ показываем: нас интересует спрос, а не «правда»
из чата (законы менялись, ответ 2022 года сегодня может быть вреден).

    python tools/chat_label.py [--limit 200] [--concurrency 8]

Вход:  chat_analysis/clusters.json + webapp/data/search.json
Выход: chat_analysis/clusters_labeled.json
"""
import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import httpx
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "chat_analysis"
MODEL = "claude-haiku-4-5-20251001"

STAGES = """0-выбор-формы, 1-легализация-право-открыть, 2-регистрация-CEIDG,
3-первые-месяцы-ulga-na-start, 4-налоги-и-PIT, 5-ZUS-взносы-льготы-больничные,
6-VAT-фактуры-KSeF, 7-бухгалтерия-банки-сервисы, 8-изменения-и-закрытие,
9-клиенты-договоры-деньги, X-не-про-JDG"""

PROMPT = f"""Ты аналитик сообщества русскоязычных ИП (JDG) в Польше. Тебе дают
кластер похожих вопросов из чата и выдержки из гайда, который мы ведём.

Задача — описать СПРОС, а не дать ответ. Ответы участников тебе не показывают
намеренно: законы (ставки ZUS, лимит VAT, KSeF) менялись, и «правда» из чата
может быть устаревшей.

Верни строго JSON-объект:
{{"label": "тема кластера, 4-8 слов, по-русски",
  "stage": "один из: {STAGES}",
  "intent": "один из: процедура-как-сделать | выбор-варианта | толкование-закона |
             поиск-контактов-и-сервисов | личный-кейс | обсуждение-без-вопроса | оффтоп",
  "covered": "yes | partial | no — покрывает ли гайд этот вопрос",
  "gap": "чего конкретно не хватает в гайде, одна фраза; пусто если covered=yes",
  "volatile": true/false — зависит ли верный ответ от года и текущих ставок/правил,
  "pain": "1-5, насколько человек в этот момент рискует ошибиться дорого"}}"""


def candidates(clusters, top=3):
    docs = json.loads((ROOT / "webapp" / "data" / "search.json").read_text(encoding="utf-8"))
    vec = TfidfVectorizer(ngram_range=(1, 2), min_df=1, max_features=80000)
    M = vec.fit_transform([d["title"] + " " + d["text"] for d in docs])
    out = []
    for c in clusters:
        q = " ".join(c["terms"]) + " " + " ".join(e["text"][:200] for e in c["examples"][:3])
        sims = cosine_similarity(vec.transform([q]), M)[0]
        idx = sims.argsort()[::-1][:top]
        out.append([{"title": docs[i]["title"], "id": docs[i]["id"],
                     "score": round(float(sims[i]), 3),
                     "excerpt": docs[i]["text"][:900]} for i in idx])
    return out


def user_msg(c, cands):
    ex = "\n".join(f"- ({e['date']}, ответов {e['answers']}) {e['text'][:300]}"
                   for e in c["examples"][:8])
    guide = "\n\n".join(f"[{g['title']}] {g['excerpt']}" for g in cands)
    years = ", ".join(f"{y}: {n}" for y, n in c["by_year"].items())
    return (f"КЛАСТЕР #{c['id']}: {c['size']} вопросов\n"
            f"Ключевые слова: {', '.join(c['terms'])}\n"
            f"По годам: {years}\n"
            f"Доля вопросов, на которые вообще ответили: {c['answered_rate']:.0%}\n\n"
            f"ПРИМЕРЫ ВОПРОСОВ:\n{ex}\n\n"
            f"САМЫЕ БЛИЗКИЕ РАЗДЕЛЫ ГАЙДА:\n{guide}")


async def label(client, key, c, cands, sem):
    async with sem:
        for attempt in range(3):
            try:
                r = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                    json={"model": MODEL, "max_tokens": 600, "system": PROMPT,
                          "messages": [{"role": "user", "content": user_msg(c, cands)}]},
                    timeout=120)
                if r.status_code == 429 or r.status_code >= 500:
                    await asyncio.sleep(3 * (attempt + 1))
                    continue
                r.raise_for_status()
                text = r.json()["content"][0]["text"]
                m = re.search(r"\{.*\}", text, re.S)
                data = json.loads(m.group(0)) if m else {}
                return {**c, **{k: data.get(k) for k in
                                ("label", "stage", "intent", "covered", "gap",
                                 "volatile", "pain")},
                        "guide_candidates": [{k: g[k] for k in ("title", "id", "score")}
                                             for g in cands]}
            except Exception as e:
                if attempt == 2:
                    return {**c, "label": None, "error": str(e)[:200]}
                await asyncio.sleep(2 * (attempt + 1))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200, help="сколько крупнейших кластеров")
    ap.add_argument("--concurrency", type=int, default=8)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        sys.exit("Нет ANTHROPIC_API_KEY")

    clusters = json.loads((DIR / "clusters.json").read_text(encoding="utf-8"))[:args.limit]
    cands = candidates(clusters)
    print(f"Размечаю {len(clusters)} кластеров, параллельно {args.concurrency}…")

    sem = asyncio.Semaphore(args.concurrency)
    done = 0
    async with httpx.AsyncClient() as client:
        tasks = [label(client, key, c, cd, sem) for c, cd in zip(clusters, cands)]
        out = []
        for fut in asyncio.as_completed(tasks):
            out.append(await fut)
            done += 1
            if done % 20 == 0:
                print(f"  …{done}/{len(clusters)}")

    out.sort(key=lambda c: -c["size"])
    (DIR / "clusters_labeled.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    bad = [c for c in out if not c.get("label")]
    print(f"\nГотово: {len(out) - len(bad)} размечено, {len(bad)} ошибок")
    print(f"-> {DIR / 'clusters_labeled.json'}")


if __name__ == "__main__":
    asyncio.run(main())
