import asyncio
import logging
import time
from typing import Optional

import httpx
import re
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand, Message, URLInputFile, FSInputFile,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

class BankState(StatesGroup):
    waiting_for_amount = State()

class FBNoteState(StatesGroup):
    waiting_for_note = State()
    uid = None

from . import db
from .util import now, parse_check_args, vnd
DAY = 86400
from .tiktok import parse_username, fetch_tiktok_info, fmt_num, build_info_caption
from .ig import (
    parse_ig_username, parse_ig_post_id,
    fetch_ig_info, fetch_ig_post_info,
    build_ig_info_caption, build_ig_video_caption
)
from .fb import check_uid, build_fb_caption
from .poller import poller

log = logging.getLogger(__name__)
router = Router()
from aiogram import BaseMiddleware
from aiogram.types import Message

_user_last_cmd = {}
_user_spam_count = {}
_user_muted_until = {}

class AntiSpamMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message) and event.text:
            tg_id = event.chat.id
            now_t = time.time()
            
            # Check if muted
            if tg_id in _user_muted_until:
                if now_t < _user_muted_until[tg_id]:
                    return  # Ignore silently
                else:
                    del _user_muted_until[tg_id]
                    if tg_id in _user_spam_count:
                        del _user_spam_count[tg_id]
            
            # Anti flood (1 cmd per 3 seconds)
            last_cmd_time = _user_last_cmd.get(tg_id, 0)
            if now_t - last_cmd_time < 3:
                _user_spam_count[tg_id] = _user_spam_count.get(tg_id, 0) + 1
                if _user_spam_count[tg_id] >= 5:
                    _user_muted_until[tg_id] = now_t + 900  # Mute 15 mins
                    await event.answer("🚫 Bạn đã gửi lệnh quá nhanh liên tục. Hệ thống tạm khóa bạn trong 15 phút để chống spam!")
                    return
                elif _user_spam_count[tg_id] == 3:
                    await event.answer("⚠️ Cảnh báo: Vui lòng gửi lệnh chậm lại (mỗi 3 giây 1 lệnh). Nếu tiếp tục bạn sẽ bị khóa 15 phút!")
                    return
                return # Ignore too fast cmds without warning if count < 3
            else:
                _user_spam_count[tg_id] = 0
                
            _user_last_cmd[tg_id] = now_t
            
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
                
            if cmd not in ("/start", "/help", "/balance", "/sub", "/bank", "/ref", "/web"):
                user = db.get_user(event.chat.id)
                if not user:
                    await event.answer("Bạn chưa /start. Gõ /start trước nhé.")
                    return
                    
                # Check daily limit for all tracking and checking cmds
                if cmd in ("/check", "/tiktok", "/ig", "/track", "/trackv", "/trackig", "/trackvig", "/trackfb"):
                    can_check, err_msg = db.check_daily_limit(tg_id)
                    if not can_check:
                        await event.answer(f"❌ {err_msg}")
                        return
                        
                has_sub = user["sub_until"] and user["sub_until"] > now()
                has_balance = user["balance"] and user["balance"] > 0
                if not has_sub and not has_balance:
                    await event.answer("⚠️ Lỗi: Bạn cần được Admin cấp tiền hoặc cấp gói ngày sử dụng để dùng các chức năng này.\n👉 Gõ /balance để kiểm tra số dư, gõ /sub để mua gói.")
                    return
        return await handler(event, data)

router.message.middleware(AntiSpamMiddleware())


MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="/tiktok"), KeyboardButton(text="/track"), KeyboardButton(text="/untrack")],
        [KeyboardButton(text="/ig"), KeyboardButton(text="/trackig"), KeyboardButton(text="/untrackig")],
        [KeyboardButton(text="/check"), KeyboardButton(text="/list"), KeyboardButton(text="/balance"), KeyboardButton(text="/sub")],
        [KeyboardButton(text="/tracklist"), KeyboardButton(text="/trackiglist"), KeyboardButton(text="/trackfblist")],
        [KeyboardButton(text="/vip"), KeyboardButton(text="/ref"), KeyboardButton(text="/bank")],
        [KeyboardButton(text="/web"), KeyboardButton(text="/help")],
    ],
    resize_keyboard=True,
)

COMMANDS = [
    BotCommand(command="start",     description="Bắt đầu sử dụng bot"),
    BotCommand(command="bank",      description="Nạp tiền / Lấy thông tin chuyển khoản"),
    BotCommand(command="tiktok",    description="Check info TikTok: /tiktok <username>"),
    BotCommand(command="track",     description="Theo dõi follower: /track <username>"),
    BotCommand(command="untrack",   description="Huỷ theo dõi: /untrack <username>"),
    BotCommand(command="tracklist", description="Danh sách đang theo dõi"),
    BotCommand(command="trackv",    description="Theo dõi video: /trackv <link>"),
    BotCommand(command="untrackv",  description="Huỷ theo dõi video: /untrackv <link>"),
    BotCommand(command="trackvlist",description="Danh sách video đang theo dõi"),
    BotCommand(command="ig",        description="Check info Instagram: /ig <username>"),
    BotCommand(command="trackig",   description="Theo dõi IG: /trackig <username>"),
    BotCommand(command="untrackig", description="Huỷ theo dõi IG: /untrackig <username>"),
    BotCommand(command="trackvig",  description="Theo dõi bài viết IG: /trackvig <link>"),
    BotCommand(command="fb",        description="Check Facebook Live/Die: /fb <uid>"),
    BotCommand(command="trackfb",   description="Theo dõi FB: /trackfb <uid>"),
    BotCommand(command="untrackfb", description="Huỷ theo dõi FB: /untrackfb <uid>"),
    BotCommand(command="ref",       description="Lấy link giới thiệu kiếm tiền"),
    BotCommand(command="refcode",   description="Đổi mã giới thiệu: /refcode <code>"),
    BotCommand(command="ruttien",   description="Rút tiền: /ruttien <số_tiền> <Tên_NH> <STK>"),
    BotCommand(command="doitien",   description="Đổi hoa hồng sang số dư (+10% Bonus)"),
    BotCommand(command="alert",     description="Bật cảnh báo: /alert <platform> <target>"),
    BotCommand(command="alertlist", description="Danh sách cảnh báo"),
    BotCommand(command="alertoff",  description="Tắt cảnh báo: /alertoff <id>"),
    BotCommand(command="help",      description="Hướng dẫn sử dụng"),
    BotCommand(command="web",       description="Đăng nhập Bảng điều khiển Web"),
    BotCommand(command="hdcookie",  description="Hướng dẫn lấy Cookie các nền tảng"),
]

@router.message(Command("web"))
async def cmd_web(msg: Message):
    tg_id = msg.chat.id
    token = db.create_magic_link(tg_id)
    web_domain = db.get_setting("web_domain", "http://127.0.0.1:8000")
    url = f"{web_domain.rstrip('/')}/auth?token={token}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🌐 Đăng nhập Web", url=url)
    ]])
    await msg.answer("🔗 Bấm vào nút bên dưới để tự động đăng nhập vào Web:", reply_markup=kb)

# ─── PROCESS TIKTOK CHECK ─────────────────────────────────────
async def process_tiktok_check(msg: Message, username: str):
    wait = await msg.answer(f"⏳ Đang kiểm tra <b>@{username}</b>...")
    try:
        info    = await fetch_tiktok_info(username)
        caption = build_info_caption(info)
        if info["avatar"]:
            try:
                await msg.answer_photo(
                    photo=URLInputFile(info["avatar"], filename="avatar.jpg"),
                    caption=caption,
                )
                await wait.delete()
                return
            except Exception:
                pass
        await wait.edit_text(caption, disable_web_page_preview=True)
    except ValueError as e:
        await wait.edit_text(f"❌ {e}")
    except httpx.TimeoutException:
        await wait.edit_text("⏰ Timeout! Thử lại sau.")
    except Exception as e:
        log.exception("Lỗi check @%s", username)
        await wait.edit_text(f"❌ Lỗi: {e}")

async def process_ig_check(msg: Message, username: str):
    wait = await msg.answer(f"⏳ Đang kiểm tra IG <b>@{username}</b>...")
    try:
        info    = await fetch_ig_info(username)
        caption = build_ig_info_caption(info)
        if info.get("avatar"):
            try:
                await msg.answer_photo(
                    photo=URLInputFile(info["avatar"], filename="avatar.jpg"),
                    caption=caption,
                )
                await wait.delete()
                return
            except Exception:
                pass
        await wait.edit_text(caption, disable_web_page_preview=True)
    except ValueError as e:
        await wait.edit_text(f"❌ {e}")
    except httpx.TimeoutException:
        await wait.edit_text("⏰ Timeout! Thử lại sau.")
    except Exception as e:
        log.exception("Lỗi check IG @%s", username)
        await wait.edit_text(f"❌ Lỗi: {e}")

async def process_fb_check(msg: Message, uid: str):
    wait = await msg.answer(f"⏳ Đang kiểm tra FB UID <b>{uid}</b>...")
    try:
        res = await check_uid(uid)
        caption = build_fb_caption(res)
        
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📝 Ghi chú", callback_data=f"fb_note_{res['uid']}"),
                InlineKeyboardButton(text="👁 Theo dõi", callback_data=f"fb_track_{res['uid']}")
            ]
        ])

        await wait.edit_text(caption, disable_web_page_preview=True, reply_markup=kb)
    except Exception as e:
        log.exception("Lỗi check FB %s", uid)
        await wait.edit_text(f"❌ Lỗi: {e}")


