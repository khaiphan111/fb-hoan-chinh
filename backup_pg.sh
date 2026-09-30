#!/bin/bash
# Backup Postgres (Supabase) bot fb-hoan-chinh qua tunnel socat 127.0.0.1:5433.
# - pg_dump custom format (-Fc): nho, restore nhanh bang pg_restore
# - xoay vong giu 7 ban moi nhat trong ~/workspace/backups/pg/
# - gui ban moi nhat ve Telegram admin (ban off-VM)
# - KHONG dung backup_db.sh (sqlite, da dong bang tu 28/09) cho production nua
set -u
ROOT="$HOME/workspace/fb-hoan-chinh"
ENV_FILE="$ROOT/botcheckv2/backend/.env"
BACKUP_DIR="$HOME/workspace/backups/pg"
mkdir -p "$BACKUP_DIR"
TS=$(date '+%Y%m%d-%H%M%S')
OUT="$BACKUP_DIR/fb_pg_$TS.dump"
# pg_dump phai cung major version voi server (Supabase chay PG17)
PG_DUMP=/usr/lib/postgresql/17/bin/pg_dump
PG_RESTORE=/usr/lib/postgresql/17/bin/pg_restore
[ -x "$PG_DUMP" ] || PG_DUMP=pg_dump
[ -x "$PG_RESTORE" ] || PG_RESTORE=pg_restore

# Doc connection string tu .env (tro ve tunnel noi bo)
DBURL=$(grep -E "^SUPABASE_DB_URL=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '\r"' | tr -d "'")
[ -n "${DBURL:-}" ] || { echo "THAT BAI: khong doc duoc SUPABASE_DB_URL tu $ENV_FILE"; exit 1; }

# Tunnel phai song thi moi dump duoc
if ! (echo > /dev/tcp/127.0.0.1/5433) 2>/dev/null; then
  echo "THAT BAI: tunnel 127.0.0.1:5433 khong mo (pg_tunnel chet?)"
  exit 1
fi

echo "dang dump Postgres -> $OUT ..."
if ! "$PG_DUMP" "$DBURL" -Fc -f "$OUT" 2>"$OUT.err"; then
  echo "THAT BAI: pg_dump loi:"
  cat "$OUT.err"
  rm -f "$OUT" "$OUT.err"
  exit 1
fi
rm -f "$OUT.err"

# verify: file phai co noi dung + pg_restore --list doc duoc
SIZE=$(stat -c%s "$OUT" 2>/dev/null || echo 0)
if [ "$SIZE" -lt 10240 ]; then
  echo "THAT BAI: file backup qua nho (${SIZE} bytes), co the dump rong"
  rm -f "$OUT"
  exit 1
fi
if ! "$PG_RESTORE" --list "$OUT" >/dev/null 2>&1; then
  echo "THAT BAI: pg_restore khong doc duoc file backup"
  rm -f "$OUT"
  exit 1
fi
echo "backup OK: $OUT ($(numfmt --to=iec-i --suffix=B "$SIZE"))"

# xoay vong: giu 7 ban moi nhat
ls -t "$BACKUP_DIR"/fb_pg_*.dump 2>/dev/null | tail -n +8 | xargs -r rm -f
echo "local: $(ls "$BACKUP_DIR"/fb_pg_*.dump 2>/dev/null | wc -l) ban"

# gui ban moi nhat ve Telegram admin (off-VM)
token=$(grep -E "^ADMIN_BOT_TOKEN=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '\r"' | tr -d "'")
chat_id=$(grep -E "^ADMIN_TG_ID=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '\r"' | tr -d "'")
if [ -n "${token:-}" ] && [ -n "${chat_id:-}" ]; then
  export https_proxy=http://hatch-egress-proxy:3128 http_proxy=http://hatch-egress-proxy:3128
  export HTTPS_PROXY=http://hatch-egress-proxy:3128 HTTP_PROXY=http://hatch-egress-proxy:3128
  if curl -s -m 180 -X POST "https://api.telegram.org/bot${token}/sendDocument" \
    -F "chat_id=${chat_id}" \
    -F "document=@${OUT}" \
    -F "caption=💾 Backup Postgres bot ${TS}" | grep -q '"ok":true'; then
    echo "da gui Telegram admin"
  else
    echo "CANH BAO: gui Telegram THAT BAI (backup local van OK)"
  fi
else
  echo "thieu ADMIN_BOT_TOKEN/ADMIN_TG_ID, bo qua gui Telegram"
fi
