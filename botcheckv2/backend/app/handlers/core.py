"""Tách từ app/bot.py (refactor 2026-09-25) — giữ nguyên 100% logic, chỉ chia module theo tính năng."""

import asyncio
import html
import logging
import os
import random
import time
from typing import Optional

import httpx
import re
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, CommandObject, StateFilter
from aiogram.types import (
    BotCommand, Message, URLInputFile, FSInputFile, BufferedInputFile,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup



from .. import db
from .. import perms as _perms
from .. import notify_bot as _notify_bot
from .. import config as _config
from ..persist import SQLiteStorage, check_cache_get, check_cache_set, init_cache_db
from ..util import now, parse_check_args, vnd, vn_time_str
from ..tiktok import parse_username, fetch_tiktok_info, fmt_num, build_info_caption
from ..ig import (
    parse_ig_username, parse_ig_post_id,
    fetch_ig_info, fetch_ig_post_info,
    build_ig_info_caption, build_ig_video_caption
)
from ..fb import check_uid, build_fb_caption
from ..poller import poller

from ..admin_bot import register_adm_menu as _register_adm_menu
from ..shop_menu import register_shop_menu as _register_shop_menu
from aiogram import BaseMiddleware

class TienIchState(StatesGroup):
    """States cho menu tiện ích /tienich — nhập liệu từng bước thay vì gõ lệnh tay."""
    waiting_for_giftcode = State()
    waiting_for_promo = State()
    waiting_for_birthday = State()
    waiting_for_refcode = State()
    waiting_for_getuid = State()
    # /ruttien 3 bước
    waiting_withdraw_amount = State()
    waiting_withdraw_bank = State()
    waiting_withdraw_stk = State()
    # /chuyentien 2 bước
    waiting_transfer_uid = State()
    waiting_transfer_amount = State()

class AntiSpamMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        global _spam_cleanup_ts
        if isinstance(event, Message) and event.text:
            # Key theo (chat, user) để 1 người spam không mute cả group
            uid = event.from_user.id if event.from_user else 0
            key = (event.chat.id, uid)
            now_t = time.time()

            # Dọn dict chống rò rỉ bộ nhớ mỗi 10 phút
            if now_t - _spam_cleanup_ts > 600:
                _spam_cleanup_ts = now_t
                for k in [k for k, v in _user_muted_until.items() if v < now_t]:
                    _user_muted_until.pop(k, None)
                for k in [k for k, v in _user_last_cmd.items() if v < now_t - 3600]:
                    _user_last_cmd.pop(k, None)
                if len(_user_spam_count) > 5000:
                    _user_spam_count.clear()

            # Exempt admin completely from anti-spam / muting
            admin_id = db.get_setting("admin_tg_id")
            if admin_id and str(uid) == str(admin_id):
                return await handler(event, data)

            # Check if muted
            if key in _user_muted_until:
                if now_t < _user_muted_until[key]:
                    return  # Ignore silently
                else:
                    del _user_muted_until[key]
                    if key in _user_spam_count:
                        del _user_spam_count[key]

            # Anti flood (1 cmd per 3 seconds)
            last_cmd_time = _user_last_cmd.get(key, 0)
            if now_t - last_cmd_time < 3:
                _user_spam_count[key] = _user_spam_count.get(key, 0) + 1
                if _user_spam_count[key] >= 5:
                    _user_muted_until[key] = now_t + 900  # Mute 15 mins
                    await event.answer("🚫 Bạn đã gửi lệnh quá nhanh liên tục. Hệ thống tạm khóa bạn trong 15 phút để chống spam!")
                    return
                elif _user_spam_count[key] == 3:
                    await event.answer("⚠️ Cảnh báo: Vui lòng gửi lệnh chậm lại (mỗi 3 giây 1 lệnh). Nếu tiếp tục bạn sẽ bị khóa 15 phút!")
                    return
                return # Ignore too fast cmds without warning if count < 3
            else:
                _user_spam_count[key] = 0

            _user_last_cmd[key] = now_t
            
            # Sub check (original logic)
            cmd = event.text.split()[0].lower()
            if "@" in cmd:
                cmd = cmd.split("@")[0]
                
            main_group_id = db.get_setting("main_tg_group_id")
            is_main_group = main_group_id and str(event.chat.id) == str(main_group_id)
            
            if is_main_group:
                user = db.get_user(event.chat.id)
                if not user:
                    db.upsert_user(event.chat.id, "admin_group", "Admin Group")
                    db.set_sub_until(event.chat.id, now() + 3650*24*3600)
                return await handler(event, data)
                
            # Whitelist public / free commands
            free_cmds = (
                "/start", "/help", "/balance", "/sub", "/bank", "/nap", "/ref", "/web",
                "/refcode", "/ruttien", "/doitien", "/vip", "/trial", "/mycodes",
                "/hdcookie", "/ping2", "/alert", "/alertlist", "/alertoff",
                "/muagoi", "/checkfile", "/checkcookie", "/dailyreport", "/code",
                "/stats", "/history", "/top", "/adm", "/daily", "/chuyentien",
                "/shop", "/damua", "/quay", "/hang", "/doiqua", "/giasi",
                "/coc", "/huycoc", "/coclist", "/napshop", "/giohang"
            )
            if cmd not in free_cmds:
                user = db.get_user(event.chat.id)
                if not user:
                    await event.answer("Bạn chưa /start. Gõ /start trước nhé.")
                    return
                    
                # Check daily limit for all tracking and checking cmds
                if cmd in ("/check", "/tiktok", "/ig", "/track", "/trackv", "/trackig", "/trackvig", "/trackfb"):
                    can_check, err_msg = db.check_daily_limit(event.chat.id)
                    if not can_check:
                        await event.answer(f"❌ {err_msg}")
                        return
                        
                has_sub = user["sub_until"] and user["sub_until"] > now()
                has_balance = user["balance"] and user["balance"] > 0
                if not has_sub and not has_balance:
                    await event.answer("⚠️ Lỗi: Bạn cần được Admin cấp tiền hoặc cấp gói ngày sử dụng để dùng các chức năng này.\n👉 Gõ /balance để kiểm tra số dư, gõ /sub để mua gói.")
                    return
        return await handler(event, data)

class BotManager:
    def __init__(self):
        self.bot: Optional[Bot] = None
        self.dp:  Optional[Dispatcher] = None
        self._task: Optional[asyncio.Task] = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _make_session(self):
        proxy = (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("http_proxy")
        )
        return AiohttpSession(proxy=proxy) if proxy else None

    async def start(self, token: str) -> bool:
        await self.stop()
        if not token:
            return False
        self.bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML), session=self._make_session())
        try:
            me = await self.bot.get_me()
        except Exception as e:
            log.error("Loi get_me: %s", e)
            self.bot = None
            return False
        try:
            await self.bot.delete_webhook(drop_pending_updates=False)  # GIỮ tin nhắn đang chờ: restart giữa chừng không được nuốt tin user
        except Exception:
            pass
        init_cache_db(_config.DB_PATH)
        self.dp = Dispatcher(storage=SQLiteStorage(_config.DB_PATH))
        self.dp.include_router(router)
        await self.bot.set_my_commands(COMMANDS)
        try:
            _admin_id = int(db.get_setting("admin_tg_id") or 0)
            if _admin_id:
                from aiogram.types import BotCommandScopeChat
                await self.bot.set_my_commands(
                    COMMANDS + ADMIN_COMMANDS_EXTRA,
                    scope=BotCommandScopeChat(chat_id=_admin_id))
        except Exception as e:
            log.warning("admin commands: %s", e)
        poller.set_bot(self.bot)
        self._task = asyncio.create_task(
            self.dp.start_polling(self.bot, handle_signals=False)
        )
        db.add_log("system", f"Bot khởi động: @{me.username}")
        log.info("Bot @%s đang chạy.", me.username)
        return True

    async def stop(self):
        if self.dp:
            try:
                await self.dp.stop_polling()
            except Exception:
                pass
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self.bot:
            try:
                await self.bot.session.close()
            except Exception:
                pass
            self.bot = None
        self.dp = None

    async def verify_token(self, token: str) -> Optional[str]:
        b = Bot(token=token, session=self._make_session())
        try:
            me = await b.get_me()
            return me.username
        except Exception:
            return None
        finally:
            await b.session.close()

