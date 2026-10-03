"""Bot thông báo riêng cho admin: nhận tin khách thuê số OTP theo thời gian thực.

- Token cấu hình qua settings `viotp_notify_bot_token` (tạo bot mới qua @BotFather,
  PHẢI khác token bot chính/bot admin/bot báo đơn acc).
- Chỉ phục vụ các ID đặc quyền (`notify_privileged_ids`, cách nhau dấu phẩy;
  mặc định dùng `admin_tg_id`). Người lạ nhắn tới sẽ bị im lặng bỏ qua.
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
import dynamic_credentials as _dc

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message

from . import db

log = logging.getLogger("viotp_notify")
router = Router()

_VAULT_CRED = "custom.viotp-notify-bot"


def _vault_token() -> str:
    """Surrogate token từ Secure Vault (Sentinel thay bằng token thật khi gọi ra ngoài)."""
    entry = _dc.dynamic_credential_entry(_VAULT_CRED)
    tok = str(entry.get("surrogate") or "").strip()
    return tok


def privileged_ids() -> set:
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
    await msg.answer(
        "📱 <b>BOT BÁO THUÊ SỐ</b>\n\n"
        "Bot này báo đơn thuê số OTP theo thời gian thực.\n"
        "Gõ /donmoi để xem 5 đơn thuê gần nhất.",
        parse_mode="HTML",
    )


@router.message(Command("donmoi"))
async def on_recent(msg: Message):
    if not _is_privileged(msg.from_user.id):
        return
    try:
        rows = db.viotp_rental_list_all(5)
    except Exception:
        rows = []
    if not rows:
        await msg.answer("📭 Chưa có đơn thuê số nào.")
        return
    import html as _html
    import time as _time
    from .util import vnd
    lines = ["📱 <b>5 ĐƠN THUÊ SỐ GẦN NHẤT</b>", ""]
    for r in rows:
        r = dict(r)
        ts = _time.strftime("%H:%M %d/%m", _time.localtime(r.get("created_at") or 0))
        label = db.VIOTP_STATUS_LABEL.get(r.get("status"), r.get("status"))
        lines.append(
            f"• #{r['id']} — {_html.escape(r.get('service_name') or '')} — "
            f"<b>{vnd(r.get('sell_price') or 0)}</b> — {label}\n"
            f"  📞 <code>{_html.escape(r.get('phone_number') or '')}</code> — "
            f"👤 <code>{r.get('tg_id')}</code> — 🕐 {ts}"
        )
    await msg.answer("\n".join(lines), parse_mode="HTML")


class ViotpNotifyManager:
    def __init__(self):
        self.bot = None
        self.dp = Dispatcher(storage=MemoryStorage())
        self.dp.include_router(router)
        self.task = None
        self.running = False

    async def start(self):
        token = _vault_token()
        main_token = (db.get_setting("bot_token", "") or "").strip()
        admin_token = (db.get_setting("admin_bot_token", "") or "").strip()
        acc_token = (db.get_setting("notify_bot_token", "") or "").strip()
        if not token or token in (main_token, admin_token, acc_token):
            log.info("ViOTP notify bot token not set (hoặc trùng bot khác). Disabled.")
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
        log.info("ViOTP Notify Bot starting...")
        try:
            await self.bot.delete_webhook(drop_pending_updates=False)
            self.task = asyncio.create_task(self.dp.start_polling(self.bot))
            return True
        except Exception as e:
            log.error("Failed to start viotp notify bot: %s", e)
            self.running = False
            return False

    async def stop(self):
        if self.running and self.bot:
            self.running = False
            log.info("ViOTP Notify Bot stopping...")
            try:
                await self.bot.session.close()
            except Exception:
                pass
            if self.task:
                self.task.cancel()
            self.bot = None

    async def send_to_privileged(self, text: str) -> bool:
        """Gửi tin tới mọi ID đặc quyền. Trả True nếu gửi được ≥1."""
        if not self.running or not self.bot:
            return False
        ok = False
        for pid in privileged_ids():
            try:
                await self.bot.send_message(pid, text, parse_mode="HTML")
                ok = True
            except Exception as e:
                log.warning("ViOTP notify send to %s failed: %s", pid, e)
        return ok


manager = ViotpNotifyManager()


def rental_notify_text(r: dict, username: str = "") -> str:
    """Tin báo admin khi có đơn thuê số mới."""
    import html as _html
    from .util import vnd
    import time as _time
    r = dict(r)
    ts = _time.strftime("%H:%M %d/%m", _time.localtime(r.get("created_at") or 0))
    profit = (r.get("sell_price") or 0) - (r.get("cost_price") or 0)
    who = f"@{_html.escape(username)} " if username else ""
    return (
        f"📱 <b>ĐƠN THUÊ SỐ MỚI #{r['id']}</b>\n"
        f"━━━━━━━━━━━━\n"
        f"📱 Dịch vụ: <b>{_html.escape(r.get('service_name') or '')}</b>\n"
        f"📞 Số: <code>{_html.escape(r.get('phone_number') or '')}</code>\n"
        f"💰 Vốn {vnd(r.get('cost_price') or 0)}đ → Bán <b>{vnd(r.get('sell_price') or 0)}đ</b> "
        f"(lãi {vnd(profit)}đ)\n"
        f"👤 Khách: {who}<code>{r.get('tg_id')}</code>\n"
        f"🕐 {ts}"
    )


def otp_notify_text(r: dict) -> str:
    """Tin báo admin khi đơn thuê đã nhận được OTP."""
    import html as _html
    r = dict(r)
    return (
        f"🔑 <b>OTP ĐÃ VỀ #{r['id']}</b>\n"
        f"━━━━━━━━━━━━\n"
        f"📱 {_html.escape(r.get('service_name') or '')}\n"
        f"📞 <code>{_html.escape(r.get('phone_number') or '')}</code>\n"
        f"🔑 Mã: <code>{_html.escape(r.get('otp_code') or '')}</code>\n"
        f"👤 <code>{r.get('tg_id')}</code>"
    )
