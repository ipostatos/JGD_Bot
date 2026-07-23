"""Какие коды PKD обсуждают в чате и рядом с какими профессиями.

Чат — источник спроса, а не истины: коды из сообщений проверяются по
официальному справочнику (webapp/data/pkd.json), а связка «профессия → код»
идёт в словарь синонимов только после ручной вычитки.

    python tools/pkd_mine.py [--top 40]

Вход:  chat_analysis/messages.jsonl, webapp/data/{pkd.json,pkd_keys.json}
Выход: chat_analysis/pkd_mentions.json — коды с частотой, примерами и
       словами-профессиями из окружающего текста
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "chat_analysis"
WEB = ROOT / "webapp" / "data"

CODE_RE = re.compile(r"\b(\d{2})[.,](\d{2})[.,\s]?([A-ZА-Я])?\b")
# профессии и роли, которые ищут по PKD (из вопросов чата)
ROLE_RE = re.compile(
    r"\b(программист|разработчик|девелопер|тестировщик|qa|аналитик|ba|pm|проджект|продакт|"
    r"дизайнер|верстальщик|фронтенд|бэкенд|фулстек|devops|админ|сисадмин|数|"
    r"маркетолог|таргетолог|смм|smm|копирайтер|редактор|блогер|блоггер|инфлюенсер|"
    r"фотограф|видеограф|монтажёр|монтажер|оператор|режиссёр|"
    r"переводчик|преподаватель|репетитор|тренер|коуч|психолог|консультант|"
    r"бухгалтер|юрист|риелтор|агент|курьер|водитель|доставщик|"
    r"парикмахер|мастер|маникюр|косметолог|массажист|фитнес|"
    r"строитель|электрик|сантехник|ремонт|дизайн интерьера|"
    r"продаж|магазин|интернет-магазин|дропшип|маркетплейс|"
    r"консалтинг|доradztwo|doradztwo|аренда|перевозк)\w*", re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=40)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    pkd = json.loads((WEB / "pkd.json").read_text(encoding="utf-8"))
    valid = {c["code"]: c["name"] for c in pkd["codes"]}
    keys = json.loads((WEB / "pkd_keys.json").read_text(encoding="utf-8"))["map"]

    freq = Counter()
    roles: dict[str, Counter] = defaultdict(Counter)
    examples: dict[str, list] = defaultdict(list)
    seen_msgs = 0

    with (DIR / "messages.jsonl").open(encoding="utf-8") as f:
        for line in f:
            m = json.loads(line)
            if m["kind"] != "msg" or "pkd" not in m["text"].lower():
                continue
            seen_msgs += 1
            found = set()
            for a, b, letter in CODE_RE.findall(m["text"]):
                code = f"{a}.{b}" + (f".{letter}" if letter else "")
                # в чате пишут и по-старому, и без буквы подкласса
                cands = [code] if code in valid else []
                if not cands and code in keys:
                    cands = keys[code]["to"]
                if not cands:
                    cands = [c for c in valid if c.startswith(f"{a}.{b}.")]
                found.update(cands[:3])
            if not found:
                continue
            found_roles = {r.lower() for r in ROLE_RE.findall(m["text"])}
            for code in found:
                freq[code] += 1
                for r in found_roles:
                    roles[code][r] += 1
                if len(examples[code]) < 3 and len(m["text"]) < 400:
                    examples[code].append(m["text"])

    out = [{"code": c, "name": valid[c], "mentions": n,
            "roles": [r for r, _ in roles[c].most_common(6)],
            "examples": examples[c]}
           for c, n in freq.most_common()]
    (DIR / "pkd_mentions.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"Сообщений со словом PKD: {seen_msgs:,}, распознано кодов: {len(freq)}")
    print(f"\nТоп-{args.top} кодов по упоминаниям:")
    for r in out[:args.top]:
        print(f"  {r['mentions']:>4} | {r['code']} | {r['name'][:58]:58} | "
              f"{', '.join(r['roles'][:4])}")
    print(f"\n-> {DIR / 'pkd_mentions.json'}")


if __name__ == "__main__":
    main()
