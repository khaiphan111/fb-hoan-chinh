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

from .core import _is_admin, log, manager, router

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
    target = parts[5] if len(parts) > 5 and parts[5] in ("main", "shop") else "main"

    try:
        if target == "shop":
            db.credit_topup(user_id, amount, reason="bank_transfer", wallet="shop")
        else:
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
            _w = "🛒 <b>Ví shop</b>" if target == "shop" else "💰 <b>Ví chính</b>"
            msg_text = (
                f"✅ <b>NẠP TIỀN THÀNH CÔNG</b>\n\n"
                f"Bạn vừa được cộng <b>{amount:,.0f} VNĐ</b> vào {_w}.\n"
                f"Cảm ơn bạn đã sử dụng dịch vụ!"
            )
            try:
                _u = db.get_user(user_id)
                _bal = (_u["shop_balance"] if target == "shop" else _u["balance"]) if _u is not None else 0
                msg_text += f"\n\n{_w} số dư hiện tại: <b>{_bal:,.0f} VNĐ</b>"
            except Exception:
                pass
            
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

@router.callback_query(lambda c: c.data and c.data.startswith("set_report_"))
async def on_set_report_cb(cb: CallbackQuery):
    val = cb.data.split("_")[-1]
    if val == "off":
        db.set_daily_report_hour(cb.from_user.id, -1)
        await cb.answer("Đã tắt báo cáo tự động!", show_alert=True)
        await cb.message.edit_text("🔴 Đã <b>TẮT</b> báo cáo tự động định kỳ.", parse_mode="HTML")
    else:
        try:
            hour = int(val)
            db.set_daily_report_hour(cb.from_user.id, hour)
            await cb.answer(f"Đã bật báo cáo lúc {hour:02d}:00!", show_alert=True)
            await cb.message.edit_text(f"✅ Đã bật báo cáo tự động hằng ngày vào <b>{hour:02d}:00</b>!", parse_mode="HTML")
        except Exception:
            await cb.answer("Lỗi cài đặt!", show_alert=True)

@router.message(Command("adm"))
async def on_adm(msg: Message, state: FSMContext):
    """Lệnh admin tập trung — chỉ admin_tg_id được cấp phép mới dùng được."""
    from ..admin_bot import _handle_adm_cmd, is_admin
    if not is_admin(msg.chat.id, msg.from_user.id):
        return
    await state.clear()
    await _handle_adm_cmd(msg, bot_instance=manager.bot)

@router.message(Command("stats"))
async def on_stats(msg: Message):
    """Thống kê cá nhân của user."""
    u = db.get_user(msg.from_user.id)
    if not u:
        await msg.answer("❌ Bạn chưa có tài khoản. Gõ /start để bắt đầu.")
        return
    total_checks = db.get_conn().execute(
        "SELECT COUNT(*) AS c FROM logs WHERE tg_id=?", (msg.from_user.id,)
    ).fetchone()["c"]
    text = (
        "📊 <b>THỐNG KÊ CÁ NHÂN</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"👤 <b>Tên:</b> {html.escape(u['name'] or '')}\n"
        f"🆔 <b>ID:</b> <code>{u['tg_id']}</code>\n"
        f"💰 <b>Số dư:</b> {vnd(u['balance'] or 0)}\n"
        f"⭐ <b>VIP:</b> {u['vip_level'] or 0}\n"
        f"🔍 <b>Tổng lượt check:</b> {total_checks:,}\n"
        f"💵 <b>Tổng đã nạp:</b> {vnd(u['total_topup'] or 0)}\n"
        f"🤝 <b>Hoa hồng giới thiệu:</b> {vnd(u['ref_earnings'] or 0)}\n"
    )
    await msg.answer(text, parse_mode="HTML")

@router.message(Command("faq"))
async def on_faq(msg: Message):
    """Admin: xem danh sách câu hỏi tự động."""
    if not _is_admin(msg.from_user.id):
        return
    items = db.shop_faq_list()
    n_default = len(db._DEFAULT_FAQ)
    lines = ["❓ <b>FAQ TỰ ĐỘNG</b> — khách nhắn đúng từ khóa → bot tự trả lời",
             "━━━━━━━━━━━━", ""]
    for i, item in enumerate(items, 1):
        tag = "mặc định" if i <= n_default else f"tự thêm #{i - n_default}"
        kws = ", ".join(item.get("kw", []))
        lines.append(f"{i}. [{tag}] <code>{html.escape(kws)}</code>")
    lines += ["",
              "Thêm: <code>/themcauhoi từ khóa, từ khóa 2 | câu trả lời</code>",
              "Xóa câu tự thêm: <code>/xoacauhoi &lt;số&gt;</code>"]
    await msg.answer("\n".join(lines), parse_mode="HTML")

@router.message(Command("themcauhoi"))
async def on_themcauhoi(msg: Message):
    """Admin: thêm câu hỏi tự động. /themcauhoi từ khóa, từ khóa 2 | câu trả lời"""
    if not _is_admin(msg.from_user.id):
        return
    raw = (msg.text or "").split(maxsplit=1)
    if len(raw) < 2 or "|" not in raw[1]:
        await msg.answer(
            "⚠️ Cú pháp: <code>/themcauhoi từ khóa, từ khóa 2 | câu trả lời</code>",
            parse_mode="HTML")
        return
    kw_part, ans = raw[1].split("|", 1)
    n = db.shop_faq_add(kw_part, ans)
    if n == -1:
        await msg.answer("❌ Thiếu từ khóa hoặc câu trả lời.")
        return
    await msg.answer(f"✅ Đã thêm câu hỏi tự động #{n}. Xem: /faq")

@router.message(Command("xoacauhoi"))
async def on_xoacauhoi(msg: Message):
    """Admin: xóa câu hỏi tự thêm. /xoacauhoi <số>"""
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await msg.answer(
            "⚠️ Cú pháp: <code>/xoacauhoi &lt;số_câu_tự_thêm&gt;</code> (xem số ở /faq)",
            parse_mode="HTML")
        return
    if db.shop_faq_del(int(parts[1])):
        await msg.answer(f"✅ Đã xóa câu hỏi #{parts[1]}.")
    else:
        await msg.answer("❌ Không tìm thấy câu này (chỉ xóa được câu tự thêm).")

__all__ = [
    "on_main_admin_confirm",
    "on_set_report_cb",
    "on_adm",
    "on_stats",
    "on_faq",
    "on_themcauhoi",
    "on_xoacauhoi",
]