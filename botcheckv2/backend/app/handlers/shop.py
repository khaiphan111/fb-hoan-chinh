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

from .core import _SHOP_WALLET_HINT, router
from .common import AccShopState, _acc_after_purchase, _acc_delivery_caption, _acc_delivery_kb, _cart_render, _cat_icon, _cat_live_check, _check_stock_live, _do_deposit, _line_price, _live_line, _mail_app_line, _notify_admin_smart, _notify_die_quarantine, _notify_purchase_admin, _pickup_suffix, _sell_live_stock, _sell_uid_items, _send_merged_acc_file, _shop_bulk_pcts, _shop_list, _shop_stall_picker, _spin_kb, _totp_now, _uid_final_price

_PICK_PAGE_SIZE = 10

@router.message(Command("shop"))
async def on_shop(msg: Message):
    stalls = [s for s in db.acc_stall_list() if int(s["n"] or 0) > 0]
    if len(stalls) > 1:
        await _shop_stall_picker(msg)
        return
    await _shop_list(msg, None)

@router.callback_query(F.data.startswith("accbuy:"))
async def on_acc_buy(cb: CallbackQuery):
    await cb.answer()
    try:
        cat_id = int(cb.data.split(":", 1)[1])
    except Exception:
        return
    c = db.acc_category_get(cat_id)
    if not c or not c["active"]:
        await cb.message.answer("❌ Loại acc này không còn bán.")
        return
    c = dict(c)
    n = db.acc_stock_count(cat_id)
    p5, p10, p20 = _shop_bulk_pcts()
    up = db.shop_unit_price(c)
    unit = up["price"]
    icon = _cat_icon(c["name"])
    sold = db.acc_sold_count(cat_id)
    sample = db.get_conn().execute(
        "SELECT uid, created_date FROM acc_stock WHERE cat_id=? AND status='AVAILABLE' ORDER BY id LIMIT 1",
        (cat_id,)).fetchone()
    mail_line = _mail_app_line()
    txt = (
        f"{icon} <b>{html.escape(c['name'])}</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"💰 <b>{vnd(unit)}</b>/acc"
        + (f"  <i>(gốc {vnd(up['base'])})</i>" if up["happy"] else "") + "\n"
        f"📦 Tồn kho: <b>{n}</b> acc"
        + (f"  •  🔁 Đã bán <b>{sold}</b>" if sold else "") + "\n"
        f"━━━━━━━━━━━━━━\n"
        f"🛡 <b>Bảo hành:</b> 1 đổi 1 khi acc die\n"
        + (mail_line + "\n" if mail_line else "")
    )
    if sample and sample["uid"]:
        uid_e = html.escape(sample["uid"])
        txt += f'🔍 <i>Acc mẫu: <a href="https://facebook.com/{uid_e}">facebook.com/{uid_e}</a>'
        if sample["created_date"]:
            txt += f" (tạo {html.escape(sample['created_date'])})"
        txt += "</i>\n"

    if up["happy"]:
        txt += f"⚡ <b>GIỜ VÀNG −{up['happy_pct']}%</b> <i>(giá gốc {vnd(up['base'])})</i>\n"
    avg, rcnt = db.acc_review_avg(cat_id)
    if rcnt:
        stars = "⭐" * int(round(avg)) if avg else "⭐"
        txt += f"{stars} <b>{avg}/5</b> <i>({rcnt} đánh giá)</i>\n"
        for rv in db.acc_review_list(cat_id, 2):
            q = html.escape((rv["comment"] or "")[:120])
            txt += f'   <i>"{q}"</i> {"⭐" * int(rv["stars"] or 5)}\n'
    if c["description"]:
        txt += f"\n<i>{html.escape(c['description'])}</i>\n"
    if int(c.get("credit_bonus") or 0) > 0:
        txt += f"\n🎁 Tặng <b>{c['credit_bonus']} credits</b> cho mỗi acc mua.\n"
    if p5 > 0 or p10 > 0 or p20 > 0:
        txt += f"\n🏷 <b>Mua nhiều, giảm nhiều:</b>\n"
        if p5 > 0:
            txt += f"   5 acc → <b>−{p5}%</b>\n"
        if p10 > 0:
            txt += f"   10 acc → <b>−{p10}%</b>\n"
        if p20 > 0:
            txt += f"   20 acc → <b>−{p20}%</b> <i>(giá sỉ)</i>\n"
    tier = db.member_tier_info(cb.from_user.id)
    if tier["pct"] > 0:
        txt += f"\n{tier['tier']} — bạn được <b>giảm thêm {tier['pct']}%</b> mọi đơn.\n"
    txt += "\n<i>⚡ Giao acc tự động sau thanh toán • Đổi mật khẩu ngay khi đăng nhập.</i>\n"
    kb_rows = []
    if n:
        kb_rows.append([InlineKeyboardButton(
            text=f"✅ Mua 1 — {vnd(unit)}",
            callback_data=f"accconfirm:{cat_id}:1")])
        if n >= 5 and p5 > 0:
            kb_rows.append([InlineKeyboardButton(
                text=f"🎁 Mua 5 — giảm {p5}%",
                callback_data=f"accconfirm:{cat_id}:5")])
        if n >= 10 and p10 > 0:
            kb_rows.append([InlineKeyboardButton(
                text=f"🎁 Mua 10 — giảm {p10}%",
                callback_data=f"accconfirm:{cat_id}:10")])
        if n >= 20 and p20 > 0:
            kb_rows.append([InlineKeyboardButton(
                text=f"💼 Mua 20 — giá sỉ giảm {p20}%",
                callback_data=f"accconfirm:{cat_id}:20")])
        kb_rows.append([InlineKeyboardButton(
            text="✏️ Nhập số lượng khác",
            callback_data=f"accqty:{cat_id}")])
        kb_rows.append([InlineKeyboardButton(
            text="🎯 Chọn UID cụ thể",
            callback_data=f"accpick:{cat_id}:0")])
        kb_rows.append([InlineKeyboardButton(
            text="🛒 Thêm vào giỏ",
            callback_data=f"accaddcart:{cat_id}")])
        kb_rows.append([InlineKeyboardButton(
            text="🔍 Soi acc mẫu (miễn phí)",
            callback_data=f"accpreview:{cat_id}")])
    else:
        # 4.9 Đặt cọc giữ hàng hot khi hết hàng
        try:
            dep_pct = int(db.get_setting("deposit_pct", "30") or 30)
        except Exception:
            dep_pct = 30
        dep_amt = int(c["price"]) * dep_pct // 100
        kb_rows.append([InlineKeyboardButton(
            text=f"💰 Đặt cọc {dep_pct}% ({vnd(dep_amt)}) giữ hàng",
            callback_data=f"acccoc:{cat_id}")])
        sub = db.acc_restock_sub(cb.from_user.id, cat_id)
        if sub:
            mode = f"tự mua {sub['qty']} acc" if sub["auto_buy"] else "chỉ báo hàng"
            kb_rows.append([InlineKeyboardButton(
                text=f"🔕 Hủy đăng ký ({mode})",
                callback_data=f"accunnotify:{cat_id}")])
        else:
            kb_rows.append([InlineKeyboardButton(
                text="🔔 Báo tôi khi có hàng",
                callback_data=f"accnotify:{cat_id}")])
    kb_rows.append([InlineKeyboardButton(text="🔙 Quay lại shop", callback_data="accshop_back")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    cover = (c.get("cover_photo") or "").strip()
    if cover:
        try:
            cap = txt if len(txt) <= 1000 else txt[:990] + "…"
            await cb.message.answer_photo(cover, caption=cap, parse_mode="HTML",
                                          reply_markup=kb)
        except Exception:
            await cb.message.answer(txt, parse_mode="HTML",
                                    disable_web_page_preview=True, reply_markup=kb)
    else:
        await cb.message.answer(txt, parse_mode="HTML",
                                disable_web_page_preview=True, reply_markup=kb)

@router.callback_query(F.data.startswith("accnotify:"))
async def on_acc_notify(cb: CallbackQuery):
    """Chọn kiểu báo hàng: chỉ báo, hoặc tự động trừ ví shop mua ngay khi hàng về."""
    await cb.answer()
    try:
        cat_id = int(cb.data.split(":", 1)[1])
    except Exception:
        return
    c = db.acc_category_get(cat_id)
    if not c:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 Chỉ báo cho tôi khi có hàng",
                              callback_data=f"accnotify_do:{cat_id}:0:1")],
        [InlineKeyboardButton(text="⚡ Tự mua 1 acc khi có hàng về",
                              callback_data=f"accnotify_do:{cat_id}:1:1")],
        [InlineKeyboardButton(text="⚡ Tự mua 5 acc khi có hàng về",
                              callback_data=f"accnotify_do:{cat_id}:1:5")],
        [InlineKeyboardButton(text="⚡ Tự mua 10 acc khi có hàng về",
                              callback_data=f"accnotify_do:{cat_id}:1:10")],
    ])
    await cb.message.answer(
        f"📦 <b>{html.escape(c['name'])}</b> đang hết hàng.\n\n"
        "Khi hàng về, bạn muốn:\n"
        "• <b>Chỉ báo:</b> bot nhắn riêng cho bạn.\n"
        "• <b>Tự mua:</b> bot tự trừ tiền <b>ví shop</b> và giao acc ngay "
        "(acc đã check LIVE). Nếu ví không đủ tiền lúc đó, bot chỉ báo hàng về thôi.",
        parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data.startswith("accnotify_do:"))
