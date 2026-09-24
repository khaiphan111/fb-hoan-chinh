# FB Live/Die Checker & Tiktok Checker
import os

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from . import config, db, util
from .api import router as api_router
from .campaigns_api import router as campaigns_router
from .reseller_api import router as reseller_router
from .bot import manager, zalo_manager
from .admin_bot import manager as admin_manager
from .notify_bot import manager as notify_manager
from .poller import poller
from .keep_alive import start_keep_alive, stop_keep_alive

app = FastAPI(title=config.APP_NAME)

# CORS: dashboard chạy cùng origin nên không cần mở. Chỉ mở cho các domain
# reseller/API bên ngoài khai báo qua biến môi trường CORS_ORIGINS (cách nhau bằng dấu phẩy).
_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(campaigns_router, prefix="/api")
app.include_router(reseller_router)

from .miniapp import router as miniapp_router
app.include_router(miniapp_router)


import asyncio
import time
import resource
import faulthandler
import signal

# BẪY CHẨN ĐOÁN (tạm): khi backend đơ, gửi `kill -USR1 <pid>` để dump stack
# toàn bộ threads ra file mà KHÔNG kill process → biết chính xác dòng nào treo.
_fault_f = open("/tmp/fb-loop-dump.txt", "w")
faulthandler.register(signal.SIGUSR1, file=_fault_f, all_threads=True)

_APP_START_TS = time.monotonic()

_STARTUP_NOTIFY_STAMP = os.path.expanduser("~/workspace/fb-hoan-chinh/watchdog/last_startup_notify")
_STARTUP_NOTIFY_MIN_INTERVAL = 3600  # giây: tối đa 1 tin "kết nối thành công" mỗi 60 phút

_HEARTBEAT_FILE = os.path.expanduser("~/workspace/fb-hoan-chinh/watchdog/backend_heartbeat")
_heartbeat_task = None


