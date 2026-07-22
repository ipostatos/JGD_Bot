#!/bin/sh
# Бэкап news.db: профили, подписки и ЗАШИФРОВАННЫЕ ключи inFakt пользователей.
# Копировать файл на живой базе нельзя — SQLite может писать в этот момент,
# копия окажется битой. Поэтому только `.backup` (консистентный снимок).
#
# Ставится в cron на VPS:
#   crontab -e
#   17 3 * * * /opt/jdg/deploy/backup.sh >> /var/log/jdg-backup.log 2>&1
set -eu

DB=/opt/jdg/news.db
DEST=/opt/backups/jdg
KEEP_DAYS=14

mkdir -p "$DEST"
chmod 700 "$DEST"

STAMP=$(date +%Y%m%d-%H%M)
OUT="$DEST/news-$STAMP.db"

/opt/jdg/venv/bin/python - "$DB" "$OUT" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(src)
d = sqlite3.connect(dst)
with d:
    s.backup(d)          # консистентный снимок под блокировкой SQLite
d.close(); s.close()
PY

gzip -f "$OUT"
chmod 600 "$OUT.gz"

# ротация: старше KEEP_DAYS дней удаляем
find "$DEST" -name 'news-*.db.gz' -mtime +"$KEEP_DAYS" -delete

# .env отдельно: в нём FERNET_KEY, без которого ключи inFakt из бэкапа
# расшифровать нельзя — база без него бесполезна
cp /opt/jdg/.env "$DEST/env-$STAMP.bak"
chmod 600 "$DEST/env-$STAMP.bak"
find "$DEST" -name 'env-*.bak' -mtime +"$KEEP_DAYS" -delete

echo "$(date -Iseconds) ok: $(du -h "$OUT.gz" | cut -f1) -> $OUT.gz"
