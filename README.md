# JDG Гид — @JGD_PL_bot

Telegram Mini App «Путеводитель по JDG (ИП) в Польше»: гайд сообщества
[JDG PBH](https://t.me/JDG_PBH) + калькуляторы ZUS/налогов + персональный план
предпринимателя. Контент — [sobolevbel/jdg](https://github.com/sobolevbel/jdg) (CC0).

## Структура

- `server.py` — FastAPI (Mini App + API) + aiogram-бот одним процессом
- `calc.py` / `webapp/calc.js` — расчётное ядро (зеркала, ставки в `rates_2026.json`)
- `tools/build_content.py` — markdown гайда → `webapp/data/` (статьи, поиск, картинки)
- `webapp/` — vanilla JS Mini App, дизайн-система ISSA (`theme.css` + `jdg.css`)
- `deploy/` — systemd-юнит и процедура деплоя
- концепция и роадмап: `docs/CONCEPT.md`

## Локальный запуск

```bash
pip install -r requirements.txt
git clone --depth 1 https://github.com/sobolevbel/jdg sources/guide
python tools/build_content.py
DISABLE_BOT=1 uvicorn server:app --port 4400   # без поллинга (иначе 409 с продом)
pytest -q
```

## Данные и актуальность

Все ставки — в `rates_2026.json` (версионируется, поле `updated`).
При смене года: создать `rates_2027.json`, обновить формулы при изменении правил,
прогнать `test_calc.py` по опорным числам гайда.
