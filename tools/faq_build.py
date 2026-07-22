"""Шаг 6 (ПЛАТНЫЙ, запасной путь): FAQ-заготовки через API.

⚠️ По умолчанию так НЕ делаем: api.anthropic.com — это кредиты, а ключ в проекте
один на всё, включая AI-ассистента в проде. Разовую генерацию ведёт ассистент в
сессии: tools/faq_prepare.py → батчи в chat_analysis/faq_tasks/ → tools/faq_validate.py.
Этот скрипт держим для случая, когда объём не влезает в сессию, и запускаем осознанно.
Актуален и нужен из него `--publish` (публикация вычитанного) и проверка чисел.

--- исходное описание ---
FAQ-заготовки из анализа чата — вопросы от людей, ответы от гайда.

Чат даёт только СПРОС: какой вопрос задают и в какой формулировке. Ответ
модель пишет строго по выданным источникам (гайд + rates_*.json + KSeF-факты),
а если их не хватает — обязана сказать «не хватает» вместо догадки. Ответы
участников чата в промпт не попадают вообще: 90% из них зависят от года.

    python tools/faq_build.py [--limit 60] [--min-size 150] [--concurrency 8]

Выход:
    chat_analysis/faq_draft.json   — заготовки со всеми флагами
    chat_analysis/faq_review.md    — то же для вычитки глазами
    chat_analysis/missing_topics.md — ТЗ на разделы, которых в гайде нет
    webapp/data/faq_data.json      — только после вычитки, ключом --publish
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
WEB = ROOT / "webapp"
# разметку кластеров тянет Haiku, но текст, который прочтут люди, пишем моделью посильнее
MODEL = "claude-sonnet-5"
YEAR = 2026

PROMPT = f"""Ты редактор справочника для русскоязычных ИП (JDG) в Польше. Сейчас {YEAR} год.

Тебе дают: тему из анализа чата (о чём людей реально спрашивают), несколько
живых формулировок вопроса и ИСТОЧНИКИ — разделы нашего гайда, актуальные
ставки и факты. Ответы участников чата тебе не показывают намеренно: они
могли устареть.

Жёсткие правила:
1. Ответ пиши ТОЛЬКО по выданным источникам. Ничего не добавляй по памяти —
   ни ставок, ни сроков, ни названий форм, которых нет в источниках.
2. Если источников не хватает на полноценный ответ — верни "a": "" и подробно
   опиши в "missing", чего именно недостаёт. Полработы здесь хуже, чем ничего:
   цена ошибки — штраф или отказ в ВНЖ. Не пиши в "a" фраз вида «источники не
   описывают» — читатель источников не видит, он видит только ответ.
3. Любая цифра в ответе должна дословно встречаться в источниках.
4. Вопрос сформулируй сам, обобщённо и без личных деталей — примеры из чата
   это живая речь конкретных людей, копировать её нельзя.
5. По-русски, польские термины в скобках оригиналом (składka zdrowotna).
   Кратко: 3-7 предложений, при необходимости список шагов.

