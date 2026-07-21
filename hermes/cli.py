"""CLI: python -m hermes <command>."""
from __future__ import annotations

import argparse
import sys

from .config import load_env


def main(argv: list[str] | None = None) -> int:
    load_env()
    p = argparse.ArgumentParser(prog="hermes", description="Hermes Supervised Accounting OS")
    sub = p.add_subparsers(dest="cmd", required=True)

    close = sub.add_parser("close", help="чек-лист закрытия месяца (read-only)")
    close.add_argument("month", help="YYYY-MM, напр. 2026-07")

    vk = sub.add_parser("validate-kedu", help="XSD-валидация и разбор KEDU, сверка с inFakt")
    vk.add_argument("files", nargs="+")
    vk.add_argument("--no-api", action="store_true", help="без сверки с inFakt")

    sub.add_parser("audit-verify", help="проверка целостности audit log")

    inv = sub.add_parser("invoice", help="фактуры (Phase 2)")
    inv_sub = inv.add_subparsers(dest="inv_cmd", required=True)

    ip = inv_sub.add_parser("preview", help="превью фактуры по шаблону (без записи)")
    ip.add_argument("month")
    ip.add_argument("--net", required=True, help="netto, напр. 7500.00")

    ic = inv_sub.add_parser("create", help="GATE -> создание фактуры")
    ic.add_argument("month")
    ic.add_argument("--net", required=True)
    ic.add_argument("--draft", action="store_true", help="черновик (тест), не попадает в księgowość")
    ic.add_argument("--yes", action="store_true", help="пропустить интерактивный гейт")

    ik = inv_sub.add_parser("ksef", help="GATE -> отправка в KSeF")
    ik.add_argument("uuid")
    ik.add_argument("--yes", action="store_true")

    ist = inv_sub.add_parser("status", help="статус фактуры + KSeF")
    ist.add_argument("uuid")

    pay = sub.add_parser("pay", help="платёжные пакеты ZUS + налог за месяц")
    pay.add_argument("month")

    sub.add_parser("watch", help="один цикл watcher: оплаты, просрочки, дедлайны")

    args = p.parse_args(argv)

    if args.cmd == "close":
        from .checklist import close_month
        rep = close_month(args.month)
        print(rep.text())
        return 0 if rep.verdict == "PASS" else 1

    if args.cmd == "validate-kedu":
        from . import kedu
        from .infakt import Infakt, zl
        client = None if args.no_api else Infakt()
        rc = 0
        for f in args.files:
            ok, errors = kedu.validate(f)
            if not ok:
                print(f"{f}: INVALID: {errors[:3]}")
                rc = 1
                continue
            dra = kedu.parse(f)
            line = (f"{f}: VALID  DRA {dra.period} kod {dra.kod} podstawa {dra.podstawa_spol} "
                    f"społ {dra.spoleczne} zdrow {dra.zdrowotna} FP {dra.fp_fs} total {dra.total}")
            if dra.extra_documents:
                line += f"  !! лишние документы: {dra.extra_documents}"
                rc = 1
            if client:
                fee = client.insurance_fee(dra.period)
                if fee is None:
                    line += "  !! в inFakt нет периода"
                    rc = 1
                elif (str(fee["id"]) != dra.id_dokumentu
                      or zl(fee["social_amount_price"]) != dra.spoleczne
                      or zl(fee["health_amount_price"]) != dra.zdrowotna
                      or zl(fee["work_amount_price"]) != dra.fp_fs):
                    line += "  !! РАСХОЖДЕНИЕ с inFakt API"
                    rc = 1
                else:
                    line += "  == inFakt API (id+суммы)"
            print(line)
        return rc

    if args.cmd == "audit-verify":
        from .audit import Audit
        ok, n = Audit().verify()
        print(f"audit log: {'OK' if ok else 'ЦЕПОЧКА ПОВРЕЖДЕНА'} ({n} записей)")
        return 0 if ok else 1

    if args.cmd == "invoice":
        return _invoice(args)

    if args.cmd == "pay":
        from .infakt import Infakt
        from .payments import tax_package, zus_package
        client = Infakt()
        packages = [p for p in (zus_package(client, args.month), tax_package(client, args.month)) if p]
        if not packages:
            print(f"Нет обязательств за {args.month} в inFakt")
            return 1
        total = sum(p.amount for p in packages if p.status != "paid")
        for pkg in packages:
            print(pkg.text())
            print()
        print(f"К оплате: {total} zł (перевод делаешь сам в банке — Hermes деньги не трогает)")
        return 0

    if args.cmd == "watch":
        from .infakt import Infakt
        from .watcher import Watcher
        events = Watcher().poll(Infakt())
        if not events:
            print("Новых событий нет")
        for e in events:
            print(f"[{e.key}] {e.text}")
        return 0

    return 2


def _gate(text: str, yes: bool) -> bool:
    """Approval Guardian: показать конкретный объект действия и спросить."""
    print(text)
    if yes:
        print("[гейт пропущен: --yes]")
        return True
    answer = input("Подтвердить? [y/N] ").strip().lower()
    return answer in ("y", "yes", "да")


def _invoice(args) -> int:
    from decimal import Decimal

    from . import invoicing
    from .audit import Audit
    from .infakt import Infakt

    client, audit = Infakt(), Audit()

    if args.inv_cmd == "preview":
        print(invoicing.build_plan(client, args.month, Decimal(args.net)).text())
        existing = invoicing.find_existing(client, args.month)
        if existing:
            print(f"!! Фактура за {args.month} уже существует: {existing['number']} — создание будет пропущено")
        return 0

    if args.inv_cmd == "create":
        plan = invoicing.build_plan(client, args.month, Decimal(args.net))
        if not _gate(plan.text() + ("\n  Режим:    ЧЕРНОВИК (тест)" if args.draft else ""), args.yes):
            print("Отменено, ничего не создано.")
            return 1
        result = invoicing.execute(client, plan, audit, draft=args.draft)
        print(f"Фактура {result['number']} (status {result['status']}, uuid {result['uuid']})")
        return 0

    if args.inv_cmd == "ksef":
        inv = client.invoice(args.uuid, fields="number,status,gross_price")
        if not _gate(f"Отправить в KSeF: фактура {inv['number']}, brutto {inv['gross_price'] / 100:.2f}, "
                     f"status {inv['status']}, uuid {args.uuid}", args.yes):
            print("Отменено.")
            return 1
        st = invoicing.ksef_send(client, args.uuid, audit)
        print(f"KSeF: status={st.get('status')} ksef_number={st.get('ksef_number')}")
        return 0

    if args.inv_cmd == "status":
        inv = client.invoice(args.uuid, fields="number,status,gross_price,paid_date")
        line = f"{inv['number']}: status {inv['status']}, brutto {inv['gross_price'] / 100:.2f}"
        if inv.get("paid_date"):
            line += f", оплачена {inv['paid_date']}"
        try:
            st = client.ksef_status(args.uuid)
            line += f" | KSeF: {st.get('status')} {st.get('ksef_number') or ''}"
        except Exception:
            line += " | KSeF: не отправлялась"
        print(line)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
