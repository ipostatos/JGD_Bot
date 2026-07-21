"""Invoice Agent (Phase 2): шаблон -> превью -> GATE -> async-создание -> KSeF.

Идемпотентность (решение D10): источник правды — сам inFakt. Перед созданием
проверяем существующие фактуры клиента за месяц; нашли — возвращаем её и НЕ
создаём вторую. Локальный стейт не нужен и не может рассинхронизироваться.

Гейт (решение D11): превью строится локально, БЕЗ черновика в inFakt —
черновик резервирует следующий номер фактуры и требует удаления при отказе.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .audit import Audit
from .infakt import Infakt, InfaktError, zl


@dataclass(frozen=True)
class InvoicePlan:
    """Конкретный объект действия для Approval Guardian — всё, что увидит user."""

    month: str
    client_id: int
    client_name: str
    service_name: str
    net: Decimal
    currency: str
    tax_symbol: str            # "np" у VAT-zwolnionego
    flat_rate_tax_symbol: str  # ставка ryczałtu позиции
    invoice_date: str
    sale_date: str
    payment_date: str
    payment_method: str
    bank_account: str
    idempotency_key: str

    def text(self) -> str:
        return "\n".join([
            f"Фактура за {self.month}",
            f"  Клиент:   {self.client_name} (id {self.client_id})",
            f"  Услуга:   {self.service_name}",
            f"  Netto:    {self.net} {self.currency} (VAT: {self.tax_symbol}, ryczałt {self.flat_rate_tax_symbol}%)",
            f"  Даты:     выставление {self.invoice_date}, продажа {self.sale_date}, оплата до {self.payment_date}",
            f"  Оплата:   {self.payment_method} -> {self.bank_account}",
            f"  Idempotency: {self.idempotency_key}",
        ])


def find_existing(client: Infakt, month: str, client_id: int | None = None) -> dict | None:
    """Идемпотентный чек: фактура этого клиента за месяц уже есть?"""
    for inv in client.invoices(month):
        if client_id is None or inv.get("client_id") == client_id:
            return inv
    return None


def build_plan(client: Infakt, month: str, net: Decimal,
               issue: date | None = None, due_days: int = 30) -> InvoicePlan:
    """Шаблон из последней фактуры (постоянный клиент/услуга — кейс user)."""
    history = client.invoices()
    if not history:
        raise InfaktError("нет фактур-шаблонов; первый раз создать в inFakt UI")
    last = max(history, key=lambda i: i.get("invoice_date") or "")
    svc = last["services"][0]
    if issue is None:
        # invoice_date ОБЯЗАН попадать в целевой месяц: паттерн user (фактура M
        # выставляется в M) + консистентность идемпотентного чека по месяцу.
        today = date.today()
        first = date(int(month[:4]), int(month[5:7]), 1)
        if today.strftime("%Y-%m") == month:
            issue = today
        elif today < first:
            issue = first          # будущий месяц: 1-е число
        else:
            raise InfaktError(f"месяц {month} уже прошёл — укажи issue-дату явно")
    return InvoicePlan(
        month=month,
        client_id=last["client_id"],
        client_name=last["client_company_name"],
        service_name=svc["name"],
        net=net,
        currency=last.get("currency") or "PLN",
        tax_symbol=svc["tax_symbol"],
        flat_rate_tax_symbol=svc["flat_rate_tax_symbol"],
        invoice_date=issue.isoformat(),
        sale_date=issue.isoformat(),
        payment_date=(issue + timedelta(days=due_days)).isoformat(),
        payment_method=last.get("payment_method") or "transfer",
        bank_account=last.get("bank_account") or "",
        idempotency_key=f"invoice:create:{last['client_id']}:{month}",
    )


def execute(client: Infakt, plan: InvoicePlan, audit: Audit,
            draft: bool = False) -> dict:
    """Создание фактуры по подтверждённому плану. Вызывать ТОЛЬКО после гейта."""
    existing = find_existing(client, plan.month, plan.client_id)
    if existing:
        audit.log("invoice.create.skipped_idempotent", {
            "key": plan.idempotency_key, "existing_number": existing["number"],
            "existing_uuid": existing["uuid"],
        })
        return existing

    grosze = int((plan.net * 100).to_integral_value())
    payload = {
        "client_id": plan.client_id,
        "invoice_date": plan.invoice_date,
        "sale_date": plan.sale_date,
        "payment_date": plan.payment_date,
        "payment_method": plan.payment_method,
        "services": [{
            "name": plan.service_name,
            "net_price": grosze,
            "unit_net_price": grosze,
            "quantity": 1,
            "tax_symbol": plan.tax_symbol,
            "flat_rate_tax_symbol": plan.flat_rate_tax_symbol,
        }],
    }
    if not draft:
        payload["status"] = "printed"  # учтена в księgowości; draft — для тестов

    task = client.create_invoice_async(payload)
    audit.log("invoice.create.submitted", {"key": plan.idempotency_key, "task": task,
                                           "plan": plan.text(), "draft": draft})
    uuid = client.wait_invoice_created(task)
    created = client.invoice(uuid, fields="number,status,gross_price,client_id")
    audit.log("invoice.create.done", {"key": plan.idempotency_key, "uuid": uuid,
                                      "number": created.get("number"),
                                      "status": created.get("status")})
    return created | {"uuid": uuid}


def ksef_send(client: Infakt, uuid: str, audit: Audit) -> dict:
    """Отправка в KSeF по подтверждению. Идемпотентность: не шлём повторно."""
    inv = client.invoice(uuid, fields="number,status")
    try:
        st = client.ksef_status(uuid)
    except InfaktError:
        st = {}  # ещё не отправлялась — статуса нет
    if st.get("ksef_number"):
        audit.log("ksef.send.skipped_idempotent", {"uuid": uuid, "ksef_number": st["ksef_number"]})
        return st
    client.send_to_ksef(uuid)
    audit.log("ksef.send.submitted", {"uuid": uuid, "number": inv.get("number")})
    return client.ksef_status(uuid)
