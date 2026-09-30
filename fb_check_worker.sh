#!/bin/bash
# Khoi dong fb-check-worker (process rieng, port 8001).
# Worker chet/treo khi Facebook chan IP thi bot chinh van song (fail-closed).
set -u
cd "$(dirname "$0")/botcheckv2/backend" || exit 1
# Nap .env (SUPABASE_DB_URL cho cookie pool)
set -a; [ -f ./.env ] && . ./.env; set +a
# Dong moi FD giu lock watchdog (bai hoc 2026-09-28) - worker khong duoc giu lock
for fd in /proc/$$/fd/*; do
  if [ -L "$fd" ] && [ "$(readlink "$fd" 2>/dev/null)" = "/tmp/fb-watchdog.lock" ]; then
    eval "exec ${fd##*/}>&-"
  fi
done
exec setsid nohup "$HOME/fbvenv/bin/python" -m uvicorn app.fb_worker:app \
  --host 127.0.0.1 --port 8001 --log-level warning \
  >>"$HOME/workspace/fb-hoan-chinh/fb_worker.log" 2>&1
