"""JDG Гид — FastAPI (Mini App + API) и aiogram-бот в одном процессе.

Запуск: uvicorn server:app --host 127.0.0.1 --port $PORT
Локально без бота (иначе 409 с продом): DISABLE_BOT=1
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qsl

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("jdg")

ROOT = Path(__file__).parent
WEBAPP = ROOT / "webapp"
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "http://127.0.0.1:4400")
DISABLE_BOT = os.environ.get("DISABLE_BOT") == "1"

TIP_AMOUNTS = (25, 50, 100, 250)


def verify_init_data(init_data: str) -> dict | None:
    """HMAC-проверка Telegram WebApp initData. Возвращает user или None."""
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        their_hash = pairs.pop("hash", "")
        check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        good = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(good, their_hash):
            return None
        return json.loads(pairs.get("user", "{}"))
    except Exception:
        return None


# ── бот ──────────────────────────────────────────────────────────────────────
def build_bot():
    from aiogram import Bot, Dispatcher, F
    from aiogram.filters import CommandStart, Command
    from aiogram.types import (
        InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice,
        MenuButtonWebApp, Message, PreCheckoutQuery, WebAppInfo,
    )

    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()

    def app_kb():
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📖 Открыть JDG Гид",
                                 web_app=WebAppInfo(url=WEBAPP_URL))
        ]])

    @dp.message(CommandStart())
    async def start(m: Message):
        await m.answer(
            "👋 Привет! Это <b>JDG Гид</b> — путеводитель по ИП в Польше.\n\n"
            "Внутри: весь гайд сообщества, калькуляторы ZUS и налогов, "
            "чек-листы, календарь предпринимателя и словарь терминов.\n\n"
            "Основано на гайде сообщества "
            "<a href=\"https://sobolevbel.github.io/jdg/\">sobolevbel.github.io/jdg</a> "
            "и чате @JDG_PBH.\n\n"
            "⚠️ Информация справочная, не является налоговой или юридической "
            "консультацией.",
            reply_markup=app_kb(), parse_mode="HTML",
            disable_web_page_preview=True)

    @dp.message(Command("app"))
    async def app_cmd(m: Message):
        await m.answer("Открывай 👇", reply_markup=app_kb())

    @dp.message(Command("donate"))
    async def donate(m: Message):
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"⭐ {a}", callback_data=f"tip:{a}")
            for a in TIP_AMOUNTS
        ]])
        await m.answer("Поддержать проект звёздами Telegram:", reply_markup=kb)

    @dp.callback_query(F.data.startswith("tip:"))
    async def tip_cb(q):
        amount = int(q.data.split(":")[1])
        await bot.send_invoice(
            chat_id=q.from_user.id, title="Поддержка JDG Гид",
            description="Спасибо, что поддерживаешь развитие проекта!",
            payload=f"tip-{amount}", currency="XTR",
            prices=[LabeledPrice(label="Донат", amount=amount)])
        await q.answer()

    @dp.pre_checkout_query()
    async def pre_checkout(q: PreCheckoutQuery):
        await q.answer(ok=True)

    @dp.message(F.successful_payment)
    async def paid(m: Message):
        await m.answer("❤️ Спасибо за поддержку! Это мотивирует развивать гайд.")

    async def on_start():
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="JDG Гид",
                                         web_app=WebAppInfo(url=WEBAPP_URL)))
        from aiogram.types import BotCommand
        await bot.set_my_commands([
            BotCommand(command="app", description="Открыть JDG Гид"),
            BotCommand(command="donate", description="Поддержать проект ⭐"),
        ])

    return bot, dp, on_start


# ── приложение ───────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    if BOT_TOKEN and not DISABLE_BOT:
        bot, dp, on_start = build_bot()
        app.state.bot = bot
        await on_start()
        task = asyncio.create_task(dp.start_polling(bot))
        log.info("bot polling started")
    else:
        app.state.bot = None
        log.info("bot disabled")
    yield
    if task:
        task.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/api/health")
async def health():
    return {"ok": True, "bot": app.state.bot is not None}


@app.post("/api/tips")
async def tips(req: Request):
    """Stars-инвойс для оплаты прямо в Mini App (openInvoice)."""
    body = await req.json()
    user = verify_init_data(body.get("initData", ""))
    if user is None:
        raise HTTPException(401, "bad initData")
    amount = body.get("amount")
    if amount not in TIP_AMOUNTS:
        raise HTTPException(400, "bad amount")
    if app.state.bot is None:
        raise HTTPException(503, "bot offline")
    link = await app.state.bot.create_invoice_link(
        title="Поддержка JDG Гид",
        description="Спасибо, что поддерживаешь развитие проекта!",
        payload=f"tip-{amount}", currency="XTR",
        prices=[{"label": "Донат", "amount": amount}])
    return {"link": link}


@app.get("/")
async def index():
    return FileResponse(WEBAPP / "index.html")

app.mount("/", StaticFiles(directory=WEBAPP), name="webapp")
