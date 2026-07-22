# Деплой JDG Гид

VPS: root@46.224.220.94, каталог `/opt/jdg`, systemd `jdg`, порт 4400,
Caddy-сайт `jdg-46-224-220-94.sslip.io` (авто-TLS).

## Обновление кода (строго git archive, см. grabli Обшака)

```bash
git -C C:/Users/user/Desktop/JDG archive HEAD | ssh root@46.224.220.94 "tar -x -C /opt/jdg"
ssh root@46.224.220.94 "cd /opt/jdg && venv/bin/pip install -q -r requirements.txt && systemctl restart jdg"
```

⚠️ Всегда `git -C <корень>` — из подкаталога archive упакует только его.
`.env` в архив не входит (не в git) — лежит на VPS отдельно, не затирается.

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

## Проверка

```bash
curl -s https://jdg-46-224-220-94.sslip.io/api/health   # {"ok":true,"bot":true}
```
