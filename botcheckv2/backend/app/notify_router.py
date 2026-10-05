"""Định tuyến thông báo: mỗi kênh -> 1 bot nhận riêng.

- 13 kênh thông báo (CHANNELS), mỗi kênh có 1 dòng ghi chú cách dùng.
- 4 bot có sẵn: notify (@regmail_110_bot), viotp (Troly_mai_bot),
  admin (bot admin), main (bot chính).
- Bot tự thêm: lưu trong setting `notify_custom_bots` (JSON),
  token từng bot ở setting `notify_bot_token_<id>`.
- Kênh -> bot: setting `notify_route_<channel>` (đổi trong panel
  là có hiệu lực ngay, không cần restart).
- Bot tự thêm chạy polling để nút Duyệt/Từ chối bấm được
  (dùng chung notify_action_router với các callback duyệt có sẵn).

Import lazy mọi thứ liên quan handlers/admin_bot để tránh circular import:
module này được import từ admin_settings, common, ops...
"""
import asyncio
import logging
import os
import re

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message

from . import db

log = logging.getLogger("notify_router")

# ─────────────────────────────────────────────────────────────
# Kênh thông báo: id | nhãn | ghi chú cách dùng | bot mặc định
# ─────────────────────────────────────────────────────────────
CHANNELS = [
    ("order_acc", "🛒 Đơn acc",
     "Tin đơn mua acc mới — khách mua xong là báo ngay.",
     "notify"),
    ("order_buff", "🚀 Đơn buff",
     "Tin đơn buff tương tác mới.",
     "notify"),
    ("rent", "📱 Thuê số",
     "Đơn thuê số mới + OTP về.",
     "viotp"),
    ("loan", "💰 Công nợ",
     "Khách xin ứng tiền (kèm nút Duyệt/Từ chối), nhắc nợ, quá hạn.",
     "notify"),
    ("topup_pending", "💳 Nạp/rút chờ duyệt",
     "Nạp bank + rút tiền chờ duyệt (kèm nút Duyệt/Từ chối).",
     "admin"),
    ("topup_done", "💵 Nạp tiền thành công",
     "PayOS cộng tiền tự động xong là báo.",
     "admin"),
    ("new_user", "👤 Khách mới",
     "Khách mới bấm Start bot (kèm tên + ai giới thiệu).",
     "admin"),
    ("consign", "🏷️ Ký gửi",
     "Tranh chấp ký gửi, đối tác rút tiền, duyệt lô ký gửi.",
     "notify"),
    ("warranty", "🛡️ Bảo hành",
     "Khiếu nại bảo hành mới (kèm ảnh bằng chứng).",
     "notify"),
    ("report", "📊 Báo cáo",
     "Báo cáo sáng 7h: doanh thu, tồn kho, top loại bán chạy.",
     "notify"),
    ("fraud", "⚠️ Gian lận",
     "Cảnh báo trial ảo, rút tiền lớn, nghi rửa tiền.",
     "notify"),
    ("stock", "📦 Kho",
     "Acc DIE bị cách ly, hàng tồn quá ngày, dọn kho, UID theo dõi.",
     "notify"),
    ("system", "🔧 Hệ thống",
     "Bot chết, tunnel DB, backup, phát giftcode...",
     "notify"),
]
CHANNEL_BY_ID = {c[0]: c for c in CHANNELS}

BUILTIN_BOTS = {
    "notify": {"name": "Bot báo đơn", "desc": "Bot báo tin có sẵn của shop"},
    "viotp": {"name": "Bot báo thuê số", "desc": "Bot báo tin thuê số OTP có sẵn"},
    "admin": {"name": "Bot admin", "desc": "Bot admin đang dùng để quản trị"},
    "main": {"name": "Bot chính", "desc": "Bot bán hàng chính"},
}


# ─────────────────────────────────────────────────────────────
# Registry bot
# ─────────────────────────────────────────────────────────────
def _custom_bots() -> list:
    try:
        raw = db.get_setting("notify_custom_bots", "") or ""
        data = __import__("json").loads(raw) if raw else []
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_custom_bots(items: list):
    import json
    db.set_setting("notify_custom_bots", json.dumps(items, ensure_ascii=False))