# ─── HANDLERS ────────────────────────────────────────────────
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
        
    user = db.get_user(u.id)
    if not user:
        user = db.upsert_user(u.id, u.username or "", u.full_name or "", ref_id)
        
        # New User Notification for Admin
        admin_msg = (
            "🎉 <b>CÓ NGƯỜI DÙNG MỚI!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Tên:</b> {u.full_name}\n"
            f"🔗 <b>Username:</b> @{u.username if u.username else 'Không có'}\n"
            f"🆔 <b>ID:</b> <code>{u.id}</code>\n"
            f"🕒 <b>Thời gian:</b> {time.strftime('%H:%M:%S %d/%m/%Y')}\n"
        )
        if ref_id > 0 and ref_id != u.id:
            admin_msg += f"🤝 <b>Mời bởi:</b> <code>{ref_id}</code>\n"
            try:
                await msg.bot.send_message(ref_id, f"🎉 <b>Tin vui!</b>\nNgười dùng <b>{u.full_name}</b> vừa tham gia Bot qua link giới thiệu của bạn!\nKhi họ nạp tiền bạn sẽ nhận được 10% hoa hồng.", parse_mode="HTML")
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
            from .admin_bot import manager as admin_manager
            admin_sender_bot = admin_manager.bot
        else:
            admin_sender_bot = msg.bot
            
        if admin_sender_bot:
            for admin_id in admins:
                try:
                    await admin_sender_bot.send_message(admin_id, admin_msg, parse_mode="HTML")
                except: pass
                
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
        f"Số dư: <b>{vnd(user['balance'])}</b>\n"
        f"Gói FB: <b>{_sub_text(user)}</b>\n\n"
        f"{trial_msg}"
        "Gõ /help để xem hướng dẫn đầy đủ.\n"
        "Gõ /ref để lấy link giới thiệu nhận 10% hoa hồng.",
        reply_markup=MENU,
    )

@router.message(Command("ref"))
async def on_ref(msg: Message):
    try:
        bot_info = await msg.bot.get_me()
        user = db.get_user(msg.chat.id)
        
        # Ensure we don't hit KeyError for ref_earnings or ref_withdrawn by converting to dict
        user_dict = dict(user) if user else {}
        
        earnings = user_dict.get("ref_earnings") or 0
        withdrawn = user_dict.get("ref_withdrawn") or 0
        available = earnings - withdrawn
        
        ref_code = user_dict.get("ref_code") or f"REF{msg.chat.id}"
        if user and not user_dict.get("ref_code"):
            with db._lock:
                db.get_conn().execute("UPDATE tg_users SET ref_code=? WHERE tg_id=?", (ref_code, msg.chat.id))
                db.get_conn().commit()
                
        ref_link = f"https://t.me/{bot_info.username}?start={msg.chat.id}"
        ref_link_code = f"https://t.me/{bot_info.username}?start={ref_code}"
        
        f1_count = 0
        f2_count = 0
        if user:
            c = db.get_conn()
            f1_count = c.execute("SELECT COUNT(*) FROM tg_users WHERE referrer_id=?", (msg.chat.id,)).fetchone()[0]
            f2_count = c.execute("SELECT COUNT(*) FROM tg_users WHERE referrer_id IN (SELECT tg_id FROM tg_users WHERE referrer_id=?)", (msg.chat.id,)).fetchone()[0]

        await msg.answer(
            f"🎁 <b>HỆ THỐNG GIỚI THIỆU - KIẾM TIỀN</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔗 <b>Link giới thiệu của bạn:</b>\n"
            f"👉 <code>{ref_link}</code>\n"
            f"Hoặc mã: <code>{ref_link_code}</code>\n\n"
            f"💰 <b>Hoa hồng nhận được (Tùy cấp độ):</b>\n"
            f"- <b>F1 Hạng Đồng (Tổng nạp < 5tr): 10%</b>\n"
            f"- <b>F1 Hạng Bạc (Tổng nạp >= 5tr): 15%</b>\n"
            f"- <b>F1 Hạng Vàng (Tổng nạp >= 20tr): 20%</b>\n"
            f"- <b>F2 (Gián tiếp): 3%</b>\n\n"
            f"📊 <b>Thống kê của bạn:</b>\n"
            f"• Đã mời F1: <b>{f1_count} người</b>\n"
            f"• Đã mời F2: <b>{f2_count} người</b>\n"
            f"• Hoa hồng tổng: <b>{vnd(earnings)}</b>\n"
            f"• Đã rút: <b>{vnd(withdrawn)}</b>\n"
            f"• Khả dụng: <b>{vnd(available)}</b>\n\n"
            f"💡 Lệnh hỗ trợ:\n"
            f"<code>/refcode &lt;mã&gt;</code> - Đổi mã giới thiệu tùy chỉnh\n"
            f"<code>/doitien &lt;số_tiền&gt;</code> - Đổi hoa hồng thành số dư xài bot (+10% Bonus)\n"
            f"<code>/ruttien &lt;số_tiền&gt; &lt;Tên_NH&gt; &lt;STK&gt;</code> - Rút tiền hoa hồng (Min 50k, 2 lần đầu miễn phí, sau đó phí 10k)",
            parse_mode="HTML"
        )
    except Exception as e:
        import traceback
        err_msg = traceback.format_exc()
        await msg.answer(f"Lỗi: {e}\n\n<code>{err_msg[:3000]}</code>", parse_mode="HTML")

@router.message(Command("refcode"))
async def on_refcode(msg: Message):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: /refcode &lt;mã_mới&gt;\nLưu ý: Mã chỉ gồm chữ và số, không dấu cách.")
        return
        
    code = parts[1].strip()
    if not code.isalnum():
        await msg.answer("❌ Mã giới thiệu chỉ được chứa chữ cái và số!")
        return
        
    c = db.get_conn()
    exists = c.execute("SELECT tg_id FROM tg_users WHERE ref_code=?", (code,)).fetchone()
    if exists and exists["tg_id"] != msg.chat.id:
        await msg.answer("❌ Mã này đã có người sử dụng. Vui lòng chọn mã khác.")
        return
        
    with db._lock:
        c.execute("UPDATE tg_users SET ref_code=? WHERE tg_id=?", (code, msg.chat.id))
        c.commit()
        
    await msg.answer(f"✅ Đã đổi mã giới thiệu thành công: <code>{code}</code>")

@router.message(Command("ruttien"))
async def on_ruttien(msg: Message):
    parts = msg.text.split(maxsplit=3)
    if len(parts) < 4:
        await msg.answer("⚠️ Cú pháp: /ruttien &lt;số tiền&gt; &lt;Ngân hàng&gt; &lt;STK&gt;\nVí dụ: /ruttien 50000 MBBank 123456789")
        return
        
    try:
        amount = int(parts[1].replace(",", "").replace(".", "").replace("k", "000").replace("K", "000").strip())
    except:
        await msg.answer("❌ Số tiền không hợp lệ!")
        return
        
    bank_name = parts[2].strip()
    bank_account = parts[3].strip()
    bank_info = f"{bank_name} - {bank_account}"

    if amount < 50000:
        await msg.answer("❌ Số tiền rút tối thiểu là 50,000 VNĐ.")
        return
        
    c = db.get_conn()
    user = c.execute("SELECT ref_earnings, ref_withdrawn FROM tg_users WHERE tg_id=?", (msg.chat.id,)).fetchone()
    user_dict = dict(user) if user else {}
    
    earnings = user_dict.get("ref_earnings") or 0
    withdrawn = user_dict.get("ref_withdrawn") or 0
    available = earnings - withdrawn
    
    import datetime
    current_month_start = int(datetime.datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
    count_withdrawals = c.execute("SELECT COUNT(*) as c FROM withdrawal_requests WHERE tg_id=? AND created_at >= ?", (msg.chat.id, current_month_start)).fetchone()["c"]

    fee = 0
    if count_withdrawals >= 2:
        fee = 10000

    if available < amount + fee:
        await msg.answer(f"❌ Số dư khả dụng không đủ! (Khả dụng: {vnd(available)}, Cần: {vnd(amount + fee)} bao gồm phí {vnd(fee)} nếu có)")
        return
        
    with db._lock:
        c.execute("INSERT INTO withdrawal_requests(tg_id, amount, bank_info, fee, status, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                  (msg.chat.id, amount, bank_info, fee, 'pending', int(time.time()), int(time.time())))
        c.commit()
        
    admin_msg = f"🔔 <b>YÊU CẦU RÚT TIỀN HOA HỒNG</b>\n👤 ID: {msg.chat.id}\n💰 Số tiền: {vnd(amount)}\n🏦 Ngân hàng: {bank_info}\n💸 Phí rút: {vnd(fee)}"
    
    # Notify admin somehow
    admin_tg_token = db.get_setting("admin_bot_token", "")
    if admin_tg_token:
        from .admin_bot import manager as admin_manager
        if admin_manager.bot:
            admins = []
            try:
                if db.get_setting("admin_tg_id"): admins.append(int(db.get_setting("admin_tg_id")))
                if db.get_setting("admin_tg_group_id"): admins.append(int(db.get_setting("admin_tg_group_id")))
                for a in admins:
                    try: await admin_manager.bot.send_message(a, admin_msg, parse_mode="HTML")
                    except: pass
            except: pass
            
    await msg.answer(f"✅ Đã gửi yêu cầu rút <b>{vnd(amount)}</b>.\nVui lòng chờ Admin xử lý!", parse_mode="HTML")

@router.message(Command("doitien"))
async def on_doitien(msg: Message):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: /doitien &lt;số_tiền&gt;\nVí dụ: /doitien 50000")
        return
        
    try:
        amount = int(parts[1].replace(",", "").replace(".", "").replace("k", "000").replace("K", "000").strip())
    except:
        await msg.answer("❌ Số tiền không hợp lệ!")
        return
        
    if amount <= 0:
        await msg.answer("❌ Số tiền không hợp lệ!")
        return

    c = db.get_conn()
    user = c.execute("SELECT ref_earnings, ref_withdrawn FROM tg_users WHERE tg_id=?", (msg.chat.id,)).fetchone()
    user_dict = dict(user) if user else {}
    
    earnings = user_dict.get("ref_earnings") or 0
    withdrawn = user_dict.get("ref_withdrawn") or 0
    available = earnings - withdrawn
    
    if amount > available:
        await msg.answer(f"❌ Số dư khả dụng không đủ! (Khả dụng: {vnd(available)})")
        return
        
    bonus_amount = int(amount * 1.1)
    
    with db._lock:
        c.execute("UPDATE tg_users SET ref_withdrawn = ref_withdrawn + ?, balance = balance + ? WHERE tg_id=?", (amount, bonus_amount, msg.chat.id))
        c.execute("INSERT INTO txns(ts, tg_id, amount, reason) VALUES(?,?,?,?)", (int(time.time()), msg.chat.id, bonus_amount, 'Đổi hoa hồng sang số dư (+10% Bonus)'))
        c.commit()
        
    await msg.answer(f"✅ Đã đổi <b>{vnd(amount)}</b> hoa hồng sang <b>{vnd(bonus_amount)}</b> số dư thành công!", parse_mode="HTML")

@router.message(Command("trial"))
async def on_trial(msg: Message):
    user = db.get_user(msg.chat.id)
    if not user:
        await msg.answer("Bạn chưa /start. Gõ /start trước nhé.")
        return
    
    if db.get_setting("enable_free_trial", "1") != "1":
        await msg.answer("❌ Rất tiếc, chương trình dùng thử hiện đang đóng.")
        return
        
    try:
        days = int(db.get_setting("free_trial_days", "3"))
    except ValueError:
        days = 3
        
    if db.activate_trial(msg.chat.id, days):
        db.add_log("trial", f"User nhận trial {days} ngày", msg.chat.id)
        u2 = db.get_user(msg.chat.id)
        await msg.answer(
            f"🎉 <b>Chúc mừng!</b>\n\nBạn đã nhận được <b>{days} ngày</b> dùng thử miễn phí full tính năng!\n"
            f"Hạn sử dụng mới: <b>{_sub_text(u2)}</b>\n\n"
            "Hãy trải nghiệm các lệnh theo dõi nhé!"
        )
    else:
        await msg.answer("⚠️ Bạn đã nhận gói dùng thử rồi hoặc gói VIP của bạn đã từng được kích hoạt!")

@router.message(Command("bank"))
async def on_bank(msg: Message):
    import json
    banks_list_str = db.get_setting("banks_list", "")
    banks = []
    if banks_list_str:
        try: banks = json.loads(banks_list_str)
        except: pass
    if not banks:
        bank_name = db.get_setting("bank_name", "")
        if bank_name:
            banks = [{"name": bank_name, "account": db.get_setting("bank_account", ""), "owner": db.get_setting("bank_owner", "")}]
            
    if not banks:
        await msg.answer("⚠️ Admin chưa thiết lập thông tin ngân hàng.")
        return
        
    transfer_content = msg.from_user.username if msg.from_user.username else msg.chat.id
    
    lines = ["🏦 <b>THÔNG TIN CHUYỂN KHOẢN</b>\n"]
    for i, b in enumerate(banks, 1):
        lines.append(f"<b>{i}. {b.get('name', '')}</b>")
        lines.append(f"• Số tài khoản: <code>{b.get('account', '')}</code>")
        lines.append(f"• Chủ tài khoản: <b>{b.get('owner', '')}</b>\n")
        
    lines.append(f"📝 <b>Nội dung CK bắt buộc:</b> <code>{transfer_content}</code>\n")
    lines.append("<i>Sau khi chuyển khoản thành công, hãy bấm nút bên dưới để xác nhận!</i>")
    text = "\n".join(lines)
    
    parts = msg.text.split(maxsplit=1)
    amount = 0
    if len(parts) > 1:
        try:
            amount = int(parts[1].replace(",", "").replace(".", "").replace("k", "000").replace("K", "000").strip())
        except: pass

    if amount > 0:
        cb_data = f"bank_confirm_{amount}"
        btn_text = f"✅ Tôi đã chuyển {vnd(amount)}"
    else:
        cb_data = "bank_confirm"
        btn_text = "✅ Tôi đã chuyển tiền"

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=btn_text, callback_data=cb_data)]])
    
    import os
    img_dir = os.path.join(os.path.dirname(__file__), "..", "data", "images")
    if os.path.exists(img_dir):
        for f in os.listdir(img_dir):
            if f.startswith("qr_"):
                try:
                    await msg.answer_photo(photo=FSInputFile(os.path.join(img_dir, f)))
                except: pass
                
    await msg.answer(text, reply_markup=kb)