def _startup_notify_allowed() -> bool:
    """Chống spam tin nhắn 'Bot đã kết nối thành công!' khi backend restart liên tục.
    Trả về True nếu đã hơn _STARTUP_NOTIFY_MIN_INTERVAL kể từ lần gửi trước."""
    try:
        last = float(open(_STARTUP_NOTIFY_STAMP).read().strip())
        if time.time() - last < _STARTUP_NOTIFY_MIN_INTERVAL:
            return False
    except Exception:
        pass
    try:
        with open(_STARTUP_NOTIFY_STAMP, "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass
    return True


def _check_prev_shutdown():
    """GĐ2 quan trắc: phát hiện lần tắt trước là clean (SIGTERM) hay
    bất thường (kill -9 / crash / mất điện) qua heartbeat file.
    Không đụng tới watchdog.sh."""
    try:
        raw = open(_HEARTBEAT_FILE).read().strip()
    except Exception:
        print("DEBUG: no heartbeat file (first run?)", flush=True)
        return
    if raw.startswith("clean:"):
        print(f"DEBUG: previous shutdown was CLEAN at {raw[6:]}", flush=True)
        return
    try:
        age = time.time() - float(raw)
    except Exception:
        return
    if age > 120:
        print(f"WARNING: previous shutdown UNCLEAN (heartbeat {int(age)}s old) "
              f"-> likely kill -9 / crash / reboot, NOT a clean SIGTERM", flush=True)
    else:
        print(f"DEBUG: previous heartbeat {int(age)}s ago (quick restart)", flush=True)


async def _heartbeat_loop():
    """Ghi heartbeat mỗi 30s để lần khởi động sau biết lần tắt trước có clean không."""
    while True:
        try:
            with open(_HEARTBEAT_FILE, "w") as f:
                f.write(str(int(time.time())))
        except Exception:
            pass
        await asyncio.sleep(30)

@app.on_event("startup")
async def on_startup():
    _check_prev_shutdown()
    print("DEBUG: Start init_db", flush=True)
    db.init_db()
    print("DEBUG: Start migrate_db", flush=True)
    db.migrate_db()
    print("DEBUG: Start migrate_new_features", flush=True)
    db.migrate_new_features()
    
    async def start_services():
        token = db.get_setting("bot_token")
        zalo_token = db.get_setting("zalo_bot_token")
        setup_done = db.get_setting("setup_done")
        # Token co san (VD: tu env BOT_TOKEN) -> coi nhu da setup de bot tu chay
        if token and setup_done != "1":
            db.set_setting("setup_done", "1")
            setup_done = "1"
        
        started_any = False
        print(f"DEBUG: bot_token={'set' if token else 'empty'}, setup_done={setup_done!r}", flush=True)
        
        if token and setup_done == "1":
            print("DEBUG: Calling manager.start(token)...", flush=True)
            tg_ok = await manager.start(token)
            print(f"DEBUG: manager.start() returned: {tg_ok}", flush=True)
            if tg_ok:
                started_any = True
                # Gửi thông báo khởi động thành công đến admin (chống spam: tối đa 1 tin/60 phút)
                admin_tg_id = db.get_setting("admin_tg_id")
                if admin_tg_id and _startup_notify_allowed():
                    try:
                        await manager.bot.send_message(
                            int(admin_tg_id),
                            "\u2705 <b>Bot đã kết nối thành công!</b>\n"
                            f"\u23f0 Thời gian: {util.vn_time_str('%H:%M:%S %d/%m/%Y')}\n"
                            "\U0001f916 Đang polling và sẵn sàng nhận lệnh.",
                            parse_mode="HTML"
                        )
                    except Exception as notify_err:
                        print(f"DEBUG: Could not notify admin: {notify_err}", flush=True)
            else:
                print("DEBUG: manager.start() FAILED - check bot token!", flush=True)
        else:
            print(f"DEBUG: Skipped TG bot - token={'empty' if not token else 'ok'}, setup_done={setup_done!r}", flush=True)
                
        if zalo_token:
            print("DEBUG: Start zalo_manager.start(zalo_token)", flush=True)
            if await zalo_manager.start(zalo_token):
                started_any = True
                
        print("DEBUG: Start admin_manager.start()", flush=True)
        await admin_manager.start()
        print("DEBUG: Start notify_manager.start()", flush=True)
        await notify_manager.start()
                
        if started_any:
            print("DEBUG: Start poller.start()", flush=True)
            poller.start()
        print("DEBUG: Start payos.start()", flush=True)
        from . import payos as payos_mod
        payos_mod.start()
        print(f"DEBUG: Finish start_services. tg_running={manager.running}", flush=True)

    # Khởi chạy dưới nền để Uvicorn có thể mở port ngay lập tức
    asyncio.create_task(start_services())
    print("DEBUG: Finish on_startup", flush=True)

    # GĐ2 quan trắc: heartbeat để phát hiện kill -9 / crash ở lần khởi động sau
    global _heartbeat_task
    _heartbeat_task = asyncio.create_task(_heartbeat_loop())

    # Giữ server Render luôn hoạt động - tự ping mỗi 14 phút
    start_keep_alive()


@app.on_event("shutdown")
async def on_shutdown():
    # GĐ2 quan trắc: dừng heartbeat TRƯỚC để nó không ghi đè marker,
    # rồi mới đánh dấu tắt clean (SIGTERM) ở CUỐI để phân biệt với kill -9.
    global _heartbeat_task
    try:
        if _heartbeat_task:
            _heartbeat_task.cancel()
    except Exception:
        pass

    async def _stop_step(name, coro, timeout=15):
        # GĐ2 fix: shutdown từng treo vĩnh viễn ở đây (chờ await không bao giờ xong).
        # wait_for đảm bảo shutdown luôn kết thúc và log rõ bước nào treo.
        try:
            await asyncio.wait_for(coro, timeout=timeout)
            print(f"DEBUG: shutdown step '{name}' ok", flush=True)
        except asyncio.TimeoutError:
            print(f"WARNING: shutdown step '{name}' TIMEOUT after {timeout}s (skipped)", flush=True)
        except Exception as e:
            print(f"WARNING: shutdown step '{name}' error: {e}", flush=True)

    await _stop_step("poller.stop", poller.stop())
    try:
        from . import payos as payos_mod
        await _stop_step("payos.stop", payos_mod.stop())
    except Exception:
        pass
    await _stop_step("manager.stop", manager.stop())
    await _stop_step("zalo_manager.stop", zalo_manager.stop())
    await _stop_step("admin_manager.stop", admin_manager.stop())
    await _stop_step("notify_manager.stop", notify_manager.stop())
    await _stop_step("stop_keep_alive", stop_keep_alive())
    try:
        with open(_HEARTBEAT_FILE, "w") as f:
            f.write(f"clean:{int(time.time())}")
    except Exception:
        pass
    print("DEBUG: on_shutdown complete", flush=True)


@app.get("/api/health")
def health():
    return {"ok": True, "app": config.APP_NAME, "version": config.APP_VERSION}


@app.get("/api/debug/status")
async def debug_status():
    """GĐ2 quan trắc: uptime, RAM, độ trễ event loop, số task asyncio.
    Chỉ bind 127.0.0.1 nên không cần auth."""
    t0 = time.monotonic()
    await asyncio.sleep(0.05)
    lag_ms = (time.monotonic() - t0 - 0.05) * 1000
    try:
        rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except Exception:
        rss_mb = None
    try:
        n_tasks = sum(1 for t in asyncio.all_tasks() if not t.done())
    except Exception:
        n_tasks = None
    hb_age = None
    try:
        raw = open(_HEARTBEAT_FILE).read().strip()
        if not raw.startswith("clean:"):
            hb_age = int(time.time() - float(raw))
    except Exception:
        pass
    return {
        "ok": True,
        "uptime_s": int(time.monotonic() - _APP_START_TS),
        "rss_mb": round(rss_mb, 1) if rss_mb is not None else None,
        "loop_lag_ms": round(lag_ms, 1),
        "async_tasks": n_tasks,
        "heartbeat_age_s": hb_age,
    }


@app.post("/payos/webhook")
async def payos_webhook(request: Request):
    """Webhook PayOS (dùng khi backend có public HTTPS).
    Verify chữ ký HMAC rồi mới cộng tiền."""
    from . import payos as payos_mod
    try:
        payload = await request.json()
    except Exception:
        return {"ok": False, "message": "Body không phải JSON"}
    ok, message = await payos_mod.handle_webhook(payload)
    return {"ok": ok, "message": message}


if os.path.isdir(config.STATIC_DIR):
    assets_dir = os.path.join(config.STATIC_DIR, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
    images_dir = os.path.join(os.path.dirname(__file__), "..", "data", "images")
    if not os.path.isdir(images_dir):
        os.makedirs(images_dir, exist_ok=True)
    app.mount("/images", StaticFiles(directory=images_dir), name="images")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        index = os.path.join(config.STATIC_DIR, "index.html")
        if os.path.isfile(index):
            return FileResponse(index)
        return JSONResponse({"detail": "Frontend chưa build"}, status_code=404)