class ZaloBotManager:
    def __init__(self):
        self.token: str = ""
        self.base_url: str = ""
        self._task: Optional[asyncio.Task] = None
        self._client: Optional[httpx.AsyncClient] = None
        self.offset = 0

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, token: str) -> bool:
        await self.stop()
        if not token:
            return False
        self.token = token
        self.base_url = f"https://bot-api.zaloplatforms.com/bot{token}"
        self._client = httpx.AsyncClient(timeout=35.0)

        # Test token validity via getMe (if exists, or just start polling)
        log.info("Zalo Bot starting polling...")
        poller.set_zalo_bot(self)
        self._task = asyncio.create_task(self.polling_loop())
        return True

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._client:
            await self._client.aclose()
            self._client = None

    def _strip_html(self, html_text: str) -> str:
        """Convert HTML to plain text for Zalo (does not support HTML)."""
        # Replace links: <a href="URL">TEXT</a> -> TEXT (URL)
        def link_replacer(match):
            url = match.group(1)
            text = match.group(2)
            if url in text:
                return text
            return f"{text} ({url})"
        text = re.sub(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', link_replacer, html_text)
        # Remove remaining tags: <b>, <i>, <code>, etc.
        text = re.sub(r'<[^>]+>', '', text)
        # Decode HTML entities
        text = text.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&').replace('&quot;', '"')
        return text

    async def send_message(self, chat_id: str, text: str, reply_markup: dict = None):
        if not self._client: return
        text = self._strip_html(text)
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            resp = await self._client.post(url, json=payload)
            data = resp.json()
            if not data.get("ok"):
                log.warning("Zalo sendMessage failed: %s", data)
        except Exception as e:
            log.warning("Zalo sendMessage error: %s", e)

    async def send_photo(self, chat_id: str, photo_url: str, caption: str = ""):
        if not self._client: return
        caption = self._strip_html(caption)
        text = f"{caption}\n\n📷 Ảnh: {photo_url}" if photo_url else caption
        await self.send_message(chat_id, text)

    async def polling_loop(self):
        while True:
            try:
                url = f"{self.base_url}/getUpdates"
                payload = {"offset": self.offset, "timeout": 30}
                resp = await self._client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("ok"):
                        result = data.get("result", [])
                        # Zalo co the tra ve dict (1 message) hoac list (nhieu message)
                        if isinstance(result, dict):
                            updates = [result]
                        elif isinstance(result, list):
                            updates = result
                        else:
                            updates = []

                        for u in updates:
                            if not isinstance(u, dict):
                                continue
                            # Cap nhat offset
                            upd_id = u.get("update_id")
                            if upd_id is not None:
                                self.offset = int(upd_id) + 1
                            # LOG CAU TRUC THUC TE DE DEBUG
                            log.warning("ZALO UPDATE STRUCT: keys=%s | data=%s", list(u.keys()), str(u)[:500])
                            # Xu ly tin nhan - thu nhieu key khac nhau
                            msg = u.get("message") or u.get("edited_message") or u.get("channel_post")
                            # Neu chinh u la tin nhan (Zalo co the tra truc tiep object)
                            if msg is None and u.get("text") is not None:
                                msg = u
                                
                            if "callback_query" in u:
                                await self.handle_callback(u["callback_query"])
                            elif isinstance(msg, dict):
                                await self.handle_message(msg)
                            elif isinstance(msg, str):
                                await self.handle_message({"text": msg, "chat": {}, "from": {}})

                    else:
                        err_code = data.get("error_code")
                        if err_code not in (408, 504):
                            log.warning("Zalo getUpdates error: %s", data)
                            await asyncio.sleep(2)
                else:
                    log.warning("Zalo HTTP %s: %s", resp.status_code, resp.text[:200])
                    await asyncio.sleep(2)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("Zalo polling error: %s", e, exc_info=True)
                await asyncio.sleep(5)



    async def handle_callback(self, cb: dict):
        data = cb.get("data", "")
        if not data: return
        
        chat_id = cb.get("message", {}).get("chat", {}).get("id")
        
        if data.startswith("zalo_confirm_"):
            parts = data.split("_")
            if len(parts) >= 4:
                tg_id = int(parts[2])
                amount = int(parts[3])
                
                code = db.get_unused_code(amount)
                
                try:
                    kb = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="🎁 Sử dụng luôn", callback_data=f"use_code_{code}")
                    ]])
                    await manager.bot.send_message(
                        tg_id, 
                        f"🎉 <b>Thanh toán thành công!</b>\n\nĐây là mã code nạp tiền trị giá <b>{vnd(amount)}</b> của bạn:\n"
                        f"👉 <code>{code}</code>\n\n"
                        "<i>Nhấn nút bên dưới để sử dụng mã ngay lập tức!</i>",
                        reply_markup=kb
                    )
                    
                    if chat_id:
                        await self.send_message(str(chat_id), f"✅ Đã xác nhận và phát mã {code} ({vnd(amount)}) cho ID {tg_id}")
                except Exception as e:
                    log.error(f"Lỗi gửi code cho khách: {e}")
                    if chat_id:
                        await self.send_message(str(chat_id), f"❌ Lỗi gửi code cho khách: {e}")

    async def handle_message(self, msg: dict):
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text = msg.get("text", "").strip()
        if not chat_id or not text: return

        username = msg.get("from", {}).get("display_name") or msg.get("from", {}).get("username") or msg.get("from", {}).get("first_name", chat_id)
        
        txt_lower = text.lower()
        if txt_lower.startswith("/start") or txt_lower.startswith("/id"):
            await self.cmd_help(chat_id, username)
        elif txt_lower.startswith("/topup"):
            await self.cmd_topup(chat_id, text)
        elif txt_lower.startswith("/phatcode"):
            await self.cmd_phatcode(chat_id, text)
        elif txt_lower.startswith("/web"):
            token = db.create_magic_link(int(chat_id))
            web_domain = db.get_setting("web_domain", "http://127.0.0.1:8000")
            url = f"{web_domain.rstrip('/')}/auth?token={token}"
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🌐 Đăng nhập Web", url=url)
            ]])
            await self.send_message(chat_id, "🔗 Bấm vào nút bên dưới để tự động đăng nhập vào Web:", reply_markup=kb)
        else:
            await self.send_message(chat_id, f"💡 Zalo Chat ID của bạn: {chat_id}\n\nLệnh có sẵn:\n- Copy Chat ID dán vào web để nhận thông báo\n- Phát Code: /phatcode <ID> <SỐ TIỀN>\n- Cộng thẳng: /topup <ID> <SỐ TIỀN>\n- Bảng điều khiển Web: /web")

    async def cmd_phatcode(self, chat_id, text):
        admin_id = db.get_setting("admin_zalo_id", "")
        if not admin_id or chat_id != admin_id:
            await self.send_message(chat_id, "⛔ Chỉ Admin mới được dùng lệnh này!")
            return
            
        parts = text.split()
        if len(parts) < 3:
            await self.send_message(chat_id, "⚠️ Cú pháp sai! Vui lòng gửi lệnh có sẵn trong thông báo.\nVD: /phatcode 12345 50000")
            return
            
        try:
            tg_id = int(parts[1])
            amount = int(parts[2])
            
            code = db.get_unused_code(amount)
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🎁 Sử dụng luôn", callback_data=f"use_code_{code}")
            ]])
            from ..bot import manager
            await manager.bot.send_message(
                tg_id, 
                f"🎉 <b>Thanh toán thành công!</b>\n\nĐây là mã code nạp tiền trị giá <b>{vnd(amount)}</b> của bạn:\n"
                f"👉 <code>{code}</code>\n\n"
                "<i>Nhấn nút bên dưới để sử dụng mã ngay lập tức!</i>",
                reply_markup=kb
            )
            await self.send_message(chat_id, f"✅ Đã phát mã {code} ({vnd(amount)}) cho ID {tg_id}")
        except Exception as e:
            await self.send_message(chat_id, f"❌ Lỗi: {e}")

    async def cmd_help(self, chat_id, username):
        help_text = (
            f"👋 Xin chào Admin <b>{username}</b>!\n\n"
            f"🆔 Zalo Chat ID của bạn là: {chat_id}\n\n"
            "1. Copy Chat ID này và dán vào ô 'Admin Zalo Chat ID' trên Web Dashboard để nhận thông báo khách nạp tiền.\n"
            "2. Để nạp tiền cho khách trực tiếp từ đây, dùng lệnh:\n"
            "👉 /topup <ID_TELE> <SỐ TIỀN>\n"
            "(Ví dụ: /topup 123456789 50000)"
        )
        await self.send_message(chat_id, help_text)

    async def cmd_topup(self, chat_id, text):
        admin_id = db.get_setting("admin_zalo_id", "")
        if not admin_id or chat_id != admin_id:
            await self.send_message(chat_id, "⛔ Chỉ Admin (Zalo ID đã cài đặt) mới được dùng lệnh này!")
            return
            
        parts = text.split()
        if len(parts) < 3:
            await self.send_message(chat_id, "⚠️ Cú pháp sai!\nVí dụ: /topup 123456789 50000")
            return
            
        try:
            tg_id = int(parts[1])
            amount = int(parts[2].replace(",", "").replace(".", "").replace("k", "000").replace("K", "000"))
        except:
            await self.send_message(chat_id, "❌ ID Telegram hoặc Số tiền không hợp lệ!")
            return
            
        db.adjust_balance(tg_id, amount, "Nạp tiền qua Zalo Bot")
        await self.send_message(chat_id, f"✅ Đã cộng thành công {vnd(amount)} cho ID Telegram: {tg_id}")
        
        try:
            from ..bot import manager
            msg_text_resp = f"🎉 <b>NẠP TIỀN THÀNH CÔNG!</b>\n\nAdmin vừa cộng cho bạn: <b>{vnd(amount)}</b>\n👉 Gõ /balance để kiểm tra số dư nhé."
            upgraded, new_vip, is_lifetime = db.check_vip_upgrade(tg_id)
            if upgraded and new_vip > 0:
                limit = db.get_setting(f"vip{new_vip}_limit", "10")
                msg_text_resp += (
                    f"\n\n🎉 <b>CHÚC MỪNG BẠN ĐÃ LÊN VIP {new_vip}!</b> 🎉\n\n"
                    f"🎁 <b>Đặc quyền mới:</b>\n"
                    f"- Mức độ theo dõi tối đa: <b>{limit} mục</b>/nền tảng\n"
                )
                if is_lifetime:
                    msg_text_resp += "- Hạn sử dụng: <b>VĨNH VIỄN</b>\n\n"
                else:
                    msg_text_resp += "\n"
            await manager.bot.send_message(tg_id, msg_text_resp, parse_mode="HTML")
        except:
            pass