@router.callback_query(F.data.startswith("use_code_"))
async def on_use_code(cb: CallbackQuery):
    code = cb.data.replace("use_code_", "")
    success, amount, msg_text = db.use_code(code, cb.fromuser.id) if hasattr(cb, 'fromuser') else db.use_code(code, cb.from_user.id)
    if success:
        db.adjust_balance(cb.from_user.id, amount, f"Sử dụng Giftcode: {code}")
        try:
            msg_text_resp = (
                f"✅ <b>NẠP TIỀN THÀNH CÔNG!</b>\n\n"
                f"Bạn đã sử dụng mã <code>{code}</code> và được cộng <b>{vnd(amount)}</b> vào tài khoản.\n"
                f"Cảm ơn bạn đã tin tưởng dịch vụ!"
            )
            upgraded, new_vip, is_lifetime = db.check_vip_upgrade(cb.from_user.id)
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
            await cb.message.edit_text(msg_text_resp, parse_mode="HTML")
        except: pass
        
        admin_zalo = db.get_setting("admin_zalo_id", "")
        if admin_zalo and zalo_manager.running:
            username_str = f"@{cb.from_user.username}" if cb.from_user.username else cb.from_user.full_name
            asyncio.create_task(zalo_manager.send_message(admin_zalo, f"💵 Khách {username_str} ({cb.from_user.id}) đã sử dụng thành công mã {code} ({vnd(amount)})"))
            
        await cb.answer("Nạp tiền thành công!")
    else:
        await cb.answer(f"❌ {msg_text}", show_alert=True)

@router.callback_query(F.data.startswith("save_code_"))
async def on_save_code(cb: CallbackQuery):
    code = cb.data.replace("save_code_", "")
    success, msg_text = db.save_code_for_user(cb.from_user.id, code)
    if success:
        await cb.answer("✅ Đã lưu mã vào ví của bạn! Dùng lệnh /mycodes để xem lại nhé.", show_alert=True)
    else:
        await cb.answer(f"❌ {msg_text}", show_alert=True)

@router.message(Command("mycodes"))
async def on_mycodes(msg: Message):
    codes = db.get_user_saved_codes(msg.chat.id)
    if not codes:
        await msg.answer("📭 Bạn không có mã lưu trữ nào chưa sử dụng.")
        return
        
    text = "📥 <b>KHO MÃ LƯU TRỮ CỦA BẠN</b>\n\n"
    keyboard = []
    
    import datetime
    for c in codes:
        code_str = c["code"]
        amount = c["amount"]
        expire_at = c["expire_at"]
        
        expire_text = "Vĩnh viễn"
        if expire_at > 0:
            expire_text = datetime.datetime.fromtimestamp(expire_at).strftime('%H:%M %d/%m')
            
        text += f"• <code>{code_str}</code>: <b>{vnd(amount)}</b> (Hạn: {expire_text})\n"
        keyboard.append([InlineKeyboardButton(text=f"🎁 Dùng mã {vnd(amount)}", callback_data=f"use_code_{code_str}")])
        
    text += "\n<i>Bấm nút bên dưới để sử dụng:</i>"
    await msg.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="HTML")

@router.callback_query(F.data.startswith("bank_confirm"))
async def on_bank_confirm(cb: CallbackQuery, state: FSMContext):
    await cb.message.edit_reply_markup(reply_markup=None)
    
    if cb.data == "bank_confirm":
        await cb.message.answer("✍️ Vui lòng nhập <b>số tiền</b> bạn đã chuyển khoản (ví dụ: 50000):")
        await state.set_state(BankState.waiting_for_amount)
        await cb.answer()
    else:
        try:
            amount = int(cb.data.split("_")[2])
        except:
            amount = 0
            
        if amount > 0:
            await process_bank_amount(cb.message, cb.from_user, amount)
        await cb.answer()

async def process_bank_amount(msg: Message, user, amount: int):
    admin_tg_token = db.get_setting("admin_bot_token", "")
    admin_zalo = db.get_setting("admin_zalo_id", "")
    
    username_str = f"@{user.username}" if user.username else user.full_name
    admin_msg = (
        "🔔 <b>CÓ KHÁCH BÁO CHUYỂN KHOẢN!</b>\n\n"
        f"👤 Khách: {username_str}\n"
        f"🆔 ID Telegram: {user.id}\n"
        f"💰 Số tiền: {vnd(amount)}\n\n"
        f"👉 ĐỂ TẠO & PHÁT CODE, gửi lệnh:\n/phatcode {user.id} {amount}\n\n"
        f"👉 Cú pháp cộng thẳng: /topup {user.id} {amount}\n"
        "👉 Hoặc cộng thủ công trên trang Quản lý."
    )
    
    notified = False
    if admin_tg_token:
        from .admin_bot import manager as admin_manager
        admin_sender_bot = admin_manager.bot
    else:
        from .bot import manager as main_manager
        admin_sender_bot = main_manager.bot

    if admin_sender_bot:
        admins = []
        try:
            if db.get_setting("admin_tg_id"): admins.append(int(db.get_setting("admin_tg_id")))
        except: pass
        try:
            if db.get_setting("admin_tg_group_id"): admins.append(int(db.get_setting("admin_tg_group_id")))
        except: pass
        
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Xác nhận + Cộng tiền", callback_data=f"tg_admin_confirm_{user.id}_{amount}")]])
        for admin_id in admins:
            try:
                await admin_sender_bot.send_message(admin_id, admin_msg, parse_mode="HTML", reply_markup=kb)
                notified = True
            except Exception as e:
                log.error("Failed to notify TG admin %s: %s", admin_id, e)
                    
    if admin_zalo and zalo_manager.running:
        zalo_kb = {
            "inline_keyboard": [
                [{"text": "✅ Đã nhận tiền (Phát Code)", "callback_data": f"zalo_confirm_{user.id}_{amount}"}]
            ]
        }
        asyncio.create_task(zalo_manager.send_message(admin_zalo, admin_msg, reply_markup=zalo_kb))
        notified = True
        
    if notified:
        await msg.answer(f"✅ Đã gửi thông báo cho Admin xác nhận khoản nạp <b>{vnd(amount)}</b>.\nTiền sẽ được cộng vào tài khoản của bạn sau khi Admin kiểm tra xong (thường trong vòng 1-5 phút)!")
    else:
        await msg.answer(f"✅ Đã ghi nhận báo cáo <b>{vnd(amount)}</b>.\nTiền sẽ được cộng vào tài khoản của bạn sau khi Admin kiểm tra xong!")

