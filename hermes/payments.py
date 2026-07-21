"""Payment Preparation Agent: платёжные пакеты ZUS и налога (только подготовка).

Перевод выполняет user в своём банке (SCA) — Hermes не трогает деньги (гейт 3).
Реквизиты берутся из account/details: индивидуальный rachunek składkowy ZUS (NRS)
и mikrorachunek podatkowy — оба проверены живым ключом.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .infakt import Infakt, zl


@dataclass(frozen=True)
class PaymentPackage:
    kind: str          # "zus" | "tax"
    month: str
    recipient: str
    account: str       # IBAN без PL-префикса, как в inFakt
    amount: Decimal
    title: str
    deadline: str
    status: str        # draft/paid из inFakt

    def text(self) -> str:
        paid = " [УЖЕ ОПЛАЧЕНО]" if self.status == "paid" else ""
        return "\n".join([
            f"Платёж: {self.recipient}{paid}",
            f"  Счёт:   {self.account}",
            f"  Сумма:  {self.amount} zł",
            f"  Тытул:  {self.title}",
            f"  Срок:   {self.deadline}",
        ])


def zus_package(client: Infakt, month: str) -> PaymentPackage | None:
    fee = client.insurance_fee(month)
    if not fee:
        return None
    acc = client.account()
    return PaymentPackage(
        kind="zus",
        month=month,
        recipient="ZUS (rachunek składkowy NRS)",
        account=acc["accounting_settings"]["insurance_fee_account_number"],
        amount=zl(fee["sum_amount_price"]),
        title=f"Składki ZUS {month}",  # NRS сам идентифицирует плательщика
        deadline=fee["payment_date"],
        status=fee["status"],
    )


def tax_package(client: Infakt, month: str) -> PaymentPackage | None:
    tax = client.income_tax(month)
    if not tax:
        return None
    if zl(tax["to_pay_price"]) == 0:
        return None  # нулевой налог не платится
    acc = client.account()
    okres = f"{month[2:4]}M{month[5:7]}"  # формат okresu: 26M07
    return PaymentPackage(
        kind="tax",
        month=month,
        recipient="Urząd Skarbowy (mikrorachunek)",
        account=acc["accounting_settings"]["individual_tax_account_number"],
        amount=zl(tax["to_pay_price"]),
        title=f"{tax['symbol']} {okres}",
        deadline=tax["payment_date"],
        status=tax["status"],
    )