def list_bots() -> list:
    """[{id, name, kind, username}] — kind: builtin | custom."""
    out = [{"id": bid, "name": info["name"], "kind": "builtin",
            "username": ""}
           for bid, info in BUILTIN_BOTS.items()]
    for c in _custom_bots():
        out.append({"id": f"custom:{c['id']}", "name": c.get("name", c["id"]),
                    "kind": "custom",
                    "username": db.get_setting(
                        f"notify_bot_username_{c['id']}", "") or ""})
    return out


def bot_display(bot_id: str) -> str:
    for b in list_bots():
        if b["id"] == bot_id:
            un = f" ({b['username']})" if b.get("username") else ""
            return f"{b['name']}{un}"
    return bot_id


def get_route(channel: str) -> str:
    ch = CHANNEL_BY_ID.get(channel)
    if not ch:
        return "notify"
    bid = (db.get_setting(f"notify_route_{channel}", "") or "").strip()
    valid = {b["id"] for b in list_bots()}
    if bid in valid:
        return bid
    return ch[3]


def set_route(channel: str, bot_id: str):
    db.set_setting(f"notify_route_{channel}", bot_id)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return (s or "bot")[:16]


def add_custom_bot(name: str) -> str:
    items = _custom_bots()
    base = _slug(name)
    cid, i = base, 2
    while any(c["id"] == cid for c in items):
        cid = f"{base}_{i}"
        i += 1
    items.append({"id": cid, "name": name.strip()[:32]})
    _save_custom_bots(items)
    return cid


def delete_custom_bot(cid: str):
    items = [c for c in _custom_bots() if c["id"] != cid]
    _save_custom_bots(items)
    for k in (f"notify_bot_token_{cid}", f"notify_bot_username_{cid}"):
        try:
            db.set_setting(k, "")
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
# Gửi tin
# ─────────────────────────────────────────────────────────────
def _proxy_session():
    proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
             or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy"))
    return AiohttpSession(proxy=proxy) if proxy else None


_custom_managers: dict = {}


def _privileged_ids() -> set:
    try:
        from .notify_bot import privileged_ids
        return privileged_ids()
    except Exception:
        return set()


def _admin_ids() -> list:
    ids = []
    for key in ("admin_tg_id", "admin_tg_group_id"):
        try:
            v = (db.get_setting(key, "") or "").strip()
            if v:
                ids.append(int(v))
        except Exception:
            pass
    return ids


async def _send_to(bot, chat_ids, text, reply_markup=None, photo=None) -> bool:
    ok = False
    for chat_id in chat_ids:
        try:
            if photo:
                await bot.send_photo(chat_id, photo, caption=text,
                                     parse_mode="HTML",
                                     reply_markup=reply_markup)
            else:
                await bot.send_message(chat_id, text, parse_mode="HTML",
                                       reply_markup=reply_markup)
            ok = True
        except Exception as e:
            log.warning("notify send via %s to %s failed: %s",
                        getattr(bot, "id", "?"), chat_id, e)
    return ok


async def _send_via(bot_id: str, text, reply_markup=None, photo=None) -> bool:
    """Gửi qua 1 bot cụ thể. Trả True nếu gửi được ≥1 người nhận."""
    try:
        if bot_id == "notify":
            from .notify_bot import manager, privileged_ids
            if not manager.running or not manager.bot:
                return False
            return await _send_to(manager.bot, privileged_ids(), text,
                                 reply_markup, photo)
        if bot_id == "viotp":
            from .viotp_notify import manager, privileged_ids
            if not manager.running or not manager.bot:
                return False
            return await _send_to(manager.bot, privileged_ids(), text,
                                 reply_markup, photo)
        if bot_id == "admin":
            from .admin_bot import manager
            if not getattr(manager, "running", False) or not manager.bot:
                return False
            return await _send_to(manager.bot, _admin_ids(), text,
                                 reply_markup, photo)
        if bot_id == "main":
            from .handlers.core import manager
            if not getattr(manager, "running", False) or not manager.bot:
                return False
            return await _send_to(manager.bot, _admin_ids(), text,
                                 reply_markup, photo)
        if bot_id.startswith("custom:"):
            mgr = _custom_managers.get(bot_id)
            bot = mgr.bot if mgr and mgr.bot else None
            if bot is None:
                token = (db.get_setting(
                    f"notify_bot_token_{bot_id[7:]}", "") or "").strip()
                if not token:
                    return False
                bot = Bot(token=token,
                          default=DefaultBotProperties(parse_mode="HTML"),
                          session=_proxy_session())
                try:
                    return await _send_to(bot, _privileged_ids(), text,
                                         reply_markup, photo)
                finally:
                    try:
                        await bot.session.close()
                    except Exception:
                        pass
            return await _send_to(bot, _privileged_ids(), text,
                                 reply_markup, photo)
    except Exception as e:
        log.warning("notify _send_via %s failed: %s", bot_id, e)
    return False


