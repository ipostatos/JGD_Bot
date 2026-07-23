# Деплой JDG HUB

VPS: root@46.224.220.94, каталог `/opt/jdg`, systemd `jdg`, порт 4400,
Caddy-сайт `jdg-46-224-220-94.sslip.io` (авто-TLS).

## Обновление кода (строго git archive, см. grabli Обшака)

```bash
git -C C:/Users/user/Desktop/JDG archive HEAD | ssh root@46.224.220.94 "tar -x -C /opt/jdg"
ssh root@46.224.220.94 "chown -R jdg:jdg /opt/jdg && cd /opt/jdg && venv/bin/pip install -q -r requirements.txt && systemctl restart jdg"
```

⚠️ Всегда `git -C <корень>` — из подкаталога archive упакует только его.
`.env` в архив не входит (не в git) — лежит на VPS отдельно, не затирается.

⚠️ **`chown -R jdg:jdg` обязателен**: сервис работает не от root, а `tar -x`
под root кладёт файлы root'у. Без chown после деплоя сервис не прочитает часть
файлов (та же грабля, что уронила KwadratPL на 8 часов). Права `.env` и
`news.db` — 600, каталог — 750.

⚠️ Если правился `deploy/jdg.service`, его надо доставить в systemd отдельно:

```bash
ssh root@46.224.220.94 "cp /opt/jdg/deploy/jdg.service /etc/systemd/system/ && systemctl daemon-reload && systemctl restart jdg"
```

Остановка ограничена `TimeoutStopSec=15`: фоновые циклы уходят в потоки и не
отменяются, без лимита рестарт висел ~90 с и Caddy всё это время отдавал 502.

## Обновление контента гайда (первоисточник обновился)

```bash
ssh root@46.224.220.94 "cd /opt/jdg/sources/guide && git pull && cd /opt/jdg && venv/bin/python tools/build_content.py"
```

Рестарт не нужен: данные статические, читаются с диска.

## Справочник PKD 2025 (нужен для /pkd и поиска кодов)

`webapp/data/pkd.json` и `pkd_keys.json` генерируются из файлов GUS и в git
не едут (весь `webapp/data/` в `.gitignore`), поэтому на новой машине их
надо собрать, иначе `/api/pkd` и команда бота ответят ошибкой:

Проще собрать локально и скопировать — на VPS тогда не нужны ни pandas,
ни pymupdf (вместе ~100 МБ ради разовой операции):

```bash
python tools/pkd_build.py --download          # локально, ~9 МБ с klasyfikacje.stat.gov.pl
scp C:/Users/user/Desktop/JDG/webapp/data/pkd*.json root@46.224.220.94:/opt/jdg/webapp/data/
ssh root@46.224.220.94 "chown jdg:jdg /opt/jdg/webapp/data/pkd*.json"
```

Если всё же собирать на сервере: `venv/bin/pip install -r requirements-tools.txt`,
затем `venv/bin/python tools/pkd_build.py --download`. Сборка около минуты —
PDF на 767 страниц. Повторять только когда GUS правит классификацию.
Словарь синонимов `webapp/pkd_synonyms.json` — ручной, лежит в git.

## Первичная установка

```bash
ssh root@46.224.220.94 "mkdir -p /opt/jdg"
git -C C:/Users/user/Desktop/JDG archive HEAD | ssh root@46.224.220.94 "tar -x -C /opt/jdg"
scp C:/Users/user/Desktop/JDG/.env root@46.224.220.94:/opt/jdg/.env
ssh root@46.224.220.94 "cd /opt/jdg && python3 -m venv venv && venv/bin/pip install -r requirements.txt \
  && git clone --depth 1 https://github.com/sobolevbel/jdg sources/guide \
  && venv/bin/python tools/build_content.py \
  && cp deploy/jdg.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now jdg"
# Caddy: добавить сайт в /etc/caddy/Caddyfile:
#   jdg-46-224-220-94.sslip.io { reverse_proxy 127.0.0.1:4400 }
# затем: systemctl reload caddy
```

## Caddy: заголовки безопасности

Блок сайта в `/etc/caddy/Caddyfile` (бэкапы там же, `Caddyfile.bak-jdg-*`):

```
jdg-46-224-220-94.sslip.io {
	@nocache { path / *.html *.css *.js *.webmanifest }
	header @nocache Cache-Control "no-cache"
	header {
		Content-Security-Policy "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; child-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors https://web.telegram.org https://telegram.org 'self'"
		X-Content-Type-Options "nosniff"
		Referrer-Policy "strict-origin-when-cross-origin"
		Permissions-Policy "geolocation=(), microphone=(), camera=(), payment=()"
		-Server
	}
	reverse_proxy 127.0.0.1:4400
}
```

⚠️ `worker-src blob:` нужен pdf.js в читалке, `script-src https://telegram.org` —
скрипту Mini App, `frame-ancestors` — Telegram Web. Менял CSP — прогони
страницы под ней (20 штук) и убедись, что в консоли нет отказов.

**`'unsafe-inline'` убран из `script-src` 2026-07-23**: весь код страниц вынесен
в `webapp/js/<страница>.js` (`tools/extract_inline_js.py`), инлайновые onclick
заменены на `addEventListener`. Тест `test_csp.py` не даст вернуть инлайн обратно.
В `style-src` директива осталась сознательно: 175 атрибутов `style` — это вёрстка,
выполнить через них скрипт нельзя, а переписывать всё — отдельная задача без
выигрыша в безопасности.
После правки: `caddy validate --config /etc/caddy/Caddyfile && systemctl reload caddy`.

## Бэкап базы

`news.db` — профили, подписки и зашифрованные ключи inFakt пользователей.
Ставится один раз:

```bash
ssh root@46.224.220.94 "chmod +x /opt/jdg/deploy/backup.sh && \
  (crontab -l 2>/dev/null | grep -v backup.sh; \
   echo '17 3 * * * /opt/jdg/deploy/backup.sh >> /var/log/jdg-backup.log 2>&1') | crontab -"
```

Снимки и копии `.env` — в `/opt/backups/jdg`, хранятся 14 дней, права 600.
⚠️ **Без `FERNET_KEY` из `.env` ключи inFakt в бэкапе расшифровать нельзя** —
восстанавливать только пару «база + .env того же дня». Копию `FERNET_KEY`
держать вне VPS (менеджер паролей).

Восстановление:

```bash
systemctl stop jdg
gunzip -c /opt/backups/jdg/news-YYYYMMDD-HHMM.db.gz > /opt/jdg/news.db
systemctl start jdg
```

## Проверка

```bash
curl -s https://jdg-46-224-220-94.sslip.io/api/health   # {"ok":true,"bot":true}
```
