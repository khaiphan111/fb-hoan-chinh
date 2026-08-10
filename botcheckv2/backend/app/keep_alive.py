"""
Keep Alive - Self Ping Module for Render Free Tier
===================================================
Tự động ping server mỗi 14 phút để tránh bị Render tắt (spin down).
"""

import asyncio
import logging
from datetime import datetime

import httpx

logger = logging.getLogger("keep_alive")

PING_INTERVAL = 840  # 14 phút = 840 giây
TARGET_URL = "https://admin.khaikhaizzy.indevs.in/api/health"

_keep_alive_task = None


async def _ping_loop():
    """Background loop: ping server mỗi 14 phút."""
    await asyncio.sleep(10)  # Chờ server khởi động xong
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                response = await client.get(TARGET_URL)
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                logger.info(f"[Keep Alive] Ping OK | Status: {response.status_code} | {now}")
                print(f"[Keep Alive] Ping OK | Status: {response.status_code} | {now}", flush=True)
            except Exception as e:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                logger.error(f"[Keep Alive] Ping FAILED | Error: {e} | {now}")
                print(f"[Keep Alive] Ping FAILED | Error: {e} | {now}", flush=True)
            await asyncio.sleep(PING_INTERVAL)


def start_keep_alive():
    """Khởi chạy background task keep-alive. Gọi trong FastAPI startup event."""
    global _keep_alive_task
    if _keep_alive_task is None or _keep_alive_task.done():
        _keep_alive_task = asyncio.create_task(_ping_loop())
        print("[Keep Alive] Started! Ping every 14 minutes.", flush=True)
        logger.info("[Keep Alive] Started! Ping every 14 minutes.")
    else:
        print("[Keep Alive] Already running.", flush=True)


async def stop_keep_alive():
    """Dừng background task keep-alive. Gọi trong FastAPI shutdown event."""
    global _keep_alive_task
    if _keep_alive_task and not _keep_alive_task.done():
        _keep_alive_task.cancel()
        try:
            await _keep_alive_task
        except asyncio.CancelledError:
            pass
        print("[Keep Alive] Stopped.", flush=True)
        logger.info("[Keep Alive] Stopped.")
    _keep_alive_task = None
