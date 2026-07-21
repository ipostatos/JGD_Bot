"""Клиент inFakt API v3 (только проверенные endpoints, см. TECHNICAL_PLAN §5).

Все суммы API приходят в грошах (int) — конвертируем в Decimal злотых на границе.
Грабля: у оплаченных фактур paid_price=0 и left_to_pay==gross_price;
источник правды об оплате — status=="paid" + paid_date.
"""
from __future__ import annotations

import os
import time
from decimal import Decimal
from typing import Any

import httpx

BASE = "https://api.infakt.pl/api/v3"


def zl(grosze: int | None) -> Decimal:
    """Гроши API -> злотые Decimal."""
    return Decimal(grosze or 0) / 100


class InfaktError(RuntimeError):
    pass


class Infakt:
    def __init__(self, api_key: str | None = None, base: str = BASE):
        key = api_key or os.environ.get("INFAKT_API_KEY")
        if not key:
            raise InfaktError("INFAKT_API_KEY не задан (.env)")
        self._http = httpx.Client(
            base_url=base,
            headers={"X-inFakt-ApiKey": key, "Content-Type": "application/json"},
            timeout=httpx.Timeout(60, connect=15),
        )

    def _get(self, path: str, **params: Any) -> dict:
        for attempt in range(4):
            try:
                r = self._http.get(path, params=params or None)
            except httpx.TimeoutException:
                if attempt == 3:
                    raise
                time.sleep(2**attempt)
                continue
            if r.status_code == 429:
                time.sleep(2**attempt)
                continue
            if r.status_code >= 400:
                raise InfaktError(f"GET {path} -> {r.status_code}: {r.text[:200]}")
            return r.json()
        raise InfaktError(f"GET {path}: rate limit не отпустил")

    def _list_all(self, path: str, limit: int = 100, max_pages: int = 10) -> list[dict]:
        """Собрать все entities с пагинацией (фильтруем на клиенте — надёжнее q[])."""
        out: list[dict] = []
        for page in range(max_pages):
            data = self._get(path, limit=limit, offset=page * limit)
            entities = data.get("entities", [])
            out.extend(entities)
            if len(out) >= data.get("metainfo", {}).get("total_count", 0) or not entities:
                break
        return out

    def _send(self, method: str, path: str, json: dict | None = None) -> dict:
        """Запись (POST/PUT/DELETE) с ретраем ТОЛЬКО на 429 (не на таймаут:
        операция могла пройти — повтор без идемпотентного чека опасен)."""
        for attempt in range(4):
            r = self._http.request(method, path, json=json)
            if r.status_code == 429:
                time.sleep(2**attempt)
                continue
            if r.status_code >= 400:
                raise InfaktError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
            return r.json() if r.content and r.status_code != 204 else {}
        raise InfaktError(f"{method} {path}: rate limit не отпустил")

    # --- endpoints (все проверены живым ключом 2026-07-21) ---

    def account(self) -> dict:
        return self._get("/account/details.json")

    def invoice(self, uuid: str, fields: str | None = None) -> dict:
        params = {"fields": fields} if fields else {}
        return self._get(f"/invoices/{uuid}.json", **params)

    # --- запись: фактуры (async-паттерн — единственный поддерживаемый) ---

    def create_invoice_async(self, invoice: dict, send_to_ksef: bool = False) -> str:
        """-> invoice_task_reference_number."""
        data = self._send("POST", "/async/invoices.json",
                          {"invoice": invoice, "send_to_ksef": send_to_ksef})
        return data["invoice_task_reference_number"]

    def async_invoice_status(self, task_ref: str) -> dict:
        """processing_code: 100 принято, 201 создана (+invoice_uuid), 4xx ошибка."""
        return self._get(f"/async/invoices/status/{task_ref}.json")

    def wait_invoice_created(self, task_ref: str, timeout_s: int = 60) -> str:
        """Поллинг задачи создания -> invoice_uuid."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            st = self.async_invoice_status(task_ref)
            code = st.get("processing_code")
            if code == 201:
                return st["invoice_uuid"]
            if code and code >= 400:
                raise InfaktError(f"создание фактуры отклонено: {st}")
            time.sleep(1.5)
        raise InfaktError(f"задача {task_ref} не завершилась за {timeout_s}с")

    def delete_invoice(self, uuid: str) -> None:
        self._send("DELETE", f"/invoices/{uuid}.json")

    def send_to_ksef(self, uuid: str) -> dict:
        return self._send("POST", f"/ksef2/documents/{uuid}/send.json")

    def deliver_email(self, uuid: str, recipient: str | None = None) -> dict:
        payload = {"recipient": recipient} if recipient else None
        return self._send("POST", f"/invoices/{uuid}/deliver_via_email.json", payload)

    def invoice_pdf(self, uuid: str) -> bytes:
        r = self._http.get(f"/invoices/{uuid}/pdf.json", params={"document_type": "original"})
        if r.status_code >= 400:
            raise InfaktError(f"PDF {uuid} -> {r.status_code}")
        return r.content

    def invoices(self, month: str | None = None) -> list[dict]:
        """month='2026-07' -> фактуры с invoice_date в этом месяце."""
        inv = self._list_all("/invoices.json")
        if month:
            inv = [i for i in inv if (i.get("invoice_date") or "").startswith(month)]
        return inv

    def costs(self, month: str | None = None) -> list[dict]:
        cc = self._list_all("/documents/costs.json")
        if month:
            cc = [c for c in cc if (c.get("issue_date") or "").startswith(month)]
        return cc

    def insurance_fee(self, month: str) -> dict | None:
        """Składki ZUS за период month='2026-07' (period в API = 'YYYY-MM-01')."""
        for f in self._list_all("/insurance_fees.json"):
            if f.get("period") == f"{month}-01":
                return f
        return None

    def income_tax(self, month: str) -> dict | None:
        for t in self._list_all("/income_taxes.json"):
            if t.get("period") == f"{month}-01":
                return t
        return None

    def ksef_status(self, invoice_uuid: str) -> dict:
        return self._get(f"/ksef2/documents/{invoice_uuid}/status.json")


def invoice_paid(inv: dict) -> bool:
    """Единственный корректный признак оплаты (см. грабли в docstring модуля)."""
    return inv.get("status") == "paid" and bool(inv.get("paid_date"))