async def on_acc_notify_do(cb: CallbackQuery):
    await cb.answer()
    try:
        _, cat_id, auto, qty = cb.data.split(":")
        cat_id, auto, qty = int(cat_id), int(auto), int(qty)
    except Exception:
        return
    c = db.acc_category_get(cat_id)
    if not c:
        return
    is_new = db.acc_sub_restock(cb.from_user.id, cat_id, qty=qty, auto_buy=auto)
    if auto:
        await cb.message.answer(
            f"⚡ Đã đặt trước <b>{qty} acc</b> <b>{html.escape(c['name'])}</b>!\n\n"
            f"Khi hàng về, bot sẽ tự trừ <b>{vnd(int(c['price']) * qty)}</b> (giá gốc, "
            "chưa tính giảm mua nhiều) từ ví shop và giao acc ngay cho bạn.\n"
            "Nhớ nạp đủ tiền vào ví shop bằng /napshop nhé!",
            parse_mode="HTML")
    else:
        await cb.message.answer(
            f"🔔 Đã đăng ký! Khi <b>{html.escape(c['name'])}</b> có hàng về, "
            f"bot sẽ nhắn riêng cho bạn ngay.",
            parse_mode="HTML")

@router.callback_query(F.data.startswith("accunnotify:"))
async def on_acc_unnotify(cb: CallbackQuery):
    await cb.answer()
    try:
        cat_id = int(cb.data.split(":", 1)[1])
    except Exception:
        return
    db.acc_unsub_restock(cb.from_user.id, cat_id)
    await cb.message.answer("🔕 Đã hủy đăng ký báo hàng.")

@router.callback_query(F.data == "accshop_back")
async def on_acc_shop_back(cb: CallbackQuery):
    await cb.answer()
    await on_shop(cb.message)

@router.callback_query(F.data.startswith("accqty:"))
async def on_acc_qty_prompt(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    try:
        cat_id = int(cb.data.split(":", 1)[1])
    except Exception:
        return
    c = db.acc_category_get(cat_id)
    if not c or not c["active"]:
        await cb.message.answer("❌ Loại acc này không còn bán.")
        return
    n = db.acc_stock_count(cat_id)
    if n <= 0:
        await cb.message.answer("⛔ Loại này vừa hết hàng.")
        return
    await state.set_state(AccShopState.waiting_for_custom_qty)
    await state.update_data(acc_qty_cat_id=cat_id)
    await cb.message.answer(
        f"✏️ Nhập <b>số lượng</b> muốn mua (1–{n} acc):\n"
        f"<i>Gõ /huy để hủy.</i>",
        parse_mode="HTML")

@router.message(AccShopState.waiting_for_custom_qty)
async def on_acc_qty_input(msg: Message, state: FSMContext):
    _t = (msg.text or "").strip()
    if _t.startswith("/") and _t.split()[0].lower() not in ("/huy", "/cancel"):
        await state.clear()
        await msg.answer("🚫 Đã hủy thao tác đang nhập. Bạn gõ lại lệnh vừa rồi nhé.")
        return
    if _t.lower() in ("/huy", "/cancel"):
        await state.clear()
        await msg.answer("Đã hủy nhập số lượng.")
        return
    data = await state.get_data()
    cat_id = data.get("acc_qty_cat_id")
    if not cat_id:
        await state.clear()
        return
    c = db.acc_category_get(cat_id)
    if not c or not c["active"]:
        await state.clear()
        await msg.answer("❌ Loại acc này không còn bán.")
        return
    try:
        qty = int(_t.replace(".", "").replace(",", "").strip())
    except Exception:
        await msg.answer("❌ Số lượng không hợp lệ. Nhập số nguyên, ví dụ: 3")
        return
    n = db.acc_stock_count(cat_id)
    if qty < 1 or qty > n:
        await msg.answer(f"❌ Số lượng phải từ 1 đến {n}. Nhập lại nhé (hoặc /huy để hủy):")
        return
    await state.clear()
    c = dict(c)
    up = db.shop_unit_price(c)
    price = up["price"]
    p5, p10, p20 = _shop_bulk_pcts()
    bulk_pct = p20 if qty >= 20 else (p10 if qty >= 10 else (p5 if qty >= 5 else 0))
    tier = db.member_tier_info(msg.from_user.id)
    tier_pct = int(tier["pct"])
    total = price * qty
    final = total * (100 - bulk_pct) // 100
    final = final * (100 - tier_pct) // 100
    disc = []
    if bulk_pct:
        disc.append(f"giảm {bulk_pct}% mua nhiều")
    if tier_pct:
        disc.append(f"giảm {tier_pct}% hạng {tier['tier']}")
    disc_txt = (" (" + ", ".join(disc) + ")") if disc else ""
    txt = (
        f"🛒 <b>Xác nhận mua</b>\n"
        f"📦 {html.escape(c['name'])} × <b>{qty}</b> acc\n"
        f"💰 Tổng: <b>{vnd(final)}</b>{disc_txt}\n"
        f"👉 Bấm xác nhận để mua ngay:")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"✅ Xác nhận mua {qty} — {vnd(final)}",
            callback_data=f"accconfirm:{cat_id}:{qty}")],
        [InlineKeyboardButton(text="🔙 Chọn lại", callback_data=f"accbuy:{cat_id}")],
    ])
    await msg.answer(txt, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data.startswith("accconfirm:"))
