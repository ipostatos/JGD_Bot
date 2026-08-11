"""Браузерный смоук всех страниц Mini App: ошибки консоли, битые ссылки, вёрстка.

Почему это есть: вся видимая логика приложения — в JS, и до сих пор её ловил
только глаз (панель под домашней полоской iPhone, невалидные имена иконок,
горизонтальный оверфлоу). Тест открывает каждую страницу в headless Chromium
на ширине телефона и проверяет то, что человек замечает первым.

Требует playwright + chromium; без них тесты пропускаются (в CI ставятся явно).
"""
import re
import socket
import threading
import time

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

PHONE = {"width": 390, "height": 844}

# Страницы Mini App: путь + чем на нём проверяется «страница ожила»
PAGES = [
    ("/index.html", ".grid .cell"),
    ("/guide.html", "body"),
    ("/calc.html", ".card"),
    ("/plan.html", "#pform"),
    ("/tools.html", ".row-card"),
    ("/about.html", ".card"),
    ("/news.html", "body"),
    ("/ai.html", "body"),
    ("/cockpit.html", "body"),
    ("/nip.html", "#nip"),
    ("/pkd.html", "#q"),
    ("/banks.html", ".card"),
    ("/zus_err.html", "body"),
    ("/ksef.html", ".tl-row"),
    ("/merge.html", "body"),
    ("/photo.html", "label.btn"),   # #pick — скрытый input, ждать его нельзя
    ("/article.html?id=glossary", "body"),
    ("/reader.html?file=docs/800plus.pdf&title=test", ".toolbar"),
]

IGNORE_URL_PARTS = ("telegram.org",)  # внешний скрипт TG: в CI его может не быть


@pytest.fixture(scope="module")
def base_url():
    """Поднимаем настоящий сервер: playwright ходит по HTTP, TestClient не годится."""
    import os

    os.environ["DISABLE_BOT"] = "1"
    os.environ.setdefault("BOT_TOKEN", "12345:TESTTOKEN")
    import uvicorn

    import server

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    cfg = uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(cfg)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started, "локальный сервер не поднялся"
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(timeout=10)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:                      # noqa: BLE001
            pytest.skip(f"chromium недоступен: {e}")
        yield b
        b.close()



def tap(page, selector):
    """Нажатие без ожидания actionability.

    Playwright требует, чтобы элемент был «стабилен» — не двигался между
    кадрами. На кнопках с `transform` при `:active` эта проверка в headless
    Chromium зависает: сам обработчик при этом работает, что подтверждается
    принудительным кликом. Поэтому проверяем то, что важно (кнопка видима
    и доступна), и отправляем событие напрямую — иначе тест меряет
    особенности эмуляции, а не поведение страницы.
    """
    el = page.locator(selector)
    assert el.is_visible() and el.is_enabled(), selector
    el.dispatch_event("click")