async def send(channel: str, text: str, reply_markup=None, photo=None,
               fallback: bool = True) -> bool:
    """Gửi tin theo kênh đã định tuyến. fallback: hỏng -> thử bot báo đơn,
    rồi bot chính (giữ nguyên hành vi cũ)."""
    primary = get_route(channel)
    if await _send_via(primary, text, reply_markup, photo):
        return True
    if fallback:
        for bid in ("notify", "main"):
            if bid == primary:
                continue
            if await _send_via(bid, text, reply_markup, photo):
                log.info("notify channel %s fallback -> %s", channel, bid)
                return True
    return False


async def test_bot(bot_id: str) -> tuple:
    """Gửi tin test qua bot. Trả (ok, msg)."""
    ok = await _send_via(bot_id, "🧪 <b>Tin test bot báo tin</b>\n"
                                "Bot đang hoạt động bình thường.\n"
                                "Từ nay các tin thuộc kênh được gán sẽ về đây.")
    return ok, ("✅ Đã gửi tin test!" if ok
                else "❌ Gửi thất bại — kiểm tra bot đã chạy + bạn đã bấm Start bot chưa.")


# ─────────────────────────────────────────────────────────────
# Router dùng chung cho nút Duyệt/Từ chối trên mọi bot báo tin
# (fix bug: nút trên @regmail_110_bot bấm không ăn vì handler nằm ở bot chính)
# ─────────────────────────────────────────────────────────────
_action_router = None


def get_action_router() -> Router:
    global _action_router
    if _action_router is not None:
        return _action_router
    from .handlers import loan as loan_h
    from .handlers import wallet as wallet_h
    from .handlers import consign as consign_h
    from . import admin_bot as admin_h
    r = Router()
    r.callback_query.register(loan_h.on_loan_approve,
                              F.data.startswith("loan:ap:"))
    r.callback_query.register(loan_h.on_loan_reject,
                              F.data.startswith("loan:rj:"))
    r.callback_query.register(wallet_h.on_admin_withdraw_approve,
                              F.data.startswith("tg_admin_withdraw_approve_"))
    r.callback_query.register(wallet_h.on_admin_withdraw_reject,
                              F.data.startswith("tg_admin_withdraw_reject_"))
    r.callback_query.register(consign_h._on_kgreport,
                              F.data.startswith("kgreport:"))
    # tg_admin_confirm_ chỉ có sẵn trên bot admin -> các bot báo tin khác
    # (notify, custom) cần đăng ký riêng để nút "Xác nhận + Cộng tiền" bấm được.
    r.callback_query.register(admin_h.on_admin_confirm,
                              F.data.startswith("tg_admin_confirm_"))
    r.callback_query.register(consign_h._kga_payout,
                              F.data.startswith("kga:payout:"))
    # admin bot đã có sẵn handler này -> KHÔNG đăng ký ở đây để tránh chạy 2 lần.
    # Các bot báo tin khác (notify, custom) chưa có -> đăng ký riêng bên dưới.
    _action_router = r
    return r


def get_action_router_for_admin_bot() -> Router:
    """Cho bot admin: chỉ các handler nó chưa có (tránh đăng ký trùng)."""
    from .handlers import loan as loan_h
    from .handlers import wallet as wallet_h
    from .handlers import consign as consign_h
    r = Router()
    r.callback_query.register(loan_h.on_loan_approve,
                              F.data.startswith("loan:ap:"))
    r.callback_query.register(loan_h.on_loan_reject,
                              F.data.startswith("loan:rj:"))
    r.callback_query.register(wallet_h.on_admin_withdraw_approve,
                              F.data.startswith("tg_admin_withdraw_approve_"))
    r.callback_query.register(wallet_h.on_admin_withdraw_reject,
                              F.data.startswith("tg_admin_withdraw_reject_"))
    r.callback_query.register(consign_h._on_kgreport,
                              F.data.startswith("kgreport:"))
    r.callback_query.register(consign_h._kga_payout,
                              F.data.startswith("kga:payout:"))
    return r


