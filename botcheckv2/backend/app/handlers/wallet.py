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

from .core import DAY, TienIchState, _is_admin, _sub_text, _tienich_back_kb, _tienich_main_kb, _tienich_text_main, log, manager, router, zalo_manager
from .common import _cart_render, _do_deposit, _do_doitien, _notify_super_subadmin_action, _show_history

class BankState(StatesGroup):
    waiting_for_amount = State()

class PayOSState(StatesGroup):
    waiting_for_amount = State()
    waiting_for_shop_amount = State()

_NAP_QUICK_AMOUNTS = [50000, 100000, 200000, 500000, 1000000]

_pending_transfer = {}

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
                try:
                    db.get_conn().execute("UPDATE tg_users SET ref_code=? WHERE tg_id=?", (ref_code, msg.chat.id))
                    db.get_conn().commit()
                except Exception as db_e:
                    log.error("Could not update ref_code: %s", db_e)
                
        ref_link = f"https://t.me/{bot_info.username}?start={msg.chat.id}"
        ref_link_code = f"https://t.me/{bot_info.username}?start={ref_code}"

        rates = db.get_ref_rates()
        _pct = lambda x: ("%g" % x) + "%"
        
        f1_count = 0
        f2_count = 0
        if user:
            try:
                c = db.get_conn()
                f1_res = c.execute("SELECT COUNT(*) FROM tg_users WHERE referrer_id=?", (msg.chat.id,)).fetchone()
                f1_count = f1_res[0] if f1_res else 0
                f2_res = c.execute("SELECT COUNT(*) FROM tg_users WHERE referrer_id IN (SELECT tg_id FROM tg_users WHERE referrer_id=?)", (msg.chat.id,)).fetchone()
                f2_count = f2_res[0] if f2_res else 0
            except Exception as cnt_e:
                log.error("Could not count refs: %s", cnt_e)

        text = (
            "🎁 <b>HỆ THỐNG GIỚI THIỆU - KIẾM TIỀN</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "🔗 <b>Link giới thiệu của bạn:</b>\n"
            f"👉 <code>{ref_link}</code>\n"
            f"Hoặc mã: <code>{ref_link_code}</code>\n\n"
            "💰 <b>Hoa hồng nhận được (tùy cấp độ):</b>\n"
            f"- F1 Hạng Đồng: <b>{_pct(rates['f1_pct'])}</b>\n"
            f"- F1 Hạng Bạc (F1 nạp từ {vnd(int(rates['f1_silver_min']))}): <b>{_pct(rates['f1_silver_pct'])}</b>\n"
            f"- F1 Hạng Vàng (F1 nạp từ {vnd(int(rates['f1_gold_min']))}): <b>{_pct(rates['f1_gold_pct'])}</b>\n"
            f"- F2 (gián tiếp): <b>{_pct(rates['f2_pct'])}</b>\n\n"
            "📊 <b>Thống kê của bạn:</b>\n"
            f"• Đã mời F1: <b>{f1_count} người</b>\n"
            f"• Đã mời F2: <b>{f2_count} người</b>\n"
            f"• Hoa hồng tổng: <b>{vnd(earnings)}</b>\n"
            f"• Đã rút: <b>{vnd(withdrawn)}</b>\n"
            f"• Khả dụng: <b>{vnd(available)}</b>\n\n"
            "💡 Lệnh hỗ trợ:\n"
            "<code>/refcode &lt;mã&gt;</code> - Đổi mã giới thiệu tùy chỉnh\n"
            "<code>/doitien &lt;số_tiền&gt;</code> - Đổi hoa hồng thành số dư (+10% Bonus)\n"
            "<code>/ruttien &lt;số_tiền&gt; &lt;Tên_NH&gt; &lt;STK&gt;</code> - Rút hoa hồng (Min 50k)"
        )
        await msg.answer(text, parse_mode="HTML")
    except Exception as e:
        import traceback
        err_msg = traceback.format_exc()
        await msg.answer(f"Lỗi ref: {e}\n\n{err_msg[:3000]}")

@router.message(Command("daily"))
async def on_daily(msg: Message):
    success, streak, reward = db.checkin_daily(msg.from_user.id)
    if success:
        await msg.answer(f"🎉 <b>Điểm danh thành công!</b>\n\nBạn nhận được <b>{reward} credits</b> check UID.\n🔥 Chuỗi điểm danh: <b>{streak} ngày</b>.", parse_mode="HTML")
    else:
        await msg.answer(f"⚠️ Bạn đã điểm danh hôm nay rồi!\n🔥 Chuỗi hiện tại: <b>{streak} ngày</b>.\nQuay lại vào ngày mai nhé.", parse_mode="HTML")

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
        
    req_id = db.create_withdrawal_request(msg.chat.id, amount, bank_info, fee)
    await notify_admin_withdrawal_request(req_id, msg.chat.id, amount, bank_info, fee)
    await msg.answer(f"✅ Đã gửi yêu cầu rút <b>{vnd(amount)}</b>.\nVui lòng chờ Admin kiểm tra và duyệt chuyển khoản!", parse_mode="HTML")

async def notify_admin_withdrawal_request(req_id: int, tg_id: int, amount: int, bank_info: str, fee: int):
    admin_tg_token = db.get_setting("admin_bot_token", "")
    admin_zalo = db.get_setting("admin_zalo_id", "")
    
    c = db.get_conn()
    user = c.execute("SELECT username, name FROM tg_users WHERE tg_id=?", (tg_id,)).fetchone()
    username_str = f"@{user['username']}" if user and user['username'] else (user['name'] if user and user['name'] else str(tg_id))
    
    actual_amount = amount - fee
    admin_msg = (
        "🔔 <b>CÓ YÊU CẦU RÚT TIỀN HOA HỒNG!</b>\n\n"
        f"🆔 Đơn rút: <b>#{req_id}</b>\n"
        f"👤 Khách: {html.escape(username_str)} (ID: <code>{tg_id}</code>)\n"
        f"💰 Số tiền yêu cầu: <b>{vnd(amount)}</b>\n"
        f"💸 Phí rút: <b>{vnd(fee)}</b>\n"
        f"💵 <b>Thực nhận chuyển khoản: {vnd(actual_amount)}</b>\n"
        f"🏦 Ngân hàng / STK: <b>{html.escape(bank_info or '')}</b>\n\n"
        "👉 Bấm nút bên dưới để Duyệt hoặc Từ chối:"
    )
    
    if admin_tg_token and admin_tg_token.strip() != (db.get_setting("bot_token") or "").strip():
        from ..admin_bot import manager as admin_manager
        admin_sender_bot = admin_manager.bot or manager.bot
    else:
        admin_sender_bot = manager.bot

    if admin_sender_bot:
        admins = _withdraw_admin_ids()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Duyệt & Đã Chuyển", callback_data=f"tg_admin_withdraw_approve_{req_id}_{tg_id}_{amount}"),
                InlineKeyboardButton(text="❌ Từ Chối", callback_data=f"tg_admin_withdraw_reject_{req_id}_{tg_id}_{amount}")
            ]
        ])
        for admin_id in admins:
            try:
                await admin_sender_bot.send_message(admin_id, admin_msg, parse_mode="HTML", reply_markup=kb)
            except Exception as e:
                log.error("Failed to notify TG admin %s for withdrawal: %s", admin_id, e)
                
    if admin_zalo and zalo_manager.running:
        zalo_kb = {
            "inline_keyboard": [
                [
                    {"text": "✅ Đã Chuyển Tiền", "callback_data": f"zalo_withdraw_approve_{req_id}"},
                    {"text": "❌ Từ Chối", "callback_data": f"zalo_withdraw_reject_{req_id}"}
                ]
            ]
        }
        asyncio.create_task(zalo_manager.send_message(admin_zalo, admin_msg, reply_markup=zalo_kb))

def _withdraw_admin_ids() -> list:
    """ID được phép duyệt/từ chối rút tiền: chủ shop + group +
    admin phụ đang có quyền Tiền tệ."""
    ids = []
    try:
        if db.get_setting("admin_tg_id"): ids.append(int(db.get_setting("admin_tg_id")))
    except: pass
    try:
        if db.get_setting("admin_tg_group_id"): ids.append(int(db.get_setting("admin_tg_group_id")))
    except: pass
    try:
        for tid in _perms.notify_extra_ids("tien"):
            if tid not in ids:
                ids.append(tid)
    except: pass
    return ids

@router.callback_query(F.data.startswith("tg_admin_withdraw_approve_"))
async def on_admin_withdraw_approve(cb: CallbackQuery):
    admins = _withdraw_admin_ids()
    if cb.message.chat.id not in admins and cb.from_user.id not in admins:
        await cb.answer("❌ Bạn không có quyền duyệt!", show_alert=True)
        return
        
    parts = cb.data.split("_")
    # tg_admin_withdraw_approve_{req_id}_{tg_id}_{amount}
    req_id = int(parts[4])
    tg_id = int(parts[5])
    amount = int(parts[6])
    
    c = db.get_conn()
    if req_id == 0:
        req = c.execute("SELECT * FROM withdrawal_requests WHERE tg_id=? AND status='pending' ORDER BY id DESC LIMIT 1", (tg_id,)).fetchone()
    else:
        req = c.execute("SELECT * FROM withdrawal_requests WHERE id=?", (req_id,)).fetchone()
        
    if not req or req["status"] != "pending":
        await cb.answer("⚠️ Đơn này đã được xử lý từ trước!", show_alert=True)
        return
        
    actual_req_id = req["id"]
    with db._lock:
        c.execute("UPDATE withdrawal_requests SET status='approved', updated_at=? WHERE id=?", (int(time.time()), actual_req_id))
        c.execute("UPDATE tg_users SET ref_withdrawn = ref_withdrawn + ? WHERE tg_id=?", (amount, tg_id))
        c.commit()
        
    await cb.answer("✅ Đã duyệt đơn rút tiền thành công!", show_alert=True)
    try:
        await cb.message.edit_text(f"{cb.message.text}\n\n✅ <b>ĐÃ DUYỆT BỞI {cb.from_user.full_name} ({vnd(amount)})</b>", parse_mode="HTML")
    except: pass
    
    # Notify customer
    try:
        cust_msg = (
            "🎉 <b>RÚT TIỀN HOA HỒNG THÀNH CÔNG!</b>\n\n"
            f"Yêu cầu rút tiền <b>#{actual_req_id}</b> của bạn đã được Admin duyệt và chuyển tiền.\n"
            f"💰 Số tiền: <b>{vnd(amount)}</b>\n"
            f"🏦 Ngân hàng / STK: <b>{html.escape(req['bank_info'] or '')}</b>\n\n"
            "Cảm ơn bạn đã đồng hành và phát triển cùng hệ thống! ❤️"
        )
        if manager.bot:
            await manager.bot.send_message(tg_id, cust_msg, parse_mode="HTML")
    except Exception as notify_err:
        log.error("Could not notify user of approved withdrawal: %s", notify_err)
    db.admin_audit_add(cb.from_user.id, cb.from_user.full_name, "duyet_rut_tien",
                       f"#{actual_req_id} {vnd(amount)} cho user {tg_id}")
    await _notify_super_subadmin_action(
        cb.from_user.id, cb.from_user.full_name,
        f"duyệt đơn rút <b>#{actual_req_id}</b> ({vnd(amount)})")

