"""УСТАРЕЛ. Не запускать без переделки — см. chat_prep/chat_analyze/chat_label/chat_report.

Скрипт просил модель писать ответы «на основе ответов участников». Замер по
реальному экспорту (473k сообщений, 2021-2026): в 90% вопросов верный ответ
зависит от года и текущих правил — ставки ZUS, лимит VAT, KSeF. То есть корпус
чата на 90% состоит из ответов, которые могли протухнуть, и модель не отличит
верный от устаревшего. Отчёт: chat_analysis/report.md.

Правильный порядок: анализ спроса (chat_analyze) → приоритеты и дыры гайда
(chat_report) → FAQ пишем от актуального гайда и rates_*.json, а чат
используем только как источник вопросов, не ответов.

--- исходное описание ---
FAQ-майнинг экспорта чата JDG_PBH → faq_data.json для AI-ассистента.

Вход: result.json из Telegram Desktop (чат → Экспорт истории, формат JSON).
Пайплайн: вопросы (длинные сообщения с «?») + их реплаи → топ по числу
ответов → Claude Haiku группирует и пишет канонические Q&A → ручная вычитка
→ webapp/data/faq_data.json. ai.py подхватывает файл автоматически (RAG).

Запуск: python tools/faq_miner.py path/to/result.json [--limit 120]
Требует ANTHROPIC_API_KEY в окружении/.env.

⏳ Блокер: нужен экспорт чата от админов JDG_PBH.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "webapp" / "data" / "faq_data.json"
MODEL = "claude-haiku-4-5-20251001"
BATCH = 20


def msg_text(m) -> str:
    t = m.get("text", "")
    if isinstance(t, list):
        t = "".join(x if isinstance(x, str) else x.get("text", "") for x in t)
    return t.strip()


def collect_qa(export: dict, min_replies: int = 2):
    """Вопросы с ответами-реплаями, отсортированы по числу ответов."""
    msgs = [m for m in export.get("messages", []) if m.get("type") == "message"]
    by_id = {m["id"]: m for m in msgs}
    replies = {}
    for m in msgs:
        rid = m.get("reply_to_message_id")
        if rid:
            replies.setdefault(rid, []).append(m)
    qa = []
    for m in msgs:
        text = msg_text(m)
        if "?" in text and len(text) > 40 and not text.startswith("/"):
            answers = [msg_text(r) for r in replies.get(m["id"], [])
                       if len(msg_text(r)) > 20]
            if len(answers) >= min_replies:
                qa.append({"id": m["id"], "q": text[:600],
                           "answers": answers[:5], "n": len(answers),
                           "date": m.get("date", "")[:10]})
    qa.sort(key=lambda x: -x["n"])
    return qa


PROMPT = """Ты редактор FAQ для русскоязычных ИП (JDG) в Польше.
Дан список вопросов из чата с ответами участников. Составь из них FAQ-записи:
- объединяй дубли в один канонический вопрос;
- ответ пиши сам на основе ответов участников, коротко и практично, по-русски;
- если ответы противоречат друг другу или явно устарели — пропусти вопрос;
- каждой записи дай topic из: ZUS, налоги, VAT, регистрация, легализация,
  банки, бухгалтерия, другое.
Верни строго JSON-массив объектов {"q": ..., "a": ..., "topic": ...}."""


def distill(qa_batch, api_key):
    listing = "\n\n".join(
        f"ВОПРОС ({x['date']}, ответов: {x['n']}): {x['q']}\nОТВЕТЫ: " +
        " || ".join(a[:300] for a in x["answers"]) for x in qa_batch)
    r = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        json={"model": MODEL, "max_tokens": 4000, "system": PROMPT,
              "messages": [{"role": "user", "content": listing}]},
        timeout=120)
    r.raise_for_status()
    text = r.json()["content"][0]["text"]
    m = re.search(r"\[.*\]", text, re.S)
    return json.loads(m.group(0)) if m else []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("export_json")
    ap.add_argument("--limit", type=int, default=120,
                    help="сколько топ-вопросов прогнать через AI")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        sys.exit("Нет ANTHROPIC_API_KEY")

    export = json.loads(Path(args.export_json).read_text(encoding="utf-8"))
    qa = collect_qa(export)
    print(f"Найдено {len(qa)} вопросов с ответами; беру топ-{args.limit}")

    faq = []
    top = qa[:args.limit]
    for i in range(0, len(top), BATCH):
        batch = top[i:i + BATCH]
        try:
            faq.extend(distill(batch, api_key))
            print(f"  {i + len(batch)}/{len(top)} → всего FAQ: {len(faq)}")
        except Exception as e:
            print(f"  ! батч {i} упал: {e}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(faq, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"OK: {len(faq)} записей -> {OUT}\n"
          "⚠️ Вычитай файл руками перед деплоем — AI мог ошибиться.")


if __name__ == "__main__":
    main()
