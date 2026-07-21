"""Конфигурация Hermes: .env + профиль JDG.

Профиль (business profile) в Phase 1 минимален: то, чего нет в inFakt
account/details, но что влияет на расчёты. Любое изменение профиля должно
попадать в audit log вызывающей стороной.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"


def load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


@dataclass(frozen=True)
class Profile:
    """Параметры расчёта, не выводимые из API надёжно."""

    tax_form: str = "ryczalt"          # skala | liniowy | ryczalt
    ryczalt_rate: str = "8.5"          # ставка ryczałtu (flat_rate_tax_symbol)
    chorobowe: bool = False            # dobrowolne chorobowe (факт: user не платит, KEDU IV = 0.00)
    zus_regime: str = "duzy"           # ulga_na_start | pref | duzy (с 06/2026 — duzy, KEDU kod 05 10)


def load_rates() -> dict:
    # в JDG-боте ставки лежат в корне (rates_years.json, единый источник
    # с калькуляторами); в standalone-Hermes — в assets/
    for path in (ROOT / "rates_years.json", ASSETS / "rates_years.json"):
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError("rates_years.json не найден ни в корне, ни в assets/")