@router.callback_query(F.data.startswith("tg_admin_withdraw_reject_"))
async def on_admin_withdraw_reject(cb: CallbackQuery):
    admins = _withdraw_admin_ids()
    if cb.message.chat.id not in admins and cb.from_user.id not in admins:
        await cb.answer("❌ Bạn không có quyền từ chối!", show_alert=True)
        return
        
    parts = cb.data.split("_")
    req_id = int(parts[4])
    tg_id = int(parts[5])
    amount = int(parts[6])
    
    c = db.get_conn()
    if req_id == 0:
        req = c.execute("SELECT * FROM withdrawal_requests WHERE tg_id=? AND status='pending' ORDER BY id DESC LIMIT 1", (tg_id,)).fetchone()
    else:
        req = c.execute("SELECT * FROM withdrawal_requests WHERE id=?", (req_id,)).fetchone()
        
    if not req or req["status"] != "pending":
        await cb.answer("⚠️ Đơn này đã được xử lý từ trước!", show_alert=True)
        return
        
    actual_req_id = req["id"]
    with db._lock:
        c.execute("UPDATE withdrawal_requests SET status='rejected', updated_at=? WHERE id=?", (int(time.time()), actual_req_id))
        c.commit()
        
    await cb.answer("❌ Đã từ chối đơn rút tiền.", show_alert=True)
    try:
        await cb.message.edit_text(f"{cb.message.text}\n\n❌ <b>ĐÃ TỪ CHỐI BỞI {cb.from_user.full_name}</b>", parse_mode="HTML")
    except: pass
    
    # Notify customer
    try:
        cust_msg = (
            "❌ <b>YÊU CẦU RÚT TIỀN BỊ TỪ CHỐI</b>\n\n"
            f"Yêu cầu rút tiền hoa hồng <b>#{actual_req_id}</b> ({vnd(amount)}) của bạn đã bị Admin từ chối.\n"
            "Số dư hoa hồng của bạn vẫn được giữ nguyên.\n"
            "Vui lòng kiểm tra lại thông tin Ngân hàng / STK hoặc liên hệ Admin để được hỗ trợ."
        )
        if manager.bot:
            await manager.bot.send_message(tg_id, cust_msg, parse_mode="HTML")
    except Exception as notify_err:
        log.error("Could not notify user of rejected withdrawal: %s", notify_err)
    db.admin_audit_add(cb.from_user.id, cb.from_user.full_name, "tu_choi_rut_tien",
                       f"#{actual_req_id} {vnd(amount)} của user {tg_id}")
    await _notify_super_subadmin_action(
        cb.from_user.id, cb.from_user.full_name,
        f"từ chối đơn rút <b>#{actual_req_id}</b> ({vnd(amount)})")

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

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=btn_text, callback_data=cb_data)],
        [InlineKeyboardButton(text="⚡ Nạp tự động (PayOS)", callback_data="payos_auto")],
    ])
    
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
    success, amount, msg_text, wallet = db.use_code(code, cb.from_user.id)
    if success:
        db.credit_wallet(cb.from_user.id, amount, f"Sử dụng Giftcode: {code}", wallet)
        try:
            wlbl = db.wallet_label(wallet)
            msg_text_resp = (
                f"✅ <b>NẠP TIỀN THÀNH CÔNG!</b>\n\n"
                f"Bạn đã sử dụng mã <code>{code}</code> và được cộng <b>{db.wallet_amount_text(wallet, amount)}</b> vào {wlbl}.\n"
                f"Cảm ơn bạn đã tin tưởng dịch vụ!"
            )
            upgraded, new_vip, is_lifetime = db.check_vip_upgrade(cb.from_user.id) if db.parse_wallet(wallet) == "main" else (False, 0, False)
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
            expire_text = vn_time_str('%H:%M %d/%m', expire_at)
            
        try:
            wlbl = db.wallet_label(c["wallet"])
        except Exception:
            wlbl = "ví chính"
        text += f"• <code>{code_str}</code>: <b>{vnd(amount)}</b> → {wlbl} (Hạn: {expire_text})\n"
        keyboard.append([InlineKeyboardButton(text=f"🎁 Dùng mã {vnd(amount)}", callback_data=f"use_code_{code_str}")])
        
    text += "\n<i>Bấm nút bên dưới để sử dụng:</i>"
    await msg.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="HTML")

async def _ask_bank_wallet(msg, amount: int):
    """Hỏi user cộng tiền bank vào ví nào (sau khi admin duyệt)."""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Ví chính", callback_data=f"bank_wallet:main:{amount}")],
        [InlineKeyboardButton(text="🛒 Ví shop", callback_data=f"bank_wallet:shop:{amount}")],
    ])
    await msg.answer(f"💳 Cộng <b>{vnd(amount)}</b> vào ví nào (sau khi admin duyệt)?",
                     parse_mode="HTML", reply_markup=kb)

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
            await _ask_bank_wallet(cb.message, amount)
        await cb.answer()

@router.callback_query(F.data.startswith("bank_wallet:"))
async def on_bank_wallet(cb: CallbackQuery):
    await cb.answer()
    try:
        _, target, amount_s = cb.data.split(":")
        amount = int(amount_s)
        assert target in ("main", "shop") and amount > 0
    except Exception:
        await cb.message.answer("❌ Yêu cầu không hợp lệ.")
        return
    await process_bank_amount(cb.message, cb.from_user, amount, target)

async def process_bank_amount(msg: Message, user, amount: int, target: str = "main"):
    admin_tg_token = db.get_setting("admin_bot_token", "")
    admin_zalo = db.get_setting("admin_zalo_id", "")
    
    username_str = f"@{user.username}" if user.username else user.full_name
    admin_msg = (
        "🔔 <b>CÓ KHÁCH BÁO CHUYỂN KHOẢN!</b>\n\n"
        f"👤 Khách: {html.escape(username_str)}\n"
        f"🆔 ID Telegram: {user.id}\n"
        f"💰 Số tiền: {vnd(amount)}\n"
        f"👛 Ví: {'🛒 Ví shop' if target == 'shop' else '💰 Ví chính'}\n\n"
        f"👉 ĐỂ TẠO & PHÁT CODE, gửi lệnh:\n/phatcode {user.id} {amount}\n\n"
        f"👉 Cú pháp cộng thẳng: /topup {user.id} {amount}\n"
        "👉 Hoặc cộng thủ công trên trang Quản lý."
    )
    
    notified = False
    if admin_tg_token:
        from ..admin_bot import manager as admin_manager
        admin_sender_bot = admin_manager.bot
    else:
        from ..bot import manager as main_manager
        admin_sender_bot = main_manager.bot

    if admin_sender_bot:
        admins = []
        try:
            if db.get_setting("admin_tg_id"): admins.append(int(db.get_setting("admin_tg_id")))
        except: pass
        try:
            if db.get_setting("admin_tg_group_id"): admins.append(int(db.get_setting("admin_tg_group_id")))
        except: pass
        
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Xác nhận + Cộng tiền", callback_data=f"tg_admin_confirm_{user.id}_{amount}_{target}")]])
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
        _w = "🛒 <b>Ví shop</b>" if target == "shop" else "💰 <b>Ví chính</b>"
        await msg.answer(f"✅ Đã gửi thông báo cho Admin xác nhận khoản nạp <b>{vnd(amount)}</b> vào {_w}.\nTiền sẽ được cộng vào tài khoản của bạn sau khi Admin kiểm tra xong (thường trong vòng 1-5 phút)!")
    else:
        await msg.answer(f"✅ Đã ghi nhận báo cáo <b>{vnd(amount)}</b>.\nTiền sẽ được cộng vào tài khoản của bạn sau khi Admin kiểm tra xong!")

@router.message(BankState.waiting_for_amount)
async def on_bank_amount(msg: Message, state: FSMContext):
    _t = (msg.text or "").strip()
    if _t.startswith("/") and _t.split()[0].lower() not in ("/huy", "/cancel"):
        await state.clear()
        await msg.answer("\U0001f6ab \u0110\u00e3 h\u1ee7y thao t\u00e1c \u0111ang nh\u1eadp. B\u1ea1n g\u00f5 l\u1ea1i l\u1ec7nh v\u1eeba r\u1ed3i nh\u00e9.")
        return
    amount_str = msg.text.strip()
    try:
        amount = int(amount_str.replace(",", "").replace(".", ""))
        if amount <= 0: raise ValueError()
    except:
        await msg.answer("❌ Số tiền không hợp lệ. Vui lòng nhập lại số tiền (ví dụ: 50000):")
        return
        
    await state.clear()
    await _ask_bank_wallet(msg, amount)

def _parse_payos_amount(raw: str) -> int:
    s = (raw or "").strip().replace(",", "").replace(".", "").replace(" ", "")
    s = s.replace("k", "000").replace("K", "000")
    amount = int(s)
    if amount <= 0:
        raise ValueError()
    return amount

async def _make_payos_order(tg_id: int, amount: int, target: str = "main"):
    """Tạo (hoặc dùng lại) đơn PayOS. Trả (order_code, checkout_url, qr_code, reused)."""
    if target not in ("main", "shop"):
        target = "main"
    from .. import payos as payos_mod
    if not payos_mod.is_configured():
        raise payos_mod.PayOSError("Admin chưa cấu hình PayOS, vui lòng nạp bằng /bank.")
    if amount < payos_mod.PAYOS_MIN_AMOUNT:
        raise payos_mod.PayOSError(
            f"Số tiền tối thiểu là {vnd(payos_mod.PAYOS_MIN_AMOUNT)}.")
    existing = db.get_user_pending_payos_order(tg_id, payos_mod.ORDER_TTL, target)
    if existing and existing["checkout_url"] and int(existing["amount"]) == int(amount):
        # Đơn dùng lại: lấy QR đã lưu lúc tạo đơn (API đọc trạng thái không trả qrCode)
        qr_code = ""
        try:
            qr_code = existing["qr_code"] or ""
        except Exception:
            pass
        return int(existing["order_code"]), existing["checkout_url"], qr_code, True
    if existing and int(existing["amount"]) != int(amount):
        # Số tiền khác -> hủy đơn cũ, tạo đơn mới (tránh link thanh toán sai tiền)
        try:
            await payos_mod.cancel_payment_link(int(existing["order_code"]))
        except Exception:
            pass
        db.mark_payos_status(int(existing["order_code"]), "CANCELLED")
    import sqlite3
    description = f"NAP{tg_id}"[-25:]
    return_url, cancel_url = payos_mod.get_return_urls()
    data = None
    for _ in range(5):
        order_code = payos_mod.new_order_code(tg_id)
        data = await payos_mod.create_payment_link(
            order_code, amount, description, return_url, cancel_url)
        try:
            db.create_payos_order(order_code, tg_id, amount,
                                  data.get("paymentLinkId", ""), data.get("checkoutUrl", ""),
                                  data.get("qrCode", "") or "", target)
            break
        except sqlite3.IntegrityError:
            continue  # mã đơn trùng (cực hiếm) -> sinh mã mới, tạo link mới
    return order_code, data.get("checkoutUrl", ""), data.get("qrCode", "") or "", False