async def on_acc_confirm(cb: CallbackQuery):
    await cb.answer()
    try:
        parts = cb.data.split(":")
        cat_id = int(parts[1])
        qty = int(parts[2]) if len(parts) > 2 else 1
        want_upsell = len(parts) > 3 and parts[3] == "upsell"
    except Exception:
        return
    if qty < 1:
        qty = 1
    if qty > 1000:
        qty = 1000
    tg_id = cb.from_user.id
    c = db.acc_category_get(cat_id)
    if not c or not c["active"]:
        await cb.message.answer("❌ Loại acc này không còn bán.")
        return
    c = dict(c)
    up = db.shop_unit_price(c)
    price = up["price"]
    # 4.6 Upsell: mua thêm trong 10 phút sau đơn trước được giảm thêm
    upsell_pct = 0
    try:
        wmin = int(db.get_setting("upsell_window_min", "10") or 10)
        upsell_pct_cfg = int(db.get_setting("upsell_pct", "5") or 5)
    except Exception:
        wmin, upsell_pct_cfg = 10, 5
    if want_upsell and upsell_pct_cfg > 0:
        last_at = db.acc_last_order_at(tg_id)
        if last_at and now() - last_at <= wmin * 60:
            upsell_pct = upsell_pct_cfg
        else:
            want_upsell = False
    p5, p10, p20 = _shop_bulk_pcts()
    bulk_pct = p20 if qty >= 20 else (p10 if qty >= 10 else (p5 if qty >= 5 else 0))
    tier = db.member_tier_info(tg_id)
    tier_pct = int(tier["pct"])
    total = price * qty
    final = total * (100 - bulk_pct) // 100
    final = final * (100 - tier_pct) // 100
    final = final * (100 - upsell_pct) // 100
    final, promo_code = db.preview_user_promo(tg_id, final, wallet="shop")
    if db.acc_stock_count(cat_id) < qty:
        await cb.message.answer(
            f"⛔ Kho chỉ còn <b>{db.acc_stock_count(cat_id)}</b> acc, "
            f"không đủ {qty} acc. Bạn chọn số lượng ít hơn nhé.",
            parse_mode="HTML")
        return
    u = db.get_user(tg_id)
    balance = int(u["shop_balance"] or 0) if u else 0
    if balance < final:
        disc_txt = []
        if bulk_pct:
            disc_txt.append(f"giảm {bulk_pct}% mua nhiều")
        if tier_pct:
            disc_txt.append(f"giảm {tier_pct}% hạng {tier['tier']}")
        if upsell_pct:
            disc_txt.append(f"giảm {upsell_pct}% mua thêm trong {wmin} phút")
        if promo_code:
            disc_txt.append(f"mã {promo_code}")
        await cb.message.answer(
            f"😢 <b>VÍ SHOP KHÔNG ĐỦ</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"Mua {qty} acc: <b>{vnd(final)}</b>"
            + (f"\n<i>🎉 {', '.join(disc_txt)}</i>" if disc_txt else "") + "\n"
            f"👛 Ví shop của bạn: <b>{vnd(balance)}</b>\n"
            f"💸 Còn thiếu: <b>{vnd(final - balance)}</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"Nạp thêm bằng /napshop (tự động, quét QR) rồi mua lại nhé.",
            parse_mode="HTML",
        )
        return
    # Trừ tiền ví shop trước, giao acc sau (nguyên tử ở acc_sell_many)
    if not db.adjust_shop_balance(tg_id, -final, f"mua_acc:{cat_id}x{qty}" + (f" (promo {promo_code})" if promo_code else "")):
        await cb.message.answer(
            "❌ <b>Ví shop không đủ!</b>\nNạp thêm bằng /napshop rồi mua lại nhé."
            + _SHOP_WALLET_HINT,
            parse_mode="HTML")
        return
    # Check LIVE trước khi giao (chỉ sạp Acc Facebook): chỉ bán acc đã check live
    _need_check = _cat_live_check(cat_id)
    if _need_check:
        await cb.message.answer("🔍 <b>Đang kiểm tra chất lượng acc...</b>", parse_mode="HTML")
    sold_orders, _sell_fail = await _sell_live_stock(
        cb.bot, tg_id, final, qty,
        lambda seen: db.acc_stock_pick_candidates(cat_id, qty + 4, seen),
        check_live=_need_check)
    if not sold_orders:
        db.add_shop_balance_only(tg_id, final, "hoan_tien_khong_du_hang_live")
        await cb.message.answer(
            "😔 <b>Rất tiếc, hiện không đủ hàng</b> để giao cho bạn.\n"
            f"Tiền <b>{vnd(final)}</b> đã được hoàn vào ví shop.\n"
            "Bạn quay lại sau hoặc chọn loại acc khác nhé!",
            parse_mode="HTML")
        return
    db.finalize_user_promo(tg_id, promo_code)  # trừ lượt promo SAU khi giao acc thành công
    await _acc_after_purchase(cb.bot, cb.message, cb.from_user, c, cat_id, tg_id,
                              final, qty, sold_orders,
                              want_upsell, upsell_pct_cfg, wmin)

def _pick_uid_page(cat_id: int, page: int):
    """1 trang UID còn hàng. Trả (rows, has_next)."""
    off = max(0, int(page)) * _PICK_PAGE_SIZE
    rows = [dict(r) for r in db.get_conn().execute(
        "SELECT id, uid, created_date FROM acc_stock "
        "WHERE cat_id=? AND status='AVAILABLE' ORDER BY id LIMIT ? OFFSET ?",
        (cat_id, _PICK_PAGE_SIZE + 1, off)).fetchall()]
    return rows[:_PICK_PAGE_SIZE], len(rows) > _PICK_PAGE_SIZE

def _pick_uid_kb(cat_id: int, page: int):
    rows, has_next = _pick_uid_page(cat_id, page)
    kb_rows = []
    for r in rows:
        label = f"👤 {r['uid']}"
        if r.get("created_date"):
            label += f" ({r['created_date']})"
        kb_rows.append([InlineKeyboardButton(
            text=label, callback_data=f"accpickuid:{cat_id}:{r['id']}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="‹ Trước", callback_data=f"accpick:{cat_id}:{page - 1}"))
    if has_next:
        nav.append(InlineKeyboardButton(
            text="Sau ›", callback_data=f"accpick:{cat_id}:{page + 1}"))
    if nav:
        kb_rows.append(nav)
    kb_rows.append([InlineKeyboardButton(
        text="✏️ Nhập UID tay", callback_data=f"accpicktype:{cat_id}")])
    kb_rows.append([InlineKeyboardButton(
        text="🔙 Quay lại", callback_data=f"accbuy:{cat_id}")])
    return InlineKeyboardMarkup(inline_keyboard=kb_rows)

def _pick_uid_confirm(cat_id: int, stock: dict, tg_id: int):
    """Dựng (text, keyboard) màn xác nhận mua UID cụ thể."""
    c = db.acc_category_get(cat_id)
    c = dict(c)
    final, price, tier, tier_pct, upsell_pct, wmin, _ = _uid_final_price(tg_id, c)
    prev, promo_code = db.preview_user_promo(tg_id, final, wallet="shop")
    u = db.get_user(tg_id)
    balance = int(u["shop_balance"] or 0) if u else 0
    disc = []
    if tier_pct:
        disc.append(f"giảm {tier_pct}% hạng {tier['tier']}")
    if upsell_pct:
        disc.append(f"giảm {upsell_pct}% mua thêm trong {wmin} phút")
    if promo_code:
        disc.append(f"mã {promo_code}")
    txt = (
        f"🎯 <b>Xác nhận mua UID</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"👤 UID: <code>{html.escape(stock['uid'] or '')}</code>\n"
        f"📦 Loại: <b>{html.escape(c['name'])}</b>\n"
    )
    if stock.get("created_date"):
        txt += f"📅 Ngày tạo: <code>{html.escape(stock['created_date'])}</code>\n"
    txt += (
        f"💰 Thanh toán: <b>{vnd(prev)}</b>"
        + (f" <i>({', '.join(disc)})</i>" if disc else "") + "\n"
        f"👛 Ví shop của bạn: <b>{vnd(balance)}</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"<i>Acc sẽ được kiểm tra LIVE trước khi giao (sạp Facebook).</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"✅ Mua UID này — {vnd(prev)}",
            callback_data=f"accbuyuid:{cat_id}:{stock['id']}")],
        [InlineKeyboardButton(
            text="🛒 Thêm UID này vào giỏ",
            callback_data=f"accuidcart:{cat_id}:{stock['id']}")],
        [InlineKeyboardButton(
            text="‹ Chọn UID khác",
            callback_data=f"accpick:{cat_id}:0")],
    ])
    return txt, kb

@router.callback_query(F.data.startswith("accpick:"))
async def on_acc_pick(cb: CallbackQuery):
    """Danh sách UID còn hàng (phân trang) để khách chọn."""
    await cb.answer()
    try:
        _, cat_id, page = cb.data.split(":")
        cat_id, page = int(cat_id), max(0, int(page))
    except Exception:
        return
    c = db.acc_category_get(cat_id)
    if not c or not c["active"]:
        await cb.message.answer("❌ Loại acc này không còn bán.")
        return
    total = db.acc_stock_count(cat_id)
    if total <= 0:
        await cb.message.answer("⛔ Loại này vừa hết hàng.")
        return
    pages = (total + _PICK_PAGE_SIZE - 1) // _PICK_PAGE_SIZE
    await cb.message.answer(
        f"🎯 <b>Chọn UID muốn mua</b> — <i>{html.escape(c['name'])}</i>\n"
        f"📦 Còn <b>{total}</b> acc (trang {page + 1}/{max(pages, 1)})\n"
        f"<i>Bấm vào UID để xem giá và xác nhận.</i>",
        parse_mode="HTML", reply_markup=_pick_uid_kb(cat_id, page),
        disable_web_page_preview=True)

