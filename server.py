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
MONITOR_INTERVAL = 6 * 3600


async def monitor_loop(app: FastAPI):
    """Каждые 6 ч: скрейп источников, классификация, пуши подписчикам."""
    import monitor
    await asyncio.sleep(90)  # дать боту стартовать
    while True:
        try:
            to_push = await asyncio.to_thread(monitor.run_once)
            bot = app.state.bot
            if bot:
                for item in to_push:
                    text = (f"🔔 <b>{item['source']}</b>\n{item['title']}\n\n"
                            f"{item['summary']}\n"
                            f"<a href=\"{item['url']}\">Читать оригинал</a>")
                    for uid in monitor.subs_for(item):
                        try:
                            await bot.send_message(
                                uid, text, parse_mode="HTML",
                                disable_web_page_preview=True)
                            await asyncio.sleep(0.1)
                        except Exception:
                            pass  # юзер заблокировал бота и т.п.
        except Exception as e:
            log.warning("monitor loop error: %s", e)
        await asyncio.sleep(MONITOR_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = []
    if BOT_TOKEN and not DISABLE_BOT:
        bot, dp, on_start = build_bot()
        app.state.bot = bot
        await on_start()
        tasks.append(asyncio.create_task(dp.start_polling(bot)))
        tasks.append(asyncio.create_task(monitor_loop(app)))
        log.info("bot polling + monitor started")
    else:
        app.state.bot = None
        log.info("bot disabled")
    yield
    for t in tasks:
        t.cancel()


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


@app.get("/api/news")
async def news_feed():
    import monitor
    return {"items": monitor.get_feed()}


@app.post("/api/subs")
async def subscribe(req: Request):
    import monitor
    body = await req.json()
    user = verify_init_data(body.get("initData", ""))
    if user is None:
        raise HTTPException(401, "bad initData")
    form = body.get("form") or "unknown"
    if form not in ("skala", "liniowy", "ryczalt", "unknown"):
        raise HTTPException(400, "bad form")
    monitor.upsert_sub(user["id"], form, bool(body.get("vat")))
    return {"ok": True}


@app.delete("/api/subs")
async def unsubscribe(req: Request):
    import monitor
    body = await req.json()
    user = verify_init_data(body.get("initData", ""))
    if user is None:
        raise HTTPException(401, "bad initData")
    monitor.delete_sub(user["id"])
    return {"ok": True}


@app.post("/api/ask")
async def api_ask(req: Request):
    import ai
    body = await req.json()
    user = verify_init_data(body.get("initData", ""))
    if user is None:
        raise HTTPException(401, "bad initData")
    res = await ai.ask(user["id"], body.get("question", ""),
                       body.get("profile") or None)
    if "error" in res:
        raise HTTPException(429 if "Лимит" in res["error"] else 400, res["error"])
    return res


@app.post("/api/infakt/connect")
async def infakt_connect(req: Request):
    import infakt
    body = await req.json()
    user = verify_init_data(body.get("initData", ""))
    if user is None:
        raise HTTPException(401, "bad initData")
    api_key = (body.get("api_key") or "").strip()
    if not (10 <= len(api_key) <= 200):
        raise HTTPException(400, "Похоже, это не ключ inFakt")
    if not await infakt.check_key(api_key):
        raise HTTPException(400, "inFakt не принял ключ — проверь и попробуй снова")
    infakt.save_key(user["id"], api_key)
    return {"ok": True}


@app.delete("/api/infakt")
async def infakt_disconnect(req: Request):
    import infakt
    body = await req.json()
    user = verify_init_data(body.get("initData", ""))
    if user is None:
        raise HTTPException(401, "bad initData")
    infakt.delete_key(user["id"])
    return {"ok": True}


@app.post("/api/infakt/summary")
async def infakt_summary(req: Request):
    import infakt
    body = await req.json()
    user = verify_init_data(body.get("initData", ""))
    if user is None:
        raise HTTPException(401, "bad initData")
    return await infakt.summary(user["id"])


TMP_DIR = ROOT / "tmp_files"
TMP_TTL = 3600  # секунда жизни временного файла
TMP_MAX = 25 * 1024 * 1024


def _tmp_cleanup():
    import time
    if not TMP_DIR.is_dir():
        return
    now = time.time()
    for f in TMP_DIR.iterdir():
        if now - f.stat().st_mtime > TMP_TTL:
            f.unlink(missing_ok=True)


@app.post("/api/tmpfile")
async def tmpfile(req: Request):
    """Принимает собранный утилитой файл, отдаёт временную ссылку (TTL 1 ч).

    Нужен мобильному Telegram WebView: скачивание blob там не работает,
    а openLink на свой URL открывает системный просмотрщик с печатью/шерингом.
    """
    import secrets
    user = verify_init_data(req.headers.get("x-init-data", ""))
    if user is None:
        raise HTTPException(401, "bad initData")
    ext = req.query_params.get("ext", "pdf")
    if ext not in ("pdf", "jpg"):
        raise HTTPException(400, "bad ext")
    body = await req.body()
    if not body or len(body) > TMP_MAX:
        raise HTTPException(413, "file too large")
    magic_ok = (ext == "pdf" and body[:4] == b"%PDF") or \
               (ext == "jpg" and body[:2] == b"\xff\xd8")
    if not magic_ok:
        raise HTTPException(400, "bad file")
    TMP_DIR.mkdir(exist_ok=True)
    _tmp_cleanup()
    token = secrets.token_urlsafe(16)
    (TMP_DIR / f"{token}.{ext}").write_bytes(body)
    return {"url": f"/tmpf/{token}.{ext}"}


@app.get("/tmpf/{name}")
async def tmpf(name: str):
    import re as _re
    if not _re.fullmatch(r"[A-Za-z0-9_-]+\.(pdf|jpg)", name):
        raise HTTPException(404)
    path = TMP_DIR / name
    if not path.is_file():
        raise HTTPException(404, "expired")
    media = "application/pdf" if name.endswith(".pdf") else "image/jpeg"
    return FileResponse(path, media_type=media)


@app.get("/")
async def index():
    return FileResponse(WEBAPP / "index.html")

app.mount("/", StaticFiles(directory=WEBAPP), name="webapp")
