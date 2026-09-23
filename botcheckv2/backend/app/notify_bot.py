"""Bot thông báo riêng cho admin: nhận tin đơn mua acc theo thời gian thực.

- Token cấu hình qua settings `notify_bot_token` (tạo bot mới qua @BotFather).
- Chỉ phục vụ các ID đặc quyền (`notify_privileged_ids`, cách nhau dấu phẩy;
  mặc định dùng `admin_tg_id`). Người lạ nhắn tới sẽ bị im lặng bỏ qua.
"""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message

from . import db

log = logging.getLogger("notify_bot")
router = Router()


def privileged_ids() -> set:
    """Tập ID đặc quyền được dùng bot thông báo."""
    ids = set()
    raw = (db.get_setting("notify_privileged_ids", "") or "").strip()
    for p in raw.replace(";", ",").split(","):
        p = p.strip()
        if p.isdigit():
            ids.add(int(p))
    if not ids:
        aid = (db.get_setting("admin_tg_id", "") or "").strip()
        if aid.isdigit():
            ids.add(int(aid))
    return ids


def _is_privileged(tg_id: int) -> bool:
    return int(tg_id) in privileged_ids()


@router.message(CommandStart())
async def on_start(msg: Message):
    if not _is_privileged(msg.from_user.id):
        return
    n = len(privileged_ids())
    await msg.answer(
        "🔔 <b>BOT THÔNG BÁO SHOP ACC</b>\n\n"
        "Bot này nhận thông báo đơn mua acc theo thời gian thực.\n"
        f"Đang phục vụ {n} ID đặc quyền.\n\n"
        "Gõ /donmoi để xem 5 đơn gần nhất.",
        parse_mode="HTML",
    )


@router.message(Command("donmoi"))
async def on_recent(msg: Message):
    if not _is_privileged(msg.from_user.id):
        return
    try:
        orders = db.acc_recent_orders(5)
    except Exception:
        orders = []
    if not orders:
        await msg.answer("📭 Chưa có đơn mua acc nào.")
        return
    import html as _html
    import time as _time
    from .util import vnd
    lines = ["🧾 <b>5 ĐƠN MUA ACC GẦN NHẤT</b>", "━━━━━━━━━━━━", ""]
    for o in orders:
        o = dict(o)
        ts = _time.strftime("%H:%M %d/%m", _time.localtime(o.get("created_at") or 0))
        who = f"@{o['username']}" if o.get("username") else ""
        lines.append(
            f"• 🆔 Mã đơn <code>{o['id']}</code> — { _html.escape(o.get('cat_name') or '')} — "
            f"<b>{vnd(o.get('price') or 0)}</b>\n"
            f"  👤 <code>{o.get('tg_id')}</code> {_html.escape(who)} — 🕐 {ts}"
        )
    await msg.answer("\n".join(lines), parse_mode="HTML")


@router.message()
async def on_other(msg: Message):
    # Người lạ: im lặng. ID đặc quyền nhắn linh tinh: gợi ý lệnh.
    if not _is_privileged(msg.from_user.id):
        return
    await msg.answer("Gõ /donmoi để xem đơn mới nhất.")


class NotifyBotManager:
    def __init__(self):
        self.bot = None
        self.dp = Dispatcher(storage=MemoryStorage())
        self.dp.include_router(router)
        self.task = None
        self.running = False

    async def start(self):
        token = (db.get_setting("notify_bot_token") or "").strip()
        main_token = (db.get_setting("bot_token", "") or "").strip()
        admin_token = (db.get_setting("admin_bot_token", "") or "").strip()
        if not token or token in (main_token, admin_token):
            log.info("Notify bot token not set (or trùng bot khác). Notify bot disabled.")
            return False
        proxy = (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("http_proxy")
        )
        session = AiohttpSession(proxy=proxy) if proxy else None
        self.bot = Bot(token=token,
                       default=DefaultBotProperties(parse_mode="HTML"),
                       session=session)
        self.running = True
        log.info("Notify Bot starting...")
        try:
            await self.bot.delete_webhook(drop_pending_updates=False)  # giữ tin nhắn đang chờ qua restart
            self.task = asyncio.create_task(self.dp.start_polling(self.bot))
            return True
        except Exception as e:
            log.error("Failed to start notify bot: %s", e)
            self.running = False
            return False

    async def stop(self):
        if self.running and self.bot:
            self.running = False
            log.info("Notify Bot stopping...")
            try:
                await self.bot.session.close()
            except Exception:
                pass
            if self.task:
                self.task.cancel()
            self.bot = None

    async def send_to_privileged(self, text: str) -> bool:
        """Gửi tin tới mọi ID đặc quyền qua bot thông báo. Trả True nếu gửi được ≥1."""
        if not self.running or not self.bot:
            return False
        ok = False
        for pid in privileged_ids():
            try:
                await self.bot.send_message(pid, text, parse_mode="HTML")
                ok = True
            except Exception as e:
                log.warning("Notify send to %s failed: %s", pid, e)
        return ok


manager = NotifyBotManager()
