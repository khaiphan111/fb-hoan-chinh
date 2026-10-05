#!/bin/bash
# tg_throttle.sh — sourced via BASH_ENV BEFORE watchdog.sh runs.
# Purpose: throttle the "Bot bị tắt bất thường" Telegram restart notice to
# at most 1 message per TG_THROTTLE_WINDOW seconds during restart bursts,
# WITHOUT modifying watchdog.sh.
#
# How: defines a curl() shell function that wraps the real curl. Only
# Telegram sendMessage calls carrying the restart-success text are gated;
# everything else (health checks, failure alerts, reboot notices) passes
# through untouched.
#
# Cron usage:
#   BASH_ENV=/home/hatch/workspace/fb-hoan-chinh/watchdog/tg_throttle.sh \
#     flock -n /tmp/fb-watchdog.lock bash /home/hatch/workspace/fb-hoan-chinh/watchdog.sh

_TG_THROTTLE_STATE="/home/hatch/workspace/fb-hoan-chinh/watchdog/tg_throttle.state"
_TG_THROTTLE_LOG="/home/hatch/workspace/fb-hoan-chinh/watchdog/watchdog.log"
_TG_THROTTLE_WINDOW=900   # 15 phut (khong dung nua tu 2026-10-05: chan han tin restart thanh cong)

# 2026-10-05: user muon tin he thong (watchdog/tunnel/backup) sang bot van hanh rieng,
# khong dung chung bot admin. Doc token tu .env (do panel ghi khi them bot).
_OPS_BOT_TOKEN=$(grep -E "^OPS_BOT_TOKEN=" /home/hatch/workspace/fb-hoan-chinh/botcheckv2/backend/.env 2>/dev/null | cut -d= -f2- | tr -d '\r"' | tr -d "'")

curl() {
    local _is_tg=0 _text="" _a _prev=""
    for _a in "$@"; do
        case "$_a" in
            https://api.telegram.org/bot*/sendMessage) _is_tg=1 ;;
        esac
    done
    if [ "$_is_tg" = "1" ]; then
        for _a in "$@"; do
            if [ "$_prev" = "--data-urlencode" ]; then
                case "$_a" in
                    text=*) _text="${_a#text=}" ;;
                esac
            fi
            _prev="$_a"
        done
        case "$_text" in
            *"Bot bị tắt bất thường"*)
                # 2026-10-05: user yeu cau AN HAN tin restart thanh cong.
                # Chan luon, khong gui nua. Tin bao LOI (KHONG duoc),
                # tin reboot may va moi thu khac van di binh thuong.
                printf '[%s] tg_notify BLOCKED (tin restart thanh cong da tat theo yeu cau user)\n' \
                    "$(date '+%F %T')" >> "$_TG_THROTTLE_LOG" 2>/dev/null
                return 0
                ;;
        esac
    fi
    # Chuyen huong sang bot van hanh (neu da cau hinh OPS_BOT_TOKEN)
    if [ -n "${_OPS_BOT_TOKEN:-}" ]; then
        local _new_args=() _u
        for _u in "$@"; do
            case "$_u" in
                https://api.telegram.org/bot*/sendMessage)
                    _u="https://api.telegram.org/bot${_OPS_BOT_TOKEN}/sendMessage" ;;
            esac
            _new_args+=("$_u")
        done
        set -- "${_new_args[@]}"
    fi
    command curl "$@"
}
