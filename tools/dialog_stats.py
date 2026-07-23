"""Сводка по диалогу PKD за окно наблюдения.

    python tools/dialog_stats.py [дней]

Читает обезличенные счётчики из `dialog_events` и печатает ровно те цифры,
по которым решается следующий пакет правил. Решение принимаем не раньше
100–200 начатых диалогов и двух недель наблюдения — до этого числа шумят,
поэтому скрипт сам говорит, набралось ли достаточно.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dialog_telemetry  # noqa: E402

ENOUGH_SESSIONS = 100
ENOUGH_DAYS = 14


def main(days: int = 14) -> None:
    s = dialog_telemetry.stats(days)
    started, finished = s["sessions_started"], s["sessions_finished"]
    print(f"Окно: {days} дн.")
    print(f"Начато разговоров: {started}")
    print(f"Доведено до результата: {finished}"
          + (f" ({finished / started:.0%})" if started else ""))
    if s["anonymous_asks"]:
        print(f"Запросов без идентификатора вкладки: {s['anonymous_asks']}"
              " (приватный режим — в счёт разговоров не идут)")
    print(f"Вопросов до результата в среднем: {s['questions_before_result_avg']}")
    print(f"Возвратов «Назад»: {s['went_back']} · уходов в обычный поиск: {s['went_to_legacy']}")

    print("\nЧем кончались:")
    for k, v in s["final_statuses"].items():
        print(f"  {k:24} {v}")
    if s["routing_hints"]:
        print("\nКуда просились (routing_hint):")
        for k, v in s["routing_hints"].items():
            print(f"  {k:24} {v}")
    if s["dropped_at_question"]:
        print("\nГде обрывался разговор:")
        for k, v in s["dropped_at_question"].items():
            print(f"  {k:32} {v}")
    print("\nОтветы ручки:", ", ".join(f"{k}: {v}" for k, v in s["http"].items()) or "нет")
    print(f"Время ответа: p50 {s['ms_p50']} мс · p95 {s['ms_p95']} мс")

    if started < ENOUGH_SESSIONS or days < ENOUGH_DAYS:
        print(f"\n⏳ Рано для выводов: нужно ≥{ENOUGH_SESSIONS} разговоров "
              f"и ≥{ENOUGH_DAYS} дней наблюдения.")
    else:
        print("\n✅ Данных достаточно, чтобы выбирать следующий пакет правил.")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 14)