@router.callback_query(F.data.startswith("tg_admin_confirm_"))
async def on_main_admin_confirm(cb: CallbackQuery):
    admins = []
    try:
        if db.get_setting("admin_tg_id"): admins.append(int(db.get_setting("admin_tg_id")))
    except: pass
    try:
        if db.get_setting("admin_tg_group_id"): admins.append(int(db.get_setting("admin_tg_group_id")))
    except: pass
    
    if cb.message.chat.id not in admins and cb.from_user.id not in admins:
        await cb.answer("❌ Bạn không có quyền duyệt!", show_alert=True)
        return
        
    parts = cb.data.split("_")
    user_id = int(parts[3])
    amount = int(parts[4])
    
    try:
        db.adjust_balance(user_id, amount, reason="bank_transfer")
        success = True
    except Exception as e:
        log.error(f"Lỗi cộng tiền: {e}")
        success = False
    if success:
        await cb.answer("✅ Đã cộng tiền thành công!", show_alert=True)
        try:
            await cb.message.edit_text(f"{cb.message.text}\n\n✅ <b>Đã duyệt {amount:,.0f} VNĐ bởi {cb.from_user.full_name}</b>", parse_mode="HTML")
        except: pass
        
        # Notify user via main bot
        try:
            msg_text = (
                f"✅ <b>NẠP TIỀN THÀNH CÔNG</b>\n\n"
                f"Bạn vừa được cộng <b>{amount:,.0f} VNĐ</b> vào tài khoản.\n"
                f"Cảm ơn bạn đã sử dụng dịch vụ!"
            )
            
            # Kiem tra VIP upgrade
            upgraded, new_vip, is_lifetime = db.check_vip_upgrade(user_id)
            if upgraded and new_vip > 0:
                limit = db.get_setting(f"vip{new_vip}_limit", "10")
                msg_text += (
                    f"\n\n🎉 <b>CHÚC MỪNG BẠN ĐÃ LÊN VIP {new_vip}!</b> 🎉\n\n"
                    f"🎁 <b>Đặc quyền mới:</b>\n"
                    f"- Mức độ theo dõi tối đa: <b>{limit} mục</b>/nền tảng\n"
                )
                if is_lifetime:
                    msg_text += "- Hạn sử dụng: <b>VĨNH VIỄN</b>\n\n"
                else:
                    msg_text += "\n"
                    
            await cb.bot.send_message(
                user_id,
                msg_text,
                parse_mode="HTML"
            )
        except: pass
    else:
        await cb.answer("❌ Lỗi khi cộng tiền!", show_alert=True)

@router.message(BankState.waiting_for_amount)
async def on_bank_amount(msg: Message, state: FSMContext):
    amount_str = msg.text.strip()
    try:
        amount = int(amount_str.replace(",", "").replace(".", ""))
        if amount <= 0: raise ValueError()
    except:
        await msg.answer("❌ Số tiền không hợp lệ. Vui lòng nhập lại số tiền (ví dụ: 50000):")
        return
        
    await state.clear()
    await process_bank_amount(msg, msg.from_user, amount)

@router.message(Command("help"))
async def on_help(msg: Message):
    help_text = (
        "📖 <b>HƯỚNG DẪN CHECKER V2</b>\n\n"
        "<b>💰 TÀI KHOẢN &amp; NẠP TIỀN</b>\n"
        "• /bank - Xem thông tin nạp tiền\n"
        "• /bank &lt;số_tiền&gt; - Nạp nhanh (VD: /bank 50000)\n"
        "• /balance - Xem số dư hiện tại\n"
        "• /vip - Xem cấp độ VIP và đặc quyền\n"
        "• /mycodes - Xem kho mã quà tặng\n"
        "• /sub - Xem gói và mua gói\n"
        "• /ref - Lấy link giới thiệu kiếm tiền\n"
        "• /doitien &lt;số_tiền&gt; - Đổi hoa hồng thành số dư (+10% bonus)\n"
        "• /ruttien &lt;tiền&gt; &lt;ngân_hàng&gt; &lt;stk&gt; - Rút tiền hoa hồng\n"
        "• /hdcookie - Hướng dẫn lấy Cookie (Dành cho Admin)\n\n"
        "<b>💻 BẢNG ĐIỀU KHIỂN WEB</b>\n"
        "• /web - Đăng nhập Web Dashboard không cần mật khẩu\n\n"
        "<b>🔔 HỆ THỐNG CẢNH BÁO (ALERTS)</b>\n"
        "• /alert &lt;platform&gt; &lt;target&gt; - Bật cảnh báo tự động\n"
        "• /alertlist - Xem danh sách cảnh báo\n"
        "• /alertoff &lt;id&gt; - Tắt cảnh báo\n\n"
        "<b>1. TIKTOK COMMANDS</b>\n"
        "• /tiktok &lt;user&gt; - Check nhanh\n"
        "• /track &lt;user&gt; - Theo dõi follower\n"
        "• /untrack &lt;user&gt; - Huỷ theo dõi\n"
        "• /tracklist - Ds theo dõi\n"
        "• /trackv &lt;link&gt; [phút] - Check video\n"
        "• /untrackv &lt;link&gt; - Huỷ video\n"
        "• /trackvlist - Ds video\n\n"
        "<b>2. INSTAGRAM COMMANDS</b>\n"
        "• /ig &lt;user&gt; - Check nhanh IG\n"
        "• /trackig &lt;user&gt; - Theo dõi follower IG\n"
        "• /untrackig &lt;user&gt; - Huỷ IG\n"
        "• /trackiglist - Ds IG\n"
        "• /trackvig &lt;link&gt; [phút] - Check IG post\n"
        "• /untrackvig &lt;link&gt; - Huỷ IG post\n"
        "• /trackviglist - Ds IG post\n\n"
        "<b>3. FACEBOOK COMMANDS</b>\n"
        "• /fb &lt;uid/link&gt; - Check FB Live/Die\n"
        "• /trackfb &lt;uid&gt; - Theo dõi Live/Die\n"
        "• /untrackfb &lt;uid&gt; - Huỷ theo dõi\n"
        "• /trackfblist - Ds FB đang theo dõi\n\n"
        "<b>4. YOUTUBE COMMANDS</b>\n"
        "• /yt &lt;link/username&gt; - Check nhanh kênh YT\n"
        "• /trackyt &lt;link/username&gt; - Theo dõi kênh YT\n"
        "• /untrackyt &lt;link/username&gt; - Huỷ theo dõi YT\n"
        "• /trackylist - Ds kênh YT đang theo dõi\n"
        "• /trackvyt &lt;link_video&gt; - Theo dõi video YT\n"
        "• /untrackvyt &lt;link_video&gt; - Huỷ video YT\n"
        "• /trackvytlist - Ds video YT đang theo dõi\n\n"
        "<b>5. ZALO COMMANDS</b>\n"
        "• /zalo &lt;sđt&gt; - Check nhanh SĐT Zalo Live/Die\n"
        "• /trackzalo &lt;sđt&gt; - Theo dõi biến động SĐT Zalo\n"
        "• /untrackzalo &lt;sđt&gt; - Huỷ theo dõi Zalo\n"
        "• /trackzalolist - Ds SĐT Zalo đang theo dõi\n\n"
        "💬 <b>Hỗ trợ:</b>\n"
        "• Telegram: @khaikhai998\n"
        "• Facebook: facebook.com/khaitradecoin"
    )
    await msg.answer(help_text)

@router.message(Command("hdcookie"))
async def on_hdcookie(msg: Message):
    text = (
        "🍪 <b>HƯỚNG DẪN LẤY COOKIE CÁC NỀN TẢNG</b> 🍪\n\n"
        "<b>1. ZALO (Lấy Cookie & IMEI)</b>\n"
        "• Truy cập: <code>chat.zalo.me</code> trên máy tính (F12 hoặc Chuột phải -> Kiểm tra)\n"
        "• Chọn tab <b>Network</b> (Mạng), F5 tải lại trang. Bấm vào một yêu cầu (request) bất kỳ, kéo xuống phần <b>Request Headers</b>, copy toàn bộ đoạn <code>Cookie: ...</code>\n"
        "• Hoặc chọn tab <b>Application</b> (Ứng dụng) -> Cookies. Tìm khóa <code>zpw_sek</code> và copy giá trị.\n"
        "• <b>Lấy IMEI:</b> Ở tab <b>Application</b> -> Local Storage -> tìm khóa <code>z_uuid</code>, đó chính là IMEI.\n\n"
        "<b>2. FACEBOOK (Cookie)</b>\n"
        "• Đăng nhập tài khoản Clone FB trên trình duyệt.\n"
        "• F12 -> tab <b>Network</b> -> F5. Bấm vào request đầu tiên, kéo xuống <b>Request Headers</b> -> copy toàn bộ dòng <code>Cookie: c_user=...</code>\n"
        "• Hoặc dùng tiện ích mở rộng (Extension) như <b>J2TEAM Security</b> hoặc <b>Get Token by Ninja</b> để copy nhanh.\n\n"
        "<b>3. INSTAGRAM</b>\n"
        "• <b>Cách 1 (Dễ nhất - Khuyên dùng):</b> Bạn chỉ cần vào trang <b>Cài đặt</b> trên Web Dashboard, chọn mục Phương thức check Instagram là <code>Instaloader</code>, sau đó điền trực tiếp <b>Tên đăng nhập (Username)</b> và <b>Mật khẩu</b> của acc Clone IG vào. Tool sẽ tự động đăng nhập ngầm và lấy Cookie cho bạn!\n"
        "• <b>Cách 2 (Thủ công):</b> Đăng nhập IG trên Web -> F12 -> <b>Application</b> -> Cookies -> tìm khóa <code>sessionid</code> và copy dán vào mục IG Session Cookie.\n\n"
        "⚠️ <i>Lưu ý: Tuyệt đối KHÔNG sử dụng tài khoản CHÍNH cho việc này để tránh rủi ro bị khóa. Chỉ nên dùng acc Clone/Phụ để nạp vào Tool!</i>"
    )
    await msg.answer(text, parse_mode="HTML")


@router.message(Command("ping2"))
async def on_ping2(msg: Message):
    await msg.answer("pong version 2 - Đã cập nhật code mới thành công!")

