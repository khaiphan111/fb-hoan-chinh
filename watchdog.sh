#!/bin/bash
# Watchdog: kiem tra backend, tu khoi dong lai neu chet, bao admin qua Telegram.
# Chay bang cron moi 5 phut. Khong in token ra log.
set -u
ROOT="$HOME/workspace/fb-hoan-chinh"
BACKEND="$ROOT/botcheckv2/backend"
ENV_FILE="$BACKEND/.env"
STATE_DIR="$ROOT/watchdog"
mkdir -p "$STATE_DIR"

export https_proxy=http://hatch-egress-proxy:3128
export http_proxy=http://hatch-egress-proxy:3128
export HTTPS_PROXY=http://hatch-egress-proxy:3128
export HTTP_PROXY=http://hatch-egress-proxy:3128
export no_proxy="localhost,127.0.0.1"
export NO_PROXY="localhost,127.0.0.1"

log() { echo "[$(date '+%F %T')] $1" >> "$STATE_DIR/watchdog.log"; }

tg_notify() {
    # $1 = noi dung tin nhan
    local token chat_id
    token=$(grep -E "^ADMIN_BOT_TOKEN=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '\r"' | tr -d "'" )
    chat_id=$(grep -E "^ADMIN_TG_ID=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '\r"' | tr -d "'" )
    [ -z "${token:-}" ] || [ -z "${chat_id:-}" ] && { log "WARN: thieu ADMIN_BOT_TOKEN/ADMIN_TG_ID, khong gui duoc Telegram"; return 1; }
    curl -s -m 15 -X POST "https://api.telegram.org/bot${token}/sendMessage" \
        -d "chat_id=${chat_id}" --data-urlencode "text=$1" -d "parse_mode=HTML" >/dev/null 2>&1
}

alive() {
    curl -s -m 8 http://127.0.0.1:8000/api/health 2>/dev/null | grep -q '"ok":true'
}

# Phat hien may vua reboot (uptime < 10 phut va marker cu khac boot_id hien tai)
boot_id=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo unknown)
last_boot=$(cat "$STATE_DIR/last_boot" 2>/dev/null || echo none)
just_rebooted="no"
if [ "$boot_id" != "$last_boot" ]; then
    echo "$boot_id" > "$STATE_DIR/last_boot"
    [ "$last_boot" != "none" ] && just_rebooted="yes"
fi

if alive; then
    [ "$just_rebooted" = "yes" ] && log "may vua reboot nhung backend van song (kha nang do he thong tu khoi dong)"
    exit 0
fi

log "backend KHONG phan hoi, bat dau khoi dong lai..."
# don dep tien trinh uvicorn chet do (neu con)
for pid in $(pgrep -f "[u]vicorn app.main:app" 2>/dev/null); do kill -9 "$pid" 2>/dev/null; done
sleep 2
cd "$ROOT" && nohup bash start_backend.sh > /tmp/backend_watchdog.log 2>&1 & disown
sleep 20

if alive; then
    if [ "$just_rebooted" = "yes" ]; then
        msg="⚠️ <b>Máy chủ vừa khởi động lại.</b>%0AMik đã tự bật lại bot, mọi thứ chạy bình thường rồi."
    else
        msg="⚠️ <b>Bot bị tắt bất thường.</b>%0AMik đã tự khởi động lại, bot chạy bình thường rồi."
    fi
    # luu y: cache file xlsx trong RAM mat khi restart
    msg="$msg%0A<i>Lưu ý: file Excel đang check dở (nếu có) cần gửi lại.</i>"
    log "khoi dong lai THANH CONG, da bao admin"
    tg_notify "$msg"
else
    log "khoi dong lai THAT BAI, can can thiep tay"
    tg_notify "🚨 <b>Bot bị tắt và mik khởi động lại KHÔNG được.</b>%0AMik kiểm tra giúp mik nhé."
fi
