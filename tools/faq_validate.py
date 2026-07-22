"""Шаг 6c: проверка FAQ-заготовок против источников + сборка файлов для вычитки.

Не важно, кто писал ответ — модель через API или ассистент в сессии: каждая
цифра должна дословно встречаться в источниках, иначе флаг. Плюс собирает
faq_review.md (на вычитку) и missing_topics.md (заявки на разделы гайда).

    python tools/faq_validate.py

Вход:  chat_analysis/faq_draft.json + faq_sources.json + clusters_labeled.json
Выход: те же faq_review.md / missing_topics.md, что и у faq_build.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from faq_build import unverified_numbers  # noqa: E402  — одна и та же проверка

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "chat_analysis"


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    draft = json.loads((DIR / "faq_draft.json").read_text(encoding="utf-8"))
    srcs = {s["cluster"]: s["sources"]
            for s in json.loads((DIR / "faq_sources.json").read_text(encoding="utf-8"))}
    meta = {c["id"]: c for c in
            json.loads((DIR / "clusters_labeled.json").read_text(encoding="utf-8"))}

    for r in draft:
        c = meta.get(r["cluster"], {})
        r.setdefault("demand", c.get("size"))
        r.setdefault("stage", c.get("stage"))
        r.setdefault("pain", c.get("pain"))
        r.setdefault("volatile", c.get("volatile"))
        r.setdefault("covered", c.get("covered"))
        r.setdefault("gap", c.get("gap"))
        r["unverified_numbers"] = unverified_numbers(r.get("a") or "",
                                                     srcs.get(r["cluster"], []))
        r["needs_review"] = bool(r["unverified_numbers"] or r.get("missing")
                                 or r.get("confidence") != "high")
    draft.sort(key=lambda r: -(r.get("demand") or 0))
    (DIR / "faq_draft.json").write_text(
        json.dumps(draft, ensure_ascii=False, indent=1), encoding="utf-8")

    written = [r for r in draft if (r.get("a") or "").strip()]
    empty = [r for r in draft if not (r.get("a") or "").strip()]

    L = [f"# FAQ-заготовки ({len(written)} шт.) — черновик, не публиковать без вычитки\n",
         "Ответы написаны по гайду и `rates_2026.json`; чат дал только вопросы.",
         "Пометь проверенные записи `\"approved\": true` в `faq_draft.json`,",
         "затем `python tools/faq_build.py --publish`.\n",
         f"⚠️ Требуют внимания: {sum(1 for r in written if r['needs_review'])} "
         f"из {len(written)}. Ещё {len(empty)} тем — в `missing_topics.md`.\n"]
    for i, r in enumerate(written, 1):
        flags = []
        if r["unverified_numbers"]:
            flags.append(f"🔴 числа не из источников: {', '.join(r['unverified_numbers'])}")
        if r.get("missing"):
            flags.append(f"🟡 не хватило источников: {r['missing']}")
        if r.get("recheck"):
            flags.append(f"🔎 перепроверить: {r['recheck']}")
        if r.get("volatile"):
            flags.append("⏳ зависит от года")
        L.append(f"\n## {i}. {r['q']}\n")
        L.append(f"*спрос {r.get('demand')} вопросов · этап {r.get('stage')} · "
                 f"боль {r.get('pain')}/5 · уверенность {r.get('confidence')} · "
                 f"источники: {', '.join(r.get('sources') or [])}*\n")
        L.append(r["a"])
        if flags:
            L.append("\n" + "\n".join(f"- {f}" for f in flags))
    (DIR / "faq_review.md").write_text("\n".join(L), encoding="utf-8")

    gaps = empty + [r for r in written if r.get("covered") == "no" or r.get("missing")]
    G = ["# Разделы, которых в гайде не хватает\n",
         "Из вопросов чата и вердикта по покрытию. Порядок — спрос × цена ошибки.\n"]
    for r in sorted(gaps, key=lambda r: -((r.get("demand") or 0) * int(r.get("pain") or 1))):
        G.append(f"\n## {r['q']}\n")
        G.append(f"- **Спрос**: {r.get('demand')} вопросов, боль {r.get('pain')}/5, "
                 f"этап {r.get('stage')}")
        G.append(f"- **Чего нет в гайде**: {r.get('gap') or '—'}")
        G.append(f"- **Не хватило при написании ответа**: {r.get('missing') or '—'}")
    (DIR / "missing_topics.md").write_text("\n".join(G), encoding="utf-8")

    ok = sum(1 for r in written if not r["needs_review"])
    flagged = [r for r in written if r["unverified_numbers"]]
    print(f"Заготовок с ответом: {len(written)} (чистых {ok}), "
          f"без ответа: {len(empty)}, заявок на разделы: {len(gaps)}")
    if flagged:
        print("Числа не из источников:")
        for r in flagged:
            print(f"  cluster {r['cluster']}: {', '.join(r['unverified_numbers'])}")
    print(f"-> {DIR / 'faq_review.md'}\n-> {DIR / 'missing_topics.md'}")


if __name__ == "__main__":
    main()