DAY = 86400

log = logging.getLogger(__name__)

router = Router()

_user_last_cmd = {}

_user_spam_count = {}

_user_muted_until = {}

_spam_cleanup_ts = 0

MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="/muagoi"), KeyboardButton(text="/checkfile"), KeyboardButton(text="/checkcookie")],
        [KeyboardButton(text="/theodoi"), KeyboardButton(text="/tiktok"), KeyboardButton(text="/ig")],
        [KeyboardButton(text="/check"), KeyboardButton(text="/list"), KeyboardButton(text="/balance"), KeyboardButton(text="/sub")],
        [KeyboardButton(text="/vip"), KeyboardButton(text="/ref"), KeyboardButton(text="/bank")],
        [KeyboardButton(text="/tienich"), KeyboardButton(text="/shop"), KeyboardButton(text="/help")],
        [KeyboardButton(text="/web"), KeyboardButton(text="/huongdan")],
    ],
    resize_keyboard=True,
)

ADMIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="/muagoi"), KeyboardButton(text="/checkfile"), KeyboardButton(text="/checkcookie")],
        [KeyboardButton(text="/theodoi"), KeyboardButton(text="/tiktok"), KeyboardButton(text="/ig")],
        [KeyboardButton(text="/check"), KeyboardButton(text="/list"), KeyboardButton(text="/balance"), KeyboardButton(text="/sub")],
        [KeyboardButton(text="/vip"), KeyboardButton(text="/ref"), KeyboardButton(text="/bank")],
        [KeyboardButton(text="/tienich"), KeyboardButton(text="/shop"), KeyboardButton(text="/help")],
        [KeyboardButton(text="/web"), KeyboardButton(text="/adm"), KeyboardButton(text="/huongdan")],
        [KeyboardButton(text="/shopadm")],
        [KeyboardButton(text="\U0001f4ca Nhập kho Sheet")],
    ],
    resize_keyboard=True,
)