@router.message(Command("tiktok"))
async def on_tiktok(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await msg.answer("⚠️ Cú pháp: /tiktok &lt;username&gt;\nVí dụ: /tiktok cristiano")
        return
    username = parse_username(parts[1].strip())
    if not username:
        await msg.answer("❌ Không nhận diện được username.")
        return
    await process_tiktok_check(msg, username)
@router.message(Command("fb"))
async def on_fb(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await msg.answer("⚠️ Cú pháp: /fb &lt;uid&gt;\nVí dụ: /fb 100089260699193")
        return
    from .fb import extract_uid
    uid = extract_uid(parts[1].strip())
    if not uid:
        await msg.answer("❌ Không nhận diện được UID.")
        return
    await process_fb_check(msg, uid)


# ─── PROCESS FB POST CHECK ────────────────────────────────────
async def process_fb_post_check(msg: Message, url: str):
    wait = await msg.answer("⏳ Đang lấy thông tin bài viết Facebook...")
    from .fb import fetch_fb_post_info, build_fb_post_caption
    info = await fetch_fb_post_info(url)
    if not info or not info.get("post_id"):
        await wait.edit_text("❌ Không lấy được thông tin bài viết FB. Vui lòng kiểm tra lại link.")
        return
    caption = build_fb_post_caption(info)
    await wait.edit_text(caption, disable_web_page_preview=True)


@router.message(Command("track"))
async def on_track(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await msg.answer("⚠️ Cú pháp: /track &lt;username&gt;\nVí dụ: /track cristiano")
        return
    username = parse_username(parts[1].strip())
    if not username:
        await msg.answer("❌ Không nhận diện được username.")
        return

    wait = await msg.answer(f"⏳ Đang thêm theo dõi <b>@{username}</b>...")
    
    # VIP Limit check
    user = db.get_user(msg.chat.id)
    vip_level = dict(user).get("vip_level", 0) if user else 0
    try:
        max_limit = int(db.get_setting(f"vip{vip_level}_limit", [5, 50, 200, 1000][vip_level if vip_level <= 3 else 3]))
    except:
        max_limit = [5, 50, 200, 1000][vip_level if vip_level <= 3 else 3]
    with db._lock:
        count = db.get_conn().execute("SELECT COUNT(*) FROM tracks WHERE tg_user_id=?", (msg.chat.id,)).fetchone()[0]
    if count >= max_limit:
        await wait.edit_text(f"❌ <b>Giới hạn hạng VIP!</b>\nHạng của bạn chỉ cho phép theo dõi tối đa <b>{max_limit}</b> mục.\nVui lòng /untrack các mục cũ hoặc nâng cấp VIP.")
        return
    try:
        info = await fetch_tiktok_info(username)
        u = msg.from_user
        result = db.add_track(
            u.id, u.username or u.full_name,
            info["username"],
            info["followers"], info["following"], info["videos"]
        )
        if result == -1:
            await wait.edit_text(f"⚠️ Bạn đã theo dõi <b>@{info['username']}</b> rồi!")
            return
        db.add_log("track_add", f"Thêm theo dõi @{info['username']}", u.id, info["username"])
        
        caption = (
            f"✅ <b>Đã thêm theo dõi tài khoản!</b>\n\n"
            f"📱 Kênh: <b><a href='https://www.tiktok.com/@{info['username']}'>@{info['username']}</a></b>\n"
            f"👥 Followers hiện tại: <b>{fmt_num(info['followers'])}</b>\n"
            f"➡️ Đang follow: <b>{fmt_num(info['following'])}</b>\n"
            f"🎬 Tổng videos: <b>{fmt_num(info['videos'])}</b>\n\n"
            f"📩 <i>Bot sẽ thông báo khi có thay đổi follower.</i>"
        )
        if info.get("avatar"):
            try:
                await msg.answer_photo(photo=URLInputFile(info["avatar"], filename="avatar.jpg"), caption=caption)
                await wait.delete()
            except Exception:
                await wait.edit_text(caption, disable_web_page_preview=False)
        else:
            await wait.edit_text(caption, disable_web_page_preview=False)
    except ValueError as e:
        await wait.edit_text(f"❌ {e}")
    except Exception as e:
        await wait.edit_text(f"❌ Lỗi: {e}")


@router.message(Command("untrack"))
async def on_untrack(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await msg.answer("⚠️ Cú pháp: /untrack &lt;username&gt;\nVí dụ: /untrack cristiano")
        return
    username = parse_username(parts[1].strip())
    if not username:
        await msg.answer("❌ Không nhận diện được username.")
        return
    ok = db.remove_track(msg.chat.id, username)
    if ok:
        db.add_log("track_remove", f"Huỷ theo dõi @{username}", msg.chat.id, username)
        await msg.answer(f"✅ Đã huỷ theo dõi <b>@{username}</b>.")
    else:
        await msg.answer(f"❌ Không tìm thấy <b>@{username}</b> trong danh sách của bạn.")


@router.message(Command("tracklist"))
async def on_tracklist(msg: Message):
    tracks = db.user_tracks(msg.chat.id)
    if not tracks:
        await msg.answer(
            "📋 Bạn chưa theo dõi tài khoản nào.\n\n"
            "Dùng /track &lt;username&gt; để thêm."
        )
        return
    lines = ["📋 <b>Danh sách đang theo dõi:</b>\n"]
    for i, t in enumerate(tracks, 1):
        lines.append(
            f"{i}. <b>@{t['tiktok_username']}</b>\n"
            f"   👥 {fmt_num(t['last_followers'])} followers"
        )
    await msg.answer("\n".join(lines))


@router.message(Command("trackvlist"))
async def on_trackvlist(msg: Message):
    vtracks = db.user_video_tracks(msg.chat.id)
    if not vtracks:
        await msg.answer("📋 Bạn chưa theo dõi video nào.\n\nDùng /trackv &lt;link_video&gt; để thêm.")
        return
    lines = ["🎬 <b>Video đang theo dõi:</b>\n"]
    for i, v in enumerate(vtracks, 1):
        interval_min = v["check_interval"] // 60
        desc = (v["video_desc"][:50] + "...") if len(v.get("video_desc","")) > 50 else v.get("video_desc","")
        lines.append(
            f"{i}. <a href=\"{v['video_url']}\">@{v['tiktok_username']}</a>\n"
            f"   📝 {desc or 'Khong co mo ta'}\n"
            f"   ▶️ {v['last_plays']:,}  ❤️ {v['last_likes']:,}  💬 {v['last_comments']:,}  🔁 {v['last_shares']:,}  ⭐ {v.get('last_favorites', 0):,}\n"
            f"   ⏱ Check mỗi {interval_min} phút"
        )
    await msg.answer("\n\n".join(lines))


@router.message(Command("trackv"))
async def on_trackv(msg: Message):
    parts = (msg.text or "").split(maxsplit=2)
    if len(parts) < 2:
        await msg.answer(
            "⚠️ Cú pháp: /trackv &lt;link_video&gt; [phút]\n\n"
            "Ví dụ:\n"
            "  /trackv https://tiktok.com/@user/video/123\n"
            "  /trackv https://tiktok.com/@user/video/123 30   (check mỗi 30 phút)"
        )
        return

    from .tiktok import fetch_video_info, parse_video_id
    video_url = parts[1].strip()
    interval_min = 60
    if len(parts) >= 3:
        try:
            interval_min = max(1, int(parts[2]))
        except ValueError:
            pass

    if not parse_video_id(video_url):
        await msg.answer("❌ Link video không hợp lệ. Cần dạng: tiktok.com/@user/video/ID")
        return

    wait = await msg.answer(f"⏳ Đang lấy thông tin video...")
    try:
        info = await fetch_video_info(video_url)
        u = msg.from_user
        r = db.add_video_track(
            u.id, u.username or u.full_name,
            video_url, info["id"], info.get("username",""),
            info.get("desc",""), info.get("cover",""),
            interval_min * 60,
            info["plays"], info["likes"], info["comments"], info["shares"], info.get("favorites", 0)
        )
        if r == -1:
            await wait.edit_text("⚠️ Bạn đã theo dõi video này rồi!")
            return
        db.add_log("video_track_add", f"Them video @{info.get('username','')}", u.id, info.get("username",""))
        desc = (info.get("desc", "")[:80]+"...") if len(info.get("desc","")) > 80 else info.get("desc","")
        caption = (
            f"✅ <b>Đã thêm theo dõi video!</b>\n\n"
            f"📱 Kênh: <b><a href='https://www.tiktok.com/@{info.get('username','')}'>@{info.get('username','')}</a></b>\n"
            f"📝 Mô tả: <i>{desc or 'Không có mô tả'}</i>\n\n"
            f"📊 <b>Thống kê hiện tại:</b>\n"
            f" ┣ ▶️ Lượt xem: <b>{info['plays']:,}</b>\n"
            f" ┣ ❤️ Lượt thích: <b>{info['likes']:,}</b>\n"
            f" ┣ 💬 Bình luận: <b>{info['comments']:,}</b>\n"
            f" ┣ 🔁 Chia sẻ: <b>{info['shares']:,}</b>\n"
            f" ┗ ⭐ Yêu thích: <b>{info.get('favorites', 0):,}</b>\n\n"
            f"⏱ <i>Tự động check mỗi <b>{interval_min} phút</b></i>\n"
            f"📩 <i>Bot sẽ thông báo khi có tương tác mới!</i>"
        )
        if info.get("cover"):
            try:
                await msg.answer_photo(photo=URLInputFile(info["cover"], filename="cover.jpg"), caption=caption)
                await wait.delete()
            except Exception:
                await wait.edit_text(caption, disable_web_page_preview=False)
        else:
            await wait.edit_text(caption, disable_web_page_preview=False)
    except Exception as e:
        await wait.edit_text(f"❌ Lỗi: {e}")


@router.message(Command("untrackv"))
async def on_untrackv(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: /untrackv &lt;link_video&gt;")
        return
    from .tiktok import parse_video_id
    vid_id = parse_video_id(parts[1].strip())
    if not vid_id:
        await msg.answer("❌ Không nhận diện được Video ID.")
        return
    ok = db.remove_video_track(msg.chat.id, vid_id)
    if ok:
        db.add_log("video_track_remove", f"Huy video ID {vid_id}", msg.chat.id)
        await msg.answer("✅ Đã huỷ theo dõi video.")
    else:
        await msg.answer("❌ Không tìm thấy video này trong danh sách của bạn.")

# ─── INSTAGRAM COMMANDS ──────────────────────────────────────
@router.message(Command("ig"))
async def on_ig(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await msg.answer("⚠️ Cú pháp: /ig &lt;username&gt;\nVí dụ: /ig cristiano")
        return
    username = parse_ig_username(parts[1].strip())
    if not username:
        await msg.answer("❌ Không nhận diện được username IG.")
        return
    await process_ig_check(msg, username)

@router.message(Command("trackig"))
async def on_trackig(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await msg.answer("⚠️ Cú pháp: /trackig &lt;username&gt;\nVí dụ: /trackig cristiano")
        return
    username = parse_ig_username(parts[1].strip())
    if not username:
        await msg.answer("❌ Không nhận diện được username IG.")
        return

    wait = await msg.answer(f"⏳ Đang thêm theo dõi IG <b>@{username}</b>...")
    try:
        info = await fetch_ig_info(username)
        u = msg.from_user
        result = db.add_ig_track(
            u.id, u.username or u.full_name,
            info["username"],
            info["followers"], info["following"], info["posts"]
        )
        if result == -1:
            await wait.edit_text(f"⚠️ Bạn đã theo dõi IG <b>@{info['username']}</b> rồi!")
            return
        db.add_log("track_add", f"Thêm theo dõi IG @{info['username']}", u.id, info["username"])
        caption = (
            f"✅ <b>Đã thêm theo dõi tài khoản IG!</b>\n\n"
            f"📸 Kênh: <b><a href='https://www.instagram.com/{info['username']}'>@{info['username']}</a></b>\n"
            f"👥 Followers hiện tại: <b>{fmt_num(info['followers'])}</b>\n"
            f"➡️ Đang follow: <b>{fmt_num(info['following'])}</b>\n"
            f"🖼 Bài viết: <b>{fmt_num(info['posts'])}</b>\n\n"
            f"📩 <i>Bot sẽ thông báo khi có thay đổi follower.</i>"
        )
        if info.get("avatar"):
            try:
                await msg.answer_photo(photo=URLInputFile(info["avatar"], filename="avatar.jpg"), caption=caption)
                await wait.delete()
            except Exception:
                await wait.edit_text(caption, disable_web_page_preview=False)
        else:
            await wait.edit_text(caption, disable_web_page_preview=False)
    except Exception as e:
        await wait.edit_text(f"❌ Lỗi: {e}")

@router.message(Command("untrackig"))
async def on_untrackig(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await msg.answer("⚠️ Cú pháp: /untrackig &lt;username&gt;")
        return
    username = parse_ig_username(parts[1].strip())
    if not username: return
    ok = db.remove_ig_track(msg.chat.id, username)
    if ok:
        db.add_log("track_remove", f"Huỷ theo dõi IG @{username}", msg.chat.id, username)
        await msg.answer(f"✅ Đã huỷ theo dõi IG <b>@{username}</b>.")
    else:
        await msg.answer(f"❌ Không tìm thấy IG <b>@{username}</b> trong danh sách của bạn.")

@router.message(Command("trackiglist"))
async def on_trackiglist(msg: Message):
    tracks = db.user_ig_tracks(msg.chat.id)
    if not tracks:
        await msg.answer("📋 Bạn chưa theo dõi tài khoản IG nào.")
        return
    lines = ["📸 <b>Danh sách IG đang theo dõi:</b>\n"]
    for i, t in enumerate(tracks, 1):
        lines.append(
            f"{i}. <b>@{t['ig_username']}</b>\n"
            f"   👥 {fmt_num(t['last_followers'])} followers"
        )
    await msg.answer("\n".join(lines))

@router.message(Command("trackvig"))
async def on_trackvig(msg: Message):
    parts = (msg.text or "").split(maxsplit=2)
    if len(parts) < 2:
        await msg.answer(
            "⚠️ Cú pháp: /trackvig &lt;link_bài_viết_ig&gt; [phút]\n\n"
            "Ví dụ:\n"
            "  /trackvig https://www.instagram.com/p/C123456/\n"
            "  /trackvig https://www.instagram.com/p/C123456/ 30"
        )
        return

    post_url = parts[1].strip()
    interval_min = 60
    if len(parts) >= 3:
        try: interval_min = max(1, int(parts[2]))
        except ValueError: pass

    post_id = parse_ig_post_id(post_url)
    if not post_id:
        await msg.answer("❌ Link bài viết IG không hợp lệ.")
        return

    wait = await msg.answer(f"⏳ Đang lấy thông tin bài viết IG...")
    try:
        info = await fetch_ig_post_info(post_url)
        u = msg.from_user
        r = db.add_ig_video_track(
            u.id, u.username or u.full_name,
            post_url, info["id"], info.get("username",""),
            info.get("desc",""), info.get("cover",""),
            interval_min * 60,
            info["likes"], info["comments"], info.get("views", 0)
        )
        if r == -1:
            await wait.edit_text("⚠️ Bạn đã theo dõi bài viết IG này rồi!")
            return
        db.add_log("video_track_add", f"Them IG post {info['id']}", u.id, info.get("username",""))
        desc = (info.get("desc","")[:80]+"...") if len(info.get("desc","")) > 80 else info.get("desc","")
        caption = (
            f"✅ <b>Đã thêm theo dõi bài viết IG!</b>\n\n"
            f"📸 Kênh: <b><a href='https://www.instagram.com/{info.get('username','')}'>@{info.get('username','')}</a></b>\n"
            f"📝 Mô tả: <i>{desc or 'Không có mô tả'}</i>\n\n"
            f"📊 <b>Thống kê hiện tại:</b>\n"
            f" ┣ ❤️ Lượt thích: <b>{info['likes']:,}</b>\n"
            f" ┣ 💬 Bình luận: <b>{info['comments']:,}</b>\n"
            f" ┗ 👁️ Lượt xem: <b>{info.get('views', 0):,}</b>\n\n"
            f"⏱ <i>Tự động check mỗi <b>{interval_min} phút</b></i>\n"
            f"📩 <i>Bot sẽ thông báo khi có tương tác mới!</i>"
        )
        if info.get("cover"):
            try:
                await msg.answer_photo(photo=URLInputFile(info["cover"], filename="cover.jpg"), caption=caption)
                await wait.delete()
            except Exception:
                await wait.edit_text(caption, disable_web_page_preview=False)
        else:
            await wait.edit_text(caption, disable_web_page_preview=False)
    except Exception as e:
        await wait.edit_text(f"❌ Lỗi: {e}")

@router.message(Command("untrackvig"))
async def on_untrackvig(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2: return
    post_id = parse_ig_post_id(parts[1].strip())
    if not post_id: return
    ok = db.remove_ig_video_track(msg.chat.id, post_id)
    if ok:
        await msg.answer("✅ Đã huỷ theo dõi bài viết IG.")
    else:
        await msg.answer("❌ Không tìm thấy bài viết IG này.")

@router.message(Command("trackviglist"))
async def on_trackviglist(msg: Message):
    vtracks = db.user_ig_video_tracks(msg.chat.id)
    if not vtracks:
        await msg.answer("📋 Bạn chưa theo dõi bài viết IG nào.")
        return
    lines = ["🎬 <b>Bài viết IG đang theo dõi:</b>\n"]
    for i, v in enumerate(vtracks, 1):
        interval_min = v["check_interval"] // 60
        desc = (v["post_desc"][:50] + "...") if len(v.get("post_desc","")) > 50 else v.get("post_desc","")
        lines.append(
            f"{i}. <a href=\"{v['post_url']}\">@{v['ig_username']}</a>\n"
            f"   📝 {desc or 'Khong co mo ta'}\n"
            f"   ❤️ {v['last_likes']:,}  💬 {v['last_comments']:,}\n"
            f"   ⏱ Check mỗi {interval_min} phút"
        )
    await msg.answer("\n\n".join(lines))


def _sub_active(user) -> bool:
    return user and user["sub_until"] and user["sub_until"] > now()

def _sub_text(user) -> str:
    if _sub_active(user):
        if user["sub_until"] >= 9999999999:
            return "VĨNH VIỄN"
        days_left = (user["sub_until"] - now()) // DAY
        return f"Còn hạn ({days_left} ngày)"
    return "Chưa có / Đã hết hạn"

def status_caption(status: str, note: str, price, header: str = "") -> str:
    icon = "🟢" if status == "live" else "🔴"
    word = "LIVE" if status == "live" else "DIE"
    lines = []
    if header:
        lines.append(header)
    lines.append(f"{icon} Tài khoản đang <b>{word}</b>")
    if note:
        lines.append(f"Ghi chú: {note}")
    if price:
        lines.append(f"Giá: {vnd(price)}")
    return "\n".join(lines)

async def _send_card(bot: Bot, chat_id: int, uid: str, status: str, note, price,
                     avatar: str, header: str = ""):
    caption = status_caption(status, note, price, header)
    if avatar:
        try:
            await bot.send_photo(chat_id, photo=URLInputFile(avatar), caption=caption)
            return
        except Exception:
            pass
    await bot.send_message(chat_id, caption)

@router.message(Command("balance"))
async def on_balance(msg: Message):
    user = db.get_user(msg.chat.id)
    if not user:
        await msg.answer("Bạn chưa /start. Gõ /start trước nhé.")
        return
    await msg.answer(f"Số dư: <b>{vnd(user['balance'])}</b>\nGói: <b>{_sub_text(user)}</b>")

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

@router.message(Command("sub"))
async def on_sub(msg: Message):
    p1 = int(db.get_setting("price_1m", "0") or 0)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"1 tháng - {vnd(p1)}", callback_data="sub:1")],
            [InlineKeyboardButton(text=f"2 tháng - {vnd(p1 * 2)}", callback_data="sub:2")],
            [InlineKeyboardButton(text=f"3 tháng - {vnd(p1 * 3)}", callback_data="sub:3")],
        ]
    )
    await msg.answer("Chọn gói muốn mua / gia hạn:", reply_markup=kb)

@router.callback_query(F.data.startswith("sub:"))
async def on_sub_pick(cb: CallbackQuery):
    months = int(cb.data.split(":")[1])
    p1 = int(db.get_setting("price_1m", "0") or 0)
    cost = p1 * months
    user = db.get_user(cb.from_user.id)
    if not user:
        await cb.answer("Bạn chưa /start.", show_alert=True)
        return
    if user["balance"] < cost:
        await cb.answer(f"Số dư không đủ. Cần {vnd(cost)}, bạn có {vnd(user['balance'])}.", show_alert=True)
        return
    db.adjust_balance(cb.from_user.id, -cost, f"Mua gói {months} tháng")
    base = max(now(), user["sub_until"] or 0)
    db.set_sub_until(cb.from_user.id, base + months * 30 * DAY)
    db.add_log("sub", f"Mua {months} tháng (-{cost})", cb.from_user.id)
    u2 = db.get_user(cb.from_user.id)
    await cb.message.answer(
        f"Đã kích hoạt gói <b>{months} tháng</b>.\n"
        f"Số dư còn: <b>{vnd(u2['balance'])}</b>\nGói: <b>{_sub_text(u2)}</b>"
    )
    await cb.answer("Thành công")

@router.message(Command("list", "trackfblist"))
async def on_list(msg: Message):
    rows = db.user_watches(msg.chat.id)
    if not rows:
        await msg.answer("Bạn chưa theo dõi UID nào. Dùng /check để thêm.")
        return
    lines = ["<b>Danh sách đang theo dõi</b>"]
    for w in rows:
        st = w["last_status"] or "?"
        icon = "🟢" if st == "live" else ("🔴" if st == "die" else "⚪")
        extra = f" — {w['note']}" if w["note"] else ""
        lines.append(f"{icon} {w['uid']}{extra}")
    await msg.answer("\n".join(lines))

@router.message(Command("remove", "untrackfb"))
async def on_remove(msg: Message):
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("Cú pháp: /remove {uid}")
        return
    n = db.remove_watch(msg.chat.id, parts[1].strip())
    await msg.answer("Đã bỏ theo dõi." if n else "Không tìm thấy UID này.")

@router.message(Command("check", "trackfb"))
async def on_check(msg: Message):
    user = db.get_user(msg.chat.id)
    if not user:
        await msg.answer("Bạn chưa /start. Gõ /start trước nhé.")
        return
    if not _sub_active(user):
        await msg.answer("Bạn cần có gói còn hạn để dùng /check. Gõ /sub để mua gói.")
        return

    uid, note, price, days = parse_check_args(msg.text or "")
    if not uid:
        await msg.answer("Cú pháp: /check {uid} [ghi chú] [giá] [số ngày]")
        return

    from .fb import check_uid, avatar_url
    res = await check_uid(uid)
    status = "live" if res["alive"] else "die"
    avatar = res["avatar_url"] or avatar_url(uid)
    expire_at = now() + days * DAY if days else 0
    wid = db.add_watch(msg.chat.id, res["uid"], note or "", price or 0, expire_at)
    db.update_watch_status(wid, status, avatar)
    db.add_log("add", f"Thêm UID {res['uid']} ({status})", msg.chat.id, res["uid"])

    header = "Đã thêm theo dõi:"
    if days:
        header += f" trong {days} ngày"
    await _send_card(msg.bot, msg.chat.id, res["uid"], status, note, price, avatar, header)


@router.callback_query(F.data.startswith("fb_track_"))
async def on_fb_track_btn(cb: CallbackQuery):
    uid = cb.data.replace("fb_track_", "")
    user = db.get_user(cb.from_user.id)
    if not user or not _sub_active(user):
        await cb.answer("❌ Bạn cần có gói còn hạn để dùng tính năng theo dõi.", show_alert=True)
        return
        
    from .fb import check_uid, avatar_url
    res = await check_uid(uid)
    status = "live" if res["alive"] else "die"
    avatar = res.get("avatar_url") or avatar_url(uid)
    
    wid = db.add_watch(cb.from_user.id, res["uid"], "", 0, 0)
    db.update_watch_status(wid, status, avatar)
    db.add_log("add", f"Thêm UID {res['uid']} ({status})", cb.from_user.id, res["uid"])
    
    await cb.answer("✅ Đã thêm vào danh sách theo dõi!", show_alert=True)
    await _send_card(cb.bot, cb.message.chat.id, res["uid"], status, "", 0, avatar, "Đã thêm theo dõi:")

@router.callback_query(F.data.startswith("fb_note_"))
async def on_fb_note_btn(cb: CallbackQuery, state: FSMContext):
    uid = cb.data.replace("fb_note_", "")
    user = db.get_user(cb.from_user.id)
    if not user or not _sub_active(user):
        await cb.answer("❌ Bạn cần có gói còn hạn để dùng tính năng này.", show_alert=True)
        return
        
    await state.set_state(FBNoteState.waiting_for_note)
    await state.update_data(uid=uid)
    await cb.message.answer(f"✍️ Vui lòng nhập nội dung ghi chú cho UID <b>{uid}</b>:")
    await cb.answer()

@router.message(FBNoteState.waiting_for_note)
async def on_fb_note_input(msg: Message, state: FSMContext):
    data = await state.get_data()
    uid = data.get("uid")
    note = msg.text.strip()
    await state.clear()
    
    from .fb import check_uid, avatar_url
    res = await check_uid(uid)
    status = "live" if res["alive"] else "die"
    avatar = res.get("avatar_url") or avatar_url(uid)
    
    wid = db.add_watch(msg.chat.id, res["uid"], note, 0, 0)
    db.update_watch_status(wid, status, avatar)
    db.add_log("add", f"Thêm UID {res['uid']} kèm ghi chú", msg.chat.id, res["uid"])
    
    await msg.answer("✅ Đã lưu ghi chú và thêm vào danh sách theo dõi!")
    await _send_card(msg.bot, msg.chat.id, res["uid"], status, note, 0, avatar, "Đã thêm theo dõi (Có ghi chú):")

@router.message(F.text & ~F.text.startswith("/"))
async def on_other(msg: Message):
    username = parse_username(msg.text or "")
    if username:
        await process_tiktok_check(msg, username)
    else:
        await msg.answer(
            "💡 Gõ /tiktok &lt;username&gt; để check TikTok.\n"
            "Hoặc /help để xem hướng dẫn.",
            reply_markup=MENU,
        )


@router.message(Command("vip"))
async def on_vip(msg: Message):
    user = db.get_user(msg.chat.id)
    if not user:
        await msg.answer("Bạn chưa /start. Gõ /start trước nhé.")
        return
    
    vip_level = dict(user).get("vip_level", 0)
    vip_names = {0: "Thường (Free)", 1: "VIP 1", 2: "VIP 2", 3: "VIP 3"}
    try:
        limit = int(db.get_setting(f"vip{vip_level}_limit", [5, 50, 200, 1000][vip_level if vip_level <= 3 else 3]))
    except:
        limit = [5, 50, 200, 1000][vip_level if vip_level <= 3 else 3]
    
    name = vip_names.get(vip_level, "Thường")
    
    auto_renew_status = "Đang Bật 🟢" if dict(user).get("auto_renew") == 1 else "Đang Tắt 🔴"
    
    text = (
        f"👑 <b>THÔNG TIN HẠNG VIP</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 Tài khoản: <b>{dict(user).get('username') or dict(user).get('full_name', '')}</b>\n"
        f"💎 Hạng hiện tại: <b>{name}</b>\n"
        f"⏳ Hạn sử dụng: <b>{_sub_text(user)}</b>\n"
        f"📊 Giới hạn theo dõi: <b>{limit} mục</b>\n"
        f"🔄 Gia hạn tự động: <b>{auto_renew_status}</b>\n\n"
        f"💡 <i>Mẹo: Nhấn nút bên dưới để Bật/Tắt tính năng tự động gia hạn gói khi hết hạn (cần đủ số dư ví).</i>\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Bật/Tắt Auto-renew", callback_data="toggle_autorenew")]])
    await msg.answer(text, reply_markup=kb)

@router.callback_query(F.data == "toggle_autorenew")
async def on_toggle_autorenew(cb: CallbackQuery):
    user = db.get_user(cb.from_user.id)
    if not user: return
    new_status = 0 if dict(user).get("auto_renew") == 1 else 1
    with db._lock:
        db.get_conn().execute("UPDATE tg_users SET auto_renew=? WHERE tg_id=?", (new_status, cb.from_user.id))
        db.get_conn().commit()
    status_text = "Bật" if new_status == 1 else "Tắt"
    await cb.answer(f"Đã {status_text} tự động gia hạn!", show_alert=True)
    # Edit msg text
    new_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Bật/Tắt Auto-renew", callback_data="toggle_autorenew")]])
    try: await cb.message.edit_reply_markup(reply_markup=new_kb)
    except: pass

@router.callback_query(F.data.startswith("chart_"))
async def on_chart(cb: CallbackQuery):
    import time, json, urllib.parse
    parts = cb.data.split("_")
    if len(parts) < 4: return
    platform = parts[1]
    track_type = parts[2]
    try: track_id = int(parts[3])
    except: return
    
    c = db.get_conn()
    rows = c.execute("SELECT stat_value, created_at FROM track_history WHERE track_id=? AND platform=? AND track_type=? ORDER BY created_at ASC LIMIT 50", (track_id, platform, track_type)).fetchall()
    
    if len(rows) < 2:
        await cb.answer("Chưa đủ dữ liệu để vẽ biểu đồ (Cần ít nhất 2 lần quét).", show_alert=True)
        return
        
    labels = [time.strftime("%d/%m %H:%M", time.localtime(r["created_at"])) for r in rows]
    data = [r["stat_value"] for r in rows]
    
    chart_config = {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [{
                "label": f"Tăng trưởng {track_type} ({platform})",
                "data": data,
                "fill": False,
                "borderColor": "blue",
                "backgroundColor": "rgba(0,0,255,0.1)",
                "borderWidth": 2
            }]
        },
        "options": {
            "title": {"display": True, "text": f"Biểu đồ {track_type}"}
        }
    }
    url = "https://quickchart.io/chart?c=" + urllib.parse.quote(json.dumps(chart_config))
    await cb.message.answer_photo(URLInputFile(url), caption=f"📊 Biểu đồ lịch sử {track_type}")
    await cb.answer()

# ─── BOT MANAGER ─────────────────────────────────────────────
class BotManager:
    def __init__(self):
        self.bot: Optional[Bot] = None
        self.dp:  Optional[Dispatcher] = None
        self._task: Optional[asyncio.Task] = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, token: str) -> bool:
        await self.stop()
        if not token:
            return False
        self.bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        try:
            me = await self.bot.get_me()
        except Exception as e:
            log.error("Loi get_me: %s", e)
            self.bot = None
            return False
        try:
            await self.bot.delete_webhook(drop_pending_updates=True)
        except Exception:
            pass
        self.dp = Dispatcher()
        self.dp.include_router(router)
        await self.bot.set_my_commands(COMMANDS)
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
        b = Bot(token=token)
        try:
            me = await b.get_me()
            return me.username
        except Exception:
            return None
        finally:
            await b.session.close()


