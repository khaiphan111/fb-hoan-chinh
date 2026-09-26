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

from .core import _is_admin, log, router
from .common import _fmt_warranty, _notify_admin_photo

class WarrantyClaimState(StatesGroup):
    waiting_for_evidence = State()

@router.callback_query(F.data == "accwarranty:pick")
async def on_acc_warranty_pick(cb: CallbackQuery):
    """Khách bấm 'Có acc cần BH' từ tin hỏi thăm gộp -> chọn đơn cần BH."""
    await cb.answer()
    tg_id = cb.from_user.id
    try:
        rows = db.get_conn().execute(
            "SELECT o.id, o.created_at, c.name cat_name FROM acc_orders o "
            "LEFT JOIN acc_categories c ON c.id=o.cat_id "
            "WHERE o.tg_id=? AND o.delivered_at>0 "
            "ORDER BY o.delivered_at DESC LIMIT 10", (tg_id,)).fetchall()
    except Exception:
        rows = []
    if not rows:
        await cb.message.answer("❌ Không tìm thấy đơn hàng nào của bạn.")
        return
    kb_rows = []
    for r in rows:
        r = dict(r)
        label = f"#{r['id']} {r['cat_name'] or ''}".strip()
        kb_rows.append([InlineKeyboardButton(
            text=f"🛡 {label}", callback_data=f"accwarranty:{r['id']}")])
    await cb.message.answer(
        "🛡 <b>Chọn đơn cần bảo hành:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))

@router.callback_query(F.data.startswith("accwarranty:"))
async def on_acc_warranty(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    try:
        order_id = int(cb.data.split(":", 1)[1])
    except Exception:
        return
    order = db.acc_get_order(order_id)
    if not order or int(order["tg_id"]) != cb.from_user.id:
        await cb.message.answer("❌ Không tìm thấy đơn hàng.")
        return
    order = dict(order)
    wh = int(order["warranty_hours"] or 0)  # phút
    age_m = (now() - int(order["created_at"])) / 60
    if age_m > wh:
        await cb.message.answer(
            f"❌ Đơn <b>#{order_id}</b> đã hết thời gian bảo hành ({_fmt_warranty(wh)}).",
            parse_mode="HTML",
        )
        return
    if db.acc_warranty_has_pending(order_id):
        await cb.message.answer("⏳ Đơn này đang chờ admin xử lý bảo hành rồi.")
        return
    # Bot tự check UID để hỗ trợ admin (không tự đổi acc nữa)
    check_txt = "không check được"
    try:
        from .. import fb as fb_mod
        r = await fb_mod.check_uid(str(order["uid"]))
        st = str(r.get("status", "")).lower()
        check_txt = "DIE" if st in ("die", "dead") else ("LIVE" if st == "live" else st.upper())
    except Exception:
        pass
    await state.set_state(WarrantyClaimState.waiting_for_evidence)
    await state.update_data(order_id=order_id, check_txt=check_txt)
    await cb.message.answer(
        "🛡 <b>BẢO HÀNH — CẦN BẰNG CHỨNG</b>\n\n"
        "Chỉ bảo hành trường hợp <b>log sai mật khẩu</b> được giao.\n"
        "📸 Vui lòng gửi <b>ảnh chụp màn hình</b> lỗi đăng nhập sai mk để admin xem xét.\n\n"
        "Gõ /huy để hủy yêu cầu.",
        parse_mode="HTML",
    )

@router.message(WarrantyClaimState.waiting_for_evidence)
async def on_warranty_evidence(msg: Message, state: FSMContext):
    if msg.text and msg.text.strip().lower() == "/huy":
        await state.clear()
        await msg.answer("Đã hủy yêu cầu bảo hành.")
        return
    _t = (msg.text or "").strip()
    if _t.startswith("/") and _t.split()[0].lower() not in ("/huy", "/cancel"):
        await state.clear()
        await msg.answer("\U0001f6ab \u0110\u00e3 h\u1ee7y thao t\u00e1c \u0111ang nh\u1eadp. B\u1ea1n g\u00f5 l\u1ea1i l\u1ec7nh v\u1eeba r\u1ed3i nh\u00e9.")
        return
    if not msg.photo:
        await msg.answer(
            "📸 Vui lòng gửi <b>ảnh chụp màn hình</b> lỗi log sai mk (gõ /huy để hủy).",
            parse_mode="HTML")
        return
    data = await state.get_data()
    order_id = int(data.get("order_id") or 0)
    check_txt = data.get("check_txt") or ""
    await state.clear()
    order = db.acc_get_order(order_id)
    if not order or int(order["tg_id"]) != msg.from_user.id:
        await msg.answer("❌ Không tìm thấy đơn hàng.")
        return
    order = dict(order)
    claim_id = db.acc_warranty_claim(order_id, msg.from_user.id, 0, check_txt)
    if claim_id == -1:
        await msg.answer("⏳ Đơn này đang chờ admin xử lý bảo hành rồi.")
        return
    db.acc_warranty_set_evidence(claim_id, msg.photo[-1].file_id)
    # Chống lạm dụng bảo hành: quá số lần/tuần -> NEEDS_REVIEW
    try:
        maxw = int(db.get_setting("warranty_max_week", "3") or 3)
    except Exception:
        maxw = 3
    week_cnt = db.acc_warranty_week_count(msg.from_user.id)
    warn = ""
    if week_cnt > maxw:
        db.acc_warranty_set_status(claim_id, "NEEDS_REVIEW")
        warn = (f"\n\n⚠️ Bạn đã gửi <b>{week_cnt}</b> yêu cầu trong 7 ngày "
                f"(giới hạn {maxw}) — yêu cầu này sẽ được admin kiểm tra kỹ.")
    uname = (msg.from_user.username or msg.from_user.full_name or "").strip()
    caption = (
        f"🛡 <b>Yêu cầu bảo hành #{claim_id}</b>\n"
        f"👤 {html.escape(uname)} (<code>{msg.from_user.id}</code>)\n"
        f"🧾 Đơn #{order_id} — {html.escape(order['cat_name'])} — {vnd(order['price'])}\n"
        f"👤 UID: <code>{html.escape(order['uid'] or '')}</code>\n"
        f"🔍 Bot tự check: <b>{html.escape(check_txt)}</b>\n"
        f"📸 Ảnh bằng chứng log sai mk đính kèm.\n\n"
        f"Xem: /bhdon | Xong: /bhdone {claim_id}"
    )
    await _notify_admin_photo(msg.bot, msg.photo[-1].file_id, caption)
    await msg.answer(
        f"✅ <b>Đã ghi nhận yêu cầu bảo hành #{claim_id}</b>\n\n"
        f"🧾 Đơn: <b>#{order_id}</b> — {html.escape(order['cat_name'])}\n"
        f"👤 UID: <code>{html.escape(order['uid'] or '')}</code>\n\n"
        f"Admin sẽ xem ảnh bằng chứng và xử lý sớm nhất.{warn}",
        parse_mode="HTML",
    )

@router.message(Command("bhdon"))
async def on_bhdon(msg: Message):
    if not _is_admin(msg.from_user.id):
        return
    claims = db.acc_warranty_pending()
    if not claims:
        await msg.answer("🛡 Không có yêu cầu bảo hành nào đang chờ.")
        return
    lines = ["🛡 <b>BẢO HÀNH ĐANG CHỜ</b>", "━━━━━━━━━━━━", ""]
    for w in claims:
        w = dict(w)
        lines.append(
            f"#{w['id']} — đơn <b>#{w['order_id']}</b> — {html.escape(w['cat_name'])}\n"
            f"👤 UID <code>{html.escape(w['uid'] or '')}</code> | khách <code>{w['tg_id']}</code>\n"
            f"🔍 Bot check: <b>{html.escape(w['check_result'] or '?')}</b> | "
            f"{vn_time_str(ts=w['created_at'])}{' | 📸 có ảnh' if w.get('evidence') else ''}\n"
            f"Xong: <code>/bhdone {w['id']}</code>"
        )
    await msg.answer("\n\n".join(lines), parse_mode="HTML")

@router.message(Command("bhdone"))
async def on_bhdone(msg: Message):
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await msg.answer("⚠️ Cú pháp: <code>/bhdone &lt;id_khiếu_nại&gt;</code>",
                         parse_mode="HTML")
        return
    cid = int(parts[1])
    cl = db.acc_warranty_get(cid)
    if not cl:
        await msg.answer(f"❌ Không tìm thấy khiếu nại <b>#{cid}</b>.", parse_mode="HTML")
        return
    db.acc_warranty_set_status(cid, "DONE", handled_by=msg.from_user.id)
    handler_name = html.escape(msg.from_user.full_name or "")
    await msg.answer(
        f"✅ Đã đánh dấu xong khiếu nại <b>#{cid}</b>.\n"
        f"👤 Người duyệt: <b>{handler_name}</b> <code>{msg.from_user.id}</code> — "
        f"{vn_time_str()}",
        parse_mode="HTML")

__all__ = [
    "WarrantyClaimState",
    "on_acc_warranty",
    "on_warranty_evidence",
    "on_bhdon",
    "on_bhdone",
]