ADMIN_COMMANDS_EXTRA = [
    BotCommand(command="setsheet", description="Cài đặt Google Sheet nhập kho"),
    BotCommand(command="nhapkhosheet", description="Nhập kho từ Google Sheet: /nhapkhosheet <id_loại>"),
    BotCommand(command="adm", description="Bảng lệnh admin"),
    BotCommand(command="shopadm", description="🛒 Menu shop acc & NCC (nút bấm)"),
    BotCommand(command="kho", description="Xem tồn kho"),
    BotCommand(command="themacc", description="Nhập kho từ file: /themacc <id_loại>"),
    BotCommand(command="capnhatacc", description="Cập nhật T.tin acc từ file: /capnhatacc <id_loại>"),
    BotCommand(command="capnhatsheet", description="Cập nhật T.tin acc từ Sheet: /capnhatsheet <id_loại>"),
    BotCommand(command="suaacc", description="Sửa thông tin 1 acc: /suaacc [id_loại] [uid]"),
]

COMMANDS = [
    BotCommand(command="start",       description="Bắt đầu sử dụng bot"),
    BotCommand(command="muagoi",      description="Mua gói theo ngày (1, 3, 5, 7 ngày)"),
    BotCommand(command="sub",         description="Xem gói tháng & mua gói"),
    BotCommand(command="bank",        description="Nạp tiền / Lấy thông tin chuyển khoản"),
    BotCommand(command="nap",         description="Nạp tiền tự động qua PayOS (chọn ví)"),
    BotCommand(command="napshop",     description="Nạp tiền vào ví shop mua acc"),
    BotCommand(command="balance",     description="Xem số dư hiện tại"),
    BotCommand(command="sodu",        description="Xem tất cả số dư: ví, credits, điểm"),
    BotCommand(command="huongdan",    description="Hướng dẫn nhanh theo từng mục"),
    BotCommand(command="vip",         description="Xem cấp độ VIP và đặc quyền"),
    BotCommand(command="checkfile",   description="Check UID Facebook qua file .txt/.xlsx"),
    BotCommand(command="checkcookie", description="Check dàn Cookie format UID|PASS|COOKIE|2FA"),
    BotCommand(command="muacredit",   description="Mua gói Credits (lượt check)"),
    BotCommand(command="lichsu",      description="Lịch sử check 7 ngày"),
    BotCommand(command="app",         description="Mở Mini App check UID"),
    BotCommand(command="accuracy",    description="Thống kê độ chính xác check"),
    BotCommand(command="theodoi",     description="👁️ Trung tâm theo dõi (menu nút gọn)"),
    BotCommand(command="tienich",      description="🧰 Tiện ích: giftcode, mã giảm giá, ngày sinh, rút/chuyển tiền, lịch sử, đặt cọc (menu nút)"),
    BotCommand(command="dailyreport", description="Cài đặt báo cáo tự động hằng ngày"),
    BotCommand(command="stats",       description="Thống kê cá nhân của bạn"),
    BotCommand(command="daily",       description="Điểm danh nhận credits mỗi ngày"),
    BotCommand(command="tiktok",      description="Check info TikTok: /tiktok <username>"),
    BotCommand(command="ig",          description="Check info Instagram: /ig <username>"),
    BotCommand(command="fb",          description="Check Facebook Live/Die: /fb <uid>"),
    BotCommand(command="shop",        description="Shop tài khoản Facebook"),
    BotCommand(command="damua",       description="Acc FB đã mua + yêu cầu bảo hành"),
    BotCommand(command="quay",        description="Vòng quay may mắn (vé từ mua acc)"),
    BotCommand(command="hang",        description="Hạng thành viên shop acc"),
    BotCommand(command="giasi",       description="Bảng giá sỉ mua nhiều"),
    BotCommand(command="doiqua",      description="Đổi điểm loyalty lấy acc miễn phí"),
    BotCommand(command="coclist",     description="Xem các khoản đặt cọc của bạn"),
    BotCommand(command="ref",         description="Lấy link giới thiệu kiếm tiền"),
    BotCommand(command="help",        description="Hướng dẫn sử dụng"),
    BotCommand(command="web",         description="Đăng nhập Bảng điều khiển Web"),
]

_WELCOME_NEW = (
    "🎉 <b>Chào mừng bạn đến với shop!</b>\n"
    "━━━━━━━━━━━━━━━\n"
    "🛒 <b>Mua acc FB:</b> gõ /shop — chọn loại acc, thanh toán là nhận acc ngay\n"
    "💳 <b>Nạp tiền:</b> gõ /nap &lt;số tiền&gt; — quét mã QR là tiền vào ví\n"
    "🎁 <b>Tân thủ</b> được tặng ngày dùng thử miễn phí (nếu đang bật)\n"
    "❓ Cần hỗ trợ? Gõ /huongdan để xem hướng dẫn theo từng mục\n"
    "━━━━━━━━━━━━━━━\n\n"
)

_SHOP_WALLET_HINT = (
    "\n<i>💡 Ví shop là ví riêng để mua acc (khác ví chính dùng để check UID/mua gói)."
    " Nạp bằng /napshop nhé.</i>"
)

_GUIDE_STEPS = {
    "1": (
        "🛒 <b>MUA ACC ĐẦU TIÊN — Bước 1/3: Nạp tiền</b>\n"
        "━━━━━━━━━━━━━━━\n"
        "Acc mua bằng <b>ví shop</b> (riêng với ví chính).\n\n"
        "👉 Gõ <b>/napshop &lt;số tiền&gt;</b> (VD: /napshop 50000)\n"
        "→ quét mã QR → tiền vào ví shop ngay.\n\n"
        "Nạp xong bấm Tiếp nhé 👇"
    ),
    "2": (
        "🛒 <b>MUA ACC ĐẦU TIÊN — Bước 2/3: Chọn acc</b>\n"
        "━━━━━━━━━━━━━━━\n"
        "👉 Gõ <b>/shop</b> → chọn loại acc → nhập số lượng\n"
        "→ bấm xác nhận.\n\n"
        "Bot tự <b>kiểm tra LIVE</b> từng acc trước khi giao —\n"
        "acc chết được đổi/cách ly, bạn chỉ nhận acc sống.\n\n"
        "Xong bước này bấm Tiếp nhé 👇"
    ),
    "3": (
        "🛒 <b>MUA ACC ĐẦU TIÊN — Bước 3/3: Nhận acc</b>\n"
        "━━━━━━━━━━━━━━━\n"
        "Sau khi mua, bạn nhận acc theo 1 trong 3 cách:\n"
        "• 📋 <b>Hiện thông tin</b> — xem ngay trong chat\n"
        "• 📄 <b>Tải file .txt</b> — lưu về máy\n"
        "• 📊 <b>Tải file .xlsx</b> — mở bằng Excel\n\n"
        "Acc lỗi trong thời gian bảo hành? Gõ <b>/damua</b>\n"
        "→ chọn đơn → yêu cầu bảo hành.\n\n"
        "🎉 Xong! Chúc bạn mua sắm vui vẻ."
    ),
}

