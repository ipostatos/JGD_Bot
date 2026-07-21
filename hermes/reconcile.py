"""Сверка расчётов Hermes с независимыми источниками (принцип §1.2 плана).

Любой DIFF != 0.00 -> статус BLOCK у вызывающей стороны. Допусков нет:
сверка грош-в-грош доказана на 6 реальных месяцах.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Check:
    name: str
    hermes: Decimal
    source: Decimal
    source_ref: str  # откуда взято значение источника

    @property
    def diff(self) -> Decimal:
        return self.hermes - self.source

    @property
    def ok(self) -> bool:
        return self.diff == 0


@dataclass
class Reconciliation:
    checks: list[Check]

    @property
    def passed(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def verdict(self) -> str:
        return "PASS" if self.passed else "BLOCK"

    def lines(self) -> list[str]:
        out = []
        for c in self.checks:
            mark = "OK " if c.ok else "DIFF"
            out.append(
                f"  [{mark}] {c.name}: hermes {c.hermes} vs {c.source} ({c.source_ref})"
                + ("" if c.ok else f"  РАСХОЖДЕНИЕ {c.diff:+}")
            )
        return out