@router.callback_query(F.data.startswith("accpickuid:"))
async def on_acc_pick_uid(cb: CallbackQuery):
    """Khách bấm 1 UID -> màn xác nhận giá."""
    await cb.answer()
    try:
        _, cat_id, stock_id = cb.data.split(":")
        cat_id, stock_id = int(cat_id), int(stock_id)
    except Exception:
        return
    c = db.acc_category_get(cat_id)
    r = db.get_conn().execute(
        "SELECT id, uid, created_date, status FROM acc_stock "
        "WHERE id=? AND cat_id=?", (stock_id, cat_id)).fetchone()
    if not c or not c["active"] or not r:
        await cb.message.answer("❌ Acc này không còn tồn tại.")
        return
    r = dict(r)
    if r["status"] != "AVAILABLE":
        await cb.message.answer(
            "😔 UID này vừa được mua mất rồi. Bạn chọn UID khác nhé.",
            parse_mode="HTML",
            reply_markup=_pick_uid_kb(cat_id, 0))
        return
    txt, kb = _pick_uid_confirm(cat_id, r, cb.from_user.id)
    await cb.message.answer(txt, parse_mode="HTML", reply_markup=kb,
                            disable_web_page_preview=True)

@router.callback_query(F.data.startswith("accpicktype:"))
async def on_acc_pick_type(cb: CallbackQuery, state: FSMContext):
    """Khách muốn gõ tay UID."""
    await cb.answer()
    try:
        cat_id = int(cb.data.split(":", 1)[1])
    except Exception:
        return
    c = db.acc_category_get(cat_id)
    if not c or not c["active"]:
        await cb.message.answer("❌ Loại acc này không còn bán.")
        return
    if db.acc_stock_count(cat_id) <= 0:
        await cb.message.answer("⛔ Loại này vừa hết hàng.")
        return
    await state.set_state(AccShopState.waiting_for_pick_uid)
    await state.update_data(pick_uid_cat_id=cat_id)
    await cb.message.answer(
        "✏️ Nhập <b>UID</b> acc muốn mua:\n"
        "<i>Nhập 1 hoặc nhiều UID, cách nhau bằng dấu phẩy, dấu cách "
        "hoặc xuống dòng (tối đa 20 UID/lần).\nGõ /huy để hủy.</i>",
        parse_mode="HTML")

@router.message(AccShopState.waiting_for_pick_uid)
async def on_acc_pick_input(msg: Message, state: FSMContext):
    _t = (msg.text or "").strip()
    if _t.lower() in ("/huy", "/cancel"):
        await state.clear()
        await msg.answer("Đã hủy chọn UID.")
        return
    if _t.startswith("/"):
        await state.clear()
        return
    data = await state.get_data()
    cat_id = data.get("pick_uid_cat_id")
    # Tách nhiều UID: dãy số (kèm đuôi .0 của Excel) cách nhau bởi
    # dấu phẩy / cách / xuống dòng. Giữ thứ tự, loại trùng, tối đa 20.
    uids = []
    for m in re.findall(r"\d+(?:\.0+)?", _t):
        u = re.sub(r"\.0+$", "", m)
        if u and u not in uids:
            uids.append(u)
    uids = uids[:20]
    if not uids:
        await msg.answer("❌ Không tìm thấy UID nào hợp lệ. "
                         "Bạn nhập lại dãy số UID nhé.")
        return
    valid, gone, missing = [], [], []
    for uid in uids:
        r = db.get_conn().execute(
            "SELECT id, uid, created_date, status FROM acc_stock "
            "WHERE cat_id=? AND uid=?", (cat_id, uid)).fetchone()
        if not r:
            missing.append(uid)
        elif r["status"] != "AVAILABLE":
            gone.append(uid)
        else:
            valid.append(dict(r))
    # 1 UID duy nhất: giữ nguyên luồng cũ (màn xác nhận 1 acc)
    if len(uids) == 1:
        await state.clear()
        uid = uids[0]
        if uid in missing:
            await msg.answer(
                "❌ Không tìm thấy UID này trong kho của loại acc đã chọn.\n"
                "Bạn kiểm tra lại UID nhé.")
            return
        if uid in gone:
            await msg.answer("😔 UID này không còn hàng (đã bán hoặc đang cách ly). "
                             "Bạn chọn UID khác nhé.")
            return
        txt, kb = _pick_uid_confirm(cat_id, valid[0], msg.from_user.id)
        await msg.answer(txt, parse_mode="HTML", reply_markup=kb,
                         disable_web_page_preview=True)
        return
    # Nhiều UID: lưu vào state để 2 nút bên dưới dùng
    await state.update_data(pick_multi=[{"id": s["id"], "uid": s["uid"]}
                                       for s in valid],
                            pick_multi_cat_id=cat_id)
    c = db.acc_category_get(cat_id)
    c = dict(c) if c else {}
    unit_final, _, _, _, _, wmin, upsell_pct_cfg = _uid_final_price(
        msg.from_user.id, c)
    lines = [f"🎯 <b>Tìm thấy {len(valid)}/{len(uids)} UID:</b>"]
    for s in valid:
        lines.append(f"✅ <code>{html.escape(s['uid'])}</code> — "
                     f"{vnd(unit_final)}")
    for uid in gone:
        lines.append(f"😔 <code>{html.escape(uid)}</code> — đã bán/hết hàng")
    for uid in missing:
        lines.append(f"❌ <code>{html.escape(uid)}</code> — không có trong kho")
    kb_rows = []
    if valid:
        total = unit_final * len(valid)
        kb_rows.append([InlineKeyboardButton(
            text=f"✅ Mua ngay {len(valid)} UID — {vnd(total)}",
            callback_data=f"accmultibuy:{cat_id}")])
        kb_rows.append([InlineKeyboardButton(
            text=f"🛒 Thêm {len(valid)} UID vào giỏ",
            callback_data=f"accmulticart:{cat_id}")])
    kb_rows.append([InlineKeyboardButton(
        text="✏️ Nhập lại", callback_data=f"accpicktype:{cat_id}")])
    await msg.answer("\n".join(lines), parse_mode="HTML",
                     reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
                     disable_web_page_preview=True)

def _pick_multi_state(data: dict, cat_id: int):
    """Lấy danh sách UID đã lưu trong state, kiểm tra còn hạn + đúng loại."""
    if data.get("pick_multi_cat_id") != cat_id:
        return None
    lst = data.get("pick_multi") or []
    return lst if lst else None

@router.callback_query(F.data.startswith("accmulticart:"))
async def on_acc_multi_cart(cb: CallbackQuery, state: FSMContext):
    """Thêm nhiều UID đã nhập vào giỏ hàng."""
    await cb.answer()
    try:
        cat_id = int(cb.data.split(":", 1)[1])
    except Exception:
        return
    data = await state.get_data()
    lst = _pick_multi_state(data, cat_id)
    if not lst:
        await cb.answer("⏰ Danh sách đã hết hạn. Bạn nhập lại UID nhé.",
                        show_alert=True)
        return
    await state.clear()
    ok = dup = miss = 0
    for s in lst:
        r = db.cart_uid_add(cb.from_user.id, cat_id, s["id"])
        if r == "ok":
            ok += 1
        elif r == "exists":
            dup += 1
        else:
            miss += 1
    parts = [f"🛒 Đã thêm <b>{ok}</b> UID vào giỏ!"]
    if dup:
        parts.append(f"<i>({dup} UID đã có sẵn trong giỏ)</i>")
    if miss:
        parts.append(f"<i>({miss} UID vừa hết hàng)</i>")
    parts.append("Vào /giohang để thanh toán nhé.")
    await cb.message.answer("\n".join(parts), parse_mode="HTML")

