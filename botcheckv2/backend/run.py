# FB Live/Die Checker — Tác giả: @khaikhai998 | Hỗ trợ: Telegram/Facebook khaikhai998
import socket
import threading
import webbrowser
import os
import urllib.request
import time

import uvicorn

from app.config import APP_NAME, APP_VERSION
from app.main import app

# Khi chạy trên Render, HOST phải là 0.0.0.0 để nhận traffic
HOST = os.getenv("HOST", "0.0.0.0" if os.getenv("RENDER") else "127.0.0.1")

def _free_port(start: int = 8000) -> int:
    for p in range(start, start + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, p))
                return p
            except OSError:
                continue
    return start

# Ưu tiên lấy PORT từ biến môi trường (Render tự cấp)
PORT = int(os.getenv("PORT", _free_port(8000)))

def _open():
    if os.getenv("RENDER"): return # Không mở trình duyệt trên Render
    
    url = f"http://127.0.0.1:{PORT}"
    for _ in range(30):
        try:
            urllib.request.urlopen(url + "/api/health", timeout=1)
            webbrowser.open(url)
            return
        except Exception:
            time.sleep(1)
    webbrowser.open(url)

if __name__ == "__main__":
    print(f"{APP_NAME} v{APP_VERSION} - http://{HOST}:{PORT}")
    threading.Timer(4.0, _open).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
