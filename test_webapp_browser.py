"""Браузерный смоук всех страниц Mini App: ошибки консоли, битые ссылки, вёрстка.

Почему это есть: вся видимая логика приложения — в JS, и до сих пор её ловил
только глаз (панель под домашней полоской iPhone, невалидные имена иконок,
горизонтальный оверфлоу). Тест открывает каждую страницу в headless Chromium
на ширине телефона и проверяет то, что человек замечает первым.

Требует playwright + chromium; без них тесты пропускаются (в CI ставятся явно).
"""
import os
import re
from pathlib import Path
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
# Скриншоты для ревью экранов: JDG_SHOTS=<папка> — снимки, без переменной — нет
SHOTS = Path(os.environ["JDG_SHOTS"]) if os.environ.get("JDG_SHOTS") else None


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


def test_safe_nav_rejects_dangerous_targets(browser, base_url):
    """?from= из ссылки идёт в навигацию — safeNav обязан пропускать только
    относительный путь к своей странице, иначе from=javascript:… исполнялся бы."""
    ctx, page, _, _ = _open(browser, base_url + "/index.html")
    try:
        cases = page.evaluate("""() => ({
            js: window.safeNavTarget("javascript:alert(1)"),
            data: window.safeNavTarget("data:text/html,x"),
            abs: window.safeNavTarget("https://evil.com/x"),
            proto: window.safeNavTarget("//evil.com"),
            slash: window.safeNavTarget("/etc/passwd"),
            good: window.safeNavTarget("tools.html"),
            query: window.safeNavTarget("zus_err.html?from=tools.html"),
        })""")
        assert cases["js"] == "index.html"      # схема отсечена
        assert cases["data"] == "index.html"
        assert cases["abs"] == "index.html"
        assert cases["proto"] == "index.html"
        assert cases["slash"] == "index.html"
        assert cases["good"] == "tools.html"    # свои страницы проходят
        assert cases["query"] == "zus_err.html?from=tools.html"
    finally:
        ctx.close()


def test_nip_card_renders_without_accounts_field(browser, base_url):
    """Ответ без поля accounts (фирма только в CEIDG/GUS) не должен ронять
    рендер карточки TypeError'ом."""
    ctx, page, errors, _ = _open(browser, base_url + "/nip.html")
    try:
        import json
        body = json.dumps({
            "nip": "1133117581", "valid": True, "name": "Тест Sp. z o.o.",
            "vat_status": "brak", "score": 60, "level": "warn", "cached": False,
            "signals": [], "sources": {"whitelist": "empty", "vies": "empty",
                                       "gus": "ok", "ceidg": "ok"},
            "pkd": [], "vies": None,
        }, ensure_ascii=False)   # без accounts / accounts_total
        page.route("**/api/nip", lambda route: route.fulfill(
            status=200, content_type="application/json", body=body))
        page.fill("#nip", "1133117581")
        tap(page, "#go")
        page.wait_for_selector("#out .card", timeout=15000)
        assert "Тест Sp. z o.o." in page.inner_text("#out")
        assert not errors, errors        # ни одной ошибки в консоли
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

        # коды из REGON при аварии CEIDG: сбой не выдаётся за «вы юрлицо из KRS»
        page.unroute("**/api/pkd/my")
        text = answer(_my_codes(version="2025", diff=False,
                                source="gus", ceidg_state="error"))
        assert "CEIDG сейчас не отвечает" in text
        assert "юрлиц" not in text

        # а честное «записи нет» от самого реестра — выдаётся
        page.unroute("**/api/pkd/my")
        text = answer(_my_codes(version="2025", diff=False,
                                source="gus", ceidg_state="empty"))
        assert "юрлиц" in text and "KRS" in text

        icons = page.eval_on_selector_all(
            "#myout .fi", "els => els.map(e => e.innerHTML.length)")
        assert icons and all(n > 0 for n in icons), "иконка не отрисовалась"
        assert not errors, errors
    finally:
        ctx.close()


