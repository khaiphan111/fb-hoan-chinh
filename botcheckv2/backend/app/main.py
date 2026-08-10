# FB Live/Die Checker & Tiktok Checker
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from . import config, db
from .api import router as api_router
from .bot import manager, zalo_manager
from .admin_bot import manager as admin_manager
from .poller import poller
from .keep_alive import start_keep_alive, stop_keep_alive

app = FastAPI(title=config.APP_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


import asyncio

@app.on_event("startup")
async def on_startup():
    print("DEBUG: Start init_db", flush=True)
    db.init_db()
    print("DEBUG: Start migrate_db", flush=True)
    db.migrate_db()
    
    async def start_services():
        token = db.get_setting("bot_token")
        zalo_token = db.get_setting("zalo_bot_token")
        
        started_any = False
        print(f"DEBUG: bot_token={bool(token)}, zalo_token={bool(zalo_token)}", flush=True)
        if token and db.get_setting("setup_done") == "1":
            print("DEBUG: Start manager.start(token)", flush=True)
            if await manager.start(token):
                started_any = True
                
        if zalo_token:
            print("DEBUG: Start zalo_manager.start(zalo_token)", flush=True)
            if await zalo_manager.start(zalo_token):
                started_any = True
                
        print("DEBUG: Start admin_manager.start()", flush=True)
        await admin_manager.start()
                
        if started_any:
            print("DEBUG: Start poller.start()", flush=True)
            poller.start()
        print("DEBUG: Finish start_services", flush=True)

    # Khởi chạy dưới nền để Uvicorn có thể mở port ngay lập tức
    asyncio.create_task(start_services())
    print("DEBUG: Finish on_startup", flush=True)

    # Giữ server Render luôn hoạt động - tự ping mỗi 14 phút
    start_keep_alive()


@app.on_event("shutdown")
async def on_shutdown():
    await poller.stop()
    await manager.stop()
    await zalo_manager.stop()
    await admin_manager.stop()
    await stop_keep_alive()


@app.get("/api/health")
def health():
    return {"ok": True, "app": config.APP_NAME, "version": config.APP_VERSION}


if os.path.isdir(config.STATIC_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(config.STATIC_DIR, "assets")), name="assets")
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
