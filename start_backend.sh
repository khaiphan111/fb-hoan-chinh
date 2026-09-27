#!/bin/bash
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
exec "$HOME/fbvenv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