@router.callback_query(F.data.startswith("accmultibuy:"))
async def on_acc_multi_buy(cb: CallbackQuery, state: FSMContext):
    """Mua ngay nhiều UID đã nhập: trừ ví 1 lần -> check LIVE từng acc -> giao."""
    await cb.answer()
    try:
        cat_id = int(cb.data.split(":", 1)[1])
    except Exception:
        return
    data = await state.get_data()
    lst = _pick_multi_state(data, cat_id)
    if not lst:
        await cb.answer("⏰ Danh sách đã hết hạn. Bạn nhập lại UID nhé.",
                        show_alert=True)
        return
    await state.clear()
    tg_id = cb.from_user.id
    c = db.acc_category_get(cat_id)
    if not c or not c["active"]:
        await cb.message.answer("❌ Loại acc này không còn bán.")
        return
    c = dict(c)
    final1, _, _, _, _, wmin, upsell_pct_cfg = _uid_final_price(tg_id, c)
    # Validate lại từng acc (có thể bị mua mất sau khi nhập)
    priced, gone = [], []
    for s in lst:
        r = db.get_conn().execute(
            "SELECT id, uid, cat_id FROM acc_stock "
            "WHERE id=? AND cat_id=? AND status='AVAILABLE'",
            (s["id"], cat_id)).fetchone()
        if r:
            priced.append((c, dict(r), final1))
        else:
            gone.append(s["uid"])
    if not priced:
        await cb.message.answer(
            "😔 Các UID vừa chọn đều đã hết hàng. Bạn chọn UID khác nhé.")
        return
    grand = final1 * len(priced)
    grand, promo_code = db.preview_user_promo(tg_id, grand, wallet="shop")
    u = db.get_user(tg_id)
    balance = int(u["shop_balance"] or 0) if u else 0
    if balance < grand:
        await cb.message.answer(
            f"😢 <b>VÍ SHOP KHÔNG ĐỦ</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"Mua {len(priced)} UID: <b>{vnd(grand)}</b>\n"
            f"👛 Ví shop của bạn: <b>{vnd(balance)}</b>\n"
            f"💸 Còn thiếu: <b>{vnd(grand - balance)}</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"Nạp thêm bằng /napshop (tự động, quét QR) rồi mua lại nhé."
            + _SHOP_WALLET_HINT,
            parse_mode="HTML")
        return
    if not db.adjust_shop_balance(
            tg_id, -grand,
            f"mua_nhieu_uid:{cat_id}:{len(priced)}acc"
            + (f" (promo {promo_code})" if promo_code else "")):
        await cb.message.answer(
            "❌ <b>Ví shop không đủ!</b>\nNạp thêm bằng /napshop rồi mua lại nhé."
            + _SHOP_WALLET_HINT,
            parse_mode="HTML")
        return
    await cb.message.answer("🔍 <b>Đang kiểm tra chất lượng acc...</b>",
                            parse_mode="HTML")
    delivered, failed = await _sell_uid_items(cb.bot, tg_id, priced)
    paid = grand - sum(p for _, _, p in failed)
    for _cat, _s, p in failed:
        db.add_shop_balance_only(tg_id, p, "hoan_tien_multi_uid")
    if not delivered:
        await cb.message.answer(
            "😔 <b>Rất tiếc, không giao được acc nào</b> "
            "(die hoặc vừa bị mua mất).\n"
            f"Tiền <b>{vnd(grand)}</b> đã được hoàn vào ví shop.",
            parse_mode="HTML")
        return
    orders = delivered  # _sell_uid_items đã trả list dict đơn hàng
    db.finalize_user_promo(tg_id, promo_code)  # trừ lượt promo SAU khi giao acc thành công
    await _acc_after_purchase(cb.bot, cb.message, cb.from_user, c, cat_id,
                              tg_id, paid, len(delivered), orders, False,
                              upsell_pct_cfg, wmin)
    if failed or gone:
        names = [html.escape(s["uid"]) for _, s, _ in failed] + \
                [html.escape(u) for u in gone]
        await cb.message.answer(
            f"⚠️ {len(failed) + len(gone)} UID không giao được "
            f"({', '.join(names)}): đã hoàn tiền phần này vào ví shop.",
            parse_mode="HTML")

@router.callback_query(F.data.startswith("accuidcart:"))
async def on_acc_uid_cart(cb: CallbackQuery):
    """Thêm UID cụ thể vào giỏ hàng."""
    await cb.answer()
    try:
        _, cat_id, stock_id = cb.data.split(":")
        cat_id, stock_id = int(cat_id), int(stock_id)
    except Exception:
        return
    c = db.acc_category_get(cat_id)
    if not c or not c["active"]:
        await cb.answer("❌ Loại acc này không còn bán.", show_alert=True)
        return
    r = db.get_conn().execute(
        "SELECT uid FROM acc_stock WHERE id=? AND cat_id=?",
        (stock_id, cat_id)).fetchone()
    uid = r["uid"] if r else ""
    res = db.cart_uid_add(cb.from_user.id, cat_id, stock_id)
    if res == "ok":
        await cb.answer(f"🛒 Đã thêm UID {uid} vào giỏ!", show_alert=True)
    elif res == "exists":
        await cb.answer("UID này đã có trong giỏ rồi.", show_alert=True)
    else:
        await cb.answer("😔 UID này vừa hết hàng.", show_alert=True)

@router.callback_query(F.data.startswith("accbuyuid:"))
async def on_acc_buy_uid(cb: CallbackQuery):
    """Mua đúng UID khách đã chọn: trừ ví shop -> check LIVE acc đó -> giao."""
    await cb.answer()
    try:
        _, cat_id, stock_id = cb.data.split(":")
        cat_id, stock_id = int(cat_id), int(stock_id)
    except Exception:
        return
    tg_id = cb.from_user.id
    c = db.acc_category_get(cat_id)
    r = db.get_conn().execute(
        "SELECT id, uid, cat_id FROM acc_stock "
        "WHERE id=? AND cat_id=? AND status='AVAILABLE'",
        (stock_id, cat_id)).fetchone()
    if not c or not c["active"] or not r:
        await cb.message.answer(
            "😔 UID này vừa được mua mất hoặc không còn hàng. "
            "Bạn chọn UID khác nhé.", parse_mode="HTML")
        return
    r = dict(r)
    c = dict(c)
    final, price, tier, tier_pct, upsell_pct, wmin, upsell_pct_cfg = \
        _uid_final_price(tg_id, c)
    final, promo_code = db.preview_user_promo(tg_id, final, wallet="shop")
    u = db.get_user(tg_id)
    balance = int(u["shop_balance"] or 0) if u else 0
    if balance < final:
        await cb.message.answer(
            f"😢 <b>VÍ SHOP KHÔNG ĐỦ</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"Mua UID <code>{html.escape(r['uid'])}</code>: <b>{vnd(final)}</b>\n"
            f"👛 Ví shop của bạn: <b>{vnd(balance)}</b>\n"
            f"💸 Còn thiếu: <b>{vnd(final - balance)}</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"Nạp thêm bằng /napshop (tự động, quét QR) rồi mua lại nhé."
            + _SHOP_WALLET_HINT,
            parse_mode="HTML")
        return
    if not db.adjust_shop_balance(
            tg_id, -final,
            f"mua_acc_uid:{cat_id}:{r['uid']}" + (f" (promo {promo_code})" if promo_code else "")):
        await cb.message.answer(
            "❌ <b>Ví shop không đủ!</b>\nNạp thêm bằng /napshop rồi mua lại nhé."
            + _SHOP_WALLET_HINT,
            parse_mode="HTML")
        return
    # Check LIVE đúng acc khách chọn (chỉ sạp Acc Facebook)
    if _cat_live_check(cat_id):
        await cb.message.answer("🔍 <b>Đang kiểm tra chất lượng acc...</b>",
                                parse_mode="HTML")
        l_ids, d_ids, u_ids = await _check_stock_live([r])
        if stock_id in d_ids:
            db.acc_stock_quarantine([stock_id])
            try:
                await _notify_die_quarantine(cb.bot, [r])
            except Exception:
                pass
            db.add_shop_balance_only(tg_id, final, "hoan_tien_uid_die")
            await cb.message.answer(
                "😔 UID này vừa kiểm tra <b>DIE</b> nên không giao được "
                "(đã cách ly khỏi kho).\n"
                f"Tiền <b>{vnd(final)}</b> đã được hoàn vào ví shop. "
                "Bạn chọn UID khác nhé!",
                parse_mode="HTML")
            return
        if stock_id in u_ids:
            db.add_shop_balance_only(tg_id, final, "hoan_tien_uid_infra")
            await cb.message.answer(
                "⚠️ Không kiểm tra được chất lượng acc lúc này (lỗi mạng).\n"
                f"Tiền <b>{vnd(final)}</b> đã được hoàn vào ví shop. "
                "Bạn thử lại sau nhé!",
                parse_mode="HTML")
            return
    # Bán đúng acc đã chọn (nguyên tử — chống 2 người mua cùng UID)
    sold = db.acc_sell_stock_ids(cat_id, tg_id, final, [stock_id])
    if not sold:
        db.add_shop_balance_only(tg_id, final, "hoan_tien_uid_race")
        await cb.message.answer(
            "😔 UID này vừa được người khác mua mất.\n"
            f"Tiền <b>{vnd(final)}</b> đã được hoàn vào ví shop. "
            "Bạn chọn UID khác nhé!",
            parse_mode="HTML")
        return
    orders = [dict(db.acc_get_order(oid)) for oid, _ in sold]
    db.finalize_user_promo(tg_id, promo_code)  # trừ lượt promo SAU khi giao acc thành công
    await _acc_after_purchase(cb.bot, cb.message, cb.from_user, c, cat_id, tg_id,
                              final, 1, orders, False, upsell_pct_cfg, wmin)