manager = BotManager()


# ─── ZALO BOT MANAGER ────────────────────────────────────────
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
            from .bot import manager
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
            from .bot import manager
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


zalo_manager = ZaloBotManager()


# --- YOUTUBE COMMANDS ---
from app.yt import parse_yt_username, fetch_yt_info, build_yt_caption, parse_yt_video_id, fetch_yt_video_info, build_yt_video_caption

@router.message(Command("yt"))
async def on_yt(msg: Message, command: CommandObject):
    username = command.args
    if not username:
        await msg.answer("💡 Gõ /yt <link_kenh_hoac_username> để xem thông tin kênh YouTube.")
        return
        
    wait = await msg.answer("⏳ Đang lấy thông tin kênh YouTube...")
    try:
        username = parse_yt_username(username)
        res = await fetch_yt_info(username)
        cap = build_yt_caption(res)
        await wait.edit_text(cap, disable_web_page_preview=True)
    except Exception as e:
        await wait.edit_text(f"❌ Lỗi: {str(e)}")

@router.message(Command("trackyt"))
async def on_trackyt(msg: Message, command: CommandObject):
    username = command.args
    if not username:
        await msg.answer("💡 Gõ /trackyt <link_kenh> để theo dõi kênh YouTube.")
        return
        
    user = db.get_user(msg.chat.id)
    vip_level = dict(user).get("vip_level", 0) if user else 0
    try: max_limit = int(db.get_setting(f"vip{vip_level}_limit", [5, 50, 200, 1000][vip_level if vip_level <= 3 else 3]))
    except: max_limit = [5, 50, 200, 1000][vip_level if vip_level <= 3 else 3]
    with db._lock: count = db.get_conn().execute("SELECT COUNT(*) FROM tracks WHERE tg_user_id=?", (msg.chat.id,)).fetchone()[0]
    with db._lock: yt_count = db.get_conn().execute("SELECT COUNT(*) FROM yt_tracks WHERE tg_user_id=?", (msg.chat.id,)).fetchone()[0]
    if count + yt_count >= max_limit:
        await msg.answer(f"❌ <b>Giới hạn hạng VIP!</b>\nHạng của bạn chỉ cho phép theo dõi tối đa <b>{max_limit}</b> mục.")
        return
        
    wait = await msg.answer("⏳ Đang xử lý theo dõi YouTube...")
    try:
        username = parse_yt_username(username)
        
        # Check limit daily
        ok, err = db.check_daily_limit(msg.chat.id)
        if not ok:
            await wait.edit_text(f"❌ {err}")
            return
            
        res = await fetch_yt_info(username)
        db.add_yt_track(msg.chat.id, msg.from_user.username or msg.from_user.full_name, res["username"], res["subscribers"], res["videos"], avatar=res["avatar"])
        db.add_log("track_add", f"Thêm theo dõi YT @{res['username']}", msg.chat.id, res["username"])
        await wait.edit_text(f"✅ Đã thêm kênh <b>{res['username']}</b> vào danh sách theo dõi YouTube!")
    except Exception as e:
        await wait.edit_text(f"❌ Lỗi: {str(e)}")