def _qr_code_png(qr_text: str):
    """Dựng ảnh PNG mã QR VietQR từ chuỗi PayOS trả về."""
    import io
    import qrcode
    qr = qrcode.QRCode(border=2)
    qr.add_data(qr_text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

async def _send_payos_invoice(msg: Message, tg_id: int, amount: int,
                              order_code: int, checkout_url: str,
                              qr_code: str, reused: bool, target: str = "main"):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        *([[InlineKeyboardButton(text="🌐 Mở trang thanh toán", url=checkout_url)]]
          if checkout_url else []),
        [InlineKeyboardButton(text="❌ Hủy đơn", callback_data=f"payos_cancel:{order_code}")],
    ])
    wallet_txt = "🛒 <b>Ví shop</b> <i>(mua tài khoản)</i>" if target == "shop" else "💰 <b>Ví chính</b> <i>(check UID • mua gói)</i>"
    caption = (
        "💳 <b>NẠP TIỀN TỰ ĐỘNG</b>" + (" <i>(dùng lại đơn đang chờ)</i>" if reused else "") + "\n"
        "━━━━━━━━━━━━━━\n"
        f"💵 Số tiền: <b>{vnd(amount)}</b>\n"
        f"👛 Nạp vào: {wallet_txt}\n"
        f"🧾 Mã đơn: <code>{order_code}</code>\n"
        "━━━━━━━━━━━━━━\n"
        "① Mở app ngân hàng, quét <b>mã QR</b> bên dưới\n"
        "② Thanh toán đúng số tiền\n"
        "③ Tiền <b>tự động cộng</b> vào ví (thường dưới 1 phút) ⚡\n\n"
        "<i>⏰ Đơn tự hủy sau 45 phút nếu chưa thanh toán.</i>"
    )
    if qr_code:
        try:
            photo = BufferedInputFile(_qr_code_png(qr_code).read(),
                                      filename=f"payos_{order_code}.png")
            await msg.answer_photo(photo, caption=caption,
                                   parse_mode="HTML", reply_markup=kb)
            return
        except Exception:
            pass
    await msg.answer(caption, parse_mode="HTML", reply_markup=kb)

async def _ask_payos_wallet(msg, amount: int):
    """Hỏi user nạp vào ví nào trước khi tạo đơn PayOS."""
    u = db.get_user(msg.chat.id if hasattr(msg, "chat") else msg.from_user.id)
    main_bal = int(u["balance"] or 0) if u else 0
    shop_bal = int(u["shop_balance"] or 0) if u and "shop_balance" in u.keys() else 0
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"💰 Ví chính — đang có {vnd(main_bal)}",
            callback_data=f"payos_wallet:main:{amount}")],
        [InlineKeyboardButton(
            text=f"🛒 Ví shop — đang có {vnd(shop_bal)}",
            callback_data=f"payos_wallet:shop:{amount}")],
    ])
    await msg.answer(
        f"💳 <b>NẠP {vnd(amount)}</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"Chọn ví muốn nạp vào:",
        parse_mode="HTML", reply_markup=kb)

async def _nap_amount_picker(msg: Message, target: str = "main"):
    """Bàn phím chọn nhanh số tiền nạp."""
    rows = []
    row = []
    for a in _NAP_QUICK_AMOUNTS:
        row.append(InlineKeyboardButton(
            text=f"{a // 1000}K", callback_data=f"nap_amt:{target}:{a}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="✏️ Nhập số khác",
                                      callback_data=f"nap_custom:{target}")])
    wallet_txt = "🛒 <b>Ví shop</b>" if target == "shop" else "💰 <b>Ví chính</b>"
    await msg.answer(
        f"💳 <b>NẠP TIỀN</b> → {wallet_txt}\n"
        f"━━━━━━━━━━━━━━\n"
        f"Chọn nhanh số tiền bên dưới, hoặc nhập số khác:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

@router.callback_query(F.data.startswith("nap_amt:"))
async def on_nap_amt(cb: CallbackQuery):
    await cb.answer()
    try:
        _, target, amount_s = cb.data.split(":")
        amount = int(amount_s)
        assert target in ("main", "shop") and amount > 0
    except Exception:
        await cb.message.answer("❌ Yêu cầu không hợp lệ, gõ /nap lại nhé.")
        return
    if target == "shop":
        try:
            order_code, checkout_url, qr_code, reused = await _make_payos_order(
                cb.from_user.id, amount, "shop")
        except Exception as e:
            await cb.message.answer(f"❌ {e}")
            return
        await _send_payos_invoice(cb.message, cb.from_user.id, amount,
                                  order_code, checkout_url, qr_code, reused, "shop")
    else:
        await _ask_payos_wallet(cb.message, amount)

@router.callback_query(F.data.startswith("nap_custom:"))
async def on_nap_custom(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    target = cb.data.split(":", 1)[1]
    await cb.message.answer("✍️ Nhập <b>số tiền</b> muốn nạp (ví dụ: 50000):",
                            parse_mode="HTML")
    if target == "shop":
        await state.set_state(PayOSState.waiting_for_shop_amount)
    else:
        await state.set_state(PayOSState.waiting_for_amount)

@router.message(Command("nap"))
async def on_nap(msg: Message, state: FSMContext):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) > 1:
        try:
            amount = _parse_payos_amount(parts[1])
        except Exception:
            await msg.answer("❌ Số tiền không hợp lệ. Ví dụ: <code>/nap 50000</code>")
            return
        await _ask_payos_wallet(msg, amount)
    else:
        await _nap_amount_picker(msg, "main")

@router.callback_query(F.data.startswith("payos_wallet:"))
async def on_payos_wallet(cb: CallbackQuery):
    await cb.answer()
    try:
        _, target, amount_s = cb.data.split(":")
        amount = int(amount_s)
        assert target in ("main", "shop") and amount > 0
    except Exception:
        await cb.message.answer("❌ Yêu cầu không hợp lệ, gõ /nap lại nhé.")
        return
    try:
        order_code, checkout_url, qr_code, reused = await _make_payos_order(
            cb.from_user.id, amount, target)
    except Exception as e:
        await cb.message.answer(f"❌ {e}")
        return
    await _send_payos_invoice(cb.message, cb.from_user.id, amount,
                              order_code, checkout_url, qr_code, reused, target)

@router.message(Command("napshop"))
async def on_napshop(msg: Message):
    """Nạp thẳng vào ví shop (mua acc)."""
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) > 1:
        try:
            amount = _parse_payos_amount(parts[1])
        except Exception:
            await msg.answer("❌ Số tiền không hợp lệ. Ví dụ: <code>/napshop 50000</code>")
            return
        try:
            order_code, checkout_url, qr_code, reused = await _make_payos_order(
                msg.from_user.id, amount, "shop")
        except Exception as e:
            await msg.answer(f"❌ {e}")
            return
        await _send_payos_invoice(msg, msg.from_user.id, amount,
                                  order_code, checkout_url, qr_code, reused, "shop")
    else:
        await _nap_amount_picker(msg, "shop")

@router.message(PayOSState.waiting_for_amount)
async def on_nap_amount(msg: Message, state: FSMContext):
    _t = (msg.text or "").strip()
    if _t.startswith("/") and _t.split()[0].lower() not in ("/huy", "/cancel"):
        await state.clear()
        await msg.answer("\U0001f6ab \u0110\u00e3 h\u1ee7y thao t\u00e1c \u0111ang nh\u1eadp. B\u1ea1n g\u00f5 l\u1ea1i l\u1ec7nh v\u1eeba r\u1ed3i nh\u00e9.")
        return
    try:
        amount = _parse_payos_amount(msg.text)
    except Exception:
        await msg.answer("❌ Số tiền không hợp lệ. Vui lòng nhập lại (ví dụ: 50000):")
        return
    await state.clear()
    await _ask_payos_wallet(msg, amount)

@router.message(PayOSState.waiting_for_shop_amount)
async def on_napshop_amount(msg: Message, state: FSMContext):
    _t = (msg.text or "").strip()
    if _t.startswith("/") and _t.split()[0].lower() not in ("/huy", "/cancel"):
        await state.clear()
        await msg.answer("\U0001f6ab \u0110\u00e3 h\u1ee7y thao t\u00e1c \u0111ang nh\u1eadp. B\u1ea1n g\u00f5 l\u1ea1i l\u1ec7nh v\u1eeba r\u1ed3i nh\u00e9.")
        return
    try:
        amount = _parse_payos_amount(msg.text)
    except Exception:
        await msg.answer("❌ Số tiền không hợp lệ. Vui lòng nhập lại (ví dụ: 50000):")
        return
    await state.clear()
    try:
        order_code, checkout_url, qr_code, reused = await _make_payos_order(
            msg.from_user.id, amount, "shop")
    except Exception as e:
        await msg.answer(f"❌ {e}")
        return
    await _send_payos_invoice(msg, msg.from_user.id, amount,
                              order_code, checkout_url, qr_code, reused, "shop")

