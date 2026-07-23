"""Вынести инлайновый JS страниц в webapp/js/<страница>.js.

Ради CSP: пока в script-src стоит 'unsafe-inline', заголовок почти не защищает
от XSS — любой внедрённый <script> выполнится. После выноса кода директиву
можно ужать до 'self' + telegram.org.

Инлайновые стили не трогаем: 175 атрибутов style и 12 блоков <style> — это
оформление, скрипты через них не выполняются, а вычистить всё разом значило бы
переверстать приложение. style-src 'unsafe-inline' остаётся сознательно.

    python tools/extract_inline_js.py [--check]

--check ничего не пишет, только сообщает, где остался инлайновый JS
(используется тестом, чтобы новый код не вернул старую проблему).
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "webapp"
JS = WEB / "js"

INLINE = re.compile(r"([ \t]*)<script(?![^>]*\bsrc=)([^>]*)>(.*?)</script>[ \t]*\n?",
                    re.S | re.I)
# type="module" терять нельзя: читалка на pdf.js — ESM, без него страница падает
# с «Cannot use import statement outside a module»
TYPE_ATTR = re.compile(r'\btype\s*=\s*"([^"]+)"', re.I)


def dedent(code: str) -> str:
    lines = [ln for ln in code.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    pads = [len(ln) - len(ln.lstrip()) for ln in lines if ln.strip()]
    cut = min(pads) if pads else 0
    return "\n".join(ln[cut:] if len(ln) >= cut else ln for ln in lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    left = []
    for page in sorted(WEB.glob("*.html")):
        html = page.read_text(encoding="utf-8")
        blocks = INLINE.findall(html)
        if not blocks:
            continue
        if args.check:
            left.append(f"{page.name}: {len(blocks)} инлайн-блоков")
            continue
        JS.mkdir(exist_ok=True)
        name = page.stem + ".js"
        code = "\n\n".join(dedent(b) for _, _, b in blocks)
        (JS / name).write_text(code, encoding="utf-8")
        types = [TYPE_ATTR.search(attrs) for _, attrs, _ in blocks]
        kind = next((t.group(1) for t in types if t and t.group(1) != "text/javascript"), "")
        type_attr = f' type="{kind}"' if kind else ""

        first = True

        def repl(m):
            nonlocal first
            if first:
                first = False
                return f'{m.group(1)}<script{type_attr} src="js/{name}"></script>\n'
            return ""

        page.write_text(INLINE.sub(repl, html), encoding="utf-8")
        print(f"  {page.name} -> js/{name} ({code.count(chr(10))} строк)")

    if args.check:
        if left:
            print("Инлайновый JS остался:")
            for x in left:
                print("  " + x)
            sys.exit(1)
        print("Инлайнового JS на страницах нет")


if __name__ == "__main__":
    main()