@router.message(Command("trackvyt"))
async def on_trackvyt(msg: Message, command: CommandObject):
    url = command.args
    if not url:
        await msg.answer("💡 Gõ /trackvyt <link_video_youtube> để theo dõi video.")
        return
        
    user = db.get_user(msg.chat.id)
    vip_level = dict(user).get("vip_level", 0) if user else 0
    try: max_limit = int(db.get_setting(f"vip{vip_level}_limit", [5, 50, 200, 1000][vip_level if vip_level <= 3 else 3]))
    except: max_limit = [5, 50, 200, 1000][vip_level if vip_level <= 3 else 3]
    with db._lock: count = db.get_conn().execute("SELECT COUNT(*) FROM tracks WHERE tg_user_id=?", (msg.chat.id,)).fetchone()[0]
    with db._lock: yt_count = db.get_conn().execute("SELECT COUNT(*) FROM yt_video_tracks WHERE tg_user_id=?", (msg.chat.id,)).fetchone()[0]
    if count + yt_count >= max_limit:
        await msg.answer(f"❌ <b>Giới hạn hạng VIP!</b>\nHạng của bạn chỉ cho phép theo dõi tối đa <b>{max_limit}</b> mục.")
        return
        
    wait = await msg.answer("⏳ Đang xử lý theo dõi video YouTube...")
    try:
        video_id = parse_yt_video_id(url)
        
        # Check limit daily
        ok, err = db.check_daily_limit(msg.chat.id)
        if not ok:
            await wait.edit_text(f"❌ {err}")
            return
            
        res = await fetch_yt_video_info(url)
        db.add_yt_video_track(msg.chat.id, msg.from_user.username or msg.from_user.full_name, url, res["id"], res["username"], res["desc"], res["cover"], views=res["views"], likes=res["likes"], comments=res["comments"])
        db.add_log("video_track_add", f"Thêm video YT {res['id']}", msg.chat.id, res.get("username",""))
        await wait.edit_text(f"✅ Đã thêm video YouTube <b>{res['id']}</b> vào danh sách theo dõi!")
    except Exception as e:
        await wait.edit_text(f"❌ Lỗi: {str(e)}")

