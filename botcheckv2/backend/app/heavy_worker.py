"""Heavy worker: chạy các poller NẶNG (buff Playwright) ở process riêng,
tách khỏi bot chính để browser crash/ăn RAM không kéo theo bot bán hàng.

Chạy:  POLLER_ROLE=heavy python -m app.heavy_worker   (từ botcheckv2/backend/)
Hoặc:  bash ~/workspace/fb-hoan-chinh/heavy_worker.sh

Giám sát: ghi heartbeat `heavy_worker_last_heartbeat` mỗi 30s;
cron `fb-heavy-worker-keepalive` restart khi heartbeat quá 3 phút.
"""
import asyncio
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [heavy_worker] %(levelname)s %(message)s")
log = logging.getLogger("heavy_worker")

HEARTBEAT_KEY = "heavy_worker_last_heartbeat"
HEARTBEAT_INTERVAL = 30


async def _heartbeat_loop():
    from app import db
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        try:
            db.set_setting(HEARTBEAT_KEY, str(int(time.time())))
        except Exception as e:
            log.warning("heartbeat lỗi: %s", e)


async def amain():
    from app import db, config
    # Fail-closed: không nối được Postgres thì crash để cron restart,
    # không chạy với dữ liệu sai.
    db.get_conn()
    db.init_db()

    token = (db.get_setting("bot_token") or "").strip()
    if not token:
        token = (os.environ.get("BOT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("heavy_worker: thiếu bot_token, không gửi được tin báo")

    from aiogram import Bot
    bot = Bot(token=token)

    from app.poller import FollowerPoller
    poller = FollowerPoller()
    poller.set_bot(bot)
    poller.start(role="heavy")
    log.info("heavy_worker đã khởi động (chỉ chạy: %s)",
             ", ".join(FollowerPoller.HEAVY_LOOP_ATTRS))
    try:
        db.set_setting(HEARTBEAT_KEY, str(int(time.time())))
    except Exception:
        pass
    await _heartbeat_loop()


def main():
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