async def _cart_refresh(cb: CallbackQuery):
    r = _cart_render(cb.from_user.id)
    if not r:
        try:
            await cb.message.edit_text(
                "🛒 Giỏ hàng đang trống.\nVào /shop chọn acc rồi bấm \"🛒 Thêm vào giỏ\" nhé!")
        except Exception:
            pass
        return
    txt, kb = r
    try:
        await cb.message.edit_text(txt, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass

def _cart_validated_lines(tg_id: int):
    """Validate giỏ: bỏ loại ngừng bán/hết hàng, cắt số lượng theo tồn kho.
    Trả (lines[(cat, qty)], notes[str])."""
    lines, notes = [], []
    for it in db.cart_list(tg_id):
        c = db.acc_category_get(it["cat_id"])
        if not c or not c["active"]:
            db.cart_remove(tg_id, it["cat_id"])
            notes.append(f"❌ {it['name']}: ngừng bán → đã xóa khỏi giỏ")
            continue
        stock = db.acc_stock_count(it["cat_id"])
        if stock <= 0:
            db.cart_remove(tg_id, it["cat_id"])
            notes.append(f"⛔ {it['name']}: vừa hết hàng → đã xóa khỏi giỏ")
            continue
        qty = it["qty"]
        if qty > stock:
            db.cart_set_qty(tg_id, it["cat_id"], stock)
            notes.append(f"⚠️ {it['name']}: kho chỉ còn {stock} → đã giảm số lượng")
            qty = stock
        lines.append((dict(c), qty))
    return lines, notes

def _cart_uid_validated(tg_id: int):
    """Validate món UID cụ thể trong giỏ: loại còn bán + acc còn AVAILABLE.
    Trả (items[(cat_dict, stock_dict)], notes[str])."""
    items, notes = [], []
    for it in db.cart_uid_list(tg_id):
        c = db.acc_category_get(it["cat_id"])
        if not c or not c["active"]:
            db.cart_uid_remove(tg_id, it["stock_id"])
            notes.append(f"❌ UID {it['uid']}: loại ngừng bán → đã xóa khỏi giỏ")
            continue
        s = db.get_conn().execute(
            "SELECT id, uid, cat_id FROM acc_stock WHERE id=? AND status='AVAILABLE'",
            (it["stock_id"],)).fetchone()
        if not s:
            db.cart_uid_remove(tg_id, it["stock_id"])
            notes.append(f"😔 UID {it['uid']}: vừa hết hàng → đã xóa khỏi giỏ")
            continue
        items.append((dict(c), dict(s)))
    return items, notes

@router.callback_query(F.data.startswith("accaddcart:"))
async def on_acc_add_cart(cb: CallbackQuery):
    try:
        cat_id = int(cb.data.split(":", 1)[1])
    except Exception:
        await cb.answer()
        return
    tg_id = cb.from_user.id
    c = db.acc_category_get(cat_id)
    if not c or not c["active"]:
        await cb.answer("❌ Loại acc này không còn bán.", show_alert=True)
        return
    stock = db.acc_stock_count(cat_id)
    if stock <= 0:
        await cb.answer("⛔ Loại này đang hết hàng.", show_alert=True)
        return
    q = db.cart_add(tg_id, cat_id, 1)
    if q > stock:
        db.cart_set_qty(tg_id, cat_id, stock)
        q = stock
    n = db.cart_count(tg_id)
    await cb.answer(f"🛒 Đã thêm vào giỏ ({n} acc). Gõ /giohang để xem và thanh toán.")

@router.callback_query(F.data == "cartnoop")
async def on_cart_noop(cb: CallbackQuery):
    await cb.answer()

@router.callback_query(F.data.startswith("cartinc:"))
async def on_cart_inc(cb: CallbackQuery):
    try:
        cat_id = int(cb.data.split(":", 1)[1])
    except Exception:
        await cb.answer()
        return
    tg_id = cb.from_user.id
    cur = next((it["qty"] for it in db.cart_list(tg_id) if it["cat_id"] == cat_id), 0)
    if not cur:
        await cb.answer()
        return
    stock = db.acc_stock_count(cat_id)
    if cur + 1 > stock:
        await cb.answer(f"⛔ Kho chỉ còn {stock} acc loại này.", show_alert=True)
        return
    await cb.answer()
    db.cart_set_qty(tg_id, cat_id, cur + 1)
    await _cart_refresh(cb)

@router.callback_query(F.data.startswith("cartdec:"))
async def on_cart_dec(cb: CallbackQuery):
    try:
        cat_id = int(cb.data.split(":", 1)[1])
    except Exception:
        await cb.answer()
        return
    tg_id = cb.from_user.id
    cur = next((it["qty"] for it in db.cart_list(tg_id) if it["cat_id"] == cat_id), 0)
    await cb.answer()
    db.cart_set_qty(tg_id, cat_id, cur - 1)
    await _cart_refresh(cb)

@router.callback_query(F.data.startswith("cartdel:"))
async def on_cart_del(cb: CallbackQuery):
    try:
        cat_id = int(cb.data.split(":", 1)[1])
    except Exception:
        await cb.answer()
        return
    await cb.answer("Đã xóa khỏi giỏ.")
    db.cart_remove(cb.from_user.id, cat_id)
    await _cart_refresh(cb)

@router.callback_query(F.data.startswith("cartuiddel:"))
async def on_cart_uid_del(cb: CallbackQuery):
    """Xóa 1 UID cụ thể khỏi giỏ."""
    try:
        stock_id = int(cb.data.split(":", 1)[1])
    except Exception:
        await cb.answer()
        return
    await cb.answer("Đã xóa UID khỏi giỏ.")
    db.cart_uid_remove(cb.from_user.id, stock_id)
    await _cart_refresh(cb)

@router.callback_query(F.data == "cartclear")
async def on_cart_clear(cb: CallbackQuery):
    db.cart_clear(cb.from_user.id)
    db.cart_uid_clear(cb.from_user.id)
    await cb.answer("🗑 Đã xóa toàn bộ giỏ hàng.")
    await _cart_refresh(cb)

@router.callback_query(F.data == "cartview")
async def on_cart_view(cb: CallbackQuery):
    await cb.answer()
    r = _cart_render(cb.from_user.id)
    if not r:
        await cb.message.answer("🛒 Giỏ hàng đang trống.")
        return
    txt, kb = r
    await cb.message.answer(txt, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "cartcheckout")
async def on_cart_checkout(cb: CallbackQuery):
    await cb.answer()
    tg_id = cb.from_user.id
    lines, notes = _cart_validated_lines(tg_id)
    uid_items, uid_notes = _cart_uid_validated(tg_id)
    notes += uid_notes
    if not lines and not uid_items:
        txt = "🛒 Giỏ hàng không còn món nào mua được."
        if notes:
            txt += "\n" + "\n".join(notes)
        await cb.message.answer(txt)
        return
    priced = []
    uid_priced = []
    grand = 0
    for c, qty in lines:
        lp = _line_price(c, qty, tg_id)
        priced.append((c, qty, lp))
        grand += lp["final"]
    for c, stock in uid_items:
        lp = _line_price(c, 1, tg_id)
        uid_priced.append((c, stock, lp))
        grand += lp["final"]
    u = db.get_user(tg_id)
    balance = int(u["shop_balance"] or 0) if u else 0
    txt = ["🧾 <b>XÁC NHẬN THANH TOÁN</b>", "━━━━━━━━━━━━━━"]
    for c, qty, lp in priced:
        txt.append(f"{_cat_icon(c['name'])} <b>{html.escape(c['name'])}</b>\n"
                   f"   {qty} × {vnd(lp['unit'])} = <b>{vnd(lp['final'])}</b>")
    for c, stock, lp in uid_priced:
        txt.append(f"👤 <code>{html.escape(stock['uid'] or '')}</code> "
                   f"<i>({html.escape(c['name'])})</i> = <b>{vnd(lp['final'])}</b>")
    txt += ["━━━━━━━━━━━━━━",
            f"🧾 <b>Tổng cộng: {vnd(grand)}</b>",
            f"👛 Ví shop: <b>{vnd(balance)}</b>"]
    if notes:
        txt.append("")
        txt += [f"<i>{html.escape(n)}</i>" for n in notes]
    if balance < grand:
        txt.append(f"\n❌ <b>Ví shop không đủ!</b> Còn thiếu <b>{vnd(grand - balance)}</b>.\n"
                   "Nạp thêm bằng /napshop rồi quay lại nhé." + _SHOP_WALLET_HINT)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Sửa giỏ hàng", callback_data="cartview")]])
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"✅ Xác nhận thanh toán {vnd(grand)}",
                                  callback_data="cartconfirm")],
            [InlineKeyboardButton(text="🔙 Sửa giỏ hàng", callback_data="cartview")]])
    await cb.message.answer("\n".join(txt), parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "cartconfirm")
