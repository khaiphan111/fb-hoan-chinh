#!/bin/bash
# Khoi dong heavy-worker (process rieng chay poller nang: buff Playwright).
# Tach khoi bot chinh de browser crash/an RAM khong keo theo bot ban hang.
set -u
cd "$(dirname "$0")/botcheckv2/backend" || exit 1
# Nap .env (SUPABASE_DB_URL, BOT_TOKEN...)
set -a; [ -f ./.env ] && . ./.env; set +a
# Dong moi FD giu lock watchdog (bai hoc 2026-09-28) - worker khong duoc giu lock
for fd in /proc/$$/fd/*; do
  if [ -L "$fd" ] && [ "$(readlink "$fd" 2>/dev/null)" = "/tmp/fb-watchdog.lock" ]; then
    eval "exec ${fd##*/}>&-"
  fi
done
export POLLER_ROLE=heavy
exec setsid nohup "$HOME/fbvenv/bin/python" -m app.heavy_worker \
  >>"$HOME/workspace/fb-hoan-chinh/heavy_worker.log" 2>&1