@router.callback_query(F.data == "payos_auto")
async def on_payos_auto(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await cb.message.answer("✍️ Nhập <b>số tiền</b> muốn nạp tự động (ví dụ: 50000):")
    await state.set_state(PayOSState.waiting_for_amount)

@router.callback_query(F.data.startswith("payos_cancel:"))
async def on_payos_cancel(cb: CallbackQuery):
    from .. import payos as payos_mod
    try:
        order_code = int(cb.data.split(":")[1])
    except Exception:
        await cb.answer("❌ Đơn không hợp lệ.", show_alert=True)
        return
    order = db.get_payos_order(order_code)
    if not order or int(order["tg_id"]) != cb.from_user.id:
        await cb.answer("❌ Không tìm thấy đơn.", show_alert=True)
        return
    if str(order["status"]).upper() != "PENDING":
        await cb.answer("Đơn này đã được xử lý rồi.", show_alert=True)
        return
    try:
        await payos_mod.cancel_payment_link(order_code, "User hủy trên bot")
    except Exception:
        pass
    db.mark_payos_status(order_code, "CANCELLED")
    await cb.answer("✅ Đã hủy đơn.", show_alert=True)
    try:
        await cb.message.edit_text(
            f"❌ <b>ĐÃ HỦY ĐƠN</b>\n\n🧾 Mã đơn: <code>{order_code}</code>",
            parse_mode="HTML", reply_markup=None)
    except Exception:
        pass

@router.message(Command("payos"))
async def on_payos_status(msg: Message):
    """Admin: xem trạng thái cấu hình + thống kê PayOS hôm nay."""
    from ..admin_bot import is_admin
    if not is_admin(msg.chat.id, msg.from_user.id):
        return
    from .. import payos as payos_mod
    cid, _, _ = payos_mod.get_creds()
    cfg = "✅ Đã cấu hình" if payos_mod.is_configured() else "❌ Chưa cấu hình"
    masked = (cid[:4] + "…" + cid[-4:]) if cid and len(cid) > 8 else ("(trống)" if not cid else "…")
    st = db.payos_stats_today()
    ret_url, cancel_url = payos_mod.get_return_urls()
    await msg.answer(
        "💳 <b>PAYOS — TRẠNG THÁI</b>\n\n"
        f"• Cấu hình: <b>{cfg}</b>\n"
        f"• Client ID: <code>{masked}</code>\n"
        f"• Return URL: <code>{ret_url}</code>\n\n"
        f"📊 <b>Hôm nay:</b> {st['paid']} đơn thành công "
        f"({vnd(st['paid_amount'])}), {st['pending']} đơn đang chờ\n\n"
        "<i>Cấu hình key: </i><code>/payosset &lt;client_id&gt; &lt;api_key&gt; &lt;checksum_key&gt;</code>",
        parse_mode="HTML",
    )

@router.message(Command("payosset"))
async def on_payosset(msg: Message):
    """Chủ shop: lưu key PayOS (tin nhắn chứa key sẽ bị xóa ngay)."""
    if not _perms.is_super(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    try:
        await msg.delete()
    except Exception:
        pass
    if len(parts) != 4:
        await msg.answer(
            "⚠️ Cú pháp: <code>/payosset &lt;client_id&gt; &lt;api_key&gt; &lt;checksum_key&gt;</code>\n"
            "<i>Tin nhắn chứa key đã được xóa. Lấy 3 key này trong trang payos (Kênh thanh toán → Thông tin tích hợp).</i>",
            parse_mode="HTML")
        return
    _, cid, akey, ckey = parts
    db.set_setting("payos_client_id", cid.strip())
    db.set_setting("payos_api_key", akey.strip())
    db.set_setting("payos_checksum_key", ckey.strip())
    await msg.answer("✅ Đã lưu cấu hình PayOS (tin nhắn chứa key đã bị xóa).\n"
                     "Dùng /payos để kiểm tra trạng thái.")

@router.message(Command("balance"))
async def on_balance(msg: Message):
    user = db.get_user(msg.chat.id)
    if not user:
        await msg.answer("Bạn chưa /start. Gõ /start trước nhé.")
        return
    credits = db.get_credits(msg.from_user.id)
    shop_bal = int(user["shop_balance"] or 0) if "shop_balance" in user.keys() else 0
    main_bal = int(user["balance"] or 0)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Nạp ví chính", callback_data="wal_nap:main"),
         InlineKeyboardButton(text="🛒 Nạp ví shop", callback_data="wal_nap:shop")],
        [InlineKeyboardButton(text="📜 Lịch sử giao dịch", callback_data="wal_hist")],
    ])
    await msg.answer(
        f"👛 <b>VÍ CỦA BẠN</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"💰 <b>Ví chính</b>\n"
        f"      <b>{vnd(main_bal)}</b>\n"
        f"      <i>Check UID • mua gói • credits</i>\n\n"
        f"🛒 <b>Ví shop</b>\n"
        f"      <b>{vnd(shop_bal)}</b>\n"
        f"      <i>Mua tài khoản tự động</i>\n\n"
        f"⚡ <b>Credits:</b> {credits} lượt\n"
        f"👑 <b>Gói:</b> {_sub_text(user)}\n"
        f"━━━━━━━━━━━━━━",
        parse_mode="HTML", reply_markup=kb,
    )

@router.callback_query(F.data.startswith("wal_nap:"))
async def on_wal_nap(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    target = cb.data.split(":", 1)[1]
    await _nap_amount_picker(cb.message, target if target == "shop" else "main")

@router.callback_query(F.data == "wal_hist")
async def on_wal_hist(cb: CallbackQuery):
    await cb.answer()
    await cb.message.answer("📜 Xem lịch sử giao dịch bằng lệnh /lichsu nhé.")

@router.message(Command("sodu"))
async def on_sodu(msg: Message):
    user = db.get_user(msg.chat.id)
    if not user:
        await msg.answer("Bạn chưa /start. Gõ /start trước nhé.")
        return
    credits = db.get_credits(msg.from_user.id)
    shop_bal = int(user["shop_balance"] or 0) if "shop_balance" in user.keys() else 0
    main_bal = int(user["balance"] or 0)
    points = db.loyalty_get(msg.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Nạp ví chính", callback_data="wal_nap:main"),
         InlineKeyboardButton(text="🛒 Nạp ví shop", callback_data="wal_nap:shop")],
    ])
    await msg.answer(
        "👛 <b>SỐ DƯ CỦA BẠN</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"💰 <b>Ví chính:</b> {vnd(main_bal)}\n"
        "      <i>→ Check UID, mua gói/VIP, mua credits</i>\n\n"
        f"🛒 <b>Ví shop:</b> {vnd(shop_bal)}\n"
        "      <i>→ Mua acc, đặt cọc, hộp mù (nạp bằng /napshop)</i>\n\n"
        f"⚡ <b>Credits:</b> {credits} lượt\n"
        "      <i>→ Check UID hàng loạt (/checkfile, /muacredit để mua thêm)</i>\n\n"
        f"🎁 <b>Điểm:</b> {points} điểm\n"
        "      <i>→ Nhận sau mỗi đơn mua acc, đổi quà bằng /doiqua</i>\n"
        "━━━━━━━━━━━━━━",
        parse_mode="HTML", reply_markup=kb,
    )

@router.message(Command("sub"))
async def on_sub(msg: Message):
    p1 = int(db.get_setting("price_1m", "0") or 0)
    bonus = _sub_credit_bonus(p1)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"1 tháng - {vnd(p1)}", callback_data="sub:1")],
            [InlineKeyboardButton(text=f"2 tháng - {vnd(p1 * 2)}", callback_data="sub:2")],
            [InlineKeyboardButton(text=f"3 tháng - {vnd(p1 * 3)}", callback_data="sub:3")],
        ]
    )
    extra = f"\n🎁 <i>Mỗi tháng được tặng kèm <b>{bonus}</b> credits (90% so với mua gói credit thẳng).</i>" if bonus > 0 else ""
    await msg.answer("Chọn gói muốn mua / gia hạn:" + extra, parse_mode="HTML", reply_markup=kb)

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
    if not db.adjust_balance(cb.from_user.id, -cost, f"Mua gói {months} tháng"):
        await cb.answer("Số dư không đủ.", show_alert=True)
        return
    base = max(now(), user["sub_until"] or 0)
    db.set_sub_until(cb.from_user.id, base + months * 30 * DAY)
    db.add_log("sub", f"Mua {months} tháng (-{cost})", cb.from_user.id)
    # Tặng credits kèm gói tháng: 90% so với mua gói credit thẳng
    bonus = _sub_credit_bonus(p1) * months
    bonus_txt = ""
    if bonus > 0:
        total_cr = db.add_credits(cb.from_user.id, bonus, f"Tặng kèm gói {months} tháng")
        bonus_txt = f"\n🎁 Tặng kèm: <b>{bonus}</b> credits (tổng: {total_cr})"
    u2 = db.get_user(cb.from_user.id)
    await cb.message.answer(
        f"Đã kích hoạt gói <b>{months} tháng</b>.\n"
        f"Số dư còn: <b>{vnd(u2['balance'])}</b>\nGói: <b>{_sub_text(u2)}</b>{bonus_txt}",
        parse_mode="HTML",
    )
    await cb.answer("Thành công")

@router.message(TienIchState.waiting_for_giftcode)
async def on_tienich_giftcode(msg: Message, state: FSMContext):
    if (msg.text or "").strip().lower() in ("/huy", "/cancel"):
        await state.clear()
        await msg.answer("❌ Đã hủy nhập giftcode.")
        return
    code = (msg.text or "").strip()
    await state.clear()
    if not code:
        await msg.answer("❌ Mã trống, thử lại nhé.")
        return
    success, amount, msg_text, wallet = db.use_code(code, msg.from_user.id)
    if success:
        db.credit_wallet(msg.from_user.id, amount, f"Sử dụng Giftcode: {code}", wallet)
        if db.parse_wallet(wallet) == "main":
            db.check_vip_upgrade(msg.from_user.id)
        await msg.answer(
            f"✅ <b>NẠP TIỀN THÀNH CÔNG!</b>\n\n"
            f"Bạn đã dùng mã <code>{html.escape(code)}</code> và được cộng <b>{db.wallet_amount_text(wallet, amount)}</b> vào {db.wallet_label(wallet)}.",
            parse_mode="HTML")
    else:
        await msg.answer(f"❌ {html.escape(msg_text)}", parse_mode="HTML")

@router.message(TienIchState.waiting_for_refcode)
async def on_tienich_refcode(msg: Message, state: FSMContext):
    if (msg.text or "").strip().lower() in ("/huy", "/cancel"):
        await state.clear()
        await msg.answer("❌ Đã hủy đổi mã giới thiệu.")
        return
    code = (msg.text or "").strip()
    await state.clear()
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
    await msg.answer(f"✅ Đã đổi mã giới thiệu thành công: <code>{html.escape(code)}</code>", parse_mode="HTML")

@router.message(TienIchState.waiting_withdraw_amount)
async def on_tienich_wd_amount(msg: Message, state: FSMContext):
    if (msg.text or "").strip().lower() in ("/huy", "/cancel"):
        await state.clear()
        await msg.answer("❌ Đã hủy rút tiền.")
        return
    try:
        amount = int((msg.text or "").replace(",", "").replace(".", "").replace("k", "000").replace("K", "000").strip())
    except Exception:
        await msg.answer("❌ Số tiền không hợp lệ! Gửi lại số tiền (VD: 50000).")
        return
    if amount < 50000:
        await msg.answer("❌ Số tiền rút tối thiểu là 50,000 VNĐ.")
        return
    await state.update_data(wd_amount=amount)
    await state.set_state(TienIchState.waiting_withdraw_bank)
    await msg.answer("💸 <b>RÚT HOA HỒNG</b> (bước 2/3)\n\nGửi <b>tên ngân hàng</b> (VD: MBBank, Vietcombank).\nGõ /huy để hủy.", parse_mode="HTML")

@router.message(TienIchState.waiting_withdraw_bank)
async def on_tienich_wd_bank(msg: Message, state: FSMContext):
    if (msg.text or "").strip().lower() in ("/huy", "/cancel"):
        await state.clear()
        await msg.answer("❌ Đã hủy rút tiền.")
        return
    bank = (msg.text or "").strip()
    if not bank:
        await msg.answer("❌ Tên ngân hàng trống, gửi lại nhé.")
        return
    await state.update_data(wd_bank=bank)
    await state.set_state(TienIchState.waiting_withdraw_stk)
    await msg.answer("💸 <b>RÚT HOA HỒNG</b> (bước 3/3)\n\nGửi <b>số tài khoản</b> ngân hàng.\nGõ /huy để hủy.", parse_mode="HTML")

