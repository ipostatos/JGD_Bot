"""KEDU: XSD-валидация и разбор ZUSDRA.

Схема kedu_5_7.xsd (v5.7.0, bip.zus.pl, действ. с 25.04.2026) вендорена в assets/
с локализованным импортом xmldsig (решение D6). Проверено: 6/6 реальных DRA VALID.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from lxml import etree

from .config import ASSETS

NS = {"k": "http://www.zus.pl/2026/KEDU_5_7"}
_schema: etree.XMLSchema | None = None


def _get_schema() -> etree.XMLSchema:
    global _schema
    if _schema is None:
        _schema = etree.XMLSchema(etree.parse(str(ASSETS / "kedu_5_7.xsd")))
    return _schema


def validate(path: str | Path) -> tuple[bool, list[str]]:
    doc = etree.parse(str(path))
    schema = _get_schema()
    ok = schema.validate(doc)
    return ok, [e.message for e in schema.error_log][:10]


@dataclass(frozen=True)
class Dra:
    id_dokumentu: str          # == insurance_fees.id из inFakt API (сквозной линк)
    period: str                # "2026-06"
    declaration_no: str        # I.p2.p1: 01 = pierwszorazowa
    nip: str
    kod: str                   # X.p1.p1, напр. "0510"
    podstawa_spol: Decimal     # X.p2
    spoleczne: Decimal         # IV.p37
    zdrowotna: Decimal         # VI.p2
    fp_fs: Decimal             # VII.p1
    total: Decimal             # IX.p2
    extra_documents: list[str] # всё кроме ZUSDRA (анти-ZIPA контроль)


def parse(path: str | Path) -> Dra:
    root = etree.parse(str(path)).getroot()
    dras = root.findall("k:ZUSDRA", NS)
    if len(dras) != 1:
        raise ValueError(f"ожидался ровно один ZUSDRA, найдено {len(dras)}")
    dra = dras[0]
    extra = [
        etree.QName(el).localname
        for el in root
        if etree.QName(el).localname not in ("naglowek.KEDU", "ZUSDRA")
    ]

    def x(xpath: str) -> str:
        el = dra.find(xpath, NS)
        return el.text if el is not None and el.text else ""

    return Dra(
        id_dokumentu=dra.get("id_dokumentu", ""),
        period=x("k:I/k:p2/k:p2"),
        declaration_no=x("k:I/k:p2/k:p1"),
        nip=x("k:II/k:p1"),
        kod=x("k:X/k:p1/k:p1"),
        podstawa_spol=Decimal(x("k:X/k:p2") or "0"),
        spoleczne=Decimal(x("k:IV/k:p37") or "0"),
        zdrowotna=Decimal(x("k:VI/k:p2") or "0"),
        fp_fs=Decimal(x("k:VII/k:p1") or "0"),
        total=Decimal(x("k:IX/k:p2") or "0"),
        extra_documents=extra,
    )