def _open(browser, url):
    """Открывает страницу, возвращает (page, errors, bad_responses)."""
    ctx = browser.new_context(viewport=PHONE, device_scale_factor=2, is_mobile=True,
                              has_touch=True)
    page = ctx.new_page()
    errors, bad = [], []
    page.on("console", lambda m: errors.append(f"console: {m.text}")
            if m.type == "error" and not any(x in m.text for x in IGNORE_URL_PARTS) else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

    def on_response(r):
        if r.status >= 400 and not any(x in r.url for x in IGNORE_URL_PARTS):
            bad.append(f"{r.status} {r.url}")

    page.on("response", on_response)
    page.goto(url, wait_until="networkidle")
    return ctx, page, errors, bad


@pytest.mark.parametrize("path,ready", PAGES, ids=[p[0] for p in PAGES])
def test_page_is_clean(browser, base_url, path, ready):
    ctx, page, errors, bad = _open(browser, base_url + path)
    try:
        page.wait_for_selector(ready, timeout=8000)
        page.wait_for_timeout(400)                  # добить отложенные fetch/иконки

        assert not errors, f"{path}: ошибки в консоли -> {errors}"
        assert not bad, f"{path}: битые ответы -> {bad}"

        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 0, f"{path}: горизонтальный оверфлоу {overflow}px"

        # иконки: каждый data-icon должен превратиться в <svg> (битое имя = пустое место)
        empty = page.evaluate(
            "() => [...document.querySelectorAll('[data-icon]')]"
            ".filter(e => !e.querySelector('svg')).map(e => e.dataset.icon)")
        assert not empty, f"{path}: не отрисованы иконки {empty}"
    finally:
        ctx.close()


def test_bottom_bars_respect_safe_area(browser, base_url):
    """Фикс-панели должны учитывать safe-area, иначе на iPhone их режет полоска.

    Headless не умеет подставлять настоящие вырезы, а инжектить их самим
    бессмысленно — так тест проверял бы собственный CSS. Поэтому проверяем сам
    инвариант, который однажды и был нарушен в reader.html: пара «viewport-fit=
    cover в meta» + «env(safe-area-inset-bottom) в правиле панели». Без первого
    env() всегда 0, без второго панель не отступает.
    """
    for path, bar in (("/index.html", ".dock"),
                      ("/reader.html?file=docs/800plus.pdf", ".toolbar")):
        ctx, page, _, _ = _open(browser, base_url + path)
        try:
            page.wait_for_selector(bar, timeout=8000)
            viewport = page.get_attribute("meta[name=viewport]", "content") or ""
            assert "viewport-fit=cover" in viewport, (
                f"{path}: без viewport-fit=cover env(safe-area-*) всегда 0")

            decls = page.evaluate(
                """(sel) => {
                    const out = [];
                    for (const sheet of document.styleSheets) {
                        let rules; try { rules = sheet.cssRules } catch (e) { continue }
                        for (const r of rules || []) {
                            if (r.selectorText && r.selectorText.includes(sel))
                                out.push(r.cssText);
                        }
                    }
                    return out;
                }""", bar)
            assert any("safe-area-inset-bottom" in d for d in decls), (
                f"{path}: правило {bar} не отступает на safe-area -> {decls}")
        finally:
            ctx.close()


def test_zus_stages_match_python_mirror(browser, base_url):
    """JS-этапы ZUS и Python-зеркало (profiles.stage_dates) должны совпадать.

    Опорные даты — те же, что в test_profiles.py: расхождение здесь означало бы
    разные сроки перехода на Duży ZUS в приложении и в пушах.
    """
    import profiles
    from datetime import date

    cases = [("2026-03-15", True), ("2026-03-01", True), ("2026-03-15", False)]
    ctx, page, _, _ = _open(browser, base_url + "/index.html")
    try:
        for reg, ulga in cases:
            js = page.evaluate(
                """([reg, ulga]) => {
                    const d = window.stageDates(new Date(reg + 'T00:00:00'), ulga);
                    const iso = x => x ? new Date(x.getTime() - x.getTimezoneOffset()*60000)
                        .toISOString().slice(0,10) : null;
                    return [iso(d.ulgaEnd), iso(d.prefEnd)];
                }""", [reg, ulga])
            py = profiles.stage_dates(date.fromisoformat(reg), ulga)
            assert js == [py[0].isoformat() if py[0] else None, py[1].isoformat()], (
                f"расхождение для {reg}, ulga={ulga}: JS {js} vs Python {py}")
    finally:
        ctx.close()


def test_pkd_card_shows_only_its_own_exclusions(browser, base_url):
    """Карточка подкласса не показывает чужой текст.

    Карточка рендерит только первую строку описания, и она была чистой —
    загрязнение из PDF жило в исключениях и в дальних строках описания,
    то есть в ответе API и в будущих правилах движка, а не на экране.
    Тест сторожит и то, и другое: и текст на экране, и то, что приходит
    с сервера в карточку.
    """
    ctx, page, _, _ = _open(browser, base_url + "/pkd.html")
    try:
        page.fill("#q", "01.19.Z")
        tap(page, "#go")
        page.wait_for_selector("#out .card", timeout=15000)
        text = page.inner_text("#out")
        assert "Pozostałe uprawy rolne" in text
        assert "Uprawa roślin wieloletnich" not in text     # заголовок группы 01.2
        for marker in ("Grupa ta obejmuje", "Grupa ta nie obejmuje", "Dział ten"):
            assert marker not in text, marker

        # и то, что пришло с сервера: исключения подкласса — его собственные
        data = page.evaluate("""async () => {
            const r = await fetch('/api/pkd', {method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({q: '01.19.Z', limit: 1})});
            return (await r.json()).results[0];
        }""")
        assert any("buraka cukrowego" in e for e in data["excludes"])
        assert all(not e.startswith(("Grupa ta", "Dział ten", "SEKCJA"))
                   for e in data["excludes"] + data["includes"])
    finally:
        ctx.close()


def test_my_codes_show_what_regon_says(browser, base_url):
    """Блок «что говорит REGON» на экране «Мои коды».

    Ручка требует initData Telegram, поэтому подменяем сам ответ: проверяем
    отрисовку — версию классификации, расхождение реестров и то, что имена
    иконок настоящие (несуществующее имя оставляет пустой квадрат молча).
    """
    ctx, page, errors, _ = _open(browser, base_url + "/pkd.html")
    try:
        def answer(body):
            page.route("**/api/pkd/my", lambda route: route.fulfill(
                status=200, content_type="application/json", body=body))
            page.fill("#nip", "1133117581")
            tap(page, "#mygo")
            page.wait_for_selector("#myout .pkd-flag", timeout=15000)
            return page.inner_text("#myout")

        # запись переведена, но списки кодов у реестров разошлись
        text = answer(_my_codes(version="2025", diff=True))
        assert "переведена" in text
        assert "разошлись" in text and "96.21.Z" in text and "70.20.Z" in text

        # старая классификация: предупреждение со сроком
        page.unroute("**/api/pkd/my")
        text = answer(_my_codes(version="2007", diff=False))
        assert "31.12.2026" in text and "разошлись" not in text
        assert page.locator("#myout .pkd-flag.warn").count() >= 1

        icons = page.eval_on_selector_all(
            "#myout .fi", "els => els.map(e => e.innerHTML.length)")
        assert icons and all(n > 0 for n in icons), "иконка не отрисовалась"
        assert not errors, errors
    finally:
        ctx.close()


def _my_codes(version, diff):
    """Ответ ручки в двух состояниях, которые бывают на самом деле: расхождение
    реестров считается только внутри одной классификации."""
    import json

    regon = {"version": version, "codes": [],
             "note": ("REGON: запись уже переведена на новую классификацию PKD 2025."
                      if version == "2025" else
                      "REGON: запись всё ещё в классификации PKD 2007. Она действует "
                      "до 31.12.2026.")}
    if diff:
        regon |= {"only_in_ceidg": ["96.21.Z"], "only_in_regon": ["70.20.Z"],
                  "diff_note": "Списки кодов в CEIDG и REGON разошлись."}
    return json.dumps({
        "nip": "1133117581", "name": "Test", "outdated": 0, "source": "ceidg",
        "summary": "Все коды уже в новой классификации.", "note": "",
        "vat_warning_codes": [], "regon": regon,
        "items": [{"code": "62.10.B", "name": "Programowanie", "status": "ok",
                   "section": "K", "flags": []}]}, ensure_ascii=False)


# ── точный подбор: живёт только за включённым флагом ──────────────────────

@pytest.fixture(scope="module")
def dialog_url():
    """Отдельный сервер с включённым флагом: основной поднят с выключенным,
    и проверять надо оба состояния, а не переключать глобальную переменную
    под работающим приложением."""
    import os
    import uvicorn

    import server

    os.environ["DISABLE_BOT"] = "1"
    os.environ.setdefault("BOT_TOKEN", "12345:TESTTOKEN")
    os.environ["PKD_DIALOG_ENABLED"] = "true"
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    cfg = uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(cfg)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(timeout=10)
    os.environ.pop("PKD_DIALOG_ENABLED", None)


def test_dialog_mode_is_invisible_without_the_flag(browser, base_url):
    """Выключенный флаг: кнопки нет, к закрытой ручке никто не ходит,
    обычный поиск работает как раньше."""
    ctx = browser.new_context(viewport=PHONE)
    page = ctx.new_page()
    calls = []
    page.on("request", lambda r: calls.append(r.url) if "/api/pkd/dialog" in r.url else None)
    try:
        page.goto(base_url + "/pkd.html", wait_until="networkidle")
        assert not page.locator("#modes.on").count()
        assert page.locator("#dialog").is_hidden()
        assert not calls, "новый JS не должен трогать выключенную ручку"

        page.fill("#q", "производство мебели")
        tap(page, "#go")
        page.wait_for_selector("#out .card", timeout=15000)
        assert "31.00.Z" in page.inner_text("#out")
    finally:
        ctx.close()


def test_dialog_full_cycle(browser, dialog_url):
    """Сквозной путь человека: описание -> вопрос -> ответ -> код.

    Тот же запрос с другим ответом обязан давать другой код: если это
    сломается, диалог превратится в декорацию вокруг угадывания.
    """
    ctx = browser.new_context(viewport=PHONE)
    page = ctx.new_page()
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error"
            and not any(x in m.text for x in IGNORE_URL_PARTS) else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        page.goto(dialog_url + "/pkd.html", wait_until="networkidle")
        tap(page, "#mode-dialog")

        page.fill("#dlg-q", "Собираю кухни")
        tap(page, "#dlg-go")
        page.wait_for_selector("#dlg-question", timeout=15000)
        assert "работаете" in page.inner_text("#dlg-question")

        # кнопка неактивна, пока вариант не выбран
        assert page.locator("#dlg-next").is_disabled()
        page.check('input[value="built_in_furniture"]')
        tap(page, "#dlg-next")
        page.wait_for_selector("#dlg-result", timeout=15000)
        result = page.inner_text("#dlg-result")
        assert "43.32.Z" in result and "Zakładanie stolarki budowlanej" in result

        # другой ответ — другой код
        tap(page, "#dlg-reset")
        page.fill("#dlg-q", "Собираю кухни")
        tap(page, "#dlg-go")
        page.wait_for_selector("#dlg-question", timeout=15000)
        page.check('input[value="freestanding_furniture"]')
        tap(page, "#dlg-next")
        page.wait_for_selector("#dlg-result", timeout=15000)
        assert "95.24.Z" in page.inner_text("#dlg-result")

        # «Назад» снимает последний ответ и возвращает к вопросу
        tap(page, "#dlg-back")
        page.wait_for_selector("#dlg-question", timeout=15000)

        assert not errors, errors
    finally:
        ctx.close()


def test_dialog_package_and_technical_data(browser, dialog_url):
    """Пакет показывается по деятельностям, а внутренние поля наружу не текут."""
    ctx = browser.new_context(viewport=PHONE)
    page = ctx.new_page()
    try:
        page.goto(dialog_url + "/pkd.html", wait_until="networkidle")
        tap(page, "#mode-dialog")
        page.fill("#dlg-q", "ремонтирую и иногда делаю мебель")
        tap(page, "#dlg-go")
        page.wait_for_selector("#dlg-result", timeout=15000)
        text = page.inner_text("#dlg-result")
        assert "31.00.Z" in text and "95.24.Z" in text
        assert "несколько кодов" in text

        body = page.inner_text("body")
        for leak in ("input_fingerprint", "furniture-v1", "pkd-2025", "rules_version",
                     "activity.", "object.", "routing_hint"):
            assert leak not in body, leak
    finally:
        ctx.close()


def test_dialog_unrecognized_activity(browser, dialog_url):
    ctx = browser.new_context(viewport=PHONE)
    page = ctx.new_page()
    try:
        page.goto(dialog_url + "/pkd.html", wait_until="networkidle")
        tap(page, "#mode-dialog")
        page.fill("#dlg-q", "ремонт сайта")
        tap(page, "#dlg-go")
        page.wait_for_selector("#dlg-result", timeout=15000)
        text = page.inner_text("#dlg-result")
        assert "Не удалось определить деятельность" in text
        # про «чужой раздел» здесь писать нельзя: движок этого не знает
        assert "другой раздел" not in text
    finally:
        ctx.close()


def test_dialog_session_id_is_temporary_and_anonymous(browser, dialog_url):
    """Счётчик разговоров живёт во вкладке и ничего о человеке не знает.

    Проверяем ровно то, что обещано в приватности: идентификатор случайный,
    лежит в sessionStorage (а не в localStorage и не в cookie), и на сервер
    уходит заголовком, а не полем запроса.
    """
    ctx = browser.new_context(viewport=PHONE)
    page = ctx.new_page()
    sent = []
    page.on("request", lambda r: sent.append(
        (r.url, r.headers.get("x-dialog-session"), r.post_data or ""))
        if "/api/pkd/dialog" in r.url else None)
    try:
        page.goto(dialog_url + "/pkd.html", wait_until="networkidle")
        tap(page, "#mode-dialog")
        page.fill("#dlg-q", "Собираю кухни")
        tap(page, "#dlg-go")
        page.wait_for_selector("#dlg-question", timeout=15000)

        sid = page.evaluate("sessionStorage.getItem('pkd_dialog_sid')")
        assert sid and re.fullmatch(r"[0-9a-f]{16}", sid), sid
        assert page.evaluate("localStorage.length") == 0, "в localStorage ничего не кладём"
        assert not ctx.cookies(), "куки для счёта разговоров не заводим"

        assert sent, "запросы диалога не пойманы"
        for url, header, body in sent:
            assert header == sid, url
            assert sid not in body, "идентификатор не должен ехать в теле запроса"
        # «открыл режим» ушло отдельным событием, и в нём нет текста человека
        events = [b for u, _, b in sent if u.endswith("/event")]
        assert events and all("кухн" not in b.lower() for b in events), events
    finally:
        ctx.close()