async def on_cart_confirm(cb: CallbackQuery):
    await cb.answer()
    tg_id = cb.from_user.id
    lines, _notes = _cart_validated_lines(tg_id)
    uid_items, _uid_notes = _cart_uid_validated(tg_id)
    if not lines and not uid_items:
        await cb.message.answer("🛒 Giỏ hàng không còn món nào mua được.")
        return
    priced = []
    uid_priced = []
    grand = 0
    for c, qty in lines:
        stock = db.acc_stock_count(c["id"])
        if stock < qty:
            await cb.message.answer(
                f"⛔ <b>{html.escape(c['name'])}</b> chỉ còn {stock} acc, "
                f"không đủ {qty}.\nBạn sửa lại giỏ hàng rồi thanh toán lại nhé.",
                parse_mode="HTML")
            return
        lp = _line_price(c, qty, tg_id)
        priced.append((c, qty, lp))
        grand += lp["final"]
    for c, stock in uid_items:
        lp = _line_price(c, 1, tg_id)
        uid_priced.append((c, stock, lp["final"]))
        grand += lp["final"]
    grand, promo_code = db.preview_user_promo(tg_id, grand, wallet="shop")
    u = db.get_user(tg_id)
    balance = int(u["shop_balance"] or 0) if u else 0
    if balance < grand:
        await cb.message.answer(
            f"❌ Ví shop không đủ ({vnd(balance)} < {vnd(grand)}). "
            "Nạp thêm bằng /napshop nhé." + _SHOP_WALLET_HINT, parse_mode="HTML")
        return
    if not db.adjust_shop_balance(tg_id, -grand, f"mua_giohang:{len(priced)}mon" + (f" (promo {promo_code})" if promo_code else "")):
        await cb.message.answer("❌ Ví shop không đủ. Nạp thêm bằng /napshop nhé."
                                + _SHOP_WALLET_HINT,
                                parse_mode="HTML")
        return
    await cb.message.answer("🔍 <b>Đang kiểm tra chất lượng acc...</b>", parse_mode="HTML")
    delivered, paid_total = [], 0
    failed = []
    for c, qty, lp in priced:
        cat_id = c["id"]
        sold, _fail = await _sell_live_stock(
            cb.bot, tg_id, lp["final"], qty,
            lambda seen, _cid=cat_id, _q=qty: db.acc_stock_pick_candidates(_cid, _q + 4, seen),
            check_live=_cat_live_check(cat_id))
        if sold:
            delivered += sold
            paid_total += lp["final"]
        else:
            failed.append((c, qty))
            db.add_shop_balance_only(tg_id, lp["final"], "hoan_tien_giohang_thieu_live")
    uid_failed = []
    if uid_priced:
        u_delivered, u_failed = await _sell_uid_items(cb.bot, tg_id, uid_priced)
        delivered += u_delivered
        paid_total += sum(p for _, _, p in uid_priced) - sum(p for _, _, p in u_failed)
        for cat, stock, price in u_failed:
            uid_failed.append((cat, stock))
            db.add_shop_balance_only(tg_id, price, "hoan_tien_giohang_uid")
    db.cart_clear(tg_id)
    db.cart_uid_clear(tg_id)
    if not delivered:
        await cb.message.answer(
            "😔 <b>Rất tiếc, hiện không đủ hàng</b> để giao.\n"
            f"Tiền <b>{vnd(grand)}</b> đã được hoàn vào ví shop.",
            parse_mode="HTML")
        return
    db.finalize_user_promo(tg_id, promo_code)  # trừ lượt promo SAU khi giao acc thành công
    for order in delivered:
        order_id = order["id"]
        await cb.message.answer(
            f"🎉 <b>MUA THÀNH CÔNG!</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"{_cat_icon(order['cat_name'])} <b>{html.escape(order['cat_name'])}</b>\n"
            f"🧾 Đơn hàng: <b>#{order_id}</b>\n"
            f"👤 UID: <code>{html.escape(order['uid'] or '')}</code>\n"
            f"💰 Đã thanh toán: <b>{vnd(order['price'])}</b>\n"
            f"{_live_line(order['cat_id'])}"
            f"━━━━━━━━━━━━━━\n\n"
            f"{_pickup_suffix()}",
            parse_mode="HTML", reply_markup=_acc_delivery_kb(order_id))
    by_cat = {}
    for o in delivered:
        by_cat.setdefault(o["cat_id"], []).append(o)
    summary = [f"🧾 <b>Xong giỏ hàng:</b> giao <b>{len(delivered)}</b> acc, "
               f"thanh toán <b>{vnd(paid_total)}</b>."]
    if failed:
        fnames = ", ".join(f"{html.escape(c['name'])}×{q}" for c, q in failed)
        summary.append(f"⚠️ {fnames}: không đủ acc LIVE → đã hoàn tiền dòng này.")
    if uid_failed:
        unames = ", ".join(f"UID {html.escape(s['uid'] or '')}" for _, s in uid_failed)
        summary.append(f"⚠️ {unames}: không giao được (die/bị mua mất) → đã hoàn tiền.")
    extras = []
    for cid, olist in by_cat.items():
        crow = db.acc_category_get(cid)
        c = dict(crow) if crow else None
        bonus_per = int((c.get("credit_bonus") or 0)) if c else 0
        if bonus_per > 0:
            db.add_credits(tg_id, bonus_per * len(olist), f"combo_mua_giohang:{cid}")
            extras.append(f"🎁 Tặng <b>{bonus_per * len(olist)} credits</b> "
                          f"({html.escape(c['name']) if c else ''})")
    tickets = db.spin_add_tickets(tg_id, len(delivered))
    extras.append(f"🎡 Nhận <b>{len(delivered)} vé quay</b> may mắn — gõ /quay "
                  f"(đang có {tickets} vé)")
    try:
        per = int(db.get_setting("loyalty_per_vnd", "100000") or 100000)
    except Exception:
        per = 100000
    pts = paid_total // per if per > 0 else 0
    if pts > 0:
        new_pts = db.loyalty_add(tg_id, pts, "mua_giohang")
        extras.append(f"⭐ Tích <b>{pts} điểm</b> loyalty (tổng: <b>{new_pts}</b> điểm) — "
                      "đủ điểm đổi acc miễn phí bằng /doiqua")
    summary.append("")
    summary += extras
    rkey = f"c{min(o['id'] for o in delivered)}" if delivered else None
    await cb.message.answer("\n".join(summary), parse_mode="HTML",
                            reply_markup=_spin_kb(rkey, tg_id, paid_total))
    try:
        guide = db.get_setting("postbuy_guide", "") or (
            "📋 <b>HƯỚNG DẪN SAU KHI MUA ACC</b>\n"
            "1️⃣ Đổi mật khẩu ngay sau khi đăng nhập.\n"
            "2️⃣ Bật xác thực 2 lớp (2FA) cho acc.\n"
            "3️⃣ 3 ngày đầu: đừng đổi tên/avatar vội, lướt newsfeed nhẹ nhàng.\n"
            "4️⃣ Không đăng nhập nhiều acc cùng 1 IP/proxy lạ.\n"
            "💡 Làm đúng các bước trên giúp acc sống lâu, ít bị checkpoint!")
        await cb.message.answer(guide, parse_mode="HTML")
    except Exception:
        pass
    try:
        f1_id, comm = db.ref_shop_commission(tg_id, paid_total)
        if f1_id and comm:
            try:
                await cb.bot.send_message(
                    f1_id,
                    "🎁 <b>Hoa hồng shop acc!</b>\n"
                    f"Người bạn giới thiệu vừa mua giỏ hàng {len(delivered)} acc — "
                    f"bạn nhận <b>{vnd(comm)}</b>.",
                    parse_mode="HTML")
            except Exception:
                pass
    except Exception:
        pass
    await _notify_purchase_admin(cb.bot, cb.from_user, delivered)
    # Mua nhiều acc 1 lúc: tự động gửi 1 file gộp
    await _send_merged_acc_file(cb.message, delivered)
    try:
        warn_at = int(db.get_setting("acc_low_stock_warn", "20") or 20)
    except Exception:
        warn_at = 20
    for cid in by_cat:
        left = db.acc_stock_count(cid)
        if left <= warn_at:
            c = db.acc_category_get(cid)
            if c:
                await _notify_admin_smart(
                    cb.bot,
                    f"⚠️ <b>Sắp hết hàng:</b> {html.escape(c['name'])} chỉ còn "
                    f"<b>{left}</b> acc. Nhập thêm bằng /themacc {cid}",
                    perm="kho")

