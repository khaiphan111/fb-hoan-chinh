#!/bin/bash
# Khoi dong backend FB Live/Die Checker (doc BOT_TOKEN tu backend/.env)
export https_proxy=http://hatch-egress-proxy:3128
export http_proxy=http://hatch-egress-proxy:3128
export HTTPS_PROXY=http://hatch-egress-proxy:3128
export HTTP_PROXY=http://hatch-egress-proxy:3128
# FIX: no_proxy mac dinh cua he thong chua [::1] dang bracket IPv6 lam httpx crash
# (InvalidURL) -> moi AsyncClient deu loi -> check FB luon bao DIE. Ghi de lai gia tri sach.
export no_proxy="localhost,127.0.0.1"
export NO_PROXY="localhost,127.0.0.1"
cd "$(dirname "$0")/botcheckv2/backend"
exec "$HOME/fbvenv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