# --- ZALO TRACKING COMMANDS ---
from app.zalo_checker import check_zalo_phone

@router.message(Command("zalo"))
async def on_zalo(msg: Message, command: CommandObject):
    phone = command.args
    if not phone:
        await msg.answer("💡 Gõ /zalo <sđt> để kiểm tra nhanh SĐT Zalo.")
        return
        
    wait = await msg.answer("⏳ Đang kiểm tra Zalo...")
    try:
        cookie = db.get_setting("zalo_cookie", "")
        imei = db.get_setting("zalo_imei", "")
        res = await check_zalo_phone(phone, cookie, imei)
        if res.get("live"):
            await wait.edit_text(f"✅ <b>LIVE</b>\\nSĐT: {phone}\\nTên Zalo: <b>{res['name']}</b>", parse_mode="HTML")
        else:
            await wait.edit_text(f"❌ <b>DIE / KHÔNG TÌM THẤY</b>\\nSĐT: {phone}\\nLỗi: {res.get('error', '')}", parse_mode="HTML")
    except Exception as e:
        await wait.edit_text(f"❌ Lỗi: {str(e)}")

@router.message(Command("trackzalo"))
async def on_trackzalo(msg: Message, command: CommandObject):
    phone = command.args
    if not phone:
        await msg.answer("💡 Gõ /trackzalo <sđt> để theo dõi biến động SĐT Zalo.")
        return
        
    user = db.get_user(msg.chat.id)
    vip_level = dict(user).get("vip_level", 0) if user else 0
    try: max_limit = int(db.get_setting(f"vip{vip_level}_limit", [5, 50, 200, 1000][vip_level if vip_level <= 3 else 3]))
    except: max_limit = [5, 50, 200, 1000][vip_level if vip_level <= 3 else 3]
    
    with db._lock: count = db.get_conn().execute("SELECT COUNT(*) FROM tracks WHERE tg_user_id=?", (msg.chat.id,)).fetchone()[0]
    with db._lock: z_count = db.get_conn().execute("SELECT COUNT(*) FROM zalo_tracks WHERE tg_user_id=?", (msg.chat.id,)).fetchone()[0]
    if count + z_count >= max_limit:
        await msg.answer(f"❌ <b>Giới hạn hạng VIP!</b>\\nHạng của bạn chỉ cho phép tối đa <b>{max_limit}</b> mục.", parse_mode="HTML")
        return
        
    wait = await msg.answer("⏳ Đang xử lý theo dõi SĐT Zalo...")
    try:
        ok, err = db.check_daily_limit(msg.chat.id)
        if not ok:
            await wait.edit_text(f"❌ {err}")
            return
            
        cookie = db.get_setting("zalo_cookie", "")
        imei = db.get_setting("zalo_imei", "")
        res = await check_zalo_phone(phone, cookie, imei)
        
        if res.get("live"):
            status = "LIVE"
            name = res.get("name", "")
            avatar = res.get("avatar", "")
        else:
            status = "DIE"
            name = ""
            avatar = ""
            
        db.add_zalo_track(msg.chat.id, msg.from_user.username or msg.from_user.full_name, phone, name, avatar, status)
        db.add_log("track_add", f"Thêm Zalo {phone}", msg.chat.id, phone)
        await wait.edit_text(f"✅ Đã thêm SĐT Zalo <b>{phone}</b> vào danh sách theo dõi!\\nTrạng thái hiện tại: {status}", parse_mode="HTML")
    except Exception as e:
        await wait.edit_text(f"❌ Lỗi: {str(e)}")

# --- ALERTS ---
@router.message(Command("alert"))
async def on_alert_cmd(msg: Message):
    parts = msg.text.split()
    if len(parts) < 3:
        await msg.answer("⚠️ Cú pháp: /alert <platform> <target>\nVD: /alert fb_watch 123456789")
        return
    platform = parts[1]
    target = parts[2]
    rule_id = db.create_alert_rule(str(msg.chat.id), platform, target)
    await msg.answer(f"✅ Đã thêm cảnh báo cho {platform} mục {target} (ID: {rule_id})")

@router.message(Command("alertlist"))
async def on_alertlist_cmd(msg: Message):
    rules = db.get_alert_rules(tg_id=str(msg.chat.id))
    if not rules:
        await msg.answer("Bạn không có cảnh báo nào.")
        return
    lines = ["🚨 <b>Danh sách Cảnh báo</b>\n"]
    for r in rules:
        lines.append(f"• ID {r['id']}: [{r['platform']}] {r['target']} ({r['condition']})")
    await msg.answer("\n".join(lines), parse_mode="HTML")

@router.message(Command("alertoff"))
async def on_alertoff_cmd(msg: Message):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: /alertoff <target_hoặc_id>")
        return
    target = parts[1]
    rules = db.get_alert_rules(tg_id=str(msg.chat.id))
    deleted = False
    for r in rules:
        if str(r["id"]) == target or r["target"] == target:
            db.delete_alert_rule(r["id"])
            deleted = True
    if deleted:
        await msg.answer(f"✅ Đã xoá cảnh báo cho {target}.")
    else:
        await msg.answer("❌ Không tìm thấy cảnh báo phù hợp.")
