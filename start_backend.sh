#!/bin/bash
# FIX 2026-09-28: backend KHONG duoc thua ke FD lock cua watchdog.
# Khi start tu trong `flock ... bash` (restart tay) hoac tu watchdog.sh
# (cron chay `flock -n ... bash watchdog.sh`), tien trinh backend thua ke FD
# dang giu /tmp/fb-watchdog.lock -> giu lock VINH VIEN -> watchdog liet hoan
# toan (moi tick flock -n deu fail). Dong moi FD tro toi file lock truoc khi
# exec uvicorn. (Khong duoc sua watchdog.sh theo yeu cau user.)
_lock_fds=""
for _fd in /proc/$$/fd/*; do
  _n=${_fd##*/}
  case $_n in 0|1|2) continue;; esac
  if [ "$(readlink "$_fd" 2>/dev/null)" = "/tmp/fb-watchdog.lock" ]; then
    _lock_fds="$_lock_fds $_n"
  fi
done
for _n in $_lock_fds; do eval "exec $_n>&-"; done
unset _fd _n _lock_fds
# Khoi dong backend FB Live/Die Checker (doc BOT_TOKEN tu backend/.env)
# FIX httpx 0.28.1 crash voi proxy IPv6 (InvalidURL: Invalid port ':1]'):
# hatch-egress-proxy resolve ra IPv6 fd8b:... lam moi AsyncClient loi -> bot mat ket noi Telegram.
# Dung IPv4 truc tiep (resolve dong, fallback 198.19.0.1).
_PROXY_IPV4=$(getent ahostsv4 hatch-egress-proxy 2>/dev/null | head -1 | awk '{print $1}')
_PROXY_HOST=${_PROXY_IPV4:-198.19.0.1}
export https_proxy=http://$_PROXY_HOST:3128
export http_proxy=http://$_PROXY_HOST:3128
export HTTPS_PROXY=http://$_PROXY_HOST:3128
export HTTP_PROXY=http://$_PROXY_HOST:3128
# FIX: no_proxy mac dinh cua he thong chua [::1] dang bracket IPv6 lam httpx crash
# (InvalidURL) -> moi AsyncClient deu loi -> check FB luon bao DIE. Ghi de lai gia tri sach.
export no_proxy="localhost,127.0.0.1"
export NO_PROXY="localhost,127.0.0.1"
cd "$(dirname "$0")/botcheckv2/backend"
# FIX 2026-09-29: nap .env TRUC TIEP vao moi truong truoc khi exec (khong phu
# thuoc python-dotenv). Da gap: watchdog restart luc 16:16 +07, load_dotenv()
# khong nap duoc SUPABASE_DB_URL -> backend roi ve SQLite cu (don toi #104,
# thieu consign_enabled) -> bot phuc vu du lieu SAI + bao "ky gui dang tat".
# set -a de moi bien trong .env tu dong export.
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi
exec "$HOME/fbvenv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