@router.message(TienIchState.waiting_withdraw_stk)
async def on_tienich_wd_stk(msg: Message, state: FSMContext):
    if (msg.text or "").strip().lower() in ("/huy", "/cancel"):
        await state.clear()
        await msg.answer("❌ Đã hủy rút tiền.")
        return
    stk = (msg.text or "").strip()
    if not stk:
        await msg.answer("❌ STK trống, gửi lại nhé.")
        return
    data = await state.get_data()
    await state.clear()
    amount = data.get("wd_amount")
    bank_info = f"{data.get('wd_bank')} - {stk}"
    # tái sử dụng logic kiểm tra của on_ruttien
    c = db.get_conn()
    user = c.execute("SELECT ref_earnings, ref_withdrawn FROM tg_users WHERE tg_id=?", (msg.chat.id,)).fetchone()
    d = dict(user) if user else {}
    available = (d.get("ref_earnings") or 0) - (d.get("ref_withdrawn") or 0)
    import datetime as _dt
    month_start = int(_dt.datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
    cnt = c.execute("SELECT COUNT(*) as c FROM withdrawal_requests WHERE tg_id=? AND created_at >= ?", (msg.chat.id, month_start)).fetchone()["c"]
    fee = 10000 if cnt >= 2 else 0
    if available < amount + fee:
        await msg.answer(f"❌ Số dư khả dụng không đủ! (Khả dụng: {vnd(available)}, Cần: {vnd(amount + fee)} bao gồm phí {vnd(fee)} nếu có)")
        return
    req_id = db.create_withdrawal_request(msg.chat.id, amount, bank_info, fee)
    await notify_admin_withdrawal_request(req_id, msg.chat.id, amount, bank_info, fee)
    await msg.answer(f"✅ Đã gửi yêu cầu rút <b>{vnd(amount)}</b>.\n🏦 {html.escape(bank_info)}\nVui lòng chờ Admin kiểm tra và duyệt chuyển khoản!", parse_mode="HTML")

@router.message(TienIchState.waiting_transfer_uid)
async def on_tienich_tf_uid(msg: Message, state: FSMContext):
    if (msg.text or "").strip().lower() in ("/huy", "/cancel"):
        await state.clear()
        await msg.answer("❌ Đã hủy chuyển tiền.")
        return
    try:
        to_id = int((msg.text or "").strip())
    except ValueError:
        await msg.answer("❌ User ID phải là số. Gửi lại nhé.")
        return
    if to_id == msg.from_user.id:
        await msg.answer("❌ Không thể chuyển tiền cho chính mình.")
        return
    recv = db.get_user(to_id)
    if not recv:
        await msg.answer("❌ Không tìm thấy người nhận (user chưa từng dùng bot).")
        return
    await state.update_data(tf_to_id=to_id)
    await state.set_state(TienIchState.waiting_transfer_amount)
    await msg.answer(
        f"↔️ <b>CHUYỂN TIỀN</b> (bước 2/2)\n\nNgười nhận: <b>{html.escape(recv['name'] or '')}</b> (<code>{to_id}</code>)\n"
        f"Gửi <b>số tiền</b> muốn chuyển.\nGõ /huy để hủy.",
        parse_mode="HTML")

@router.message(TienIchState.waiting_transfer_amount)
async def on_tienich_tf_amount(msg: Message, state: FSMContext):
    if (msg.text or "").strip().lower() in ("/huy", "/cancel"):
        await state.clear()
        await msg.answer("❌ Đã hủy chuyển tiền.")
        return
    try:
        amount = int((msg.text or "").replace(".", "").replace(",", "").replace("đ", "").strip())
    except ValueError:
        await msg.answer("❌ Số tiền phải là số. Gửi lại nhé.")
        return
    if amount <= 0:
        await msg.answer("❌ Số tiền phải lớn hơn 0.")
        return
    data = await state.get_data()
    await state.clear()
    to_id = data.get("tf_to_id")
    recv = db.get_user(to_id)
    if not recv:
        await msg.answer("❌ Không tìm thấy người nhận.")
        return
    _pending_transfer[(msg.chat.id, msg.from_user.id)] = (to_id, amount)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Xác nhận chuyển", callback_data="tf_yes"),
        InlineKeyboardButton(text="❌ Hủy", callback_data="tf_no"),
    ]])
    await msg.answer(
        f"💸 Xác nhận chuyển <b>{vnd(amount)}</b> cho "
        f"{html.escape(recv['name'] or '')} (<code>{to_id}</code>)?",
        reply_markup=kb, parse_mode="HTML")

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

@router.message(Command("dailyreport"))
@router.message(Command("report"))
async def on_daily_report_cmd(msg: Message):
    """Cấu hình giờ nhận báo cáo tự động định kỳ hằng ngày."""
    parts = msg.text.split(maxsplit=1)
    raw_user = db.get_user(msg.from_user.id)
    user = dict(raw_user) if raw_user else {}
    curr_hour = user.get("daily_report_hour", -1)

    if len(parts) < 2:
        status_str = "🔴 TẮT" if curr_hour < 0 else f"🟢 BẬT (Lúc {curr_hour:02d}:00 hàng ngày)"
        text = (
            "☀️ <b>CẤU HÌNH BÁO CÁO TỰ ĐỘNG HẰNG NGÀY</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Trạng thái hiện tại: <b>{status_str}</b>\n\n"
            "<b>Cú pháp cài đặt:</b>\n"
            "• <code>/dailyreport &lt;giờ&gt;</code> — Đặt giờ báo cáo (0 đến 23)\n"
            "• <code>/dailyreport off</code> — Tắt báo cáo tự động\n\n"
            "<i>Ví dụ: <code>/dailyreport 8</code> để nhận báo cáo vào 8h00 sáng mỗi ngày.</i>"
        )
        buttons = [
            [
                InlineKeyboardButton(text="☀️ 08:00 Sáng", callback_data="set_report_8"),
                InlineKeyboardButton(text="🌙 20:00 Tối", callback_data="set_report_20"),
            ],
            [
                InlineKeyboardButton(text="🔴 Tắt Báo Cáo", callback_data="set_report_off"),
            ]
        ]
        await msg.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        return

    arg = parts[1].strip().lower()
    if arg in ("off", "tat", "tắt", "stop"):
        db.set_daily_report_hour(msg.from_user.id, -1)
        await msg.answer("🔴 Đã <b>TẮT</b> báo cáo tự động định kỳ.", parse_mode="HTML")
        return

    try:
        hour = int(arg)
        if not (0 <= hour <= 23):
            raise ValueError()
    except ValueError:
        await msg.answer("❌ Giờ không hợp lệ! Vui lòng chọn số từ 0 đến 23 (Ví dụ: /dailyreport 8)")
        return

    db.set_daily_report_hour(msg.from_user.id, hour)
    await msg.answer(f"✅ Đã bật báo cáo tự động hằng ngày vào <b>{hour:02d}:00</b>!", parse_mode="HTML")

@router.message(Command("muagoi"))
async def on_muagoi(msg: Message):
    """Hiển thị bảng giá và mua gói theo ngày bằng số dư."""
    parts = msg.text.split(maxsplit=1)

    price_1d = int(db.get_setting("price_1d") or 0)
    price_3d = int(db.get_setting("price_3d") or 0)
    price_5d = int(db.get_setting("price_5d") or 0)
    price_7d = int(db.get_setting("price_7d") or 0)

    raw_user = db.get_user(msg.from_user.id)
    user = dict(raw_user) if raw_user else {}
    balance = user.get("balance") or 0
    pkgs = [(d, p) for d, p in [(1, price_1d), (3, price_3d), (5, price_5d), (7, price_7d)] if p > 0]

    if len(parts) < 2:
        # Hiện bảng giá kèm nút bấm
        if not pkgs:
            await msg.answer("❌ Hệ thống chưa cấu hình giá gói ngày. Vui lòng liên hệ Admin!")
            return
        sub_until = user.get("sub_until") or 0
        sub_text = "Không có" if not sub_until else (
            "Vĩnh viễn" if sub_until > 9000000000
            else time.strftime("%d/%m/%Y %H:%M", time.localtime(sub_until))
        )
        text = (
            "📦 <b>MUA GÓI SỬ DỤNG THEO NGÀY</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💳 Số dư hiện tại: <b>{vnd(balance)}</b>\n"
            f"⏳ Hạn hiện tại: <b>{sub_text}</b>\n\n"
            "<b>📋 Các gói:</b>\n"
        )
        buttons = []
        for days, price in pkgs:
            per_day = price // days
            ok = balance >= price
            status = "✅" if ok else "❌"
            text += f"{status} <b>{days} ngày</b> — {vnd(price)} (~{vnd(per_day)}/ngày)\n"
            buttons.append([InlineKeyboardButton(
                text=f"{status} {days} ngày — {vnd(price)}",
                callback_data=f"buy_pkg_{days}_{price}"
            )])
        text += "\n💡 <i>Gói cộng dồn nếu bạn đang còn hạn. Bấm nút để mua ngay:</i>"
        await msg.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        return

    # /muagoi <số_ngày>
    try:
        days = int(parts[1].strip())
        if days not in (1, 3, 5, 7):
            raise ValueError()
    except ValueError:
        await msg.answer("❌ Số ngày hợp lệ: 1, 3, 5, 7\nVD: /muagoi 7\nGõ /muagoi để xem bảng giá.")
        return

    price_map = {1: price_1d, 3: price_3d, 5: price_5d, 7: price_7d}
    price = price_map[days]
    if not price:
        await msg.answer(f"❌ Gói {days} ngày chưa được cấu hình. Liên hệ Admin!")
        return
    await _do_buy_package(msg, msg.from_user.id, days, price)

@router.callback_query(lambda c: c.data and c.data.startswith("buy_pkg_"))
async def on_buy_pkg_cb(cb: CallbackQuery):
    """Xử lý khi bấm nút mua gói inline."""
    try:
        parts = cb.data.split("_")
        days = int(parts[2])
        price = int(parts[3])
    except (ValueError, IndexError):
        await cb.answer("Lỗi dữ liệu!", show_alert=True)
        return
    raw_user = db.get_user(cb.from_user.id)
    user = dict(raw_user) if raw_user else {}
    if not user or (user.get("balance") or 0) < price:
        await cb.answer(f"❌ Số dư không đủ! Cần {vnd(price)}", show_alert=True)
        return
    await cb.answer()
    await _do_buy_package(cb.message, cb.from_user.id, days, price, edit_msg=True)