def _my_codes(version, diff, source="ceidg", ceidg_state="ok"):
    """Ответ ручки в состояниях, которые бывают на самом деле: расхождение
    реестров считается только внутри одной классификации, а «коды из REGON»
    случается и у юрлиц (в CEIDG записи нет), и при аварии CEIDG."""
    import json

    regon = {"version": version, "outdated_version": "2007" in version, "codes": [],
             "note": ("REGON: запись уже переведена на новую классификацию PKD 2025."
                      if version == "2025" else
                      "REGON: запись всё ещё в классификации PKD 2007. Она действует "
                      "до 31.12.2026.")}
    if diff:
        regon |= {"only_in_ceidg": ["96.21.Z"], "only_in_regon": ["70.20.Z"],
                  "diff_note": "Списки кодов в CEIDG и REGON разошлись."}
    return json.dumps({
        "nip": "1133117581", "name": "Test", "outdated": 0, "source": source,
        "ceidg_state": ceidg_state,
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


def test_dialog_primary_code_can_be_chosen(browser, dialog_url):
    """Режим «коды и главный код» доходит до конца: вопрос о выручке под
    кодами, ответ даёт главный код. Раньше здесь был тупик."""
    ctx = browser.new_context(viewport=PHONE)
    page = ctx.new_page()
    try:
        page.goto(dialog_url + "/pkd.html", wait_until="networkidle")
        tap(page, "#mode-dialog")
        page.check('input[name="dlg-intent"][value="find_codes_and_primary"]')
        page.fill("#dlg-q", "Произвожу мебель и ремонтирую старую")
        tap(page, "#dlg-go")
        page.wait_for_selector("#dlg-result [data-primary]", timeout=15000)
        assert page.locator("#dlg-result [data-primary]").count() == 2
        tap(page, '#dlg-result [data-primary="repair_or_restore"]')
        page.wait_for_selector("#dlg-result >> text=по вашему ответу", timeout=15000)
        assert "95.24.Z" in page.inner_text("#dlg-result")
        assert not page.locator("#dlg-result [data-primary]").count()
    finally:
        ctx.close()


def test_dialog_design_is_asked_not_lost(browser, dialog_url):
    ctx = browser.new_context(viewport=PHONE)
    page = ctx.new_page()
    try:
        page.goto(dialog_url + "/pkd.html", wait_until="networkidle")
        tap(page, "#mode-dialog")
        page.fill("#dlg-q", "проектирую мебель")
        tap(page, "#dlg-go")
        page.wait_for_selector("#dlg-question", timeout=15000)
        page.check('input[value="design_only"]')
        tap(page, "#dlg-next")
        page.wait_for_selector("#dlg-result", timeout=15000)
        assert "74.11.Z" in page.inner_text("#dlg-result")
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


def test_calc_core_matches_python_mirror_by_year(browser, base_url):
    """calc.js и calc.py обязаны считать одинаково для каждого года из данных.

    До ноября 2026 зеркало ничто не сверяло, а годы стали жить в обоих
    ядрах — расхождение дало бы разные цифры в калькуляторе и у ассистента.
    """
    import calc
    ctx, page, _, _ = _open(browser, base_url + "/calc.html")
    try:
        page.wait_for_function("window.YEARS && window.compareForms")
        for y in map(int, calc.YEARS["years"]):
            js = page.evaluate("""(y) => ({
                pref: spoleczneMonthly('pref', true, y), duzy: spoleczneMonthly('duzy', true, y),
                skala: zdrowotnaMonthly('skala', 90000, y), ryc: zdrowotnaMonthly('ryczalt', 100000, y),
                missing: missingForCompare(y),
                cmp: compareForms(240000, 12000, 'pref', false, 0.12, y),
            })""", y)
            assert js["pref"] == calc.spoleczne_monthly("pref", True, y), y
            assert js["duzy"] == calc.spoleczne_monthly("duzy", True, y), y
            assert js["skala"] == calc.zdrowotna_monthly("skala", 90000, y), y
            assert js["ryc"] == calc.zdrowotna_monthly("ryczalt", 100000, y), y
            assert js["missing"] == calc.missing_for_compare(y), y
            assert js["cmp"] == calc.compare_forms(240000, 12000, "pref", False, 0.12, y), y
        cur = page.evaluate("currentRatesYear()")
        assert cur == calc.current_year()
    finally:
        ctx.close()


def test_calc_shows_2027_honestly(browser, base_url):
    """Год без ставок не выдаёт прошлогодние цифры за новые."""
    ctx, page, errors, _ = _open(browser, base_url + "/calc.html")
    try:
        page.wait_for_selector("#zyear button")
        years = page.eval_on_selector_all("#zyear button", "bs => bs.map(b => b.dataset.v)")
        assert "2027" in years and "2025" in years
        page.click("#zyear button[data-v='2027']")      # ryczałt выбран по умолчанию
        assert "объявят в январе" in page.inner_text("#zres")
        page.click("#tabs button[data-t='cmp']")
        page.click("#cyear button[data-v='2027']")
        txt = page.inner_text("#cres")
        assert "пока не посчитать" in txt and "шкала PIT" in txt
        assert "выгоднее всего" not in txt
        assert not errors, errors
    finally:
        ctx.close()


# ── wFirma: экраны подключения и кокпит ─────────────────────────────────────
TG_STUB = """window.Telegram = { WebApp: { initData: 'stub', initDataUnsafe: {},
  ready(){}, expand(){}, setHeaderColor(){}, setBackgroundColor(){},
  BackButton: { show(){}, hide(){}, onClick(){}, offClick(){} },
  HapticFeedback: { impactOccurred(){}, notificationOccurred(){}, selectionChanged(){} },
  themeParams: {}, colorScheme: 'light', onEvent(){}, openLink(){}, close(){} } };"""


def _wfirma_page(browser, base_url, routes, storage=None):
    """Страница с эмуляцией Telegram и подменёнными ручками wFirma."""
    import json as _json
    ctx = browser.new_context(viewport=PHONE)
    ctx.add_init_script(TG_STUB)
    if storage:
        ctx.add_init_script("".join(
            f"localStorage.setItem({_json.dumps(k)}, {_json.dumps(v)});" for k, v in storage.items()))
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/telegram.org/**", lambda r: r.abort())

    def health(route):
        route.fulfill(json={"ok": True, "bot": False, "pkd_dialog": False,
                            "ai": None, "ai_reason": None, "wfirma": True})
    page.route("**/api/health", health)
    for pattern, fn in routes.items():
        page.route(pattern, fn)
    return ctx, page, errors


def test_wfirma_connect_screens(browser, base_url):
    """«Мой план»: вкладка wFirma → ключи → выбор фирмы → сводка."""
    import json as _json
    calls = []

    def connect(route):
        body = _json.loads(route.request.post_data or "{}")
        calls.append(body)
        if not body.get("company_id"):
            route.fulfill(status=409, json={"detail": "Выбери фирму", "companies": [
                {"id": "11", "name": "JK Studio"}, {"id": "22", "name": "Другая фирма"}]})
        else:
            route.fulfill(json={"ok": True, "company_name": "JK Studio"})

    def summary(route):
        route.fulfill(json={"connected": True, "provider": "wfirma", "company_name": "JK Studio",
                            "net_ytd": 182400.5,
                            "vat_due": {"period": "2026-10", "to_pay": 2300.0, "carry": 0}})
    ctx, page, errors = _wfirma_page(browser, base_url, {
        "**/api/wfirma/connect": connect, "**/api/wfirma/summary": summary})
    try:
        page.goto(base_url + "/plan.html", wait_until="networkidle")
        page.wait_for_selector("#acc-seg:not(.hidden)", timeout=10000)
        tap(page, '#acc-seg button[data-acc="wfirma"]')
        assert page.locator("#acc-wfirma").is_visible()
        assert page.locator("#acc-infakt").is_hidden()
        page.locator("#infakt-card").screenshot(path=str(SHOTS / "wfirma_connect.png")) \
            if SHOTS else None

        page.fill("#wf-access", "a" * 20)
        page.fill("#wf-secret", "b" * 20)
        tap(page, "#wf-connect")
        page.wait_for_selector("#wf-company-box:not(.hidden)", timeout=10000)
        assert page.locator("#wf-company option").count() == 2
        page.select_option("#wf-company", "11")
        tap(page, "#wf-connect")
        page.wait_for_selector("#wf-on:not(.hidden)", timeout=10000)
        text = page.inner_text("#wf-sums")
        assert "JK Studio" in text and "2" in text and "JPK" in text
        assert "ZUS и аванс налога" in text, "честно говорим, чего API не отдаёт"
        assert calls[-1]["company_id"] == "11"
        assert page.evaluate("localStorage.getItem('jdg_wfirma_on')") == "1"
        if SHOTS:
            page.locator("#infakt-card").screenshot(path=str(SHOTS / "wfirma_connected.png"))
        assert not errors, errors
    finally:
        ctx.close()


def test_wfirma_hidden_when_server_has_no_app_key(browser, base_url):
    ctx = browser.new_context(viewport=PHONE)
    page = ctx.new_page()
    try:
        page.goto(base_url + "/plan.html", wait_until="networkidle")
        assert page.locator("#acc-seg").is_hidden()
        assert "wFirma" not in page.inner_text("#infakt-card")
    finally:
        ctx.close()


def test_wfirma_cockpit_overview(browser, base_url):
    """Кокпит на wFirma: только обзор, без ZUS/налога, с JPK и KSeF."""
    from datetime import date as _date
    import wfirma

    raw = {"company": {"altname": "JK Studio", "vat_payer": "1", "tax": "lumpregister"},
           "invoices": [wfirma.norm_invoice(r) for r in [
               {"fullnumber": "FV 1/11/2026", "date": "2026-11-03", "netto": "8000.00",
                "total": "9840.00", "alreadypaid": "9840.00", "paymentstate": "paid",
                "ksef_reference_number": "K-1"},
               {"fullnumber": "FV 7/10/2026", "date": "2026-10-20", "netto": "6500.00",
                "total": "7995.00", "alreadypaid": "0", "paymentstate": "unpaid",
                "paymentdate": "2026-10-30"},
               {"fullnumber": "FV 3/09/2026", "date": "2026-09-12", "netto": "7200.00",
                "total": "8856.00", "alreadypaid": "8856.00", "paymentstate": "paid",
                "ksef_reference_number": "K-3"}]],
           "expenses": [{"date": "2026-11-04", "netto": 1200.0, "paid": True, "due": None},
                        {"date": "2026-10-04", "netto": 900.0, "paid": True, "due": None}],
           "jpk": {"to_pay": 1610.0, "carry": 0.0, "period": "2026-10"}}
    data = {"connected": True} | wfirma.build_overview(raw, _date(2026, 11, 22))
    ctx, page, errors = _wfirma_page(
        browser, base_url,
        {"**/api/wfirma/overview": lambda r: r.fulfill(json=data)},
        storage={"jdg_wfirma_on": "1"})
    try:
        page.goto(base_url + "/cockpit.html", wait_until="networkidle")
        page.wait_for_selector("#out .kpis", timeout=10000)
        assert page.locator("#tabs").is_hidden(), "у wFirma только обзор"
        out = page.inner_text("#out")
        assert "Прибыль до налогов" in out and "JK Studio" in out
        assert "VAT за 2026-10 по JPK" in out and "KSeF" in out
        assert "Обязательства месяца" not in out, "ZUS/налога у wFirma нет — не рисуем"
        assert "Налоги (ZUS + PIT)" not in out
        if SHOTS:
            page.screenshot(path=str(SHOTS / "wfirma_cockpit.png"), full_page=True)
        assert not errors, errors
    finally:
        ctx.close()