Верни строго JSON:
{{"q": "канонический вопрос",
  "a": "ответ по источникам",
  "topic": "ZUS | налоги | VAT | KSeF | регистрация | легализация | банки |
            бухгалтерия | договоры | другое",
  "sources": ["id использованных источников"],
  "missing": "чего не хватило в источниках; пусто если хватило",
  "recheck": "что перепроверить перед публикацией (ставка, срок, форма); пусто если нечего",
  "confidence": "high | medium | low"}}"""


def sources_for(c, docs, vec, M, rates, ksef_facts, zus_errors):
    q = " ".join(c["terms"]) + " " + " ".join(e["text"][:200] for e in c["examples"][:3])
    sims = cosine_similarity(vec.transform([q]), M)[0]
    picked = [{"id": docs[i]["id"], "title": docs[i]["title"],
               "text": docs[i]["text"][:2600]} for i in sims.argsort()[::-1][:4]]
    picked.append({"id": "rates", "title": f"Актуальные ставки {YEAR}",
                   "text": json.dumps(rates, ensure_ascii=False)})
    topic = (c.get("label", "") + " " + (c.get("stage") or "")).lower()
    if any(k in topic for k in ("vat", "ksef", "фактур", "налог", "pit")):
        picked.append({"id": "ksef", "title": "KSeF: проверенные факты",
                       "text": ksef_facts[:3000]})
    if "zus" in topic or "взнос" in topic or "деклараци" in topic:
        picked.append({"id": "zus_errors", "title": "Коды ошибок ZUS",
                       "text": zus_errors[:3000]})
    return picked


def user_msg(c, srcs):
    ex = "\n".join(f"- {e['text'][:220]}" for e in c["examples"][:5])
    src = "\n\n".join(f"[{s['id']}] {s['title']}\n{s['text']}" for s in srcs)
    return (f"ТЕМА: {c['label']}\n"
            f"Этап пути: {c.get('stage')}\nВопросов в чате: {c['size']}, "
            f"цена ошибки {c.get('pain')}/5, ответ зависит от года: "
            f"{'да' if c.get('volatile') else 'нет'}\n"
            f"Что, по нашей же оценке, гайд не покрывает: {c.get('gap') or '—'}\n\n"
            f"ЖИВЫЕ ФОРМУЛИРОВКИ ИЗ ЧАТА (только для понимания спроса):\n{ex}\n\n"
            f"ИСТОЧНИКИ:\n{src}")


NUM_RE = re.compile(r"\d[\d\s\u00a0\u202f]*(?:[.,]\d+)?")
SPACES = re.compile(r"[\s\u00a0\u202f]")


def norm_num(s: str) -> str:
    """«240 000», «240\u00a0000» и «240000» — одно и то же число."""
    v = SPACES.sub("", s).rstrip(".,").replace(",", ".")
    try:  # «5652», «5652.00» и «5652.0» из JSON-источника — одно число
        return repr(float(v))
    except ValueError:
        return v


def unverified_numbers(answer: str, srcs) -> list[str]:
    """Числа, которых нет в источниках, — главный риск галлюцинации."""
    pool = " ".join(s["text"] for s in srcs)
    pool_nums = {norm_num(n) for n in NUM_RE.findall(pool)}
    bad = []
    for n in NUM_RE.findall(answer):
        v = norm_num(n)
        digits = SPACES.sub("", n).rstrip(".,").replace(",", ".").split(".")[0]
        # мелкие числа («до 20 числа», «6 месяцев») и годы проверять бессмысленно;
        # считаем по целой части ДО нормализации, иначе «20» → «20.0» и фильтр не срабатывает
        if len(digits) < 3 or digits in {str(y) for y in range(2018, 2031)}:
            continue
        if v in pool_nums:
            continue
        try:  # «4,9%» в тексте против «0.049» в rates_*.json — то же самое
            if repr(float(v) / 100) in pool_nums:
                continue
        except ValueError:
            pass
        bad.append(v)
    return sorted(set(bad))


async def one(client, key, c, srcs, sem, model=MODEL):
    async with sem:
        for attempt in range(3):
            try:
                r = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                    json={"model": model, "max_tokens": 2500, "system": PROMPT,
                          "messages": [{"role": "user", "content": user_msg(c, srcs)}]},
                    timeout=180)
                if r.status_code == 429 or r.status_code >= 500:
                    await asyncio.sleep(3 * (attempt + 1))
                    continue
                if r.status_code == 400:  # кончились кредиты, плохой ключ — ретраи бессмысленны
                    return {"cluster": c["id"], "q": None,
                            "error": r.json().get("error", {}).get("message", r.text[:200])}
                r.raise_for_status()
                body = r.json()
                text = "".join(b.get("text", "") for b in body["content"])
                m = re.search(r"\{.*\}", text, re.S)
                if not m:
                    raise ValueError(f"без JSON (stop={body.get('stop_reason')}): "
                                     f"{text[:160]}")
                d = json.loads(m.group(0))
                bad = unverified_numbers(d.get("a", ""), srcs)
                return {**{k: d.get(k) for k in
                           ("q", "a", "topic", "sources", "missing", "recheck",
                            "confidence")},
                        "cluster": c["id"], "demand": c["size"],
                        "stage": c.get("stage"), "pain": c.get("pain"),
                        "volatile": c.get("volatile"), "covered": c.get("covered"),
                        "gap": c.get("gap"),
                        "unverified_numbers": bad,
                        "needs_review": bool(bad or d.get("missing")
                                             or d.get("confidence") != "high")}
            except Exception as e:
                if attempt == 2:
                    return {"cluster": c["id"], "q": None, "error": str(e)[:200]}
                await asyncio.sleep(2 * (attempt + 1))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--min-size", type=int, default=150)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--publish", action="store_true",
                    help="скопировать вычитанное в webapp/data/faq_data.json")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.publish:
        draft = json.loads((DIR / "faq_draft.json").read_text(encoding="utf-8"))
        ready = [{"q": x["q"], "a": x["a"], "topic": x["topic"]}
                 for x in draft if x.get("approved")]
        if not ready:
            sys.exit("Нет записей с \"approved\": true — сначала вычитка faq_review.md")
        out = WEB / "data" / "faq_data.json"
        out.write_text(json.dumps(ready, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Опубликовано {len(ready)} записей -> {out}")
        return

    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        sys.exit("Нет ANTHROPIC_API_KEY")

    clusters = json.loads((DIR / "clusters_labeled.json").read_text(encoding="utf-8"))
    real = [c for c in clusters
            if c.get("cohesion", 1) >= 0.5 and c["size"] >= args.min_size
            and (c.get("intent") or "").split("|")[0].strip() not in
            ("обсуждение-без-вопроса", "оффтоп")
            and c.get("stage") != "X-не-про-JDG"]
    real.sort(key=lambda c: -(c["size"] * int(c.get("pain") or 1)))
    real = real[:args.limit]

    docs = json.loads((WEB / "data" / "search.json").read_text(encoding="utf-8"))
    vec = TfidfVectorizer(ngram_range=(1, 2), min_df=1, max_features=80000)
    M = vec.fit_transform([d["title"] + " " + d["text"] for d in docs])
    rates = json.loads((WEB / "data" / "rates_2026.json").read_text(encoding="utf-8"))
    ksef_facts = (WEB / "ksef.json").read_text(encoding="utf-8")
    zus_errors = (WEB / "zus_errors.json").read_text(encoding="utf-8")

    srcs = [sources_for(c, docs, vec, M, rates, ksef_facts, zus_errors) for c in real]
    print(f"Пишу {len(real)} заготовок (спрос {sum(c['size'] for c in real):,} вопросов)…")

    sem = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient() as client:
        res = await asyncio.gather(*[one(client, key, c, s, sem, args.model)
                                     for c, s in zip(real, srcs)])
    failed = [r for r in res if not (r and r.get("q"))]
    if failed:
        print(f"  не получилось: {len(failed)}, первая ошибка: "
              f"{(failed[0] or {}).get('error')}")
    res = [r for r in res if r and r.get("q")]
    if not res:
        # иначе пустой прогон затрёт прошлый результат — так и потеряли первую партию
        sys.exit("Ни одной заготовки не вышло, файлы не трогаю. "
                 "Проверь ключ и баланс API, потом запусти снова.")
    res.sort(key=lambda r: -r["demand"])
    (DIR / "faq_draft.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    # заготовка без ответа — не FAQ, а заявка на раздел гайда
    written = [r for r in res if (r.get("a") or "").strip()]
    empty = [r for r in res if not (r.get("a") or "").strip()]

    # ── файл для вычитки ───────────────────────────────────────────────────
    L = [f"# FAQ-заготовки ({len(written)} шт.) — черновик, не публиковать без вычитки\n",
         "Ответы написаны по гайду и `rates_2026.json`; чат дал только вопросы.",
         "Пометь проверенные записи `\"approved\": true` в `faq_draft.json`, ",
         "затем `python tools/faq_build.py --publish`.\n",
         f"⚠️ Требуют внимания: {sum(1 for r in written if r['needs_review'])} "
         f"из {len(written)}. Ещё {len(empty)} тем ушли в `missing_topics.md`: "
         f"источников не хватило на ответ.\n"]
    for i, r in enumerate(written, 1):
        flags = []
        if r["unverified_numbers"]:
            flags.append(f"🔴 числа не из источников: {', '.join(r['unverified_numbers'])}")
        if r.get("missing"):
            flags.append(f"🟡 не хватило источников: {r['missing']}")
        if r.get("recheck"):
            flags.append(f"🔎 перепроверить: {r['recheck']}")
        if r.get("volatile"):
            flags.append("⏳ зависит от года")
        L.append(f"\n## {i}. {r['q']}\n")
        L.append(f"*спрос {r['demand']} вопросов · этап {r.get('stage')} · "
                 f"боль {r.get('pain')}/5 · уверенность {r.get('confidence')} · "
                 f"источники: {', '.join(r.get('sources') or [])}*\n")
        L.append(r["a"] or "")
        if flags:
            L.append("\n" + "\n".join(f"- {f}" for f in flags))
    (DIR / "faq_review.md").write_text("\n".join(L), encoding="utf-8")

    # ── чего в гайде нет вовсе ─────────────────────────────────────────────
    gaps = empty + [r for r in written if r.get("covered") == "no" or r.get("missing")]
    G = ["# Разделы, которых в гайде не хватает\n",
         "Сформировано из вопросов чата и вердикта по покрытию. "
         "Порядок — по объёму спроса × цене ошибки.\n"]
    for r in sorted(gaps, key=lambda r: -(r["demand"] * int(r.get("pain") or 1))):
        G.append(f"\n## {r['q']}\n")
        G.append(f"- **Спрос**: {r['demand']} вопросов, боль {r.get('pain')}/5, "
                 f"этап {r.get('stage')}")
        G.append(f"- **Чего нет**: {r.get('gap') or r.get('missing')}")
        G.append(f"- **Не хватило при написании ответа**: {r.get('missing') or '—'}")
    (DIR / "missing_topics.md").write_text("\n".join(G), encoding="utf-8")

    ok = sum(1 for r in written if not r["needs_review"])
    print(f"\nГотово: {len(written)} заготовок с ответом (чистых {ok}, "
          f"на вычитку {len(written) - ok}), {len(empty)} тем без ответа, "
          f"{len(gaps)} заявок на разделы гайда")
    print(f"-> {DIR / 'faq_review.md'}\n-> {DIR / 'missing_topics.md'}")


if __name__ == "__main__":
    asyncio.run(main())