_HUONGDAN_TEXTS = {
    "shop": (
        "🛒 <b>MUA ACC FACEBOOK</b>\n"
        "━━━━━━━━━━━━━━━\n"
        "1️⃣ Gõ <b>/shop</b> → chọn loại acc\n"
        "2️⃣ Nhập số lượng (hoặc bấm nút nhập số tùy ý)\n"
        "3️⃣ Xác nhận → bot <b>tự check LIVE</b> trước khi giao\n"
        "4️⃣ Nhận acc: 📋 hiện thông tin / 📄 file .txt / 📊 file .xlsx\n\n"
        "💡 Mua bằng <b>ví shop</b> — nạp bằng /napshop.\n"
        "🛒 Mua nhiều loại 1 lần: dùng /giohang."
    ),
    "nap": (
        "💳 <b>NẠP TIỀN</b>\n"
        "━━━━━━━━━━━━━━━\n"
        "• <b>/nap &lt;số tiền&gt;</b> — nạp tự động (quét QR), chọn ví chính/shop\n"
        "• <b>/napshop &lt;số tiền&gt;</b> — nạp thẳng vào ví shop để mua acc\n"
        "• <b>/bank &lt;số tiền&gt;</b> — lấy thông tin chuyển khoản tay\n"
        "• <b>/sodu</b> — xem tất cả số dư: ví chính, ví shop, credits, điểm\n\n"
        "💡 <b>Ví chính</b>: check UID, mua gói/VIP.\n"
        "💡 <b>Ví shop</b>: mua acc, đặt cọc, hộp mù."
    ),
    "bh": (
        "🛡️ <b>BẢO HÀNH ACC</b>\n"
        "━━━━━━━━━━━━━━━\n"
        "1️⃣ Gõ <b>/damua</b> → chọn đơn hàng\n"
        "2️⃣ Bấm yêu cầu bảo hành, ghi rõ lỗi\n"
        "3️⃣ Admin duyệt → được đổi acc hoặc hoàn tiền\n\n"
        "⏱ Mỗi loại acc có thời gian bảo hành riêng\n"
        "(xem trong chi tiết loại acc ở /shop)."
    ),
    "check": (
        "🔍 <b>KIỂM TRA ACC (LIVE/DIE)</b>\n"
        "━━━━━━━━━━━━━━━\n"
        "• <b>/fb &lt;uid&gt;</b> — check 1 acc Facebook\n"
        "• <b>/checkfile</b> — check hàng loạt từ file .txt/.xlsx\n"
        "• <b>/tiktok &lt;username&gt;</b> / <b>/ig &lt;username&gt;</b> — check TikTok/IG\n"
        "• <b>/theodoi</b> — theo dõi UID tự động, die báo ngay\n\n"
        "💡 Check lẻ trừ ví chính, check hàng loạt dùng credits."
    ),
}

_HUONGDAN_MENU_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🛒 Mua acc", callback_data="hd:shop"),
     InlineKeyboardButton(text="💳 Nạp tiền", callback_data="hd:nap")],
    [InlineKeyboardButton(text="🛡️ Bảo hành", callback_data="hd:bh"),
     InlineKeyboardButton(text="🔍 Kiểm tra acc", callback_data="hd:check")],
])

manager = BotManager()

zalo_manager = ZaloBotManager()

@router.message.middleware()
async def _perm_middleware(handler, event, data):
    """Chặn admin phụ gõ lệnh tay thuộc nhóm quyền chưa được cấp.

    Chỉ áp dụng cho admin phụ (không phải super admin). Super admin và
    khách thường đi qua bình thường. Menu nút (/adm, /shopadm) tự lọc riêng.
    """
    try:
        from aiogram.types import Message as _Msg
        if isinstance(event, _Msg) and event.from_user:
            uid = event.from_user.id
            if _perms.is_admin(uid) and not _perms.is_super(uid):
                need = _perms.cmd_perm_for_text(event.text or "")
                if need and not _perms.has_perm(uid, need):
                    await event.answer(
                        f"🚫 Bạn không có quyền <b>{_perms.perm_label(need)}</b>.\n"
                        f"Liên hệ chủ shop để được cấp thêm quyền.",
                        parse_mode="HTML")
                    return
            if _perms.is_admin(uid):
                # Nhật ký: admin (kể cả chủ shop) gõ lệnh tay thuộc diện quản trị
                _need2 = _perms.cmd_perm_for_text(event.text or "")
                _head = ((event.text or "").strip().split() or [""])[0].lower()
                if _need2 or _head in ("/adm", "/shopadm"):
                    try:
                        db.admin_audit_add(
                            uid, event.from_user.full_name, "lenh_tay",
                            (event.text or "")[:200])
                    except Exception:
                        pass
    except Exception:
        pass
    return await handler(event, data)

@router.message(Command("web"))
async def cmd_web(msg: Message):
    tg_id = msg.chat.id
    web_domain = (db.get_setting("web_domain", "") or "").strip().rstrip("/")
    if not web_domain:
        await msg.answer(
            "🌐 <b>WEB DASHBOARD</b>\n\n"
            "Web chưa được cấu hình public URL.\n"
            "<i>Admin: set setting <code>web_domain</code> thành địa chỉ public của backend "
            "(VD: https://bot.ban.com) rồi thử lại.</i>",
            parse_mode="HTML",
        )
        return
    token = db.create_magic_link(tg_id)
    url = f"{web_domain}/auth?token={token}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🌐 Đăng nhập Web", url=url)
    ]])
    await msg.answer("🔗 Bấm vào nút bên dưới để tự động đăng nhập vào Web:", reply_markup=kb)

