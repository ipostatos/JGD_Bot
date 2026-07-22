"""Сборка контента Mini App из гайда sobolevbel/jdg (CC0).

sources/guide (клон https://github.com/sobolevbel/jdg) ->
  webapp/data/content.json     — секции и метаданные статей (из nav mkdocs.yml)
  webapp/data/articles/<id>.html — отрендеренные статьи (RU)
  webapp/data/search.json      — плоский индекс {id, title, text} для поиска
  webapp/data/gimg/            — картинки гайда

Запуск: python tools/build_content.py  (из корня проекта; и локально, и на VPS)
"""
import json
import re
import shutil
import sys
from html.parser import HTMLParser
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / "sources" / "guide"
DOCS = GUIDE / "docs"
OUT = ROOT / "webapp" / "data"

MD_EXTENSIONS = [
    "admonition", "attr_list", "def_list", "tables",
    "pymdownx.details", "pymdownx.superfences", "pymdownx.tasklist",
]

# у гайда кастомные mkdocs-иконки — заменяем на эмодзи.
# ВАЖНО: неизвестные :icon: preprocess вырезает, поэтому все реально
# встречающиеся в гайде иконки должны быть тут, иначе исчезнут из текста.
ICON_EMOJI = {
    "youtube": "▶️", "telegram": "✈️", "simple/telegram": "✈️",
    "fontawesome-brands-youtube": "▶️", "material-web": "\U0001f310",
    "warning": "⚠️", "coffee": "☕",
}

# Локализация callout-блоков (admonition/details) + Lucide-иконка на тон UI.
# python-markdown ставит английский заголовок по типу («Note», «Warning») —
# в русском гайде это чужеродно; переводим и добавляем иконку из icons.js.
CALLOUT = {
    "note": ("Заметка", "bookmark"),
    "abstract": ("Кратко", "file-text"), "summary": ("Кратко", "file-text"),
    "tldr": ("Кратко", "file-text"),
    "info": ("К сведению", "info"), "todo": ("К сведению", "info"),
    "tip": ("Совет", "lightbulb"), "hint": ("Совет", "lightbulb"),
    "important": ("Важно", "alert-triangle"),
    "success": ("Готово", "check-circle"), "check": ("Готово", "check-circle"),
    "done": ("Готово", "check-circle"),
    "question": ("Вопрос", "circle-help"), "faq": ("Вопрос", "circle-help"),
    "help": ("Вопрос", "circle-help"),
    "warning": ("Важно", "alert-triangle"), "caution": ("Внимание", "alert-triangle"),
    "attention": ("Внимание", "alert-triangle"),
    "danger": ("Осторожно", "flame"), "error": ("Ошибка", "x-circle"),
    "failure": ("Ошибка", "x-circle"), "fail": ("Ошибка", "x-circle"),
    "bug": ("Проблема", "wrench"),
    "example": ("Пример", "list"),
    "quote": ("Цитата", "message-circle"), "cite": ("Цитата", "message-circle"),
}

# Lucide-иконка и тон плашки для каждой секции (дизайн-система ISSA).
# Секции «reference» уходят в блок «Справочное» на странице гайда.
SECTION_META = {
    "Главная": ("house", "blue", False),
    "PESEL": ("id-card", "blue", False),
    "Profil Zaufany": ("lock", "violet", False),
    "Регистрация": ("clipboard-list", "green", False),
    "ZUS": ("shield-check", "amber", False),
    "Налоги": ("coins", "gold", False),
    "Декларации": ("file-check", "cyan", False),
    "Легализация": ("flag", "violet", False),
    "Рабочий процесс": ("refresh-cw", "green", False),
    "Как отправить письмо в налоговую": ("send", "blue", False),
    "inFakt": ("file-text", "cyan", False),
    "wFirma": ("file-text", "violet", False),
    "Словарь": ("book-marked", "cyan", True),
    "F.A.Q.": ("circle-help", "amber", True),
    "Что нового": ("bell", "red", True),
    "Поддержать": ("heart", "red", True),
}


def parse_nav(mkdocs_yml: str):
    """Достаёт nav из mkdocs.yml тупым индентационным парсером.

    PyYAML не берём: в конфиге есть !!python/name-теги.
    Возвращает [{title, file} | {title, items: [{title, file}]}].
    """
    lines = mkdocs_yml.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == "nav:")
    except StopIteration:
        sys.exit("nav: не найден в mkdocs.yml")
    nav = []
    for line in lines[start + 1:]:
        if line.strip() and not line.startswith(" "):
            break  # следующий top-level ключ
        m = re.match(r"^(\s*)- (.+?):\s*(\S+\.md)?\s*(#.*)?$", line)
        if not m:
            continue
        indent, title, file = len(m.group(1)), m.group(2).strip(), m.group(3)
        title = title.replace("&nbsp;", " ").replace("♥", "").strip()
        if indent <= 2:
            if file:
                nav.append({"title": title, "file": file})
            else:
                nav.append({"title": title, "items": []})
        else:
            if not nav or "items" not in nav[-1]:
                continue
            nav[-1]["items"].append({"title": title, "file": file})
    return nav


FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def split_frontmatter(text: str):
    meta = {}
    m = FRONTMATTER_RE.match(text)
    if m:
        for line in m.group(1).splitlines():
            kv = re.match(r"^(\w+):\s*(.+)$", line)
            if kv:
                meta[kv.group(1)] = kv.group(2).strip().strip("'\"")
        text = text[m.end():]
    return meta, text


def preprocess(md_text: str) -> str:
    # :icon-name: -> эмодзи (или убрать неизвестные, чтобы не мусорить)
    md_text = re.sub(
        r":([a-z0-9/_-]+):",
        lambda m: ICON_EMOJI.get(m.group(1), ""),
        md_text,
    )
    return md_text


