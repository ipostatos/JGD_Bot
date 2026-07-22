"""KSeF: персональный ответ на вопрос «а меня это уже касается?».

Факты (этапы, поблажки, исключения, FAQ) лежат в webapp/ksef.json — один
источник для страницы, ассистента и бота. Здесь только даты-константы и
логика статуса; тест сверяет константы с датами этапов из JSON.

Личный статус считается из профиля (VAT, дата регистрации) и, если юзер
подключил inFakt, из его же фактур: порог 10 000 zł считается по месяцам,
а фактуры без номера KSeF показывают, ушло ли что-то в систему на самом деле.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

log = logging.getLogger("jdg.ksef")

FACTS_JSON = Path(__file__).parent / "webapp" / "ksef.json"

# Ключевые даты (зеркало stages в ksef.json — менять оба места, тест сверяет)
RECEIVE_SINCE = date(2026, 2, 1)   # принимать фактуры обязаны все
BIG_SINCE = date(2026, 2, 1)       # выставляют фирмы с оборотом > 200 млн zł
ALL_SINCE = date(2026, 4, 1)       # выставляют остальные, включая zwolnionych z VAT
NO_RELIEF_SINCE = date(2027, 1, 1)  # порог 10 000 zł отменён, включаются санкции
RELIEF_LIMIT = 10_000.0            # zł брутто за календарный месяц

_facts: dict | None = None


def facts() -> dict:
    global _facts
    if _facts is None:
        _facts = json.loads(FACTS_JSON.read_text(encoding="utf-8"))
    return _facts


def _months_over_limit(months: dict[str, float]) -> list[dict]:
    """Месяцы (с апреля 2026), где брутто-продажи перевалили за порог."""
    out = []
    for m in sorted(months):
        if m >= ALL_SINCE.strftime("%Y-%m") and months[m] > RELIEF_LIMIT:
            out.append({"month": m, "gross": round(months[m], 2)})
    return out


def status(profile: dict | None, today: date | None = None,
           sales: dict | None = None) -> dict:
    """Где юзер по обязанности KSeF.

    profile: {reg: 'YYYY-MM-DD', vat: bool} — любое поле необязательно.
    sales:   {'months': {'2026-05': 12345.0}, 'no_ksef': int, 'with_ksef': int}
             — из inFakt, если ключ подключён.
    """
    today = today or date.today()
    profile = profile or {}
    f = facts()

    must_receive = today >= RECEIVE_SINCE
    must_issue = today >= ALL_SINCE
    relief_open = today < NO_RELIEF_SINCE

    reg = None
    if profile.get("reg"):
        try:
            reg = date.fromisoformat(profile["reg"])
        except (ValueError, TypeError):
            reg = None

    over = _months_over_limit((sales or {}).get("months") or {})
    relief_lost = over[0] if over else None

    # Вердикт одной строкой — то, что человек прочитает первым.
    if not must_issue:
        headline = f"Выставлять в KSeF нужно с {ALL_SINCE.strftime('%d.%m.%Y')}"
        level = "warn"
    elif relief_lost:
        headline = "Ты уже обязан выставлять фактуры в KSeF"
        level = "bad"
    elif relief_open:
        headline = "Обязан выставлять в KSeF — кроме месяцев под порогом 10 000 zł"
        level = "warn"
    else:
        headline = "Все фактуры — только через KSeF"
        level = "bad"

    lines = []
    if must_receive:
        lines.append({"level": "warn", "icon": "download",
                      "text": "Принимать входящие фактуры через KSeF обязаны все "
                              f"с {RECEIVE_SINCE.strftime('%d.%m.%Y')} — это уже "
                              "работает. Не смотришь входящие — теряешь расходы и вычет VAT."})
    if reg and reg >= ALL_SINCE:
        lines.append({"level": "warn", "icon": "calendar",
                      "text": f"JDG зарегистрирован {reg.strftime('%d.%m.%Y')}, уже в эпоху KSeF: "
                              "выставляешь через систему с первой фактуры, если не пользуешься порогом."})
    elif reg:
        lines.append({"level": "ok", "icon": "calendar",
                      "text": f"Обязанность выставлять пришла к тебе {ALL_SINCE.strftime('%d.%m.%Y')} — "
                              "независимо от того, платишь ты VAT или на zwolnieniu."})

    if profile.get("vat") is False or profile.get("vat") == 0:
        lines.append({"level": "ok", "icon": "info",
                      "text": "Zwolnienie z VAT от KSeF не освобождает: с 01.04.2026 обязанность "
                              "у всех, разница только в пороге 10 000 zł."})

    if relief_lost:
        lines.append({"level": "bad", "icon": "alert-triangle",
                      "text": f"В {relief_lost['month']} твои фактуры дали "
                              f"{relief_lost['gross']:.2f} zł брутто — порог 10 000 zł перейдён. "
                              "С той фактуры KSeF обязателен, и поблажка больше не возвращается."})
    elif relief_open and sales and sales.get("months"):
        lines.append({"level": "ok", "icon": "check-circle",
                      "text": "По данным inFakt ни в одном месяце с апреля порог 10 000 zł "
                              "не превышен — до 31.12.2026 можно выставлять вне KSeF. "
                              "Порог считается по фактурам под обязательным KSeF, консументские в него не входят."})
    elif relief_open:
        lines.append({"level": "ok", "icon": "info",
                      "text": "Пока продажи по фактурам не больше 10 000 zł брутто в месяц, "
                              "до 31.12.2026 можно выставлять вне KSeF. Превысил — с той же "
                              "фактуры уходишь в KSeF навсегда."})

    if sales and (sales.get("no_ksef") or sales.get("with_ksef")):
        n_no, n_yes = sales.get("no_ksef", 0), sales.get("with_ksef", 0)
        lines.append({"level": "ok" if not n_no else "warn", "icon": "file-text",
                      "text": f"В inFakt за {today.year} год: {n_yes} фактур с номером KSeF, "
                              f"{n_no} — без него."})

    if relief_open:
        lines.append({"level": "ok", "icon": "shield-check",
                      "text": "Весь 2026 год — переходный: штрафов за ошибки и за неподключение нет, "
                              "санкции включаются с 01.01.2027."})

    days_left = (NO_RELIEF_SINCE - today).days
    return {
        "today": today.isoformat(),
        "updated": f["updated"],
        "headline": headline,
        "level": level,
        "must_receive": must_receive,
        "must_issue": must_issue,
        "relief_open": relief_open,
        "relief_lost": relief_lost,
        "days_to_full": max(0, days_left),
        "lines": lines,
    }


def sales_from_invoices(invoices: list[dict], today: date | None = None) -> dict:
    """Свод по фактурам inFakt: брутто по месяцам + сколько ушло в KSeF.

    Суммы inFakt приходят в грошах (int). Консументские фактуры в порог не
    входят, но по API их не отличить надёжно — считаем все и говорим об этом
    юзеру прямым текстом (лучше перестраховаться, чем занизить порог).
    """
    today = today or date.today()
    months: dict[str, float] = {}
    year = str(today.year)
    no_ksef = with_ksef = 0
    for inv in invoices:
        d = inv.get("invoice_date") or ""
        gross = (inv.get("gross_price") or 0) / 100
        if len(d) >= 7:
            months[d[:7]] = round(months.get(d[:7], 0.0) + gross, 2)
        if d.startswith(year):
            if inv.get("ksef_number"):
                with_ksef += 1
            else:
                no_ksef += 1
    return {"months": months, "no_ksef": no_ksef, "with_ksef": with_ksef}


def index_entries() -> list[dict]:
    """Записи для RAG-индекса ассистента (формат search.json)."""
    f = facts()
    out = []
    for s in f["stages"]:
        out.append({
            "id": f"ksef-stage-{s['date']}",
            "title": f"KSeF с {s['date']}: {s['title']}",
            "text": f"KSeF {s['date']} {s['title']} {s['who']} {s['what']}".lower()})
    for i, r in enumerate(f["relief"]):
        out.append({"id": f"ksef-relief-{i}",
                    "title": f"KSeF, поблажка до {r['until']}: {r['title']}",
                    "text": f"KSeF {r['title']} {r['text']}".lower()})
    for i, q in enumerate(f["faq"]):
        out.append({"id": f"ksef-faq-{i}", "title": f"KSeF: {q['q']}",
                    "text": f"KSeF ksef фактура {q['q']} {q['a']}".lower()})
    out.append({"id": "ksef-excluded",
                "title": "KSeF: что НЕ идёт в систему",
                "text": ("KSeF исключения не идут в систему " +
                         " ".join(f["excluded"])).lower()})
    out.append({"id": "ksef-access",
                "title": "KSeF: как получить доступ и чем выставлять",
                "text": ("KSeF доступ аутентификация " +
                         " ".join(f"{a['title']} {a['text']}" for a in f["access"])).lower()})
    return out
