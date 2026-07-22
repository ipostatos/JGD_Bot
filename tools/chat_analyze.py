"""Шаг 2: анализ чата — паттерны вопросов, стадия пути, дыры в гайде, эксперты.

Это НЕ майнер ответов. Ответы из чата здесь не считаются истиной: законы
менялись (ставки ZUS, лимит VAT, KSeF), и ответ 2022 года сегодня может быть
вреден. Мы измеряем спрос — о чём и на каком этапе спрашивают, что чат
переспрашивает годами, чего нет в гайде — и кто в чате реально отвечает.

    python tools/chat_analyze.py [--k 160] [--min-len 30]

Вход:  chat_analysis/messages.jsonl (см. chat_prep.py)
Выход: chat_analysis/{clusters.json, experts.json, report.md}
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from sklearn.cluster import MiniBatchKMeans
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import Normalizer

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "chat_analysis"
SRC = DIR / "messages.jsonl"
GUIDE = ROOT / "webapp" / "data" / "search.json"

Q_MARK = re.compile(
    r"\?|^\s*(подскажите|подскажи|посоветуйте|кто\s+\w{0,12}\s*(знает|подскажет|сталкивал)|"
    r"есть\s+ли|можно\s+ли|нужно\s+ли|надо\s+ли|стоит\s+ли|как\s+|где\s+|куда\s+|"
    r"что\s+делать|правда\s+ли)", re.I)
THANKS = re.compile(r"(спасибо|благодар|дзякуй|dzięki|dziekuje|спс|thx|🙏|👍)", re.I)

STOP = set("""и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по
только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни
быть был него до вас нибудь опять уж вам ведь там потом себя ничего ей может они тут где есть
надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под будет ж тогда кто
этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были
куда зачем всех никогда можно при наконец два об другой хоть после над больше тот через эти
нас про всего них какая много разве три эту моя впрочем хорошо свою этой перед иногда лучше
чуть том нельзя такой им более всегда конечно всю между это как-то кстати вообще просто очень
всё ещё также либо тк тд др итд например почему который которые которая ваш ваши наш если бы
делать сделать хочу хотел нужно надо можно есть нет буду будут думаю знаю понял скажите
пожалуйста добрый день день привет всем подскажите подскажи вопрос спасибо заранее
""".split())

# Этапы пути JDG-шника. Порядок важен: первый сработавший с наибольшим весом.
STAGES = [
    # без общих слов вроде «jdg», «ип», «выбрать» — иначе этап собирает весь чат
    ("0. До открытия: выбрать форму", """sp z oo spzoo społka spolka зоо самозанятость
     umowa zlecenie uop этат этате трудовой договор совмещать совмещение параллельно
     стоит ли открывать имеет ли смысл выгоднее ли не открывать закрыть и открыть"""),
    ("1. Легализация и право открыть", """виза pbh pobyt karta pobytu побыт карта временный
     wiza biznesowa poland business harbour мультивиза внж стал пмж резидент право открыть
     украинц белорус паспорт pesel профиль zaufany profil zaufany epuap mObywatel decyzja
     оседлы długoterminowy rezydent ue статус ukr"""),
    ("2. Регистрация JDG (CEIDG)", """ceidg регистрац зарегистрирова заявк wniosek подал подать
     заполн анкет pkd код деятельности адрес регистрации adres siedziba прописка мельдунк
     zameldowanie дата начала rozpoczęcie nazwa название фирмы отказ отклонил wpis"""),
    ("3. Первые месяцы: ulga na start", """ulga na start ульга улга старт первые полгода 6 месяцев
     льгот освобожден начал только открыл первый месяц первая деклараци первый взнос
     zgłoszenie zus zua zza регистрация в zus 7 дней"""),
    ("4. Форма налогообложения и PIT", """ryczałt рычалт ryczalt liniowy линейный skala шкала
     12 32 19 8.5 12.5 15 5.5 ставка налога форма налогообложения pit pit-36 pit-28 pit-37
     годовая деклараци rozliczenie zaliczka аванс налоговый us urząd skarbowy налоговая
     kwota wolna необлагаем вычет ulga dla młodych ip box ipbox 5%"""),
    ("5. ZUS: взносы, льготы, больничные", """zus składki складки взнос preferencyjne преференц
     mały zus plus duży большой полный дра dra rca rsa deklaracja декларац zdrowotna здоровотна
     медицинск больничн chorobowe l4 zwolnienie mac tacierzy zasiłek декрет пенси emerytura
     płatnik платник epłatnik pue eskladka esk"""),
    ("6. VAT, фактуры, KSeF", """vat ват vat-r vatr vat-ue vatue белый список whitelist фактур
     faktura invoice ksef ксеф jpk jpk_v7 kseF номер nip проформ mpp split payment
     оборот лимит 200000 240000 освобождение zwolnienie z vat отчет по vat корректировк"""),
    ("7. Бухгалтерия, банки, сервисы", """бухгалтер księgowa ksiegowa бухгалтерия infakt инфакт
     wfirma taxe fakturownia банк bank счет konto firmowe mbank pkobp ing santander revolut
     wise карта эквайринг терминал касса kasa fiskalna programy книга kpir приход расход
     расходы koszty амортизац koszt uzysk"""),
    ("8. Изменения и закрытие", """zawieszenie заморозк приостанов закрыт likwidacja закрытие
     wykreślenie смена адреса изменени pkd переезд выезд из польши переехал сменил резидентство
     kontrola контроль проверка штраф kara долг отсрочк rozłożenie"""),
    ("9. Клиенты, договоры, деньги", """клиент заказчик договор umowa контракт b2b оплата
     инвойс валют курс nbp перевод получить деньги из-за границы нерезидент exchange
     paypal stripe upwork фриланс zagraniczny kontrahent"""),
]
STAGE_LEX = [(name, set(re.findall(r"[\w\-]+", lex.lower()))) for name, lex in STAGES]


def is_question(text: str) -> bool:
    return bool(Q_MARK.search(text[:200]))


def stage_of(text: str) -> str:
    """Нормируем попадания на размер лексикона — иначе побеждает самый длинный."""
    words = set(re.findall(r"[\w\-]+", text.lower()))
    best, score = "не определён", 0.0
    for name, lex in STAGE_LEX:
        s = len(words & lex) / len(lex) ** 0.5
        if s > score:
            best, score = name, s
    return best


def load(min_len: int):
    """3 прохода по jsonl: вопросы → ответы на них → «спасибо» за ответы."""
    questions: dict[int, dict] = {}
    print("  проход 1: вопросы")
    with SRC.open(encoding="utf-8") as f:
        for line in f:
            m = json.loads(line)
            if m["kind"] != "msg" or not (min_len <= m["n"] <= 2000):
                continue
            if is_question(m["text"]):
                questions[m["id"]] = {
                    "id": m["id"], "uid": m["uid"], "from": m["from"],
                    "date": m["date"], "text": m["text"],
                    "root": m.get("reply_to") is None,
                    "react": m.get("react", 0),
                }

    print(f"  найдено вопросов: {len(questions):,}\n  проход 2: ответы")
    answers: dict[int, list] = defaultdict(list)
    ans_meta: dict[int, dict] = {}
    with SRC.open(encoding="utf-8") as f:
        for line in f:
            m = json.loads(line)
            rid = m.get("reply_to")
            if rid is None or rid not in questions or m["kind"] not in ("msg", "link_only"):
                continue
            if m["uid"] == questions[rid]["uid"] or m["n"] < 40:
                continue
            rec = {"id": m["id"], "uid": m["uid"], "from": m["from"], "n": m["n"],
                   "react": m.get("react", 0), "react_up": m.get("react_up", 0),
                   "qid": rid, "date": m["date"], "text": m["text"]}
            answers[rid].append(rec)
            ans_meta[m["id"]] = rec

    print(f"  ответов: {sum(len(v) for v in answers.values()):,}\n  проход 3: «спасибо»")
    thanks = Counter()
    with SRC.open(encoding="utf-8") as f:
        for line in f:
            m = json.loads(line)
            rid = m.get("reply_to")
            if rid in ans_meta and THANKS.search(m["text"][:120]):
                if m["uid"] == questions[ans_meta[rid]["qid"]]["uid"]:
                    thanks[ans_meta[rid]["uid"]] += 1
    return questions, answers, ans_meta, thanks


def guide_coverage(cluster_terms: list[str]):
    docs = json.loads(GUIDE.read_text(encoding="utf-8"))
    vec = TfidfVectorizer(max_features=60000, ngram_range=(1, 2), min_df=1)
    M = vec.fit_transform([d["title"] + " " + d["text"] for d in docs])
    out = []
    for terms in cluster_terms:
        q = vec.transform([" ".join(terms)])
        sims = cosine_similarity(q, M)[0]
        i = int(sims.argmax())
        out.append({"article": docs[i]["title"], "id": docs[i]["id"],
                    "score": round(float(sims[i]), 3)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=160, help="число кластеров")
    ap.add_argument("--min-len", type=int, default=30)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    questions, answers, ans_meta, thanks = load(args.min_len)
    qids = list(questions)
    texts = [questions[i]["text"][:600] for i in qids]

    print(f"\nКластеризация {len(texts):,} вопросов на {args.k} тем…")
    vec = TfidfVectorizer(stop_words=list(STOP), ngram_range=(1, 2), min_df=8,
                          max_df=0.25, max_features=120000, sublinear_tf=True)
    X = vec.fit_transform(texts)
    # без LSA k-means на разреженных коротких текстах схлопывается в один кластер
    print(f"  TF-IDF {X.shape} → LSA 180")
    svd = TruncatedSVD(n_components=180, random_state=0)
    Z = Normalizer(copy=False).fit_transform(svd.fit_transform(X))
    print(f"  объяснённая дисперсия: {svd.explained_variance_ratio_.sum():.1%}")
    km = MiniBatchKMeans(n_clusters=args.k, random_state=0, n_init=10,
                         batch_size=4096, max_iter=300)
    labels = km.fit_predict(Z)
    terms = np.array(vec.get_feature_names_out())
    # центры кластеров обратно в пространство слов — для читаемых меток
    word_centers = svd.inverse_transform(km.cluster_centers_)

    clusters = []
    for c in range(args.k):
        idx = np.where(labels == c)[0]
        if len(idx) < 15:
            continue
        top = terms[word_centers[c].argsort()[::-1][:14]].tolist()
        sims = cosine_similarity(Z[idx], km.cluster_centers_[c].reshape(1, -1))[:, 0]
        order = idx[sims.argsort()[::-1]]
        qs = [questions[qids[i]] for i in order]
        years = Counter(q["date"][:4] for q in qs)
        n_ans = [len(answers.get(q["id"], [])) for q in qs]
        stages = Counter(stage_of(q["text"]) for q in qs[:400])
        examples = [{"date": q["date"][:10], "text": q["text"][:400],
                     "answers": len(answers.get(q["id"], []))}
                    for q in qs[:8]]
        clusters.append({
            "id": c, "size": len(idx), "terms": top,
            # связность: низкая = «мешок» из непохожих вопросов, а не паттерн
            "cohesion": round(float(sims.mean()), 3),
            "stage": stages.most_common(1)[0][0],
            "stage_mix": dict(stages.most_common(3)),
            "by_year": {y: years.get(y, 0) for y in sorted(years)},
            "answered_rate": round(sum(1 for n in n_ans if n) / len(n_ans), 2),
            "avg_answers": round(sum(n_ans) / len(n_ans), 2),
            "examples": examples,
        })

    clusters.sort(key=lambda c: -c["size"])
    for c, cov in zip(clusters, guide_coverage([c["terms"] for c in clusters])):
        c["guide"] = cov

    # ── эксперты ───────────────────────────────────────────────────────────
    prof: dict[str, dict] = defaultdict(
        lambda: {"answers": 0, "chars": 0, "askers": set(), "react": 0,
                 "react_up": 0, "stages": Counter(), "first": "9", "last": "0",
                 "name": ""})
    for qid, lst in answers.items():
        st = stage_of(questions[qid]["text"])
        for a in lst:
            p = prof[a["uid"]]
            p["name"] = a["from"] or p["name"]
            p["answers"] += 1
            p["chars"] += a["n"]
            p["askers"].add(questions[qid]["uid"])
            p["react"] += a["react"]
            p["react_up"] += a["react_up"]
            p["stages"][st] += 1
            p["first"] = min(p["first"], a["date"])
            p["last"] = max(p["last"], a["date"])
    asked = Counter(q["uid"] for q in questions.values())

    experts = []
    for uid, p in prof.items():
        if p["answers"] < 30:
            continue
        experts.append({
            "uid": uid, "name": p["name"], "answers": p["answers"],
            "people_helped": len(p["askers"]),
            "avg_len": round(p["chars"] / p["answers"]),
            "reactions": p["react"], "positive_reactions": p["react_up"],
            "thanks": thanks.get(uid, 0),
            "asked_questions": asked.get(uid, 0),
            "give_take": round(p["answers"] / max(asked.get(uid, 0), 1), 1),
            "active": f'{p["first"][:7]} … {p["last"][:7]}',
            "top_stages": [s for s, _ in p["stages"].most_common(3)],
            # вес: помощь разным людям и явная благодарность важнее вала сообщений
            "score": round(p["answers"] * 0.4 + len(p["askers"]) * 1.0
                           + thanks.get(uid, 0) * 3 + p["react_up"] * 1.5, 1),
        })
    experts.sort(key=lambda e: -e["score"])

    DIR.mkdir(exist_ok=True)
    # привязка вопрос→кластер: по ней отчёт считает специализацию экспертов
    with (DIR / "assignments.jsonl").open("w", encoding="utf-8") as w:
        for qid, lab in zip(qids, labels):
            w.write(json.dumps({"qid": qid, "cluster": int(lab)}) + "\n")
    (DIR / "clusters.json").write_text(
        json.dumps(clusters, ensure_ascii=False, indent=1), encoding="utf-8")
    (DIR / "experts.json").write_text(
        json.dumps(experts, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\nКластеров ≥15 вопросов: {len(clusters)}, экспертов ≥30 ответов: {len(experts)}")
    print(f"Вопросов всего: {len(questions):,}, с ответом: "
          f"{sum(1 for q in questions if answers.get(q)):,} "
          f"({sum(1 for q in questions if answers.get(q)) / len(questions):.0%})")
    print("\nТоп-10 экспертов:")
    for e in experts[:10]:
        print(f"  {e['score']:>7} | {e['name'][:28]:28} | ответов {e['answers']:>5} | "
              f"людям {e['people_helped']:>4} | спасибо {e['thanks']:>3} | {e['active']}")
    print("\nТоп-15 тем:")
    for c in clusters[:15]:
        print(f"  {c['size']:>5} | {c['stage'][:34]:34} | ответ {c['answered_rate']:.0%} | "
              f"гайд {c['guide']['score']:.2f} | {', '.join(c['terms'][:6])}")
    print(f"\n-> {DIR}")


if __name__ == "__main__":
    main()
