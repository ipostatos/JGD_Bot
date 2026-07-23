"""Браузерный смоук всех страниц Mini App: ошибки консоли, битые ссылки, вёрстка.

Почему это есть: вся видимая логика приложения — в JS, и до сих пор её ловил
только глаз (панель под домашней полоской iPhone, невалидные имена иконок,
горизонтальный оверфлоу). Тест открывает каждую страницу в headless Chromium
на ширине телефона и проверяет то, что человек замечает первым.

Требует playwright + chromium; без них тесты пропускаются (в CI ставятся явно).
"""
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