_basic_router = None


def _get_basic_router() -> Router:
    global _basic_router
    if _basic_router is not None:
        return _basic_router
    r = Router()

    @r.message(CommandStart())
    async def _on_start(msg: Message):
        if int(msg.from_user.id) not in _privileged_ids():
            return
        await msg.answer(
            "🔔 <b>BOT BÁO TIN SHOP</b>\n\n"
            "Bot này chỉ nhận thông báo phân loại từ shop.\n"
            "Bạn không cần gõ lệnh gì ở đây — có tin mới là bot tự báo.\n\n"
            "Đổi kênh nào về bot nào: vào bot admin → ⚙️ Cài đặt → 🔔 Bot báo tin.",
            parse_mode="HTML")
    _basic_router = r
    return r


class CustomBotManager:
    """Bot báo tin tự thêm: polling để nhận callback Duyệt/Từ chối."""

    def __init__(self, cid: str, name: str):
        self.cid = cid
        self.name = name
        self.bot = None
        self.dp = Dispatcher(storage=MemoryStorage())
        self.dp.include_router(get_action_router())
        self.dp.include_router(_get_basic_router())
        self.task = None
        self.running = False

    async def start(self):
        token = (db.get_setting(f"notify_bot_token_{self.cid}", "") or "").strip()
        if not token:
            log.info("Custom notify bot %s: chưa có token.", self.cid)
            return False
        self.bot = Bot(token=token,
                       default=DefaultBotProperties(parse_mode="HTML"),
                       session=_proxy_session())
        self.running = True
        try:
            await self.bot.delete_webhook(drop_pending_updates=False)
            try:
                me = await self.bot.get_me()
                if me.username:
                    db.set_setting(f"notify_bot_username_{self.cid}",
                                   f"@{me.username}")
            except Exception:
                pass
            self.task = asyncio.create_task(
                self.dp.start_polling(self.bot, handle_signals=False))
            log.info("Custom notify bot %s (%s) started.", self.cid, self.name)
            return True
        except Exception as e:
            log.error("Custom notify bot %s start failed: %s", self.cid, e)
            self.running = False
            self.bot = None
            return False

    async def stop(self):
        self.running = False
        try:
            if self.bot:
                await self.bot.session.close()
        except Exception:
            pass
        if self.task:
            self.task.cancel()
        self.bot = None


async def start_custom_bots():
    for c in _custom_bots():
        bid = f"custom:{c['id']}"
        if bid in _custom_managers:
            continue
        mgr = CustomBotManager(c["id"], c.get("name", c["id"]))
        if await mgr.start():
            _custom_managers[bid] = mgr


async def start_one_custom_bot(cid: str) -> bool:
    bid = f"custom:{cid}"
    old = _custom_managers.pop(bid, None)
    if old:
        await old.stop()
    items = _custom_bots()
    name = next((c.get("name", cid) for c in items if c["id"] == cid), cid)
    mgr = CustomBotManager(cid, name)
    if await mgr.start():
        _custom_managers[bid] = mgr
        return True
    return False


async def stop_one_custom_bot(cid: str):
    mgr = _custom_managers.pop(f"custom:{cid}", None)
    if mgr:
        await mgr.stop()


async def validate_token(token: str) -> tuple:
    """Kiểm tra token qua getMe. Trả (ok, username_or_err)."""
    token = (token or "").strip()
    if ":" not in token:
        return False, "Token không đúng định dạng (phải dạng 123456:ABC...)"
    b = Bot(token=token, session=_proxy_session())
    try:
        me = await b.get_me()
        return True, f"@{me.username}" if me.username else me.first_name
    except Exception:
        return False, "Token không hợp lệ hoặc không kết nối được Telegram."
    finally:
        try:
            await b.session.close()
        except Exception:
            pass
