#!/bin/bash
# Backup DB bot fb-hoan-chinh:
# - sqlite backup API (an toan khi DB dang chay WAL)
# - xoay vong giu 7 ban moi nhat trong ~/workspace/backups/db/
# - gui ban moi nhat ve Telegram admin (ban off-VM)
set -u
ROOT="$HOME/workspace/fb-hoan-chinh"
DB="$ROOT/botcheckv2/backend/data.db"
ENV_FILE="$ROOT/botcheckv2/backend/.env"
BACKUP_DIR="$HOME/workspace/backups/db"
mkdir -p "$BACKUP_DIR"
TS=$(date '+%Y%m%d-%H%M%S')
OUT="$BACKUP_DIR/data.db.$TS.db"

[ -f "$DB" ] || { echo "DB khong ton tai: $DB"; exit 1; }

python3 - "$DB" "$OUT" <<'EOF'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
d = sqlite3.connect(dst)
with d:
    s.backup(d)
d.close(); s.close()
print("backup OK:", dst)
EOF

# xoay vong: giu 7 ban moi nhat
ls -t "$BACKUP_DIR"/data.db.*.db 2>/dev/null | tail -n +8 | xargs -r rm -f
echo "local: $(ls "$BACKUP_DIR"/data.db.*.db 2>/dev/null | wc -l) ban"

# gui ban backup moi nhat ve Telegram admin (off-VM)
token=$(grep -E "^ADMIN_BOT_TOKEN=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '\r"' | tr -d "'")
chat_id=$(grep -E "^ADMIN_TG_ID=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '\r"' | tr -d "'")
if [ -n "${token:-}" ] && [ -n "${chat_id:-}" ]; then
  export https_proxy=http://hatch-egress-proxy:3128 http_proxy=http://hatch-egress-proxy:3128
  export HTTPS_PROXY=http://hatch-egress-proxy:3128 HTTP_PROXY=http://hatch-egress-proxy:3128
  if curl -s -m 120 -X POST "https://api.telegram.org/bot${token}/sendDocument" \
    -F "chat_id=${chat_id}" \
    -F "document=@${OUT}" \
    -F "caption=💾 Backup DB bot ${TS}" | grep -q '"ok":true'; then
    echo "da gui Telegram admin"
  else
    echo "gui Telegram THAT BAI"
  fi
else
  echo "thieu ADMIN_BOT_TOKEN/ADMIN_TG_ID, bo qua gui Telegram"
fi