async def _do_buy_package(target_msg, tg_id: int, days: int, price: int, edit_msg: bool = False):
    """Logic mua gói chung — trừ số dư, gia hạn sub_until."""
    raw_user = db.get_user(tg_id)
    if not raw_user:
        await target_msg.answer("❌ Không tìm thấy tài khoản!")
        return
    user = dict(raw_user)
    balance = user.get("balance") or 0
    if balance < price:
        txt = (
            f"❌ <b>Số dư không đủ!</b>\n\n"
            f"💳 Số dư: <b>{vnd(balance)}</b>\n"
            f"💰 Cần: <b>{vnd(price)}</b>\n"
            f"🔺 Thiếu: <b>{vnd(price - balance)}</b>\n\n"
            "Nạp thêm bằng lệnh /bank"
        )
        await target_msg.answer(txt, parse_mode="HTML")
        return

    # Trừ tiền
    if not db.adjust_balance(tg_id, -price, f"Mua gói {days} ngày"):
        await target_msg.answer("❌ Số dư không đủ.", parse_mode="HTML")
        return

    # Tặng credits kèm gói (tỉ lệ theo số ngày, chuẩn 30 ngày = 1 tháng)
    monthly_ref = int(db.get_setting("price_1m", "0") or 0)
    if monthly_ref <= 0 and days > 0:
        monthly_ref = int(price * 30 / days)
    bonus = int(_sub_credit_bonus(monthly_ref) * days / 30) if monthly_ref > 0 else 0
    bonus_txt = ""
    if bonus > 0:
        total_cr = db.add_credits(tg_id, bonus, f"Tặng kèm gói {days} ngày")
        bonus_txt = f"\n🎁 Tặng kèm: <b>{bonus}</b> credits (tổng: {total_cr})"

    # Gia hạn sub_until — cộng dồn nếu còn hạn
    now_ts = int(time.time())
    current_sub = user.get("sub_until") or 0
    new_sub = max(now_ts, current_sub) + days * 86400

    # Đảm bảo ít nhất VIP 1 nếu user chưa có
    if (user.get("vip_level") or 0) < 1:
        db.admin_set_vip(tg_id, 1, 0)

    # Ghi sub_until
    with db._lock:
        c = db.get_conn()
        c.execute("UPDATE tg_users SET sub_until=? WHERE tg_id=?", (new_sub, tg_id))
        try:
            c.commit()
        except Exception:
            pass

    updated_user = db.get_user(tg_id)
    new_balance = (dict(updated_user).get("balance") or 0) if updated_user else 0
    expire_str = time.strftime("%d/%m/%Y %H:%M", time.localtime(new_sub))
    result = (
        f"✅ <b>MUA GÓI THÀNH CÔNG!</b>\n\n"
        f"📦 Gói: <b>{days} ngày</b>\n"
        f"💰 Đã trừ: <b>{vnd(price)}</b>\n"
        f"💳 Số dư còn: <b>{vnd(new_balance)}</b>\n"
        f"⏳ Hạn đến: <b>{expire_str}</b>{bonus_txt}\n\n"
        "🎉 Cảm ơn bạn đã sử dụng dịch vụ!"
    )
    if edit_msg:
        try:
            await target_msg.edit_text(result, parse_mode="HTML")
        except Exception:
            await target_msg.answer(result, parse_mode="HTML")
    else:
        await target_msg.answer(result, parse_mode="HTML")

@router.message(Command("history"))
async def on_history(msg: Message, command: CommandObject):
    """Lịch sử check: /history [fb|tiktok|ig|zalo]"""
    arg = (command.args or "").strip().lower()
    valid = {"fb", "tiktok", "ig", "zalo"}
    if arg and arg not in valid:
        await msg.answer("❌ Nền tảng không hợp lệ. Dùng: <code>/history [fb|tiktok|ig|zalo]</code>", parse_mode="HTML")
        return
    rows = db.get_user_logs(msg.from_user.id, kind=(arg or None), limit=15)
    if not rows:
        await msg.answer("📭 Bạn chưa có lịch sử check nào.")
        return
    lines = ["📜 <b>LỊCH SỬ CHECK GẦN ĐÂY</b>", "━━━━━━━━━━━━━━━"]
    for r in rows:
        ts = vn_time_str("%d/%m %H:%M", r["ts"])
        uid = html.escape(str(r["uid"] or ""))
        info = html.escape(str(r["message"] or ""))[:50]
        lines.append(f"• <code>{uid}</code> [{r['kind']}] {info} <i>({ts})</i>")
    await msg.answer("\n".join(lines), parse_mode="HTML")

@router.message(Command("top"))
async def on_top(msg: Message, command: CommandObject):
    """Bảng xếp hạng: /top (nạp tiền tháng) hoặc /top ref (giới thiệu)."""
    arg = (command.args or "").strip().lower()
    kind = "ref" if arg == "ref" else "topup"
    rows = db.get_leaderboard(kind=kind, limit=10)
    title = "🤝 <b>TOP GIỚI THIỆU</b>" if kind == "ref" else "🏆 <b>TOP NẠP TIỀN THÁNG</b>"
    if not rows:
        await msg.answer("📭 Chưa có dữ liệu xếp hạng.")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = [title, "━━━━━━━━━━━━━━━"]
    for i, r in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i + 1}."
        name = html.escape(r["name"] or r["username"] or str(r["tg_id"]))
        val = r["ref_earnings"] if kind == "ref" else r["monthly_topup"]
        lines.append(f"{medal} {name} — <b>{vnd(val or 0)}</b>")
    lines.append("\n💡 Dùng <code>/top ref</code> để xem top giới thiệu.")
    await msg.answer("\n".join(lines), parse_mode="HTML")

@router.message(Command("chuyentien"))
async def on_chuyentien(msg: Message, command: CommandObject):
    """/chuyentien <user_id> <số tiền> — có bước xác nhận chống nhập nhầm."""
    args = (command.args or "").split()
    if len(args) != 2:
        await msg.answer(
            "💸 <b>CHUYỂN TIỀN</b>\n"
            "Cú pháp: <code>/chuyentien &lt;user_id&gt; &lt;số_tiền&gt;</code>\n"
            "VD: <code>/chuyentien 123456789 50000</code>",
            parse_mode="HTML",
        )
        return
    try:
        to_id = int(args[0])
        amount = int(args[1].replace(".", "").replace(",", "").replace("đ", ""))
    except ValueError:
        await msg.answer("❌ User ID và số tiền phải là số.")
        return
    if amount <= 0:
        await msg.answer("❌ Số tiền phải lớn hơn 0.")
        return
    if to_id == msg.from_user.id:
        await msg.answer("❌ Không thể chuyển tiền cho chính mình.")
        return
    recv = db.get_user(to_id)
    if not recv:
        await msg.answer("❌ Không tìm thấy người nhận (user chưa từng dùng bot).")
        return
    _pending_transfer[(msg.chat.id, msg.from_user.id)] = (to_id, amount)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Xác nhận chuyển", callback_data="tf_yes"),
        InlineKeyboardButton(text="❌ Hủy", callback_data="tf_no"),
    ]])
    await msg.answer(
        f"💸 Xác nhận chuyển <b>{vnd(amount)}</b> cho "
        f"{html.escape(recv['name'] or '')} (<code>{to_id}</code>)?",
        reply_markup=kb, parse_mode="HTML",
    )

@router.callback_query(F.data.in_({"tf_yes", "tf_no"}))
async def on_transfer_confirm(cb: CallbackQuery):
    key = (cb.message.chat.id, cb.from_user.id)
    pending = _pending_transfer.pop(key, None)
    if cb.data == "tf_no" or not pending:
        try:
            await cb.message.edit_text("❌ Đã hủy chuyển tiền.")
        except Exception:
            pass
        await cb.answer()
        return
    to_id, amount = pending
    ok, text = db.transfer_balance(cb.from_user.id, to_id, amount)
    try:
        await cb.message.edit_text(("✅ " if ok else "❌ ") + html.escape(text), parse_mode="HTML")
    except Exception:
        pass
    await cb.answer()

@router.message(Command("code"))
async def on_code(msg: Message, command: CommandObject):
    """/code <mã giftcode> — nhập mã nhận thưởng."""
    code = (command.args or "").strip()
    if not code:
        await msg.answer("🎁 Nhập mã giftcode: <code>/code &lt;mã&gt;</code>", parse_mode="HTML")
        return
    success, amount, msg_text, wallet = db.use_code(code, msg.from_user.id)
    if success:
        db.credit_wallet(msg.from_user.id, amount, f"Sử dụng Giftcode: {code}", wallet)
        if db.parse_wallet(wallet) == "main":
            db.check_vip_upgrade(msg.from_user.id)
        await msg.answer(
            f"✅ <b>NẠP TIỀN THÀNH CÔNG!</b>\n\n"
            f"Bạn đã dùng mã <code>{html.escape(code)}</code> và được cộng <b>{db.wallet_amount_text(wallet, amount)}</b> vào {db.wallet_label(wallet)}.",
            parse_mode="HTML",
        )
    else:
        await msg.answer(f"❌ {html.escape(msg_text)}", parse_mode="HTML")

def _credit_packs() -> list:
    """Đọc gói credits từ setting credit_packs (JSON). Mặc định 3 gói."""
    import json
    default = [
        {"credits": 500, "price": 50000, "label": "500 lượt"},
        {"credits": 900, "price": 200000, "label": "900 lượt"},
        {"credits": 2000, "price": 350000, "label": "2000 lượt"},
    ]
    try:
        raw = db.get_setting("credit_packs", "")
        if raw:
            packs = json.loads(raw)
            if isinstance(packs, list) and packs:
                return packs
    except Exception:
        pass
    return default

def _sub_credit_bonus(price_per_month: int) -> int:
    """Credits tặng kèm khi mua 1 tháng gói.

    = tỉ lệ (setting sub_credit_ratio, mặc định 0.9) × số lượt của gói credit
    có giá cao nhất mà vẫn <= giá 1 tháng. Nếu giá tháng thấp hơn mọi gói thì
    lấy gói rẻ nhất làm chuẩn.
    """
    try:
        ratio = float(db.get_setting("sub_credit_ratio", "0.9") or 0.9)
    except Exception:
        ratio = 0.9
    if price_per_month <= 0:
        return 0
    packs = [p for p in _credit_packs()
             if int(p.get("price", 0) or 0) > 0 and int(p.get("credits", 0) or 0) > 0]
    if not packs:
        return 0
    packs.sort(key=lambda p: int(p["price"]))
    chosen = packs[0]
    for p in packs:
        if int(p["price"]) <= price_per_month:
            chosen = p
    return int(int(chosen["credits"]) * ratio)

