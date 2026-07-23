"""JDG HUB — FastAPI (Mini App + API) и aiogram-бот в одном процессе.

Запуск: uvicorn server:app --host 127.0.0.1 --port $PORT
Локально без бота (иначе 409 с продом): DISABLE_BOT=1
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
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


def esc_html(s) -> str:
    """Экранирование для сообщений бота (parse_mode=HTML)."""
    import html
    return html.escape(str(s or ""), quote=True)


# initData подписан навсегда: Telegram не кладёт в него срок жизни, поэтому
# перехваченную строку можно переигрывать месяцами. Ограничиваем возрастом.
INIT_DATA_TTL = 24 * 3600


def verify_init_data(init_data: str) -> dict | None:
    """HMAC-проверка Telegram WebApp initData. Возвращает user или None."""
    if not BOT_TOKEN:
        return None          # без токена HMAC считается от пустого секрета
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        their_hash = pairs.pop("hash", "")
        check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        good = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(good, their_hash):
            return None
        age = time.time() - int(pairs.get("auth_date", 0))
        if not 0 <= age <= INIT_DATA_TTL:
            log.info("initData отклонён по возрасту: %.0f с", age)
            return None
        return json.loads(pairs.get("user", "{}"))
    except Exception:
        return None


# ── бот ──────────────────────────────────────────────────────────────────────
def build_bot():
    from aiogram import Bot, Dispatcher, F
    from aiogram.filters import CommandStart, Command
    from aiogram.types import (
        InlineKeyboardButton, InlineKeyboardMarkup,
        MenuButtonWebApp, Message, WebAppInfo,
    )

    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()

    def app_kb():
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📖 Открыть JDG HUB",
                                 web_app=WebAppInfo(url=WEBAPP_URL))
        ]])

    @dp.message(CommandStart())
    async def start(m: Message):
        await m.answer(
            "👋 Привет! Это <b>JDG HUB</b> — путеводитель по ИП в Польше.\n\n"
            "Внутри: весь гайд сообщества, калькуляторы ZUS и налогов, "
            "личный план с дедлайнами, раздел KSeF, проверка контрагента по NIP, "
            "коды ошибок ZUS и AI-ассистент по базе гайда.\n\n"
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

    @dp.message(Command("podderzhat"))
    async def support(m: Message):
        await m.answer(
            "Приложение денег не собирает. Гайд и инструменты пишут другие люди — "
            "поддержи их напрямую:\n\n"
            "❤️ <a href=\"https://t.me/JDG_PBH/234948\">Как поддержать авторов гайда</a>\n"
            "💻 <a href=\"https://github.com/sobolevbel/jdg\">github.com/sobolevbel/jdg</a> — сам гайд\n"
            "🔧 <a href=\"https://github.com/justandrei/jdg-tools\">github.com/justandrei/jdg-tools</a> — "
            "инструменты Андрея\n\n"
            "А меня лучше всего поддержать, попробовав другие мои проекты — "
            "они собраны в разделе «Ещё».",
            parse_mode="HTML", disable_web_page_preview=True)

    @dp.message(Command("nip"))
    async def nip_cmd(m: Message):
        arg = (m.text or "").partition(" ")[2].strip()
        if not arg:
            await m.answer("Пришли NIP контрагента — 10 цифр, можно с дефисами. "
                           "Проверю статус VAT, данные фирмы и счета в белом списке.")
            return
        await m.answer(await _nip_reply(arg), parse_mode="HTML",
                       disable_web_page_preview=True)

    @dp.message(Command("ksef"))
    async def ksef_cmd(m: Message):
        import profiles
        prof = await asyncio.to_thread(profiles.get, m.from_user.id)
        await m.answer(_ksef_reply(prof), parse_mode="HTML",
                       disable_web_page_preview=True, reply_markup=app_kb())

    @dp.message(Command("blad"))
    async def blad_cmd(m: Message):
        arg = (m.text or "").partition(" ")[2].strip()
        if not arg:
            await m.answer("Пришли код ошибки ZUS (8 цифр, например 69004101) — "
                           "объясню, что он значит и как чинить.")
            return
        await m.answer(_zus_error_reply(arg), parse_mode="HTML",
                       disable_web_page_preview=True)

    @dp.message(Command("pkd"))
    async def pkd_cmd(m: Message):
        arg = (m.text or "").partition(" ")[2].strip()
        if not arg:
            await m.answer(
                "Напиши, чем занимаешься — подберу коды PKD 2025 по официальному "
                "справочнику GUS.\n\nНапример: <code>/pkd я блогер</code>, "
                "<code>/pkd пишу код на питоне</code>, <code>/pkd маникюр на дому</code>.\n"
                "Старый код тоже приму — покажу, во что он превратился "
                "(<code>/pkd 62.01.Z</code>).", parse_mode="HTML")
            return
        await m.answer(await asyncio.to_thread(_pkd_reply, arg), parse_mode="HTML",
                       disable_web_page_preview=True, reply_markup=app_kb())

    # Голые цифры в личке: 10 — это NIP контрагента, 8 — код ошибки ZUS
    @dp.message(F.chat.type == "private", F.text.regexp(r"^[\d\s\-]{8,16}$"))
    async def digits_plain(m: Message):
        digits = "".join(ch for ch in m.text if ch.isdigit())
        if len(digits) == 10:
            await m.answer(await _nip_reply(digits), parse_mode="HTML",
                           disable_web_page_preview=True)
        elif len(digits) == 8:
            await m.answer(_zus_error_reply(digits), parse_mode="HTML",
                           disable_web_page_preview=True)

    async def on_start():
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="JDG HUB",
                                         web_app=WebAppInfo(url=WEBAPP_URL)))
        from aiogram.types import BotCommand
        await bot.set_my_commands([
            BotCommand(command="app", description="Открыть JDG HUB"),
            BotCommand(command="nip", description="Проверить контрагента по NIP"),
            BotCommand(command="pkd", description="Подобрать код PKD по деятельности"),
            BotCommand(command="ksef", description="KSeF — меня это уже касается?"),
            BotCommand(command="blad", description="Код ошибки ZUS — что делать"),
            BotCommand(command="podderzhat", description="Как поддержать авторов гайда"),
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
                    # текст чужой (скрейп + пересказ модели): без экранирования
                    # «<» из заголовка ломает parse_mode=HTML, а <a href> из
                    # источника подменил бы ссылку в нашем сообщении
                    src, title, summary = (esc_html(item.get(k, "")) for k in
                                           ("source", "title", "summary"))
                    url = item.get("url", "")
                    text = (f"🔔 <b>{src}</b>\n{title}\n\n{summary}\n"
                            + (f"<a href=\"{esc_html(url)}\">Читать оригинал</a>"
                               if url.startswith(("http://", "https://")) else ""))
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
        _tmp_cleanup()   # временные файлы юзеров: чистим по расписанию, а не
                         # только когда кто-то грузит новый
        await asyncio.sleep(MONITOR_INTERVAL)


async def deadline_loop(app: FastAPI):
    """Раз в час; шлёт напоминания в 9 утра по Варшаве (за 5 дней и за 1)."""
    import profiles
    from datetime import datetime
    from zoneinfo import ZoneInfo
    warsaw = ZoneInfo("Europe/Warsaw")
    await asyncio.sleep(120)
    while True:
        try:
            now = datetime.now(warsaw)
            if now.hour == 9:
                due = await asyncio.to_thread(profiles.due_pushes, now.date())
                bot = app.state.bot
                for d in due:
                    e, days = d["event"], d["days"]
                    when = "завтра" if days == 1 else f"через {days} дн."
                    text = (f"📅 <b>{when.capitalize()}, "
                            f"{e['d'].strftime('%d.%m')}: {e['title']}</b>\n{e['desc']}")
                    if e["key"].startswith("dra"):
                        text += await _dra_sum_line(d["profile"]["user_id"])
                    text += "\n\nДетали — в «Моём плане»."
                    try:
                        await bot.send_message(d["user_id"], text, parse_mode="HTML")
                        profiles.mark_sent(d["user_id"], d["key"])
                        await asyncio.sleep(0.1)
                    except Exception:
                        profiles.mark_sent(d["user_id"], d["key"])  # blocked и т.п.
                if due:
                    log.info("deadline pushes sent: %d", len(due))
        except Exception as e:
            log.warning("deadline loop error: %s", e)
        await asyncio.sleep(3600)


SIG_EMOJI = {"ok": "✅", "warn": "⚠️", "bad": "❌"}
SEV_LABEL = {"K": "❌ критическая — блокирует отправку",
             "Z": "⚠️ обычная — отправить можно",
             "I": "ℹ️ информация — исправлять нечего"}
_zus_errors: list | None = None


def zus_errors() -> list:
    """База кодов ошибок ZUS (webapp/zus_errors.json), читается один раз."""
    global _zus_errors
    if _zus_errors is None:
        try:
            _zus_errors = json.loads(
                (WEBAPP / "zus_errors.json").read_text(encoding="utf-8"))["errors"]
        except Exception as e:
            log.warning("zus_errors.json не прочитан: %s", e)
            _zus_errors = []
    return _zus_errors


def find_zus_error(query: str) -> dict | None:
    q = query.strip().lower()
    for e in zus_errors():
        if (e.get("code") or "").lower() == q:
            return e
    if not q:
        return None
    return next((e for e in zus_errors()
                 if q in " ".join([e["title_ru"], e["msg_pl"], e["why_ru"],
                                   *e.get("tags", [])]).lower()), None)


def _zus_error_reply(query: str) -> str:
    e = find_zus_error(query)
    if not e:
        return ("Такого кода в базе пока нет. Посмотри полный польский текст на вкладке "
                "«uwagi i błędy» и спроси AI-ассистента в приложении — он ищет и по тексту. "
                "Если ошибка новая, скинь её в чат @JDG_PBH, добавим в базу.")
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(e["fix_ru"], 1))
    return (f"<b>{e.get('code', '')} — {e['title_ru']}</b>\n"
            f"{SEV_LABEL.get(e['severity'], '')}\n"
            f"Документ: {', '.join(e.get('doc') or ['—'])}\n\n"
            f"<i>{e['msg_pl']}</i>\n\n"
            f"{e['why_ru']}\n\n<b>Что делать:</b>\n{steps}")


def _pkd_reply(query: str) -> str:
    """Ответ бота на /pkd: тот же движок, что у API, но короче — три кода."""
    import pkd
    r = pkd.lookup(query, limit=3)
    if not r["results"]:
        return ("Не нашёл подходящих кодов. Опиши деятельность обычными словами — "
                "«делаю сайты», «маникюр на дому», «вожу такси» — или пришли сам код.")
    out = []
    if r.get("migration"):
        mg = r["migration"]
        out.append(f"<b>{esc_html(mg['old'])}</b> — код старой классификации "
                   f"PKD 2007 ({esc_html(mg['old_name'])}).\nСейчас это:")
    for x in r["results"]:
        block = [f"<b>{esc_html(x['code'])}</b> — {esc_html(x['name'])}"]
        if x["includes"]:
            block.append(f"<i>{esc_html(x['includes'][0][:220])}</i>")
        for f in x["flags"]:
            mark = "⚠️" if f["level"] == "warn" else "ℹ️"
            block.append(f"{mark} <b>{esc_html(f['title'])}</b>: {esc_html(f['text'])}")
        if x["was_pkd2007"]:
            block.append(f"Раньше это было {esc_html(', '.join(x['was_pkd2007']))}.")
        out.append("\n".join(block))
    out.append(f"<i>{esc_html(r['note'])}</i>")
    return "\n\n".join(out)


async def _nip_reply(raw_nip: str) -> str:
    """Ответ бота на NIP: карточка контрагента в HTML (тот же движок, что API)."""
    import registries
    try:
        d = await registries.check_nip(raw_nip)
    except Exception as e:
        log.warning("bot nip check failed: %s", e)
        return "Реестры сейчас не отвечают — попробуй чуть позже."
    if not d.get("valid"):
        return d.get("error", "Не похоже на NIP.")
    # данные из чужих реестров идут в сообщение с parse_mode=HTML —
    # название фирмы со знаком «<» иначе ломает разметку
    head = (f"<b>{esc_html(d['name'] or 'Название не найдено')}</b>\n"
            f"NIP {esc_html(d['nip'])}")
    facts = [f"{k}: {esc_html(v)}" for k, v in (
        ("REGON", d.get("regon")), ("KRS", d.get("krs")),
        ("Адрес", d.get("address")), ("PKD", ", ".join(d.get("pkd") or [])),
    ) if v]
    signals = "\n".join(f"{SIG_EMOJI[s['level']]} {esc_html(s['text'])}"
                        for s in d["signals"])
    accounts = d.get("accounts") or []
    acc_line = (f"\n\n💳 Счетов в белом списке: {len(accounts)}"
                if accounts else "\n\n💳 Счетов в белом списке нет")
    return (f"{head}\n" + ("\n".join(facts) + "\n" if facts else "")
            + f"\n{signals}{acc_line}\n\n"
            f"Полная карточка и проверка конкретного счёта — в приложении.")


async def _ksef_sales(user_id: int) -> dict | None:
    """Свод по фактурам inFakt для порога 10 000 zł (если ключ подключён)."""
    import ksef
    try:
        import infakt
        key = infakt.load_key(user_id)
        if not key:
            return None
        cached = infakt.cache_get(user_id, "ksef_sales")
        if cached:
            return cached
        from hermes.infakt import Infakt
        invoices = await asyncio.to_thread(lambda: Infakt(key).invoices())
        sales = ksef.sales_from_invoices(invoices)
        infakt.cache_put(user_id, "ksef_sales", sales)
        return sales
    except Exception as e:
        log.warning("ksef sales for %s failed: %s", user_id, e)
        return None


def _ksef_reply(profile: dict | None) -> str:
    """Короткий статус KSeF для чата (детали — в Mini App)."""
    import ksef
    st = ksef.status(profile)
    lines = "\n".join(f"{SIG_EMOJI[l['level']]} {l['text']}" for l in st["lines"][:4])
    return (f"<b>KSeF: {st['headline']}</b>\n\n{lines}\n\n"
            f"До 1 января 2027 (когда обязаны все и включаются штрафы) — "
            f"{st['days_to_full']} дн.\nЭтапы, чек-лист и FAQ — в приложении, "
            f"раздел «KSeF».")


async def _dra_sum_line(user_id: int) -> str:
    """Если подключён inFakt — реальная сумма ZUS в напоминание."""
    try:
        import infakt
        if infakt.load_key(user_id):
            s = await infakt.summary(user_id)
            zus = s.get("zus") or {}
            if zus.get("total") is not None and not zus.get("paid"):
                return f"\n💰 По данным inFakt к оплате: <b>{zus['total']:.2f} zł</b>"
    except Exception:
        pass
    return ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = []
    if BOT_TOKEN and not DISABLE_BOT:
        bot, dp, on_start = build_bot()
        app.state.bot = bot
        await on_start()
        tasks.append(asyncio.create_task(dp.start_polling(bot)))
        tasks.append(asyncio.create_task(monitor_loop(app)))
        tasks.append(asyncio.create_task(deadline_loop(app)))
        log.info("bot polling + monitor started")
    else:
        app.state.bot = None
        log.info("bot disabled")
    yield
    # Отмены мало: monitor/deadline-циклы уходят в asyncio.to_thread (скрейпинг,
    # запросы к inFakt), а поток отменить нельзя — интерпретатор ждёт его на
    # выходе. Из-за этого рестарт при деплое висел ~90 с и Caddy отдавал 502.
    # Ждём завершения ограниченно; добить процесс — дело systemd (TimeoutStopSec).
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.wait(tasks, timeout=5)


app = FastAPI(lifespan=lifespan)


@app.get("/api/health")
async def health():
    return {"ok": True, "bot": app.state.bot is not None}


@app.get("/api/news")
async def news_feed():
    import monitor
    return {"items": monitor.get_feed()}


@app.post("/api/profile/get")
async def api_profile_get(req: Request):
    """Серверная копия профиля — чтобы он не терялся при смене устройства.

    POST, а не GET: initData слишком длинный для строки запроса и не должен
    попадать в логи прокси.
    """
    import profiles
    body = await req.json()
    user = verify_init_data(body.get("initData", ""))
    if user is None:
        raise HTTPException(401, "bad initData")
    return {"profile": profiles.get(user["id"])}


@app.post("/api/profile")
async def api_profile(req: Request):
    """Частичное обновление серверного профиля (только присланные поля)."""
    import profiles
    body = await req.json()
    user = verify_init_data(body.get("initData", ""))
    if user is None:
        raise HTTPException(401, "bad initData")
    fields = {}
    if "form" in body:
        if body["form"] not in profiles.FORMS:
            raise HTTPException(400, "bad form")
        fields["form"] = body["form"]
    if "reg" in body:
        reg = body["reg"]
        if reg is not None:
            import re as _re
            if not (isinstance(reg, str) and _re.fullmatch(r"\d{4}-\d{2}-\d{2}", reg)):
                raise HTTPException(400, "bad reg date")
        fields["reg"] = reg
    for flag in ("vat", "ulga", "news_sub", "dl_sub"):
        if flag in body:
            fields[flag] = int(bool(body[flag]))
    profiles.upsert(user["id"], **fields)
    return {"ok": True, "profile": profiles.get(user["id"])}


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


@app.post("/api/ksef")
async def api_ksef(req: Request):
    """Персональный статус KSeF. initData необязателен: без него отвечаем по
    присланному профилю, с ним — добавляем расчёт порога по фактурам inFakt."""
    import ksef
    body = await req.json()
    user = verify_init_data(body.get("initData", ""))
    profile = body.get("profile") or None
    sales = await _ksef_sales(user["id"]) if user else None
    return {"status": ksef.status(profile, sales=sales),
            "infakt": sales is not None}


@app.post("/api/pkd")
async def api_pkd(req: Request):
    """Подбор кода PKD по описанию деятельности. Работает офлайн по данным GUS,
    без обращений к платному API, поэтому initData здесь не требуется."""
    import pkd
    body = await req.json()
    query = (body.get("q") or "").strip()[:200]
    if not query:
        raise HTTPException(400, "Опиши, чем занимаешься, или пришли код PKD")
    return await asyncio.to_thread(pkd.lookup, query, int(body.get("limit", 5)))


@app.post("/api/pkd/my")
async def api_pkd_my(req: Request):
    """Коды PKD, которые реально стоят в CEIDG у этого NIP, + разбор.
    Лимиты те же, что у проверки контрагента: ходим в чужой реестр."""
    import pkd
    import registries
    body = await req.json()
    user = verify_init_data(body.get("initData", ""))
    if user is None:
        raise HTTPException(401, "bad initData")
    _limit_or_429("nip", user["id"])
    try:
        d = await registries.check_nip((body.get("nip") or "").strip())
    except Exception as e:
        log.warning("pkd/my nip check failed: %s", e)
        raise HTTPException(502, "Реестры не ответили — попробуй ещё раз")
    if not d.get("valid"):
        raise HTTPException(400, d.get("error", "Не похоже на NIP."))
    codes = d.get("pkd") or []
    if not codes:
        return {"nip": d["nip"], "name": d.get("name"), "codes": [],
                "note": "В CEIDG по этому NIP кодов не видно. Так бывает у юрлиц "
                        "(они в KRS, а не в CEIDG) и при закрытой записи."}
    return {"nip": d["nip"], "name": d.get("name"),
            **await asyncio.to_thread(pkd.audit, codes)}


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


def _month_arg(body: dict) -> str:
    import re
    from datetime import date
    month = (body.get("month") or "").strip() or date.today().strftime("%Y-%m")
    if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", month):
        raise HTTPException(400, "month: ожидается YYYY-MM")
    return month


@app.post("/api/infakt/overview")
async def infakt_overview(req: Request):
    """Кокпит: финансовый обзор (доход/расходы/налоги, VAT-прогноз, здоровье)."""
    import cockpit
    body = await req.json()
    user = verify_init_data(body.get("initData", ""))
    if user is None:
        raise HTTPException(401, "bad initData")
    try:
        return await cockpit.overview(user["id"])
    except Exception as e:
        log.warning("cockpit.overview failed for %s: %s", user["id"], e)
        raise HTTPException(502, "inFakt не ответил — попробуй ещё раз")


@app.post("/api/infakt/close")
async def infakt_close(req: Request):
    """Кокпит: закрытие месяца движком Hermes (чек-лист + сверка)."""
    import cockpit
    body = await req.json()
    user = verify_init_data(body.get("initData", ""))
    if user is None:
        raise HTTPException(401, "bad initData")
    try:
        return await cockpit.close(user["id"], _month_arg(body))
    except Exception as e:
        log.warning("cockpit.close failed for %s: %s", user["id"], e)
        raise HTTPException(502, "inFakt не ответил — попробуй ещё раз")


@app.post("/api/infakt/pay")
async def infakt_pay(req: Request):
    """Кокпит: платёжные пакеты ZUS + налог за месяц."""
    import cockpit
    body = await req.json()
    user = verify_init_data(body.get("initData", ""))
    if user is None:
        raise HTTPException(401, "bad initData")
    try:
        return await cockpit.pay(user["id"], _month_arg(body))
    except Exception as e:
        log.warning("cockpit.pay failed for %s: %s", user["id"], e)
        raise HTTPException(502, "inFakt не ответил — попробуй ещё раз")


def _limit_or_429(bucket: str, user_id: int):
    """Отдаём 429 с внятным текстом — фронт показывает его как есть."""
    import ratelimit
    why = ratelimit.check(bucket, user_id)
    if why:
        log.info("rate limit %s для %s: %s", bucket, user_id, why)
        raise HTTPException(429, why)


@app.post("/api/nip")
async def api_nip(req: Request):
    """Проверка контрагента по NIP (White List + VIES + GUS/CEIDG по ключам)."""
    import registries
    body = await req.json()
    user = verify_init_data(body.get("initData", ""))
    if user is None:
        raise HTTPException(401, "bad initData")
    _limit_or_429("nip", user["id"])
    try:
        res = await registries.check_nip(body.get("nip", ""))
    except Exception as e:
        log.warning("nip check failed: %s", e)
        raise HTTPException(502, "Реестры не ответили — попробуй ещё раз")
    if not res.get("valid"):
        raise HTTPException(400, res.get("error", "плохой NIP"))
    return res


@app.post("/api/nip/account")
async def api_nip_account(req: Request):
    """Проверка счёта контрагента в белом списке перед оплатой."""
    import registries
    body = await req.json()
    user = verify_init_data(body.get("initData", ""))
    if user is None:
        raise HTTPException(401, "bad initData")
    _limit_or_429("nip", user["id"])
    try:
        res = await registries.check_account(body.get("nip", ""), body.get("account", ""))
    except Exception as e:
        log.warning("account check failed: %s", e)
        raise HTTPException(502, "Белый список не ответил — попробуй ещё раз")
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "плохие данные"))
    return res


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
    # TTL раньше был на словах: чистка запускалась только при новой загрузке,
    # и без неё чужой документ лежал на диске и отдавался по ссылке вечно.
    if time.time() - path.stat().st_mtime > TMP_TTL:
        path.unlink(missing_ok=True)
        raise HTTPException(404, "expired")
    media = "application/pdf" if name.endswith(".pdf") else "image/jpeg"
    return FileResponse(path, media_type=media)


@app.get("/")
async def index():
    return FileResponse(WEBAPP / "index.html")

app.mount("/", StaticFiles(directory=WEBAPP), name="webapp")
