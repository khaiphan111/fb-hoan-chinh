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
_TG_THROTTLE_WINDOW=900   # 15 phut

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
                local _now _last
                _now=$(date +%s)
                _last=$(cat "$_TG_THROTTLE_STATE" 2>/dev/null || echo 0)
                _last=$(printf '%s' "$_last" | tr -cd '0-9')
                [ -z "$_last" ] && _last=0
                if [ $((_now - _last)) -lt "$_TG_THROTTLE_WINDOW" ]; then
                    printf '[%s] tg_notify THROTTLED (restart burst, tin truoc cach day %ss)\n' \
                        "$(date '+%F %T')" "$((_now - _last))" >> "$_TG_THROTTLE_LOG" 2>/dev/null
                    return 0
                fi
                printf '%s' "$_now" > "$_TG_THROTTLE_STATE" 2>/dev/null
                ;;
        esac
    fi
    command curl "$@"
}