@router.message(CommandStart())
async def on_start(msg: Message):
    u = msg.from_user
    parts = (msg.text or "").split(maxsplit=1)
    ref_id = 0
    if len(parts) > 1:
        code_or_id = parts[1].strip()
        try: 
            ref_id = int(code_or_id)
        except: 
            c = db.get_conn()
            row = c.execute("SELECT tg_id FROM tg_users WHERE ref_code=?", (code_or_id,)).fetchone()
            if row:
                ref_id = row["tg_id"]
        
    if ref_id and int(ref_id) == int(u.id):
        ref_id = 0  # chống tự giới thiệu chính mình để ăn hoa hồng
    user = db.get_user(u.id)
    is_new = False
    if not user:
        is_new = True
        user = db.upsert_user(u.id, u.username or "", u.full_name or "", ref_id)
        
        # New User Notification for Admin
        admin_msg = (
            "🎉 <b>CÓ NGƯỜI DÙNG MỚI!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Tên:</b> {html.escape(u.full_name or '')}\n"
            f"🔗 <b>Username:</b> @{html.escape(u.username) if u.username else 'Không có'}\n"
            f"🆔 <b>ID:</b> <code>{u.id}</code>\n"
            f"🕒 <b>Thời gian:</b> {vn_time_str('%H:%M:%S %d/%m/%Y')}\n"
        )
        if ref_id > 0 and ref_id != u.id:
            admin_msg += f"🤝 <b>Mời bởi:</b> <code>{ref_id}</code>\n"
            try:
                await msg.bot.send_message(ref_id, f"🎉 <b>Tin vui!</b>\nNgười dùng <b>{html.escape(u.full_name or "")}</b> vừa tham gia Bot qua link giới thiệu của bạn!\nKhi họ nạp tiền bạn sẽ nhận được 10% hoa hồng.", parse_mode="HTML")
            except: pass
            
        # Send to admin tg
        admins = []
        try:
            if db.get_setting("admin_tg_id"): admins.append(int(db.get_setting("admin_tg_id")))
        except: pass
        try:
            if db.get_setting("admin_tg_group_id"): admins.append(int(db.get_setting("admin_tg_group_id")))
        except: pass
        
        admin_tg_token = db.get_setting("admin_bot_token", "")
        if admin_tg_token:
            from ..admin_bot import manager as admin_manager
            admin_sender_bot = admin_manager.bot
        else:
            admin_sender_bot = msg.bot
            
        if admin_sender_bot:
            for admin_id in admins:
                try:
                    await admin_sender_bot.send_message(admin_id, admin_msg, parse_mode="HTML")
                except Exception as e:
                    log.error("Failed to notify TG admin %s of new user: %s", admin_id, e)
                
        # Send to admin zalo
        admin_zalo = db.get_setting("admin_zalo_id", "")
        if admin_zalo:
            try:
                if zalo_manager.running:
                    asyncio.create_task(zalo_manager.send_message(admin_zalo, admin_msg))
            except: pass
    else:
        user = db.upsert_user(u.id, u.username or "", u.full_name or "", 0)
    db.add_log("system", f"/start {u.id} @{u.username}", u.id)
    
    # Auto Trial Logic
    trial_msg = ""
    if db.get_setting("enable_free_trial", "1") == "1":
        try:
            days = int(db.get_setting("free_trial_days", "3"))
        except ValueError:
            days = 3
        if db.activate_trial(u.id, days):
            db.add_log("trial", f"Auto trial {days} ngày", u.id)
            user = db.get_user(u.id) # refresh user data
            trial_msg = f"🎁 <b>Quà tặng tân thủ:</b> Bạn đã được hệ thống tự động tặng <b>{days} ngày</b> dùng thử miễn phí!\n\n"
    
    await msg.answer(
        f"👋 Xin chào <b>{msg.from_user.full_name}</b>!\n\n"
        "📱 Bot <b>TikTok/IG/FB Checker V2</b> sẵn sàng!\n\n"
        f"💰 Số dư: <b>{vnd(user['balance'])}</b>\n"
        f"Gói FB: <b>{_sub_text(user)}</b>\n\n"
        f"{trial_msg}"
        + (_WELCOME_NEW if is_new else "") +
        "Gõ /huongdan để xem hướng dẫn theo từng mục.\n"
        "Gõ /ref để lấy link giới thiệu nhận 10% hoa hồng.",
        reply_markup=(ADMIN_MENU if _is_admin(u.id) else MENU),
    )
    # Khách mới: gửi thêm tin hướng dẫn 3 bước mua acc đầu tiên (nút bấm)
    if is_new:
        try:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🛒 Mua acc đầu tiên (3 bước)",
                                      callback_data="guide_firstbuy:1")],
                [InlineKeyboardButton(text="📖 Hướng dẫn nhanh",
                                      callback_data="guide_hd")],
            ])
            await msg.answer(
                "🆕 <b>Bạn là khách mới?</b>\n"
                "Bấm nút bên dưới để được dẫn từng bước mua acc đầu tiên nhé 👇",
                parse_mode="HTML", reply_markup=kb,
            )
        except Exception:
            pass