@router.callback_query(F.data.startswith("acc2fa:"))
async def on_acc_2fa(cb: CallbackQuery):
    """Khách lấy mã 2FA hiện tại của đơn mình đã mua."""
    try:
        order_id = int(cb.data.split(":", 1)[1])
    except Exception:
        return
    order = db.acc_get_order(order_id)
    if not order or int(order["tg_id"]) != cb.from_user.id:
        await cb.answer("❌ Không tìm thấy đơn hàng.", show_alert=True)
        return
    order = dict(order)
    code = _totp_now(order.get("totp") or "")
    if not code:
        await cb.answer("❌ Đơn này không có 2FA.", show_alert=True)
        return
    import time as _t
    remain = 30 - int(_t.time() % 30)
    await cb.answer()
    await cb.message.answer(
        f"🔐 <b>MÃ 2FA — ĐƠN #{order_id}</b>\n"
        f"👤 UID: <code>{html.escape(order['uid'] or '')}</code>\n"
        f"🔢 Mã hiện tại: <code>{code}</code> <i>(hết hạn sau {remain}s)</i>\n"
        f"⚠️ Mã đổi 30s/lần — cần mã mới thì bấm lại nút này.",
        parse_mode="HTML",
    )

@router.callback_query(F.data.startswith("accshow:"))
async def on_acc_show(cb: CallbackQuery):
    """Hiện toàn bộ thông tin acc để sao chép."""
    await cb.answer()
    try:
        order_id = int(cb.data.split(":", 1)[1])
    except Exception:
        return
    order = db.acc_get_order(order_id)
    if not order or int(order["tg_id"]) != cb.from_user.id:
        await cb.answer("❌ Không tìm thấy đơn hàng.", show_alert=True)
        return
    await cb.message.answer(_acc_delivery_caption(dict(order)), parse_mode="HTML")

@router.callback_query(F.data.startswith("accfile:"))
async def on_acc_file(cb: CallbackQuery):
    """Xuất acc đã mua ra file .txt / .xlsx."""
    await cb.answer()
    try:
        _, oid, fmt = cb.data.split(":")
        order_id = int(oid)
    except Exception:
        return
    order = db.acc_get_order(order_id)
    if not order or int(order["tg_id"]) != cb.from_user.id:
        await cb.answer("❌ Không tìm thấy đơn hàng.", show_alert=True)
        return
    order = dict(order)
    uid = (order.get("uid") or "").strip()
    fields = [order.get("uid") or "", order.get("password") or "",
              order.get("created_date") or "", order.get("backup_mail") or "",
              order.get("note") or "", order.get("totp") or "",
              order.get("cookie") or "", order.get("token") or ""]
    if fmt == "xlsx":
        import openpyxl
        import io as _io
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "acc"
        ws.append(["UID", "MK", "Ngày tạo", "Mail thay", "Ghi chú",
                   "2FA", "Cookie", "Token"])
        ws.append(fields)
        buf = _io.BytesIO()
        wb.save(buf)
        data = buf.getvalue()
        fname = f"acc_don_{order_id}.xlsx"
    else:
        data = "|".join(fields).encode("utf-8")
        fname = f"acc_don_{order_id}.txt"
    await cb.message.answer_document(
        BufferedInputFile(data, filename=fname),
        caption=f"📥 File acc đơn <b>#{order_id}</b> — "
                f"UID <code>{html.escape(uid)}</code>",
        parse_mode="HTML")


@router.message(Command("coc"))
async def on_coc(msg: Message):
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: <code>/coc &lt;id_loại&gt;</code> (xem id: /shop)",
                         parse_mode="HTML")
        return
    try:
        cat_id = int(parts[1])
    except Exception:
        await msg.answer("❌ ID loại phải là số.")
        return
    await _do_deposit(msg.from_user.id, cat_id, msg, msg.bot)

@router.message(Command("coclist"))
async def on_coclist(msg: Message):
    deps = db.acc_deposit_list(msg.from_user.id)
    if not deps:
        await msg.answer("💰 Bạn chưa đặt cọc loại acc nào.")
        return
    lines = ["💰 <b>ĐẶT CỌC CỦA BẠN</b>", "━━━━━━━━━━━━", ""]
    st_map = {"WAITING": "⏳ chờ hàng", "DONE": "✅ đã giao", "CANCELLED": "❌ đã hủy"}
    for d in deps:
        d = dict(d)
        lines.append(
            f"#{d['id']} — <b>{html.escape(d['cat_name'] or '')}</b>\n"
            f"   Cọc: {vnd(d['amount'])} — {st_map.get(d['status'], d['status'])}")
    lines += ["", "Hủy cọc (hoàn tiền): <code>/huycoc &lt;id_cọc&gt;</code>"]
    await msg.answer("\n".join(lines), parse_mode="HTML")

@router.message(Command("huycoc"))
async def on_huycoc(msg: Message):
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: <code>/huycoc &lt;id_cọc&gt;</code>",
                         parse_mode="HTML")
        return
    try:
        dep_id = int(parts[1])
    except Exception:
        await msg.answer("❌ ID cọc phải là số.")
        return
    amt = db.acc_deposit_cancel(dep_id, msg.from_user.id)
    if amt is None:
        await msg.answer("❌ Không tìm thấy khoản cọc chờ hàng này của bạn.")
        return
    db.add_shop_balance_only(msg.from_user.id, amt, f"huy_coc:{dep_id}")
    await msg.answer(f"✅ Đã hủy cọc <b>#{dep_id}</b>, hoàn <b>{vnd(amt)}</b> vào ví shop.",
                     parse_mode="HTML")

__all__ = [
    "_PICK_PAGE_SIZE",
    "on_shop",
    "on_acc_buy",
    "on_acc_notify",
    "on_acc_notify_do",
    "on_acc_unnotify",
    "on_acc_shop_back",
    "on_acc_qty_prompt",
    "on_acc_qty_input",
    "on_acc_confirm",
    "_pick_uid_page",
    "_pick_uid_kb",
    "_pick_uid_confirm",
    "on_acc_pick",
    "on_acc_pick_uid",
    "on_acc_pick_type",
    "on_acc_pick_input",
    "_pick_multi_state",
    "on_acc_multi_cart",
    "on_acc_multi_buy",
    "on_acc_uid_cart",
    "on_acc_buy_uid",
    "_cart_refresh",
    "_cart_validated_lines",
    "_cart_uid_validated",
    "on_acc_add_cart",
    "on_cart_noop",
    "on_cart_inc",
    "on_cart_dec",
    "on_cart_del",
    "on_cart_uid_del",
    "on_cart_clear",
    "on_cart_view",
    "on_cart_checkout",
    "on_cart_confirm",
    "on_acc_2fa",
    "on_acc_show",
    "on_acc_file",
    "on_coc",
    "on_coclist",
    "on_huycoc",
]