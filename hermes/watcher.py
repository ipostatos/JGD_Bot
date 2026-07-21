"""Payment Watcher (Phase 3): polling inFakt + SQLite-дедупликация событий.

Webhooks в API нет (факт исследования) -> периодический опрос. Watcher реентерабелен:
каждое событие имеет ключ, повторный poll не дублирует уведомления. Никогда не
помечает фактуру оплаченной сам — только сообщает о фактах из API.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from .config import ROOT
from .infakt import Infakt, invoice_paid, zl

DUE_SOON_DAYS = 7


@dataclass(frozen=True)
class Event:
    key: str      # дедуп-ключ
    text: str


class Watcher:
    def __init__(self, db_path: Path | None = None):
        self.db = sqlite3.connect(db_path or (ROOT / "state.db"))
        self.db.execute("CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY, ts TEXT, text TEXT)")
        try:  # миграция ранней схемы без text
            self.db.execute("ALTER TABLE seen ADD COLUMN text TEXT")
        except sqlite3.OperationalError:
            pass
        self.db.commit()

    def _new(self, key: str, text: str = "") -> bool:
        cur = self.db.execute("SELECT 1 FROM seen WHERE key = ?", (key,))
        if cur.fetchone():
            return False
        self.db.execute("INSERT INTO seen VALUES (?, ?, ?)",
                        (key, datetime.now(timezone.utc).isoformat(), text))
        self.db.commit()
        return True

    def recent(self, limit: int = 20) -> list[tuple[str, str, str]]:
        """Лента последних событий (для Mini App): [(ts, key, text)]."""
        cur = self.db.execute(
            "SELECT ts, key, text FROM seen WHERE text != '' ORDER BY ts DESC, key LIMIT ?",
            (limit,))
        return cur.fetchall()

    def poll(self, client: Infakt, today: date | None = None) -> list[Event]:
        today = today or date.today()
        events: list[Event] = []

        def emit(key: str, text: str) -> None:
            if self._new(key, text):
                events.append(Event(key, text))

        # 1. Оплаты и просрочки фактур
        for inv in client.invoices():
            if invoice_paid(inv):
                emit(f"paid:{inv['uuid']}",
                     f"Оплачена фактура {inv['number']}: {zl(inv['gross_price'])} zł ({inv['paid_date']})")
            elif inv.get("status") in ("printed", "sent"):
                due = inv.get("payment_date") or ""
                if due and due < today.isoformat():
                    emit(f"overdue:{inv['uuid']}:{due}",
                         f"ПРОСРОЧКА: фактура {inv['number']} ({zl(inv['gross_price'])} zł, срок был {due})")

        # 2. Дедлайны ZUS и налога (за DUE_SOON_DAYS дней и в день срока)
        for kind, fetch in (("zus", client.insurance_fee), ("tax", client.income_tax)):
            for probe_month in (today.strftime("%Y-%m"), _prev(today)):
                item = fetch(probe_month)
                if not item or item.get("status") == "paid":
                    continue
                amount = zl(item.get("sum_amount_price") or item.get("to_pay_price"))
                if amount == 0:
                    continue
                due = item["payment_date"]
                days_left = (date.fromisoformat(due) - today).days
                if 0 < days_left <= DUE_SOON_DAYS:
                    emit(f"due:{kind}:{item['period']}:soon",
                         f"Через {days_left} дн. срок {kind.upper()} за {probe_month}: {amount} zł (до {due})")
                elif days_left <= 0:
                    emit(f"due:{kind}:{item['period']}:{due}",
                         f"СЕГОДНЯ/ПРОСРОЧЕН срок {kind.upper()} за {probe_month}: {amount} zł (до {due})")
        return events


def _prev(today: date) -> str:
    y, m = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    return f"{y}-{m:02d}"
