"""Выгрузка истории чата JDG_PBH через Telethon (свой аккаунт, без админов).

Результат — JSON в формате экспорта Telegram Desktop, чтобы `faq_miner.py`
съел его без изменений: {"name", "id", "messages": [{id, type, date, from,
text, reply_to_message_id}, ...]}.

Порядок работы (api_id/api_hash берутся из .env: TG_API_ID / TG_API_HASH):

    python tools/export_chat.py login --phone +48XXXXXXXXX   # придёт код в Telegram
    python tools/export_chat.py login --code 12345 [--password 2FA]
    python tools/export_chat.py export --chat JDG_PBH --out chat_export.json

Сессия лежит в tools/.tg_session.session — это доступ к аккаунту,
в git не коммитить (в .gitignore), на VPS не носить.
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import PasswordHashInvalidError, SessionPasswordNeededError
from telethon.tl.types import Message, MessageService

ROOT = Path(__file__).resolve().parent.parent
SESSION = ROOT / "tools" / ".tg_session"
PENDING = ROOT / "tools" / ".tg_login.json"  # phone_code_hash между шагами
DEFAULT_OUT = ROOT / "chat_export.json"


def creds():
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    api_id = os.environ.get("TG_API_ID") or os.environ.get("TELEGRAM_API_ID", "")
    api_hash = os.environ.get("TG_API_HASH") or os.environ.get("TELEGRAM_API_HASH", "")
    if not api_id.strip().isdigit() or len(api_hash.strip()) != 32:
        sys.exit("Нужны TELEGRAM_API_ID (число, поле App api_id) и TELEGRAM_API_HASH "
                 "(32 hex, поле App api_hash) — my.telegram.org → API development tools.\n"
                 "Публичный RSA-ключ с той же страницы сюда не нужен.")
    return int(api_id), api_hash.strip()


def client():
    """Подключение без client.start() — тот лезет в input() и падает без tty."""
    api_id, api_hash = creds()
    return TelegramClient(str(SESSION), api_id, api_hash)


async def connected(tg):
    await tg.connect()
    return tg


async def cmd_login(args):
    tg = await connected(client())
    try:
        if await tg.is_user_authorized():
            me = await tg.get_me()
            print(f"Уже авторизован: {me.first_name} (@{me.username}) id={me.id}")
            return
        if args.phone:
            sent = await tg.send_code_request(args.phone)
            PENDING.write_text(json.dumps(
                {"phone": args.phone, "hash": sent.phone_code_hash}), encoding="utf-8")
            print("Код отправлен в Telegram. Дальше:\n"
                  "  python tools/export_chat.py login --code 12345")
            return
        if args.hint:
            from telethon.tl.functions.account import GetPasswordRequest
            pw = await tg(GetPasswordRequest())
            print(f"Подсказка к паролю: {pw.hint or '(не задана)'}\n"
                  f"Сброс по email: {'да, ' + pw.email_unconfirmed_pattern if pw.email_unconfirmed_pattern else 'привязан ли email — покажет форма сброса'}")
            return
        if not args.code and args.password:
            # код уже принят, сервер ждёт только облачный пароль
            try:
                await tg.sign_in(password=args.password)
            except PasswordHashInvalidError:
                sys.exit("Неверный облачный пароль (Настройки → Конфиденциальность → "
                         "Двухэтапная аутентификация). Попыток не жалко, код заново не нужен.")
            PENDING.unlink(missing_ok=True)
            me = await tg.get_me()
            print(f"OK, вошли как {me.first_name} (@{me.username}) id={me.id}")
            return
        if not args.code:
            sys.exit("Нужен --phone (шаг 1), --code (шаг 2) или --password (шаг 3, если 2FA)")
        if not PENDING.exists():
            sys.exit("Нет .tg_login.json — сначала шаг 1 с --phone")
        pend = json.loads(PENDING.read_text(encoding="utf-8"))
        try:
            await tg.sign_in(pend["phone"], args.code, phone_code_hash=pend["hash"])
        except SessionPasswordNeededError:
            if not args.password:
                sys.exit("Включена 2FA — повтори с --password '<облачный пароль>'")
            await tg.sign_in(password=args.password)
        PENDING.unlink(missing_ok=True)
        me = await tg.get_me()
        print(f"OK, вошли как {me.first_name} (@{me.username}) id={me.id}")
    finally:
        await tg.disconnect()


def text_of(msg: Message) -> str:
    return (msg.message or "").strip()


async def cmd_export(args):
    out = Path(args.out) if args.out else DEFAULT_OUT
    tg = await connected(client())
    try:
        if not await tg.is_user_authorized():
            sys.exit("Не авторизован — сначала login")
        entity = await tg.get_entity(args.chat)
        title = getattr(entity, "title", None) or getattr(entity, "username", args.chat)
        print(f"Чат: {title} (id={entity.id}), качаю историю…")

        names: dict[int, str] = {}
        messages, total = [], 0
        async for m in tg.iter_messages(entity, limit=args.limit, reverse=True):
            total += 1
            if total % 2000 == 0:
                print(f"  …{total} сообщений")
            date = m.date.isoformat() if m.date else ""
            sender_id = None
            if m.from_id is not None:
                sender_id = getattr(m.from_id, "user_id", None) or \
                    getattr(m.from_id, "channel_id", None)
            name = names.get(sender_id) if sender_id else None
            if name is None and sender_id:
                try:
                    s = await m.get_sender()
                    name = " ".join(filter(None, [
                        getattr(s, "first_name", None), getattr(s, "last_name", None)
                    ])) or getattr(s, "title", None) or getattr(s, "username", None) or ""
                except Exception:
                    name = ""
                names[sender_id] = name
            if isinstance(m, MessageService):
                messages.append({"id": m.id, "type": "service", "date": date,
                                 "actor": name or "", "actor_id": sender_id})
                continue
            rec = {"id": m.id, "type": "message", "date": date,
                   "from": name or "", "from_id": sender_id,
                   "text": text_of(m)}
            if m.reply_to and m.reply_to.reply_to_msg_id:
                rec["reply_to_message_id"] = m.reply_to.reply_to_msg_id
            if m.media is not None:
                rec["media_type"] = type(m.media).__name__
            if getattr(m, "forward", None):
                rec["forwarded_from"] = getattr(m.forward, "from_name", "") or ""
            messages.append(rec)

        data = {"name": title, "type": "public_supergroup",
                "id": entity.id, "messages": messages}
        out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        real = sum(1 for m in messages if m["type"] == "message")
        with_reply = sum(1 for m in messages if m.get("reply_to_message_id"))
        print(f"OK: {len(messages)} записей ({real} сообщений, {with_reply} реплаев)"
              f"\n  -> {out}  ({out.stat().st_size // 1024} КБ)"
              f"\nДальше: python tools/faq_miner.py {out}")
    finally:
        await tg.disconnect()


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # иначе кириллица в cp866
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    lg = sub.add_parser("login", help="вход в аккаунт (два шага)")
    lg.add_argument("--phone", help="шаг 1: телефон в формате +48...")
    lg.add_argument("--code", help="шаг 2: код из Telegram")
    lg.add_argument("--password", help="облачный пароль, если включена 2FA")
    lg.add_argument("--hint", action="store_true", help="показать подсказку к облачному паролю")

    ex = sub.add_parser("export", help="выгрузка истории")
    ex.add_argument("--chat", default="JDG_PBH", help="username/ссылка/id чата")
    ex.add_argument("--out", help=f"файл вывода (по умолчанию {DEFAULT_OUT})")
    ex.add_argument("--limit", type=int, default=None, help="сколько сообщений (по умолчанию все)")

    args = ap.parse_args()
    asyncio.run(cmd_login(args) if args.cmd == "login" else cmd_export(args))


if __name__ == "__main__":
    main()