async def _send_credit_packs(target_msg, tg_id: int, edit: bool = False):
    """Gửi bảng gói credits (dùng chung cho /muacredit và nút mua nhanh)."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    packs = _credit_packs()
    credits = db.get_credits(tg_id)
    up = db.get_user_promo(tg_id)
    promo_line = ""
    if up:
        ok, _, row = db.promo_valid(up["code"])
        if ok:
            try:
                w = db.parse_wallet(row["wallet"])
            except Exception:
                w = "main"
            if w == "main":
                promo_line = f"\n🎟️ Mã <b>{up['code']}</b> đang áp dụng: <b>giảm {int(row['pct'])}%</b> cho lần mua tới!\n"
            else:
                promo_line = f"\n🎟️ Bạn đang giữ mã <b>{up['code']}</b> (giảm {int(row['pct'])}% khi mua acc ở /shop).\n"
    lines = [
        "⚡ <b>MUA GÓI CREDITS (lượt check)</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"💠 Credits hiện có: <b>{credits}</b>",
        "",
        "Credits dùng để check file/cookie hàng loạt và gọi API reseller.",
        "Người có gói VIP đang hoạt động được check bulk miễn phí.",
        promo_line,
        "<b>Chọn gói:</b>",
    ]
    kb = []
    for i, p in enumerate(packs):
        c_num = int(p.get("credits", 0))
        price = int(p.get("price", 0))
        kb.append([InlineKeyboardButton(
            text=f"⚡ {p.get('label', f'{c_num} lượt')} — {vnd(price)}",
            callback_data=f"buycredit:{i}",
        )])
    kb.append([InlineKeyboardButton(text="🎟️ Nhập mã giảm giá", callback_data="promo_input")])
    markup = InlineKeyboardMarkup(inline_keyboard=kb)
    txt = "\n".join(lines)
    if edit:
        try:
            await target_msg.edit_text(txt, parse_mode="HTML", reply_markup=markup)
            return
        except Exception:
            pass
    await target_msg.answer(txt, parse_mode="HTML", reply_markup=markup)

@router.message(Command("muacredit"))
async def on_muacredit(msg: Message):
    """Mua gói credits (lượt check) bằng số dư."""
    await _send_credit_packs(msg, msg.from_user.id)

@router.callback_query(F.data == "buymore")
async def on_buymore(cb: CallbackQuery):
    """Nút mua nhanh khi sắp hết credits."""
    await _send_credit_packs(cb.message, cb.from_user.id, edit=True)
    await cb.answer()

@router.message(Command("flashsale"))
async def on_flashsale(msg: Message):
    """Admin broadcast thông báo flash sale cho toàn bộ user."""
    if not _is_admin(msg.from_user.id):
        await msg.answer("❌ Bạn không có quyền!")
        return
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer("Cú pháp: <code>/flashsale MÃ_CODE</code>", parse_mode="HTML")
        return
    ok, why, row = db.promo_valid(parts[1])
    if not ok:
        await msg.answer(f"❌ {why}")
        return
    d = dict(row)
    exp_txt = f"\n⏰ Kết thúc: {time.strftime('%d/%m %H:%M', time.localtime(d['expires_at']))}" if d["expires_at"] else ""
    lim_txt = f"\n🎫 Chỉ {int(d['max_uses']) - int(d['used_count'])} suất" if d["max_uses"] else ""
    text = (
        f"🔥 <b>FLASH SALE — GIẢM {int(d['pct'])}% GÓI CREDITS!</b> 🔥\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎟️ Mã: <code>{d['code']}</code>\n"
        f"💸 Giảm <b>{int(d['pct'])}%</b> khi mua gói credits{lim_txt}{exp_txt}\n\n"
        f"👉 Áp mã ngay: <code>/promo {d['code']}</code>\n"
        f"⚡ Mua gói: /muacredit"
    )
    users = [r["tg_id"] for r in db.get_all_users_for_broadcast()]
    sent, failed = 0, 0
    sem = asyncio.Semaphore(20)

    async def _send(uid):
        nonlocal sent, failed
        async with sem:
            try:
                await msg.bot.send_message(uid, text, parse_mode="HTML")
                sent += 1
            except Exception:
                failed += 1

    await asyncio.gather(*[_send(u) for u in users], return_exceptions=True)
    await msg.answer(f"📢 Đã gửi flash sale <b>{d['code']}</b>: <b>{sent}</b> thành công, {failed} thất bại.", parse_mode="HTML")

@router.callback_query(F.data.startswith("buycredit:"))
async def on_buycredit(cb: CallbackQuery):
    idx = int(cb.data.split(":")[1])
    packs = _credit_packs()
    if idx < 0 or idx >= len(packs):
        await cb.answer("❌ Gói không tồn tại!", show_alert=True)
        return
    p = packs[idx]
    c_num, price = int(p.get("credits", 0)), int(p.get("price", 0))
    # Áp mã giảm giá flash sale nếu user đã nhập /promo
    final_price, used_code = db.preview_user_promo(cb.from_user.id, price)
    promo_txt = f"\n🎟️ Đã áp mã <b>{used_code}</b>!" if used_code else ""
    user = db.get_user(cb.from_user.id)
    balance = (user["balance"] or 0) if user else 0
    if balance < final_price:
        await cb.answer(
            f"❌ Số dư không đủ! Cần {vnd(final_price)}, bạn có {vnd(balance)}. Nạp thêm bằng /bank",
            show_alert=True,
        )
        return
    if not db.adjust_balance(cb.from_user.id, -final_price, f"Mua gói {c_num} credits" + (f" (promo {used_code})" if used_code else "")):
        await cb.answer("❌ Số dư không đủ.", show_alert=True)
        return
    new_credits = db.add_credits(cb.from_user.id, c_num, f"Mua gói {c_num} credits ({vnd(final_price)})")
    db.add_log("credit", f"Mua {c_num} credits (-{vnd(final_price)})", cb.from_user.id)
    db.finalize_user_promo(cb.from_user.id, used_code)  # trừ lượt promo SAU khi mua thành công
    await cb.answer("✅ Mua thành công!", show_alert=True)
    await cb.message.answer(
        f"✅ <b>MUA CREDITS THÀNH CÔNG!</b>{promo_txt}\n\n"
        f"⚡ Nhận: <b>{c_num}</b> credits\n"
        f"💰 Đã trừ: <b>{vnd(final_price)}</b>\n"
        f"💠 Tổng credits: <b>{new_credits}</b>",
        parse_mode="HTML",
    )

@router.message(Command("taokey"))
async def on_taokey(msg: Message):
    """Admin: tạo API key cho reseller. Cú pháp: /taokey <tên shop> [số credits]"""
    from ..admin_bot import is_admin
    if not is_admin(msg.chat.id, msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: <code>/taokey &lt;tên shop&gt; [số credits]</code>\nVí dụ: <code>/taokey shopA 1000</code>")
        return
    name = parts[1]
    credits = 0
    if len(parts) > 2:
        try:
            credits = max(0, int(parts[2]))
        except ValueError:
            await msg.answer("❌ Số credits phải là số nguyên!")
            return
    k = db.create_reseller_key(name, credits)
    await msg.answer(
        "🔑 <b>TẠO API KEY THÀNH CÔNG</b>\n\n"
        f"🏪 Shop: <b>{html.escape(name)}</b>\n"
        f"🆔 Key ID: <code>{k['id']}</code>\n"
        f"⚡ Credits: <b>{k['credits']}</b>\n\n"
        f"🔐 API Key:\n<code>{k['api_key']}</code>\n\n"
        "⚠️ <i>Gửi key này cho reseller qua kênh riêng. Key chỉ hiện 1 lần!</i>\n\n"
        "📖 Tài liệu API:\n"
        "• <code>POST /api/v1/fb/check</code> — header <code>X-API-Key</code>, body <code>{\"uid\": \"...\"}</code>\n"
        "• <code>POST /api/v1/fb/getuid</code> — body <code>{\"link\": \"...\"}</code>\n"
        "• <code>GET /api/v1/balance</code> — xem credits\n"
        "• <code>GET /api/v1/usage</code> — lịch sử dùng",
        parse_mode="HTML",
    )

@router.message(Command("napkey"))
async def on_napkey(msg: Message):
    """Admin: nạp credits cho reseller key. Cú pháp: /napkey <key_id> <số credits>"""
    from ..admin_bot import is_admin
    if not is_admin(msg.chat.id, msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 3:
        await msg.answer("⚠️ Cú pháp: <code>/napkey &lt;key_id&gt; &lt;số credits&gt;</code>")
        return
    try:
        key_id, n = int(parts[1]), int(parts[2])
    except ValueError:
        await msg.answer("❌ key_id và số credits phải là số!")
        return
    db.add_reseller_credits(key_id, n)
    await msg.answer(f"✅ Đã nạp <b>{n}</b> credits cho key <code>{key_id}</code>.", parse_mode="HTML")

@router.message(Command("lichsu"))
async def on_lichsu(msg: Message):
    """Xem lại lịch sử check 7 ngày gần nhất (không tốn credit khi xem lại)."""
    rows = db.get_check_history(msg.from_user.id, days=7)
    if not rows:
        await msg.answer(
            "🕘 <b>LỊCH SỬ CHECK (7 ngày)</b>\n\n"
            "Bạn chưa có lượt check nào trong 7 ngày qua.",
            parse_mode="HTML",
        )
        return
    icon = {"live": "🟢", "die": "🔴", "checkpoint_282": "🟡", "checkpoint_956": "🟠",
            "checkpoint": "⚪", "disabled": "⛔", "dead": "🔴"}
    lines = ["🕘 <b>LỊCH SỬ CHECK (7 ngày)</b>", "━━━━━━━━━━━━━━━", ""]
    for r in rows[:20]:
        d = dict(r)
        st = (d.get("result") or "?").lower()
        ic = icon.get(st, "⚪")
        t = time.strftime("%d/%m %H:%M", time.localtime(d.get("checked_at") or 0))
        target = (d.get("target") or "")[:40]
        lines.append(f"{ic} <code>{target}</code> — <b>{st.upper()}</b> <i>({t})</i>")
    if len(rows) > 20:
        lines.append(f"\n<i>...và {len(rows) - 20} lượt khác</i>")
    lines.append("\n💡 <i>Muốn check lại UID nào, cứ gửi UID/link như bình thường.</i>")
    await msg.answer("\n".join(lines), parse_mode="HTML")

@router.callback_query(F.data.startswith("tienich:"))
async def on_tienich_cb(cb: CallbackQuery, state: FSMContext):
    data = cb.data or ""
    action = data.split(":", 1)[1] if ":" in data else "main"
    tg_id = cb.from_user.id

    try:
        if action == "main":
            await state.clear()
            await cb.message.edit_text(_tienich_text_main(), parse_mode="HTML", reply_markup=_tienich_main_kb())
            await cb.answer()
            return

        # ── Giftcode ──
        if action == "code":
            await state.set_state(TienIchState.waiting_for_giftcode)
            await cb.message.edit_text(
                "🎁 <b>NHẬP GIFTCODE</b>\n\nGửi mã giftcode của bạn ngay tin nhắn tiếp theo.\nGõ /huy để hủy.",
                parse_mode="HTML", reply_markup=_tienich_back_kb())
            await cb.answer()
            return

        # ── Promo ──
        if action == "promo":
            await state.set_state(TienIchState.waiting_for_promo)
            await cb.message.edit_text(
                "🎟️ <b>ÁP MÃ GIẢM GIÁ</b>\n\nGửi mã giảm giá (VD: SALE20) ngay tin nhắn tiếp theo.\nGõ /huy để hủy.",
                parse_mode="HTML", reply_markup=_tienich_back_kb())
            await cb.answer()
            return

        # ── Birthday ──
        if action == "birthday":
            await state.set_state(TienIchState.waiting_for_birthday)
            await cb.message.edit_text(
                "🎂 <b>NGÀY SINH NHẬN QUÀ</b>\n\nGửi ngày sinh theo định dạng <b>ngày/tháng/năm</b>.\nVD: <code>25/12/2000</code>\n\nGõ /huy để hủy.",
                parse_mode="HTML", reply_markup=_tienich_back_kb())
            await cb.answer()
            return

        # ── Refcode ──
        if action == "refcode":
            await state.set_state(TienIchState.waiting_for_refcode)
            await cb.message.edit_text(
                "✏️ <b>ĐỔI MÃ GIỚI THIỆU</b>\n\nGửi mã mới (chỉ chữ và số, không dấu cách).\nGõ /huy để hủy.",
                parse_mode="HTML", reply_markup=_tienich_back_kb())
            await cb.answer()
            return

        # ── GetUID ──
        if action == "getuid":
            await state.set_state(TienIchState.waiting_for_getuid)
            await cb.message.edit_text(
                "🔗 <b>LẤY UID TỪ LINK FB</b>\n\nGửi link Facebook ngay tin nhắn tiếp theo.\nGõ /huy để hủy.",
                parse_mode="HTML", reply_markup=_tienich_back_kb())
            await cb.answer()
            return

        # ── Withdraw (ruttien) bước 1: số tiền ──
        if action == "withdraw":
            await state.set_state(TienIchState.waiting_withdraw_amount)
            await cb.message.edit_text(
                "💸 <b>RÚT HOA HỒNG</b> (bước 1/3)\n\nGửi <b>số tiền</b> muốn rút (tối thiểu 50.000đ).\nGõ /huy để hủy.",
                parse_mode="HTML", reply_markup=_tienich_back_kb())
            await cb.answer()
            return

        # ── Transfer (chuyentien) bước 1: user_id ──
        if action == "transfer":
            await state.set_state(TienIchState.waiting_transfer_uid)
            await cb.message.edit_text(
                "↔️ <b>CHUYỂN TIỀN</b> (bước 1/2)\n\nGửi <b>User ID</b> người nhận.\nGõ /huy để hủy.",
                parse_mode="HTML", reply_markup=_tienich_back_kb())
            await cb.answer()
            return

        # ── Doitien: không cần nhập, chạy luôn ──
        if action == "doitien":
            await cb.answer()
            # tái sử dụng logic on_doitien bằng cách giả lập msg
            await _do_doitien(cb.message, tg_id)
            return

        # ── History: nút lọc ──
        if action == "history":
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📜 Tất cả", callback_data="tienich:hist_all"),
                 InlineKeyboardButton(text="📘 FB", callback_data="tienich:hist_fb")],
                [InlineKeyboardButton(text="🎵 TikTok", callback_data="tienich:hist_tiktok"),
                 InlineKeyboardButton(text="📸 IG", callback_data="tienich:hist_ig")],
                [InlineKeyboardButton(text="💬 Zalo", callback_data="tienich:hist_zalo")],
                [InlineKeyboardButton(text="◀️ Quay lại Tiện ích", callback_data="tienich:main")],
            ])
            await cb.message.edit_text("📜 <b>LỊCH SỬ CHECK</b>\n\nChọn nền tảng muốn xem:", parse_mode="HTML", reply_markup=kb)
            await cb.answer()
            return

        if action.startswith("hist_"):
            kind = action[5:]
            kind = None if kind == "all" else kind
            await cb.answer()
            await _show_history(cb.message, tg_id, kind)
            return

        # ── Coc: chọn loại acc ──
        if action == "coc":
            cats = [dict(x) for x in db.acc_category_list() if x.get("active", 1)]
            if not cats:
                await cb.message.edit_text("⛔ Hiện chưa có loại acc nào để đặt cọc.", reply_markup=_tienich_back_kb())
                await cb.answer()
                return
            rows = []
            for c in cats[:10]:
                rows.append([InlineKeyboardButton(
                    text=f"💰 {c['name']} (#{c['id']})",
                    callback_data=f"tienich:coc_go_{c['id']}")])
            rows.append([InlineKeyboardButton(text="◀️ Quay lại Tiện ích", callback_data="tienich:main")])
            await cb.message.edit_text("💰 <b>ĐẶT CỌC GIỮ HÀNG</b>\n\nChọn loại acc muốn đặt cọc:", parse_mode="HTML",
                                       reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
            await cb.answer()
            return

        if action.startswith("coc_go_"):
            cat_id = int(action[7:])
            await cb.answer()
            await _do_deposit(tg_id, cat_id, cb.message, cb.message.bot)
            return

        # ── Huycoc: chọn khoản cọc ──
        if action == "huycoc":
            deps = [dict(d) for d in db.acc_deposit_list(tg_id) if d.get("status") == "WAITING"]
            if not deps:
                await cb.message.edit_text("💰 Bạn chưa có khoản đặt cọc chờ hàng nào.", reply_markup=_tienich_back_kb())
                await cb.answer()
                return
            rows = []
            for d in deps[:10]:
                rows.append([InlineKeyboardButton(
                    text=f"❌ Hủy #{d['id']} — {d.get('cat_name') or ''} ({vnd(d['amount'])})",
                    callback_data=f"tienich:huycoc_go_{d['id']}")])
            rows.append([InlineKeyboardButton(text="◀️ Quay lại Tiện ích", callback_data="tienich:main")])
            await cb.message.edit_text("❌ <b>HỦY ĐẶT CỌC</b>\n\nChọn khoản cọc muốn hủy (hoàn tiền vào ví shop):", parse_mode="HTML",
                                       reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
            await cb.answer()
            return

        if action.startswith("huycoc_go_"):
            dep_id = int(action[11:])
            await cb.answer()
            amt = db.acc_deposit_cancel(dep_id, tg_id)
            if amt is None:
                await cb.message.answer("❌ Không tìm thấy khoản cọc chờ hàng này của bạn.")
                return
            db.add_shop_balance_only(tg_id, amt, f"huy_coc:{dep_id}")
            await cb.message.answer(f"✅ Đã hủy cọc <b>#{dep_id}</b>, hoàn <b>{vnd(amt)}</b> vào ví shop.", parse_mode="HTML")
            return

        await cb.answer("⏳ Đang phát triển.")
    except Exception as e:
        log.exception("tienich cb %s: %s", action, e)
        try:
            await cb.answer("❌ Có lỗi xảy ra.", show_alert=True)
        except Exception:
            pass

@router.message(Command("giohang"))
async def on_giohang(msg: Message):
    r = _cart_render(msg.from_user.id)
    if not r:
        await msg.answer("🛒 Giỏ hàng đang trống.\nVào /shop chọn acc rồi bấm \"🛒 Thêm vào giỏ\" nhé!")
        return
    txt, kb = r
    await msg.answer(txt, parse_mode="HTML", reply_markup=kb)

@router.message(Command("hang"))
async def on_hang(msg: Message):
    """Xem hạng thành viên shop acc."""
    t = db.member_tier_info(msg.from_user.id)
    lines = ["🏅 <b>HẠNG THÀNH VIÊN SHOP ACC</b>", "━━━━━━━━━━━━",
             f"Hạng hiện tại: <b>{t['tier']}</b>",
             f"Tổng đã mua: <b>{vnd(t['spent'])}</b>"]
    if t["pct"]:
        lines.append(f"Ưu đãi: <b>giảm {t['pct']}%</b> mọi đơn mua acc")
    lines += ["",
              f"🥈 Bạc: mua từ {vnd(t['silver_min'])} — giảm {t['silver_pct']}%",
              f"🥇 Vàng: mua từ {vnd(t['gold_min'])} — giảm {t['gold_pct']}%"]
    if t["next_tier"]:
        need = t["next_min"] - t["spent"]
        lines.append(f"\n👉 Mua thêm <b>{vnd(need)}</b> để lên {t['next_tier']}!")
    else:
        lines.append("\n🌟 Bạn đang ở hạng cao nhất!")
    await msg.answer("\n".join(lines), parse_mode="HTML")

@router.message(Command("creditbonus"))
async def on_creditbonus(msg: Message):
    """Admin: tặng credits khi khách mua acc. Cú pháp: /creditbonus <id_loại> <số_credits/acc>"""
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 3:
        await msg.answer("⚠️ Cú pháp: <code>/creditbonus &lt;id_loại&gt; &lt;số_credits&gt;</code>\n"
                         "VD: <code>/creditbonus 3 50</code> — mua 1 acc loại #3 tặng 50 credits.",
                         parse_mode="HTML")
        return
    try:
        cid, n = int(parts[1]), int(parts[2])
    except Exception:
        await msg.answer("❌ ID và số credits phải là số.")
        return
    if n < 0:
        await msg.answer("❌ Số credits không được âm.")
        return
    if db.acc_category_update(cid, credit_bonus=n):
        await msg.answer(f"✅ Loại <b>#{cid}</b>: mua 1 acc tặng <b>{n} credits</b>.",
                         parse_mode="HTML")
    else:
        await msg.answer("❌ Không tìm thấy loại này.")

@router.message(Command("donhang"))
async def on_donhang(msg: Message):
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    limit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10
    rev = db.acc_revenue()
    orders = db.acc_recent_orders(limit)
    lines = ["🧾 <b>ĐƠN HÀNG SHOP ACC</b>", "━━━━━━━━━━━━",
             f"Tổng: <b>{rev['count']}</b> đơn — doanh thu <b>{vnd(rev['total'])}</b>", ""]
    for cat in rev["by_cat"]:
        lines.append(f"• {html.escape(cat['name'])}: {cat['n']} đơn — {vnd(cat['s'])}")
    lines.append("")
    for o in orders:
        o = dict(o)
        who = f"@{o['username']}" if o["username"] else f"<code>{o['tg_id']}</code>"
        lines.append(f"#{o['id']} {html.escape(o['cat_name'])} — {vnd(o['price'])} — {who} "
                     f"({vn_time_str(ts=o['created_at'])})")
    await msg.answer("\n".join(lines), parse_mode="HTML")

__all__ = [
    "_NAP_QUICK_AMOUNTS",
    "_pending_transfer",
    "BankState",
    "PayOSState",
    "on_ref",
    "on_daily",
    "on_refcode",
    "on_ruttien",
    "notify_admin_withdrawal_request",
    "_withdraw_admin_ids",
    "on_admin_withdraw_approve",
    "on_admin_withdraw_reject",
    "on_doitien",
    "on_trial",
    "on_bank",
    "on_use_code",
    "on_save_code",
    "on_mycodes",
    "_ask_bank_wallet",
    "on_bank_confirm",
    "on_bank_wallet",
    "process_bank_amount",
    "on_bank_amount",
    "_parse_payos_amount",
    "_make_payos_order",
    "_qr_code_png",
    "_send_payos_invoice",
    "_ask_payos_wallet",
    "_nap_amount_picker",
    "on_nap_amt",
    "on_nap_custom",
    "on_nap",
    "on_payos_wallet",
    "on_napshop",
    "on_nap_amount",
    "on_napshop_amount",
    "on_payos_auto",
    "on_payos_cancel",
    "on_payos_status",
    "on_payosset",
    "on_balance",
    "on_wal_nap",
    "on_wal_hist",
    "on_sodu",
    "on_sub",
    "on_sub_pick",
    "on_tienich_giftcode",
    "on_tienich_refcode",
    "on_tienich_wd_amount",
    "on_tienich_wd_bank",
    "on_tienich_wd_stk",
    "on_tienich_tf_uid",
    "on_tienich_tf_amount",
    "on_vip",
    "on_toggle_autorenew",
    "on_daily_report_cmd",
    "on_muagoi",
    "on_buy_pkg_cb",
    "_do_buy_package",
    "on_history",
    "on_top",
    "on_chuyentien",
    "on_transfer_confirm",
    "on_code",
    "_credit_packs",
    "_sub_credit_bonus",
    "_send_credit_packs",
    "on_muacredit",
    "on_buymore",
    "on_flashsale",
    "on_buycredit",
    "on_taokey",
    "on_napkey",
    "on_lichsu",
    "on_tienich_cb",
    "on_giohang",
    "on_hang",
    "on_creditbonus",
    "on_donhang",
]