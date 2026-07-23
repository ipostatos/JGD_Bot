"""Кандидаты в словарь профессий: чем люди в чате называют свою работу.

Берём не любые существительные, а слова в характерных для профессий позициях
(«я фотограф», «работаю дизайнером», «ищу бухгалтера») — так в выборку не лезут
«вопрос», «документ» и прочий частотный мусор. Уже покрытые словарём слова
отсекаем, чтобы список был про то, чего не хватает.

    python tools/pkd_professions.py [--top 120]

Вход:  chat_analysis/messages.jsonl, webapp/pkd_synonyms.json
Выход: список кандидатов в stdout + chat_analysis/pkd_professions.json
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "chat_analysis"

W = r"[а-яёa-z][а-яёa-z-]{4,}"
# Только сильные позиции: «для X» и «как X» ловят пол-языка и топят сигнал
PATTERNS = [
    re.compile(rf"\bя\s+({W})\b", re.I),
    re.compile(rf"\bработаю\s+({W})(?:ом|ем|ой)?\b", re.I),
    re.compile(rf"\bищу\s+({W})(?:а|у)?\b", re.I),
    re.compile(rf"\bпо\s+профессии\s+({W})\b", re.I),
]
# Плюс сами слова-профессии по характерным суффиксам — их частота в корпусе
# и есть словарь занятий сообщества
PROF_SUFFIX = re.compile(
    r"^[а-яё-]{4,}(ист|щик|чик|тель|лог|ер|ёр|ор|ант|ент|арь|ник|вед|граф|мен|"
    r"олог|мейкер|райтер|дизайнер|менеджер)$", re.I)
NOT_PROF = re.compile(
    r"^(вопрос|ответ|номер|период|размер|характер|партнер|партнёр|компьютер|"
    r"пример|интернет|документ|момент|процент|контракт|телефон|сентябр|октябр|"
    r"декабр|ноябр|январ|феврал|апрел|которы|перевод|доход|расход|платель)", re.I)
# слова-паразиты в тех же позициях: «я думаю», «для этого», «как обычно»
STOP = set("""этого этом того такого себя вас него меня тебя нас них всех всего
думаю знаю понял понимаю хочу могу буду должен слышал видел читал написал сделал
просто вроде тоже уже ещё когда если чтобы потому этот эта тот такой самый
вопрос ответ пример случай момент вариант человек люди фирма фирмы контракт
документ документы деньги месяц год день время сайт ссылка чат группа канал
обычно кажется наверное конечно правда точно кстати спасибо пожалуйста
себе всем этому которая которые которого которую""".split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=120)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    sys.path.insert(0, str(ROOT))
    import pkd
    idx = pkd.index()

    freq = Counter()
    with (DIR / "messages.jsonl").open(encoding="utf-8") as f:
        for line in f:
            m = json.loads(line)
            if m["kind"] != "msg":
                continue
            text = m["text"]
            for p in PATTERNS:
                for w in p.findall(text):
                    w = w.lower().strip("-")
                    if w not in STOP and not w.isdigit():
                        freq[w] += 2          # сильная позиция весит больше
            for w in re.findall(r"[а-яё-]{5,}", text.lower()):
                if PROF_SUFFIX.match(w) and not NOT_PROF.match(w) and w not in STOP:
                    freq[w] += 1

    covered, gaps = [], []
    for word, n in freq.most_common(3000):
        if n < 3:
            break
        _, _, matched = idx.expand(word)
        (covered if matched else gaps).append((word, n))

    (DIR / "pkd_professions.json").write_text(json.dumps(
        {"gaps": gaps[:400], "covered": covered[:200]}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    print(f"Кандидатов: {len(freq):,}; словарь узнаёт {len(covered)}, "
          f"не узнаёт {len(gaps)}\n")
    print(f"Топ-{args.top} слов, которых словарь не знает:")
    for i in range(0, min(args.top, len(gaps)), 4):
        row = gaps[i:i + 4]
        print("  " + " | ".join(f"{w} ({n})" for w, n in row))
    print(f"\n-> {DIR / 'pkd_professions.json'}")


if __name__ == "__main__":
    main()
