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
# 9>&- : dong fd lock truoc khi spawn de socat KHONG thua ke lock vinh vien
# (bay 2026-09-29: socat giu lock -> moi tick cron flock -n fail -> keepalive liet)
# >> thay vi > : giu lich su log cho bao cao su co
9>&- setsid nohup socat TCP-LISTEN:5433,bind=127.0.0.1,reuseaddr,fork \
    "PROXY:${PROXY_HOST}:db.mbfgzthgbzhxzdynvfuv.supabase.co:5432,proxyport=3128" \
    >>/tmp/socat-pg.log 2>&1 < /dev/null &
echo "[$(date '+%F %T')] pg_tunnel: (re)started socat, proxy=$PROXY_HOST" >> /tmp/socat-pg.log