def _callout_label(type_word: str, current: str) -> str:
    """Заголовок callout: перевод английского дефолта + Lucide-иконка."""
    ru, icon = CALLOUT.get(type_word, (current, "info"))
    is_default = current.strip().lower() == type_word or current.strip() == type_word.capitalize()
    label = ru if is_default else current  # кастомный заголовок сохраняем
    return f'<span class="cal-ic" data-icon="{icon}"></span>{label}'


def localize_callouts(html: str) -> str:
    """Русские заголовки + иконки для admonition (!!!) и details (???)."""
    def _adm(m):
        classes, title = m.group(1).strip(), m.group(2)
        types = [c for c in classes.split() if c != "admonition"]
        label = _callout_label(types[0], title) if types else title
        return (f'<div class="admonition {classes}">'
                f'<p class="admonition-title">{label}</p>')

    def _det(m):
        classes, is_open, title = m.group(1).strip(), m.group(2) or "", m.group(3)
        types = [c for c in classes.split() if c != "admonition"]
        label = _callout_label(types[0], title) if types else title
        return f'<details class="{classes}"{is_open}><summary>{label}</summary>'

    html = re.sub(r'<div class="admonition([^"]*)">\s*<p class="admonition-title">([^<]*)</p>',
                  _adm, html)
    html = re.sub(r'<details class="([^"]*)"( open="open")?>\s*<summary>([^<]*)</summary>',
                  _det, html)
    return html


# Врезки в начало отдельных статей: сам контент тянется из upstream и правки в
# .md затрутся при git pull, поэтому связь со своими экранами живёт здесь.
ARTICLE_BANNERS = {
    "zus_errors": (
        '<div class="admonition tip"><p class="admonition-title">'
        '<span data-icon="wrench"></span>Справочник кодов в приложении</p>'
        '<p>Коды ошибок ZUS с поиском, фильтрами и пошаговыми решениями — '
        '<a href="zus_err.html?from=article.html%3Fid%3Dzus_errors">открыть справочник</a>. '
        'В боте можно просто прислать код из восьми цифр.</p></div>'
    ),
}


def postprocess(html: str) -> str:
    # ссылки между статьями: foo.md / foo.md#anchor -> article.html?id=foo
    html = re.sub(
        r'href="(?!https?://|#|mailto:)([\w-]+)\.md(#[^"]*)?"',
        lambda m: f'href="article.html?id={m.group(1)}{m.group(2) or ""}"',
        html,
    )
    # картинки: images/... -> data/gimg/...
    html = re.sub(r'src="images/', 'src="data/gimg/', html)
    html = re.sub(r'href="images/', 'href="data/gimg/', html)
    # внешние ссылки — в новой вкладке (в TG WebView откроется браузером)
    html = re.sub(r'(<a href="https?://[^"]*")', r'\1 target="_blank" rel="noopener"', html)
    html = localize_callouts(html)
    return html


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chunks = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.chunks.append(data)


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    return re.sub(r"\s+", " ", " ".join(p.chunks)).strip()


def main():
    if not DOCS.is_dir():
        sys.exit(f"Нет {DOCS}. Сначала: git clone --depth 1 "
                 f"https://github.com/sobolevbel/jdg {GUIDE}")
    nav = parse_nav((GUIDE / "mkdocs.yml").read_text(encoding="utf-8"))

    articles_dir = OUT / "articles"
    if OUT.exists():
        shutil.rmtree(OUT)
    articles_dir.mkdir(parents=True)

    md = markdown.Markdown(extensions=MD_EXTENSIONS)
    search, sections = [], []
    n_articles = 0

    def render(entry):
        nonlocal n_articles
        aid = entry["file"][:-3]
        src = DOCS / entry["file"]
        if not src.is_file():
            print(f"! пропуск (нет файла): {entry['file']}")
            return None
        meta, body = split_frontmatter(src.read_text(encoding="utf-8"))
        md.reset()
        html = postprocess(md.convert(preprocess(body)))
        if aid in ARTICLE_BANNERS:
            html = ARTICLE_BANNERS[aid] + html
        (articles_dir / f"{aid}.html").write_text(html, encoding="utf-8")
        search.append({"id": aid, "title": entry["title"],
                       "text": html_to_text(html).lower()})
        n_articles += 1
        return {"id": aid, "title": entry["title"],
                "desc": meta.get("description", "")}

    for item in nav:
        icon, tone, ref = SECTION_META.get(item["title"], ("file-text", "blue", False))
        meta = {"title": item["title"], "icon": icon, "tone": tone, "ref": ref}
        if "items" in item:
            arts = [a for a in (render(e) for e in item["items"]) if a]
            if arts:
                sections.append({**meta, "items": arts})
        else:
            art = render(item)
            if art:
                sections.append({**meta, "items": [art], "single": True})

    (OUT / "content.json").write_text(
        json.dumps({"sections": sections}, ensure_ascii=False), encoding="utf-8")
    (OUT / "search.json").write_text(
        json.dumps(search, ensure_ascii=False), encoding="utf-8")

    if (DOCS / "images").is_dir():
        shutil.copytree(DOCS / "images", OUT / "gimg")
    # ставки нужны webapp-у как данные
    shutil.copy(ROOT / "rates_2026.json", OUT / "rates_2026.json")
    shutil.copy(ROOT / "rates_years.json", OUT / "rates_years.json")

    print(f"OK: {len(sections)} секций, {n_articles} статей, "
          f"search {len(search)} записей -> {OUT}")


if __name__ == "__main__":
    main()
