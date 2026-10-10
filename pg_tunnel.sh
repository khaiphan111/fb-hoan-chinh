#!/bin/bash
# Giu socat tunnel 127.0.0.1:5433 -> Supabase PG (qua egress proxy) luon song.
# Backend tren VM khong noi truc tiep Supabase:5432 (proxy chan) nen can tunnel nay.
LOCK=/tmp/pg-tunnel.lock
exec 9>"$LOCK"
flock -n 9 || exit 0
if (echo > /dev/tcp/127.0.0.1/5433) 2>/dev/null; then
    exit 0
fi
PROXY_HOST=$(getent ahostsv4 hatch-egress-proxy 2>/dev/null | head -1 | awk '{print $1}')
PROXY_HOST=${PROXY_HOST:-$(python3 -c "from urllib.parse import urlparse; import os; print(urlparse(os.environ.get('https_proxy','')).hostname or 'hatch-egress-proxy')")}
# kill socat cu chet (neu con process nhung khong nghe)
pkill -f "socat TCP-LISTEN:5433" 2>/dev/null
sleep 1
# FIX (bảo mật): KHÔNG hard-code project ref Supabase trong repo — trước đây dòng socat
# chứa thẳng "db.<project-ref>.supabase.co", lộ hạ tầng DB cho người đọc source.
# Nay lấy host từ biến môi trường PG_SUPABASE_HOST hoặc SUPABASE_DB_URL (trong .env).
PG_HOST="${PG_SUPABASE_HOST:-}"
if [ -z "$PG_HOST" ]; then
    ENV_FILE="${FB_ENV_FILE:-$HOME/workspace/fb-hoan-chinh/botcheckv2/backend/.env}"
    if [ -f "$ENV_FILE" ]; then
        # FIX 2026-10-10: đọc PG_SUPABASE_HOST từ .env trước (SUPABASE_DB_URL
        # trỏ 127.0.0.1:5433 là tunnel, suy host từ đó là vòng lặp)
        PG_HOST=$(grep -m1 '^PG_SUPABASE_HOST=' "$ENV_FILE" | cut -d= -f2- | tr -d '"' | tr -d "'")
    fi
fi
if [ -z "$PG_HOST" ]; then
    ENV_FILE="${FB_ENV_FILE:-$HOME/workspace/fb-hoan-chinh/botcheckv2/backend/.env}"
    if [ -f "$ENV_FILE" ]; then
        PG_URL=$(grep -m1 '^SUPABASE_DB_URL=' "$ENV_FILE" | cut -d= -f2- | tr -d '"' | tr -d "'")
        PG_HOST=$(python3 -c "import sys;from urllib.parse import urlsplit;print(urlsplit(sys.argv[1]).hostname or '')" "$PG_URL" 2>/dev/null)
    fi
fi
if [ -z "$PG_HOST" ]; then
    echo "[$(date '+%F %T')] pg_tunnel: THIEU PG_SUPABASE_HOST/SUPABASE_DB_URL -> khong tao tunnel" >> /tmp/socat-pg.log
    exit 1
fi
# 9>&- : dong fd lock truoc khi spawn de socat KHONG thua ke lock vinh vien
# (bay 2026-09-29: socat giu lock -> moi tick cron flock -n fail -> keepalive liet)
# >> thay vi > : giu lich su log cho bao cao su co
9>&- setsid nohup socat TCP-LISTEN:5433,bind=127.0.0.1,reuseaddr,fork \
    "PROXY:${PROXY_HOST}:${PG_HOST}:5432,proxyport=3128" \
    >>/tmp/socat-pg.log 2>&1 < /dev/null &
echo "[$(date '+%F %T')] pg_tunnel: (re)started socat, proxy=$PROXY_HOST db=$PG_HOST" >> /tmp/socat-pg.log