@router.callback_query(F.data.startswith("guide_firstbuy:"))
async def on_guide_firstbuy(cb: CallbackQuery):
    await cb.answer()
    step = cb.data.split(":", 1)[1]
    text = _GUIDE_STEPS.get(step, _GUIDE_STEPS["1"])
    buttons = []
    if step == "1":
        buttons.append([InlineKeyboardButton(text="Tiếp: chọn acc →",
                                             callback_data="guide_firstbuy:2")])
    elif step == "2":
        buttons.append([InlineKeyboardButton(text="Tiếp: nhận acc →",
                                             callback_data="guide_firstbuy:3")])
    else:
        buttons.append([InlineKeyboardButton(text="← Xem lại từ đầu",
                                             callback_data="guide_firstbuy:1")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass

@router.callback_query(F.data == "guide_hd")
async def on_guide_hd(cb: CallbackQuery):
    await cb.answer()
    await _send_huongdan(cb.message, edit=True)

@router.message(Command("ping2"))
async def on_ping2(msg: Message):
    await msg.answer("pong version 2 - Đã cập nhật code mới thành công!")

def _sub_active(user) -> bool:
    return user and user["sub_until"] and user["sub_until"] > now()

def _sub_text(user) -> str:
    if _sub_active(user):
        if user["sub_until"] >= 9999999999:
            return "VĨNH VIỄN"
        days_left = (user["sub_until"] - now()) // DAY
        return f"Còn hạn ({days_left} ngày)"
    return "Chưa có / Đã hết hạn"

async def _send_huongdan(target, edit: bool = False):
    text = "📖 <b>HƯỚNG DẪN NHANH</b>\nChọn mục bạn cần 👇"
    if edit:
        try:
            await target.edit_text(text, parse_mode="HTML",
                                   reply_markup=_HUONGDAN_MENU_KB)
        except Exception:
            pass
    else:
        await target.answer(text, parse_mode="HTML",
                            reply_markup=_HUONGDAN_MENU_KB)

@router.message(Command("huongdan"))
async def on_huongdan(msg: Message):
    await _send_huongdan(msg)

@router.callback_query(F.data.startswith("hd:"))
async def on_hd(cb: CallbackQuery):
    await cb.answer()
    topic = cb.data.split(":", 1)[1]
    text = _HUONGDAN_TEXTS.get(topic)
    if not text:
        await _send_huongdan(cb.message, edit=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Quay lại", callback_data="hd:back")],
    ])
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass

@router.message(Command("help"))
async def on_help(msg: Message):
    """Hướng dẫn đầy đủ Checker V2 — tất cả tính năng user."""
    help_text = (
        "📖 <b>HƯỚNG DẪN CHECKER V2 PRO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"

        "<b>💰 TÀI KHOẢN &amp; SỐ DƯ</b>\n"
        "• /balance — Xem số dư hiện tại\n"
        "• /bank — Xem thông tin nạp tiền &amp; QR\n"
        "• /bank &lt;số_tiền&gt; — Nạp nhanh (VD: /bank 50000)\n"
        "• /nap &lt;số_tiền&gt; — Nạp tự động qua PayOS, chọn ví chính/shop (VD: /nap 50000)\n"
        "• /napshop &lt;số_tiền&gt; — Nạp thẳng vào ví shop mua acc\n"
        "• /ref — Lấy link giới thiệu kiếm hoa hồng\n"
        "• /doitien &lt;số_tiền&gt; — Đổi hoa hồng → số dư (+10% Bonus)\n"
        "• /ruttien &lt;tiền&gt; &lt;ngân_hàng&gt; &lt;stk&gt; — Rút hoa hồng về bank\n"
        "• /chuyentien &lt;user_id&gt; &lt;số_tiền&gt; — Chuyển số dư cho người khác\n\n"

        "<b>🛒 SHOP ACC FACEBOOK</b>\n"
        "• /shop — Xem và mua tài khoản Facebook\n"
        "• /giohang — Giỏ hàng: gom nhiều loại acc, thanh toán 1 lần\n"
        "• /damua — Acc đã mua + yêu cầu bảo hành\n"
        "• /quay — Vòng quay may mắn (mua acc được vé)\n"
        "• /hang — Hạng thành viên & ưu đãi giảm giá\n"
        "• /doiqua — Đổi điểm loyalty lấy acc miễn phí\n"
        "• /giasi — Bảng giá sỉ khi mua nhiều\n"
        "• /coc &lt;id_loại&gt; — Đặt cọc giữ hàng hot khi hết hàng\n"
        "• /coclist — Xem các khoản đặt cọc của bạn\n"
        "• /huycoc &lt;id_cọc&gt; — Hủy đặt cọc (hoàn tiền)\n"
        "• <i>Mua acc/đặt cọc/hộp mù trừ <b>ví shop</b> riêng, không dùng ví chính</i>\n\n"

        "<b>🎫 GÓI &amp; VIP</b>\n"
        "• /muagoi — Bảng giá &amp; mua gói ngày (1, 3, 5, 7 ngày)\n"
        "• /muagoi &lt;số_ngày&gt; — Mua nhanh gói ngày (VD: /muagoi 7)\n"
        "• /sub — Xem gói tháng &amp; mua gói\n"
        "• /vip — Xem cấp độ VIP và đặc quyền\n"
        "• /mycodes — Xem kho mã quà tặng\n"
        "• /code &lt;mã&gt; — Nhập mã Giftcode nhận tiền\n"
        "• /muacredit — Mua gói Credits (lượt check hàng loạt)\n"
        "• /promo &lt;mã&gt; — Áp mã giảm giá Flash Sale\n"
        "• /lichsu — Xem lại lịch sử check 7 ngày\n"
        "• /app — Mở Mini App check UID\n\n"

        "<b>🎁 TÍNH NĂNG V2 PRO</b>\n"
        "• /daily — Điểm danh nhận thưởng mỗi ngày\n"
        "• /stats — Dashboard thống kê cá nhân\n"
        "• /history — Lịch sử check 7 ngày gần nhất\n"
        "• /history &lt;platform&gt; — Lọc lịch sử (fb/tiktok/ig/zalo)\n"
        "• /top — Bảng xếp hạng nạp tiền tháng\n"
        "• /top ref — Bảng xếp hạng giới thiệu\n"
        "• /dailyreport &lt;giờ|off&gt; — Hẹn giờ nhận báo cáo dàn nick hằng ngày\n"
        "• /scan_all — Báo cáo tổng hợp toàn bộ dàn tài khoản\n"
        "• /accuracy — Thống kê độ chính xác &amp; nhất quán khi check\n"
"• /sinhnhat &lt;ngày/tháng/năm&gt; — Nhập ngày sinh, nhận quà sinh nhật hằng năm\n\n"

        "<b>📁 CHECK FILE &amp; DANH SÁCH</b>\n"
        "• /checkfile — Check hàng loạt UID FB từ file .txt/.xlsx (link FB tự giải thành UID)\n"
        "• /checkcookie — Check dàn Cookie format UID|PASS|COOKIE|2FA từ file .txt\n"
        "• /newlist &lt;tên&gt; — Tạo danh sách tài khoản mới\n"
        "• /lists — Xem tất cả danh sách đã tạo\n"
        "• /addtolist &lt;tên&gt; &lt;uid&gt; — Thêm UID vào danh sách\n"
        "• /scanlist &lt;tên&gt; — Check siêu tốc Live/Die toàn bộ danh sách\n"
        "• /deletelist &lt;tên&gt; — Xóa danh sách\n\n"

        "<b>🔔 CẢNH BÁO TỰ ĐỘNG (ALERTS)</b>\n"
        "• /alert &lt;platform&gt; &lt;target&gt; — Bật cảnh báo tự động\n"
        "• /alertlist — Xem danh sách cảnh báo\n"
        "• /alertoff &lt;id&gt; — Tắt cảnh báo\n"
        "• /alertpause &lt;id&gt; — Tạm dừng (không xóa)\n"
        "• /alertsnooze &lt;id&gt; &lt;giờ&gt; — Tắt tạm N giờ rồi tự bật lại\n\n"

        "<b>📘 FACEBOOK</b>\n"
        "• /fb &lt;uid/link&gt; — Check Live/Die nhanh\\n"
        "• /getuid &lt;link&gt; — Lấy UID từ link FB\n"
        "• /trackfb &lt;uid&gt; — Theo dõi Live/Die\n"
        "• /untrackfb &lt;uid&gt; — Huỷ theo dõi\n"
        "• /trackfblist — Danh sách FB đang theo dõi\n"
        "• /mywatches — UID đang theo dõi + chế độ báo\n"
        "• /trackmode &lt;uid&gt; &lt;all|die&gt; — Chế độ báo (VD: /trackmode 1000123 die)\n\n"

        "<b>🎵 TIKTOK</b>\n"
        "• /tiktok &lt;user&gt; — Check nhanh\n"
        "• /track &lt;user&gt; — Theo dõi follower\n"
        "• /untrack &lt;user&gt; — Huỷ theo dõi\n"
        "• /tracklist — Danh sách TikTok đang theo dõi\n"
        "• /trackv &lt;link&gt; [phút] — Theo dõi video\n"
        "• /untrackv &lt;link&gt; — Huỷ video\n"
        "• /trackvlist — Danh sách video đang theo dõi\n\n"

        "<b>📷 INSTAGRAM</b>\n"
        "• /ig &lt;user&gt; — Check nhanh\n"
        "• /trackig &lt;user&gt; — Theo dõi follower\n"
        "• /untrackig &lt;user&gt; — Huỷ theo dõi\n"
        "• /trackiglist — Danh sách IG đang theo dõi\n"
        "• /trackvig &lt;link&gt; [phút] — Theo dõi bài viết\n"
        "• /untrackvig &lt;link&gt; — Huỷ bài viết\n"
        "• /trackviglist — Danh sách bài viết IG\n\n"

        "<b>💬 ZALO</b>\n"
        "• /zalo &lt;sđt&gt; — Check SĐT Zalo Live/Die\n"
        "• /trackzalo &lt;sđt&gt; — Theo dõi SĐT Zalo\n\n"

        "<b>💻 KHÁC</b>\n"
        "• /web — Đăng nhập Bảng điều khiển Web\n"
        "• /hdcookie — Hướng dẫn lấy Cookie các nền tảng\n"
        "• /trial — Nhận dùng thử\n"
        "• /refcode — Xem mã giới thiệu của bạn\n"
        "• /report — Báo cáo dàn nick hằng ngày\n\n"

        "💬 <b>Hỗ trợ:</b>\n"
        "• Telegram: @khaikhai998\n"
        "• Facebook: facebook.com/khaitradecoin"
    )
    await msg.answer(help_text, parse_mode="HTML")

def _is_admin(tg_id: int) -> bool:
    """Kiểm tra quyền admin: super admin (setting admin_tg_id / admin_tg_group_id)
    hoặc admin phụ được cấp quyền. Phân quyền chi tiết xem app/perms.py."""
    try:
        if db.get_setting("admin_tg_id") and int(db.get_setting("admin_tg_id")) == int(tg_id):
            return True
    except Exception:
        pass
    try:
        if db.get_setting("admin_tg_group_id") and int(db.get_setting("admin_tg_group_id")) == int(tg_id):
            return True
    except Exception:
        pass
    try:
        return _perms.is_admin(tg_id)
    except Exception:
        return False

@router.message(Command("setwebhook"))
async def on_setwebhook(msg: Message):
    """Admin gán webhook cho reseller key. Cú pháp: /setwebhook <tên_key|id> <url> (url trống = xóa)"""
    if not _is_admin(msg.from_user.id):
        await msg.answer("❌ Bạn không có quyền!")
        return
    parts = (msg.text or "").split(maxsplit=2)
    if len(parts) < 2:
        await msg.answer(
            "🔔 Cú pháp: <code>/setwebhook &lt;tên_key|id&gt; &lt;url&gt;</code>\n"
            "VD: <code>/setwebhook khachA https://site.com/hook</code>\n"
            "Xóa: <code>/setwebhook khachA off</code>",
            parse_mode="HTML",
        )
        return
    key = db.get_reseller_by_name_or_id(parts[1])
    if not key:
        await msg.answer(f"❌ Không tìm thấy key <b>{parts[1]}</b>.", parse_mode="HTML")
        return
    url = "" if len(parts) < 3 or parts[2].strip().lower() in ("off", "xoa", "xóa", "-") else parts[2].strip()
    if url and not (url.startswith("http://") or url.startswith("https://")):
        await msg.answer("❌ URL phải bắt đầu bằng http:// hoặc https://")
        return
    db.set_reseller_webhook(key["id"], url)
    if url:
        await msg.answer(
            f"✅ Đã gán webhook cho key <b>{key['name']}</b>:\n<code>{url}</code>\n\n"
            f"<i>Mỗi lượt /api/v1/fb/check và /api/v1/fb/getuid sẽ POST kết quả về URL này.</i>",
            parse_mode="HTML",
        )
    else:
        await msg.answer(f"✅ Đã xóa webhook của key <b>{key['name']}</b>.", parse_mode="HTML")

@router.message(Command("app"))
async def on_app(msg: Message):
    """Mở Mini App check UID (cần cấu hình miniapp_url là HTTPS public)."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
    url = (db.get_setting("miniapp_url", "") or "").strip()
    if not url:
        await msg.answer(
            "📱 <b>MINI APP</b>\n\n"
            "Mini App chưa được cấu hình public URL.\n"
            "<i>Admin: set setting <code>miniapp_url</code> thành địa chỉ HTTPS public của backend + <code>/miniapp</code> (VD: https://bot.ban.com/miniapp) rồi thử lại.</i>",
            parse_mode="HTML",
        )
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Mở Mini App Check UID", web_app=WebAppInfo(url=url))]
    ])
    await msg.answer(
        "📱 <b>MINI APP — CHECK UID NHANH</b>\n\n"
        "Bấm nút bên dưới để mở giao diện check UID, xem số dư và lịch sử ngay trong Telegram 👇",
        parse_mode="HTML", reply_markup=kb,
    )

def _tienich_main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Nhập giftcode", callback_data="tienich:code"),
         InlineKeyboardButton(text="🎟️ Áp mã giảm giá", callback_data="tienich:promo")],
        [InlineKeyboardButton(text="🎂 Ngày sinh", callback_data="tienich:birthday"),
         InlineKeyboardButton(text="✏️ Đổi mã giới thiệu", callback_data="tienich:refcode")],
        [InlineKeyboardButton(text="🔗 Lấy UID từ link FB", callback_data="tienich:getuid"),
         InlineKeyboardButton(text="💸 Rút hoa hồng", callback_data="tienich:withdraw")],
        [InlineKeyboardButton(text="↔️ Chuyển tiền", callback_data="tienich:transfer"),
         InlineKeyboardButton(text="💱 Đổi hoa hồng → số dư", callback_data="tienich:doitien")],
        [InlineKeyboardButton(text="📜 Lịch sử check", callback_data="tienich:history")],
        [InlineKeyboardButton(text="💰 Đặt cọc giữ hàng", callback_data="tienich:coc"),
         InlineKeyboardButton(text="❌ Hủy đặt cọc", callback_data="tienich:huycoc")],
    ])

def _tienich_text_main() -> str:
    return (
        "🧰 <b>TIỆN ÍCH</b>\n"
        "━━━━━━━━━━━━\n\n"
        "Các thao tác hay dùng — bấm nút, khỏi gõ lệnh tay:\n\n"
        "🎁 <b>Giftcode / Mã giảm giá</b> — nhập mã nhận thưởng\n"
        "🎂 <b>Ngày sinh</b> — nhận quà sinh nhật hằng năm\n"
        "🔗 <b>Lấy UID</b> — từ link Facebook\n"
        "💸 <b>Rút / Chuyển tiền</b> — hoa hồng & số dư\n"
        "📜 <b>Lịch sử</b> — xem nhanh có nút lọc\n"
        "💰 <b>Đặt cọc</b> — giữ hàng hot, hủy cọc"
    )

def _tienich_back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Quay lại Tiện ích", callback_data="tienich:main")]
    ])

@router.message(Command("tienich"))
async def on_tienich(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(_tienich_text_main(), parse_mode="HTML", reply_markup=_tienich_main_kb())

_register_adm_menu(router)  # menu nút /adm — đăng ký sớm, trước on_other
_register_shop_menu(router)  # menu nút /shopadm — đăng ký sớm, trước on_other
router.message.middleware(AntiSpamMiddleware())

__all__ = [
    "DAY",
    "log",
    "router",
    "_user_last_cmd",
    "_user_spam_count",
    "_user_muted_until",
    "_spam_cleanup_ts",
    "MENU",
    "ADMIN_MENU",
    "ADMIN_COMMANDS_EXTRA",
    "COMMANDS",
    "_WELCOME_NEW",
    "_SHOP_WALLET_HINT",
    "_GUIDE_STEPS",
    "_HUONGDAN_TEXTS",
    "_HUONGDAN_MENU_KB",
    "manager",
    "zalo_manager",
    "TienIchState",
    "AntiSpamMiddleware",
    "BotManager",
    "ZaloBotManager",
    "_perm_middleware",
    "cmd_web",
    "on_start",
    "on_guide_firstbuy",
    "on_guide_hd",
    "on_ping2",
    "_sub_active",
    "_sub_text",
    "_send_huongdan",
    "on_huongdan",
    "on_hd",
    "on_help",
    "_is_admin",
    "on_setwebhook",
    "on_app",
    "_tienich_main_kb",
    "_tienich_text_main",
    "_tienich_back_kb",
    "on_tienich",
]