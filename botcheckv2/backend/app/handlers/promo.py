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

from .core import TienIchState, _SHOP_WALLET_HINT, _is_admin, router
from .common import AccShopState, _acc_delivery_kb, _do_spin, _doiqua_redeem_acc, _fb_stall_gift_cats, _live_line, _loyalty_random_cfg, _loyalty_random_today, _notify_purchase_admin, _order_total_for_randkey, _pickup_suffix, _sell_live_stock, _spin_kb

_doiqua_processing: set = set()

@router.message(TienIchState.waiting_for_promo)
async def on_tienich_promo(msg: Message, state: FSMContext):
    if (msg.text or "").strip().lower() in ("/huy", "/cancel"):
        await state.clear()
        await msg.answer("❌ Đã hủy áp mã giảm giá.")
        return
    code = (msg.text or "").strip().upper()
    await state.clear()
    if not code:
        await msg.answer("❌ Mã trống, thử lại nhé.")
        return
    ok, why, row = db.promo_valid(code)
    if not ok:
        await msg.answer(f"❌ {why}")
        return
    db.set_user_promo(msg.from_user.id, code)
    exp_txt = f"\n⏳ Hết hạn: {time.strftime('%d/%m/%Y %H:%M', time.localtime(row['expires_at']))}" if row["expires_at"] else ""
    left_txt = f"\n🎫 Còn lại: <b>{int(row['max_uses']) - int(row['used_count'])}</b> lượt" if row["max_uses"] else ""
    await msg.answer(
        f"✅ <b>Áp mã thành công!</b>\n\n"
        f"🎟️ Mã: <b>{code}</b>\n"
        f"💸 Giảm: <b>{int(row['pct'])}%</b> cho lần mua gói credit tiếp theo{exp_txt}{left_txt}\n\n"
        f"<i>Mở /muacredit để mua ngay.</i>",
        parse_mode="HTML")

@router.message(TienIchState.waiting_for_birthday)
async def on_tienich_birthday(msg: Message, state: FSMContext):
    import datetime as _dt
    import re as _re
    if (msg.text or "").strip().lower() in ("/huy", "/cancel"):
        await state.clear()
        await msg.answer("❌ Đã hủy nhập ngày sinh.")
        return
    arg = (msg.text or "").strip()
    await state.clear()
    m = _re.match(r"^\s*(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\s*$", arg)
    if not m:
        await msg.answer("❌ Sai định dạng. Ví dụ: <code>25/12/2000</code>", parse_mode="HTML")
        return
    try:
        d = _dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        await msg.answer("❌ Ngày không hợp lệ. Ví dụ: <code>25/12/2000</code>", parse_mode="HTML")
        return
    today = _dt.date.today()
    if d > today or d.year < 1920:
        await msg.answer("❌ Ngày sinh không hợp lệ.")
        return
    db.set_dob(msg.from_user.id, d.isoformat())
    await msg.answer(f"✅ Đã lưu ngày sinh <b>{d.strftime('%d/%m/%Y')}</b>!\n🎁 Bot sẽ tự tặng quà vào đúng ngày sinh nhật hằng năm.", parse_mode="HTML")

@router.message(Command("sinhnhat"))
async def on_birthday_cmd(msg: Message):
    """Nhap ngay sinh de nhan qua sinh nhat tu dong moi nam."""
    import datetime as _dt
    import re as _re
    parts = (msg.text or "").split(None, 1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    u = db.get_user(msg.from_user.id) or {}
    try:
        cur = dict(u).get("dob") or ""
    except Exception:
        cur = ""
    if not arg:
        if cur:
            d = _dt.date(*map(int, cur.split("-")))
            await msg.answer(f"🎂 Ngày sinh của bạn: <b>{d.strftime('%d/%m/%Y')}</b>\n"
                             "Bot sẽ tự tặng quà vào đúng ngày sinh nhật hằng năm.\n"
                             "Muốn đổi, gửi: <code>/sinhnhat ngày/tháng/năm</code>")
        else:
            await msg.answer("🎂 Nhập ngày sinh để nhận quà sinh nhật hằng năm:\n"
                             "<code>/sinhnhat 25/12/2000</code>")
        return
    m = _re.match(r"^\s*(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\s*$", arg)
    if not m:
        await msg.answer("❌ Sai định dạng. Ví dụ: <code>/sinhnhat 25/12/2000</code>")
        return
    try:
        d = _dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        await msg.answer("❌ Ngày không hợp lệ. Ví dụ: <code>/sinhnhat 25/12/2000</code>")
        return
    today = _dt.date.today()
    if d > today or d.year < 1920:
        await msg.answer("❌ Ngày sinh không hợp lệ.")
        return
    db.set_dob(msg.from_user.id, d.isoformat())
    note = ""
    if (d.month, d.day) == (today.month, today.day):
        note = "\n<i>Lưu ý: cần nhập trước sinh nhật ít nhất 1 ngày mới được tặng quà năm nay.</i>"
    await msg.answer(f"✅ Đã lưu ngày sinh: <b>{d.strftime('%d/%m/%Y')}</b>\n"
                     f"🎁 Bot sẽ tự tặng giftcode vào sinh nhật của bạn mỗi năm.{note}")

@router.callback_query(F.data == "promo_input")
async def on_promo_input(cb: CallbackQuery):
    await cb.message.answer(
        "🎟️ <b>NHẬP MÃ GIẢM GIÁ</b>\n\nGõ lệnh:\n<code>/promo MÃ_CODE</code>\n\nVD: <code>/promo SALE20</code>",
        parse_mode="HTML",
    )
    await cb.answer()

@router.message(Command("promo"))
async def on_promo(msg: Message):
    """User áp mã giảm giá cho lần mua gói credit tiếp theo."""
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await msg.answer("🎟️ Cú pháp: <code>/promo MÃ_CODE</code>\nVD: <code>/promo SALE20</code>", parse_mode="HTML")
        return
    code = parts[1].strip().upper()
    ok, why, row = db.promo_valid(code)
    if not ok:
        await msg.answer(f"❌ {why}")
        return
    db.set_user_promo(msg.from_user.id, code)
    try:
        w = db.parse_wallet(row["wallet"])
    except Exception:
        w = "main"
    exp_txt = ""
    if row["expires_at"]:
        exp_txt = f"\n⏳ Hết hạn: {time.strftime('%d/%m/%Y %H:%M', time.localtime(row['expires_at']))}"
    left_txt = ""
    if row["max_uses"]:
        left_txt = f"\n🎫 Còn lại: <b>{int(row['max_uses']) - int(row['used_count'])}</b> lượt"
    scope_txt = "cho lần mua gói credit tiếp theo (trừ ví chính)" if w == "main" else "cho lần mua acc ở /shop tiếp theo (trừ ví shop)"
    go_txt = "<i>Mở /muacredit để mua ngay.</i>" if w == "main" else "<i>Mở /shop để mua ngay.</i>"
    await msg.answer(
        f"✅ <b>Áp mã thành công!</b>\n\n"
        f"🎟️ Mã: <b>{code}</b>\n"
        f"💸 Giảm: <b>{int(row['pct'])}%</b> {scope_txt}{exp_txt}{left_txt}\n\n"
        f"{go_txt}",
        parse_mode="HTML",
    )

@router.message(Command("taopromo"))
async def on_taopromo(msg: Message):
    """Admin tạo mã giảm giá flash sale. Cú pháp: /taopromo <CODE> <phần_trăm> [số_lượt] [số_giờ] [ví]"""
    if not _is_admin(msg.from_user.id):
        await msg.answer("❌ Bạn không có quyền!")
        return
    parts = (msg.text or "").split()
    if len(parts) < 3:
        await msg.answer(
            "🎟️ Cú pháp: <code>/taopromo &lt;CODE&gt; &lt;phần_trăm&gt; [số_lượt_dùng] [số_giờ_hiệu_lực] [ví]</code>\n"
            "VD: <code>/taopromo SALE20 20 100 24</code> — giảm 20% khi mua gói credit (ví chính)\n"
            "VD: <code>/taopromo SHOP10 10 0 0 shop</code> — giảm 10% khi mua acc ở /shop (ví shop)",
            parse_mode="HTML",
        )
        return
    try:
        pct = int(parts[2])
    except ValueError:
        await msg.answer("❌ Phần trăm phải là số (1-90).")
        return
    max_uses = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
    hours = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
    wallet = db.parse_wallet(parts[5]) if len(parts) > 5 else "main"
    if wallet == "credits":
        await msg.answer("❌ Mã giảm % chỉ áp dụng cho <b>ví chính</b> hoặc <b>ví shop</b>.", parse_mode="HTML")
        return
    ok, txt = db.create_promo(parts[1], pct, max_uses, hours, wallet)
    extra = ""
    if ok:
        if max_uses:
            extra += f"\n🎫 Giới hạn: {max_uses} lượt"
        if hours:
            extra += f"\n⏳ Hiệu lực: {hours} giờ"
        extra += f"\n\n📢 Gõ <code>/flashsale {parts[1].strip().upper()}</code> để thông báo cho toàn bộ user."
    await msg.answer(("✅ " if ok else "❌ ") + txt + extra, parse_mode="HTML")

@router.message(Command("xoapromo"))
async def on_xoapromo(msg: Message):
    if not _is_admin(msg.from_user.id):
        await msg.answer("❌ Bạn không có quyền!")
        return
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer("Cú pháp: <code>/xoapromo MÃ_CODE</code>", parse_mode="HTML")
        return
    if db.delete_promo(parts[1]):
        await msg.answer(f"✅ Đã xóa mã <b>{parts[1].strip().upper()}</b>.", parse_mode="HTML")
    else:
        await msg.answer("❌ Mã không tồn tại.")

@router.message(Command("dspromo"))
async def on_dspromo(msg: Message):
    if not _is_admin(msg.from_user.id):
        await msg.answer("❌ Bạn không có quyền!")
        return
    rows = db.list_promos()
    if not rows:
        await msg.answer("Chưa có mã giảm giá nào. Tạo bằng /taopromo")
        return
    lines = ["🎟️ <b>DANH SÁCH MÃ GIẢM GIÁ</b>", "━━━━━━━━━━━━━━━"]
    for r in rows:
        d = dict(r)
        status = "✅" if db.promo_valid(d["code"])[0] else "⛔"
        lim = f"{int(d['used_count'])}/{int(d['max_uses'])}" if d["max_uses"] else f"{int(d['used_count'])}/∞"
        try:
            wlbl = db.wallet_label(d["wallet"])
        except Exception:
            wlbl = "ví chính"
        lines.append(f"{status} <code>{d['code']}</code> — giảm {int(d['pct'])}% ({wlbl}) — đã dùng {lim}")
    await msg.answer("\n".join(lines), parse_mode="HTML")

@router.callback_query(F.data.startswith("accpreview:"))
async def on_acc_preview(cb: CallbackQuery):
    """Soi 5 acc AVAILABLE ngẫu nhiên (chỉ hiện link để khách check, không lộ pass/cookie/token)."""
    await cb.answer()
    try:
        cat_id = int(cb.data.split(":", 1)[1])
    except Exception:
        return
    c = db.acc_category_get(cat_id)
    if not c:
        return
    rows = db.get_conn().execute(
        "SELECT uid, created_date FROM acc_stock "
        "WHERE cat_id=? AND status='AVAILABLE' ORDER BY RANDOM() LIMIT 5",
        (cat_id,)).fetchall()
    rows = [r for r in rows if (r["uid"] or "").strip()]
    if not rows:
        await cb.message.answer("⛔ Loại này vừa hết hàng.")
        return
    e = html.escape
    parts = [f"🔍 <b>SOI ACC MẪU</b> — {e(c['name'])}",
             f"Lấy ngẫu nhiên <b>{len(rows)}</b> acc trong kho để bạn check chất lượng:", ""]
    for i, r in enumerate(rows, 1):
        uid = (r["uid"] or "").strip()
        line = f'{i}. <a href="https://facebook.com/{e(uid)}">facebook.com/{e(uid)}</a>'
        if r["created_date"]:
            line += f" (tạo {e(r['created_date'])})"
        parts.append(line)
    parts += ["", "🔒 Mật khẩu, 2FA, cookie, token chỉ hiện sau khi bạn mua.",
              "🛡 Các acc trong kho đã qua quét kiểm tra chất lượng."]
    await cb.message.answer("\n".join(parts), parse_mode="HTML", disable_web_page_preview=True)

@router.message(Command("quay"))
async def on_quay(msg: Message):
    """Vòng quay may mắn — mỗi acc mua = 1 vé."""
    tg_id = msg.from_user.id
    tickets = db.spin_get_tickets(tg_id)
    if tickets <= 0:
        await msg.answer(
            "🎡 <b>VÒNG QUAY MAY MẮN</b>\n\n"
            "Bạn chưa có vé quay nào.\n"
            "👉 Mua acc ở /shop — mỗi acc = 1 vé quay, trúng credits và tiền mặt vào số dư!",
            parse_mode="HTML")
        return
    await _do_spin(msg, tg_id, tickets)

@router.callback_query(F.data == "spin_now")
async def on_spin_now(cb: CallbackQuery):
    await cb.answer()
    tickets = db.spin_get_tickets(cb.from_user.id)
    if tickets <= 0:
        await cb.message.answer("😅 Bạn đã hết vé quay. Mua acc ở /shop để nhận thêm vé nhé!")
        return
    await _do_spin(cb.message, cb.from_user.id, tickets)

@router.callback_query(F.data.startswith("randpts:"))
async def on_randpts(cb: CallbackQuery):
    tg_id = cb.from_user.id
    key = (cb.data or "").split(":", 1)[1].strip()[:40]
    cfg = _loyalty_random_cfg()
    if not cfg or not key:
        await cb.answer("Tính năng đang tắt.", show_alert=True)
        return
    lo, hi, cap, min_order, daily_max = cfg
    # Chống cày điểm: kiểm tra lại điều kiện lúc bấm (đơn tối thiểu + lượt/ngày)
    if min_order > 0 and _order_total_for_randkey(key) < min_order:
        await cb.answer(
            f"Đơn này chưa đủ {vnd(min_order)} để nhận điểm ngẫu nhiên.",
            show_alert=True)
        return
    if daily_max > 0 and _loyalty_random_today(tg_id) >= daily_max:
        await cb.answer(
            f"Hôm nay bạn đã nhận đủ {daily_max} lượt điểm ngẫu nhiên rồi!",
            show_alert=True)
        return
    import random as _random
    with db._lock:
        c = db.get_conn()
        dup = c.execute("SELECT 1 FROM loyalty_history WHERE tg_id=? AND reason=?",
                        (tg_id, f"randpts:{key}")).fetchone()
        r = c.execute("SELECT COALESCE(SUM(delta),0) AS s FROM loyalty_history "
                      "WHERE tg_id=? AND reason LIKE 'randpts:%'", (tg_id,)).fetchone()
        try:
            received = int(r["s"] or 0)
        except Exception:
            received = 0
        if dup:
            outcome, pts, total = "dup", 0, 0
        elif received >= cap:
            outcome, pts, total = "capped", 0, 0
        else:
            pts = min(_random.randint(lo, hi), cap - received)
            total = db.loyalty_add(tg_id, pts, f"randpts:{key}")
            outcome = "ok"
    if outcome == "dup":
        await cb.answer("Đơn này bạn đã nhận điểm rồi!", show_alert=True)
        return
    if outcome == "capped":
        await cb.answer(f"Bạn đã nhận đủ tối đa {cap} điểm ngẫu nhiên!",
                        show_alert=True)
        return
    try:
        await cb.message.edit_reply_markup(reply_markup=_spin_kb())
    except Exception:
        pass
    new_recv = received + pts
    await cb.answer(f"🎲 Bạn nhận được {pts} điểm ngẫu nhiên! ({new_recv}/{cap})",
                    show_alert=True)
    try:
        await cb.message.answer(
            f"🎲 <b>ĐIỂM NGẪU NHIÊN</b>\n"
            f"Chúc mừng! Bạn nhận được <b>{pts}</b> điểm loyalty "
            f"(đã nhận <b>{new_recv}/{cap}</b> điểm ngẫu nhiên, "
            f"tổng: <b>{total}</b> điểm) — đủ điểm đổi acc miễn phí bằng /doiqua.",
            parse_mode="HTML")
    except Exception:
        pass

@router.message(Command("doiqua"))
async def on_doiqua(msg: Message):
    """Đổi điểm loyalty lấy quà (acc hoặc tiền về ví)."""
    tg_id = msg.from_user.id
    try:
        need = int(db.get_setting("loyalty_redeem_points", "10") or 10)
    except Exception:
        need = 10
    pts = db.loyalty_get(tg_id)
    mode = db.get_setting("loyalty_redeem_mode", "acc") or "acc"
    if mode == "money":
        try:
            amt = int(db.get_setting("loyalty_redeem_amount", "0") or 0)
        except Exception:
            amt = 0
        wallet = db.get_setting("loyalty_redeem_wallet", "main") or "main"
        if amt <= 0:
            await msg.answer(
                "🎁 <b>ĐỔI QUÀ LOYALTY</b>\n\n"
                "Shop chưa mở quà đổi điểm. Quay lại sau nhé!",
                parse_mode="HTML")
            return
        if pts < need:
            await msg.answer(
                f"🎁 <b>ĐỔI QUÀ LOYALTY</b>\n\n"
                f"Điểm của bạn: <b>{pts}</b> / cần <b>{need}</b> điểm.\n"
                f"Quà: <b>{db.wallet_amount_text(wallet, amt)}</b> vào {db.wallet_label(wallet)}.\n\n"
                f"👉 Mua acc ở /shop để tích thêm điểm (1 điểm / 100k).",
                parse_mode="HTML")
            return
        if not db.loyalty_consume(tg_id, need):
            await msg.answer("😅 Điểm của bạn vừa thay đổi, thử lại nhé!")
            return
        db.credit_wallet(tg_id, amt, "Đổi điểm loyalty", wallet)
        await msg.answer(
            f"🎉 <b>ĐỔI QUÀ THÀNH CÔNG!</b> (−{need} điểm)\n"
            f"🎁 Bạn nhận <b>{db.wallet_amount_text(wallet, amt)}</b> vào {db.wallet_label(wallet)}.\n"
            f"⭐ Điểm còn lại: <b>{db.loyalty_get(tg_id)}</b>",
            parse_mode="HTML")
        return
    scope = db.get_setting("loyalty_redeem_scope", "cat") or "cat"
    if scope == "stall":
        # Khách tự chọn 1 loại acc bất kỳ trong gian hàng Acc Facebook
        cats = _fb_stall_gift_cats()
        if not cats:
            await msg.answer(
                "🎁 <b>ĐỔI QUÀ LOYALTY</b>\n\n"
                "Gian hàng Acc Facebook hiện hết hàng, bạn quay lại sau nhé!",
                parse_mode="HTML")
            return
        if pts < need:
            await msg.answer(
                f"🎁 <b>ĐỔI QUÀ LOYALTY</b>\n\n"
                f"Điểm của bạn: <b>{pts}</b> / cần <b>{need}</b> điểm.\n"
                f"Quà: 1 acc <b>bất kỳ trong gian hàng Acc Facebook</b> miễn phí.\n\n"
                f"👉 Mua acc ở /shop để tích thêm điểm (1 điểm / 100k).",
                parse_mode="HTML")
            return
        kb_rows = [[InlineKeyboardButton(
            text="🎲 Nhận acc BẤT KỲ trong gian hàng",
            callback_data="doiqua_any")]]
        kb_rows += [
            [InlineKeyboardButton(
                text=f"🎁 {c['name']} ({n} acc)",
                callback_data=f"doiqua_stall:{c['id']}")]
            for c, n in cats
        ]
        kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        await msg.answer(
            f"🎁 <b>ĐỔI QUÀ LOYALTY</b>\n\n"
            f"Điểm của bạn: <b>{pts}</b> / cần <b>{need}</b> điểm.\n"
            f"Chọn cách nhận quà:\n"
            f"🎲 <b>Bất kỳ</b> — shop chọn ngẫu nhiên 1 acc trong gian hàng Acc Facebook\n"
            f"🎁 <b>Cố định</b> — bạn chọn 1 loại acc bên dưới:",
            parse_mode="HTML", reply_markup=kb)
        return
    cat_id = db.loyalty_redeem_cat()
    c = db.acc_category_get(cat_id) if cat_id else None
    if not c or not c["active"]:
        await msg.answer(
            "🎁 <b>ĐỔI QUÀ LOYALTY</b>\n\n"
            "Shop chưa mở quà đổi điểm. Quay lại sau nhé!",
            parse_mode="HTML")
        return
    c = dict(c)
    if pts < need:
        await msg.answer(
            f"🎁 <b>ĐỔI QUÀ LOYALTY</b>\n\n"
            f"Điểm của bạn: <b>{pts}</b> / cần <b>{need}</b> điểm.\n"
            f"Quà: 1 acc <b>{html.escape(c['name'])}</b> miễn phí.\n\n"
            f"👉 Mua acc ở /shop để tích thêm điểm (1 điểm / 100k).",
            parse_mode="HTML")
        return
    ok, order, reason = await _doiqua_redeem_acc(msg.bot, tg_id, c["id"], need)
    if not ok:
        await msg.answer(_doiqua_fail_text(reason), parse_mode="HTML")
        return
    await _doiqua_success(msg.bot, msg, msg.from_user, tg_id, order, c["id"], need)

def _doiqua_try_lock(tg_id: int) -> bool:
    """Chống bấm callback lặp: mỗi user chỉ đổi quà 1 lần tại 1 thời điểm."""
    if tg_id in _doiqua_processing:
        return False
    _doiqua_processing.add(tg_id)
    return True

def _doiqua_unlock(tg_id: int):
    _doiqua_processing.discard(tg_id)

def _doiqua_fail_text(reason: str) -> str:
    if reason == "out_of_stock":
        return "😅 Quà đổi điểm hiện hết hàng, bạn quay lại sau nhé!"
    if reason == "points_changed":
        return "😅 Điểm của bạn vừa thay đổi, thử lại nhé!"
    return ("😔 Quà đổi điểm hiện hết acc <b>LIVE</b> (shop vừa kiểm tra lại), "
            "điểm đã được hoàn lại. Bạn quay lại sau nhé!")

async def _doiqua_success(bot, answer_target, user, tg_id: int, order, cat_id: int, need: int):
    """Tin MUA THÀNH CÔNG sau khi đổi quà (dùng chung cho /doiqua và nút chọn)."""
    order_id = order["id"]
    tickets = db.spin_add_tickets(tg_id, 1)
    text = (
        f"🎉 <b>ĐỔI QUÀ THÀNH CÔNG!</b> (−{need} điểm)\n"
        f"🎡 +1 vé quay may mắn (đang có {tickets} vé — gõ /quay)\n"
        f"👤 UID: <code>{html.escape(order['uid'] or '')}</code>\n"
        f"{_live_line(cat_id)}\n"
        f"{_pickup_suffix()}"
    )
    await answer_target.answer(text, parse_mode="HTML",
                               reply_markup=_acc_delivery_kb(order_id))
    await _notify_purchase_admin(bot, user, [order])

@router.callback_query(F.data.startswith("doiqua_stall:"))
async def on_doiqua_stall(cb: CallbackQuery):
    """Khách đã chọn 1 loại acc cố định trong gian hàng FB để đổi quà."""
    try:
        cat_id = int(cb.data.split(":", 1)[1])
    except Exception:
        await cb.answer("❌ Loại không hợp lệ.", show_alert=True)
        return
    scope = db.get_setting("loyalty_redeem_scope", "cat") or "cat"
    mode = db.get_setting("loyalty_redeem_mode", "acc") or "acc"
    c = db.acc_category_get(cat_id)
    if scope != "stall" or mode != "acc" or not c or not c["active"] \
            or (dict(c).get("stall") or "Acc Facebook") != "Acc Facebook":
        await cb.answer("😅 Quà đổi điểm đã thay đổi, thử lại nhé!", show_alert=True)
        return
    try:
        need = int(db.get_setting("loyalty_redeem_points", "10") or 10)
    except Exception:
        need = 10
    await _doiqua_redeem_flow(cb, cat_id, need)

@router.callback_query(F.data == "doiqua_any")
async def on_doiqua_any(cb: CallbackQuery):
    """Khách chọn nhận acc BẤT KỲ — shop chọn ngẫu nhiên 1 loại còn hàng."""
    scope = db.get_setting("loyalty_redeem_scope", "cat") or "cat"
    mode = db.get_setting("loyalty_redeem_mode", "acc") or "acc"
    if scope != "stall" or mode != "acc":
        await cb.answer("😅 Quà đổi điểm đã thay đổi, thử lại nhé!", show_alert=True)
        return
    cats = _fb_stall_gift_cats()
    if not cats:
        await cb.answer("😅 Gian hàng hiện hết hàng, bạn quay lại sau nhé!",
                        show_alert=True)
        return
    cat_id = random.choice([c["id"] for c, _n in cats])
    try:
        need = int(db.get_setting("loyalty_redeem_points", "10") or 10)
    except Exception:
        need = 10
    await _doiqua_redeem_flow(cb, cat_id, need)

async def _doiqua_redeem_flow(cb: CallbackQuery, cat_id: int, need: int) -> None:
    """Chạy đổi quà từ nút bấm: chống bấm lặp + gỡ nút sau khi đổi xong."""
    tg_id = cb.from_user.id
    if not _doiqua_try_lock(tg_id):
        await cb.answer("⏳ Đang xử lý đổi quà, chờ xíu nhé!")
        return
    try:
        if db.loyalty_get(tg_id) < need:
            await cb.answer(f"😅 Bạn cần {need} điểm để đổi quà.", show_alert=True)
            return
        ok, order, reason = await _doiqua_redeem_acc(cb.bot, tg_id, cat_id, need)
        if not ok:
            await cb.message.answer(_doiqua_fail_text(reason), parse_mode="HTML")
            await cb.answer()
            return
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await _doiqua_success(cb.bot, cb.message, cb.from_user, tg_id, order,
                              cat_id, need)
        await cb.answer("🎉 Đổi quà thành công!")
    finally:
        _doiqua_unlock(tg_id)

@router.message(Command("loyaltyrandom"))
async def on_loyaltyrandom(msg: Message):
    """Admin: cài điểm loyalty ngẫu nhiên sau mua.
    /loyaltyrandom <min> <max> <cap_user> [min_order] [daily_max] (cap=0 tắt)"""
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 4:
        cfg = _loyalty_random_cfg()
        if not cfg:
            cur = "đang tắt"
        else:
            cur = (f"{cfg[0]}–{cfg[1]} điểm/lần, tối đa {cfg[2]} điểm/user"
                   f"{f', đơn ≥ {vnd(cfg[3])}' if cfg[3] > 0 else ''}"
                   f"{f', tối đa {cfg[4]} lượt/ngày' if cfg[4] > 0 else ''}")
        await msg.answer(
            f"🎲 Điểm ngẫu nhiên hiện tại: <b>{html.escape(cur)}</b>\n"
            f"Cú pháp: <code>/loyaltyrandom &lt;min&gt; &lt;max&gt; &lt;cap_user&gt; "
            f"[đơn_tối_thiểu] [lượt_tối_đa_ngày]</code>\n"
            f"<i>cap_user = 0 để tắt tính năng</i>",
            parse_mode="HTML")
        return
    try:
        lo, hi, cap = int(parts[1]), int(parts[2]), int(parts[3])
        min_order = int(parts[4]) if len(parts) > 4 else int(
            db.get_setting("loyalty_random_min_order", "0") or 0)
        daily_max = int(parts[5]) if len(parts) > 5 else int(
            db.get_setting("loyalty_random_daily_max", "0") or 0)
    except Exception:
        await msg.answer("❌ Các giá trị phải là số.")
        return
    if lo <= 0 or hi < lo or cap < 0 or min_order < 0 or daily_max < 0:
        await msg.answer("❌ Giá trị không hợp lệ (cần 0 < min ≤ max, cap ≥ 0).")
        return
    db.set_setting("loyalty_random_min", str(lo))
    db.set_setting("loyalty_random_max", str(hi))
    db.set_setting("loyalty_random_cap", str(cap))
    db.set_setting("loyalty_random_min_order", str(min_order))
    db.set_setting("loyalty_random_daily_max", str(daily_max))
    db.admin_audit_add(msg.from_user.id, msg.from_user.full_name, "loyaltyrandom",
                       f"min={lo} max={hi} cap={cap} min_order={min_order} daily_max={daily_max}")
    extra = ""
    if min_order > 0:
        extra += f", đơn ≥ {vnd(min_order)}"
    if daily_max > 0:
        extra += f", tối đa {daily_max} lượt/ngày"
    await msg.answer(
        f"✅ Điểm ngẫu nhiên: <b>{lo}–{hi}</b> điểm/lần, tối đa <b>{cap}</b> điểm/user"
        f"{extra}"
        + (" (đang tắt)" if cap <= 0 else "") + ".",
        parse_mode="HTML")

@router.callback_query(F.data == "accmystery")
async def on_mystery(cb: CallbackQuery):
    await cb.answer()
    try:
        m_price = int(db.get_setting("mystery_price", "0") or 0)
    except Exception:
        m_price = 0
    if m_price <= 0:
        await cb.message.answer("🎁 Hộp mù hiện đang tắt.")
        return
    m_cats = [dict(x) for x in db.acc_category_list()
              if int(x["mystery_eligible"] or 0) and db.acc_stock_count(x["id"]) > 0]
    if not m_cats:
        await cb.message.answer("⛔ Hộp mù hiện hết hàng, quay lại sau nhé!")
        return
    names = ", ".join(html.escape(x["name"]) for x in m_cats[:10])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🎁 Mở hộp — {vnd(m_price)}",
                              callback_data="accmysterybuy")],
        [InlineKeyboardButton(text="🔙 Quay lại shop", callback_data="accshop_back")],
    ])
    await cb.message.answer(
        f"🎁 <b>HỘP MÙ ACC — {vnd(m_price)}</b>\n\n"
        f"Mở ngẫu nhiên <b>1 acc</b> từ các loại:\n{names}\n\n"
        f"🍀 Hên xui trúng acc xịn giá rẻ!",
        parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "accmysterybuy")
async def on_mystery_buy(cb: CallbackQuery):
    await cb.answer()
    tg_id = cb.from_user.id
    try:
        m_price = int(db.get_setting("mystery_price", "0") or 0)
    except Exception:
        m_price = 0
    if m_price <= 0:
        return
    m_price, promo_code = db.preview_user_promo(tg_id, m_price, wallet="shop")
    u = db.get_user(tg_id)
    bal = int(u["shop_balance"] or 0) if u else 0
    if bal < m_price:
        await cb.message.answer(
            f"❌ Ví shop không đủ! Hộp mù {vnd(m_price)}, bạn có {vnd(bal)}.\n"
            f"Nạp thêm bằng /napshop nhé." + _SHOP_WALLET_HINT,
            parse_mode="HTML")
        return
    if not db.adjust_shop_balance(tg_id, -m_price, "mua_hop_mu" + (f" (promo {promo_code})" if promo_code else "")):
        await cb.message.answer("❌ Ví shop không đủ." + _SHOP_WALLET_HINT, parse_mode="HTML")
        return
    await cb.message.answer("🔍 <b>Đang kiểm tra chất lượng acc...</b>", parse_mode="HTML")
    sold_orders, _sell_fail = await _sell_live_stock(
        cb.bot, tg_id, m_price, 1,
        lambda seen: db.acc_mystery_pick_candidates(5, seen))
    if not sold_orders:
        db.add_shop_balance_only(tg_id, m_price, "hoan_tien_hop_mu")
        await cb.message.answer(
            "😔 Hộp mù hiện hết acc <b>LIVE</b> (shop vừa kiểm tra lại), "
            "tiền đã được hoàn lại. Bạn quay lại sau nhé!",
            parse_mode="HTML")
        return
    order = sold_orders[0]
    order_id = order["id"]
    db.finalize_user_promo(tg_id, promo_code)  # trừ lượt promo SAU khi mở hộp mù thành công
    cat_name = order.get("cat_name") or ""
    db.spin_add_tickets(tg_id, 1)
    try:
        per = int(db.get_setting("loyalty_per_vnd", "100000") or 100000)
    except Exception:
        per = 100000
    if per > 0 and m_price // per:
        db.loyalty_add(tg_id, m_price // per, "mua_hop_mu")
    order = dict(order)
    await cb.message.answer(
        f"🎁 <b>MỞ HỘP THÀNH CÔNG!</b>\n"
        f"🍀 Bạn trúng acc loại: <b>{html.escape(cat_name)}</b>\n"
        f"👤 UID: <code>{html.escape(order['uid'] or '')}</code>\n"
        f"🟢 <i>Đã kiểm tra LIVE trước khi giao</i>\n\n"
        f"{_pickup_suffix()}",
        parse_mode="HTML", reply_markup=_acc_delivery_kb(order_id))
    await _notify_purchase_admin(cb.bot, cb.from_user, [order])

@router.message(Command("hopmu"))
async def on_hopmu(msg: Message):
    """Admin: /hopmu <id> bật/tắt loại acc tham gia hộp mù."""
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: <code>/hopmu &lt;id_loại&gt;</code> (bật/tắt)",
                         parse_mode="HTML")
        return
    try:
        cid = int(parts[1])
    except Exception:
        await msg.answer("❌ ID phải là số.")
        return
    c = db.acc_category_get(cid)
    if not c:
        await msg.answer("❌ Không có loại này.")
        return
    new = 0 if int(c["mystery_eligible"] or 0) else 1
    db.acc_category_update(cid, mystery_eligible=new)
    await msg.answer(f"✅ Loại <b>{html.escape(c['name'])}</b> đã "
                     f"{'<b>THAM GIA</b> hộp mù' if new else 'rời khỏi hộp mù'}.",
                     parse_mode="HTML")

@router.message(Command("hopmugia"))
async def on_hopmugia(msg: Message):
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: <code>/hopmugia &lt;giá&gt;</code> (0 = tắt hộp mù)",
                         parse_mode="HTML")
        return
    try:
        price = int(parts[1].replace(".", "").replace(",", ""))
    except Exception:
        await msg.answer("❌ Giá phải là số.")
        return
    db.set_setting("mystery_price", str(max(0, price)))
    await msg.answer(f"✅ Giá hộp mù: <b>{vnd(max(0, price))}</b>"
                     + (" (đang tắt)" if price <= 0 else ""), parse_mode="HTML")

@router.message(Command("hopmutile"))
async def on_hopmutile(msg: Message):
    """Admin: đặt tỷ lệ trúng hộp mù cho 1 loại acc.
    - /hopmutile <id_loại> <tỷ_trọng>: kiểu cũ (số càng lớn càng dễ trúng).
    - /hopmutile <id_loại> <pct>%: đặt % trực tiếp, các loại còn lại tự chia
      phần % còn lại theo đúng tỷ lệ cũ (menu nút dùng kiểu này)."""
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 3:
        lines = ["⚠️ Cú pháp: <code>/hopmutile &lt;id_loại&gt; &lt;tỷ_trọng&gt;</code> hoặc <code>&lt;phần_trăm&gt;%</code>\n"]
        for i in db.acc_mystery_weights():
            lines.append(f"#{i['id']} {html.escape(i['name'])}: "
                         f"trọng số <b>{i['weight']}</b> (~{i['pct']}%)")
        await msg.answer("\n".join(lines) or "Chưa có loại nào tham gia hộp mù.",
                         parse_mode="HTML")
        return
    try:
        cid = int(parts[1])
    except Exception:
        await msg.answer("❌ ID loại phải là số.")
        return
    c = db.acc_category_get(cid)
    c = dict(c) if c else None
    if not c:
        await msg.answer("❌ Không có loại này.")
        return
    was_out = not int(c.get("mystery_eligible") or 0)
    tail = (parts[2] or "").strip()
    if tail.endswith("%"):
        # Chế độ %: loại này đúng bằng pct%, còn lại tự chia phần còn lại
        try:
            pct = int(tail[:-1])
        except Exception:
            pct = 0
        if not (1 <= pct <= 99):
            await msg.answer("❌ % phải từ 1 đến 99.")
            return
        if not db.acc_mystery_set_pct(cid, pct):
            await msg.answer("❌ Không đặt được %.")
            return
        note = " — đã <b>tự bật</b> tham gia hộp mù" if was_out else ""
        lines = [f"✅ <b>{html.escape(c['name'])}</b>: <b>{pct}%</b>{note}\n",
                 "🎲 Tỷ lệ trúng hộp mù hiện tại:"]
    else:
        try:
            w = int(tail)
        except Exception:
            await msg.answer("❌ Tỷ trọng phải là số (hoặc VD: 60%).")
            return
        if not (1 <= w <= 100000):
            await msg.answer("❌ Tỷ trọng phải từ 1 đến 100000.")
            return
        db.acc_category_update(cid, mystery_weight=w, mystery_eligible=1)
        lines = [f"✅ <b>{html.escape(c['name'])}</b>: trọng số <b>{w}</b>"
                 + (" — đã <b>tự bật</b> tham gia hộp mù" if was_out else "") + "\n",
                 "🎲 Tỷ lệ trúng hộp mù hiện tại:"]
    for i in db.acc_mystery_weights():
        mark = " 👈" if i["id"] == cid else ""
        lines.append(f"#{i['id']} {html.escape(i['name'])}: ~<b>{i['pct']}%</b>{mark}")
    await msg.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("hopmutilemulti"))
async def on_hopmutilemulti(msg: Message):
    """Admin: /hopmutilemulti 12:56,17:18 — đặt % cho NHIỀU loại 1 lúc.
    Các loại được chỉ định giữ đúng %; phần còn lại bot tự chia cho các loại
    khác theo tỷ lệ cũ. Tổng không quá 100%."""
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split(None, 1)
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: <code>/hopmutilemulti 12:56,17:18</code>\n"
                         "(mỗi cặp ID:% — tổng không quá 100%)", parse_mode="HTML")
        return
    pct_map = {}
    for tok in parts[1].replace(",", " ").split():
        if ":" not in tok:
            await msg.answer(f"❌ Sai định dạng ở '{tok}' (đúng: <code>12:56</code>).",
                             parse_mode="HTML")
            return
        a, b = tok.split(":", 1)
        try:
            pct_map[int(a)] = int(b)
        except Exception:
            await msg.answer(f"❌ ID/% phải là số (lỗi ở '{tok}').")
            return
    ok, note = db.acc_mystery_set_multi(pct_map)
    if not ok:
        await msg.answer(f"❌ {html.escape(note)}", parse_mode="HTML")
        return
    lines = ["✅ Đã set % hộp mù.\n", "🎲 Tỷ lệ trúng hộp mù hiện tại:"]
    for i in db.acc_mystery_weights():
        mark = " 👈" if i["id"] in pct_map else ""
        lines.append(f"#{i['id']} {html.escape(i['name'])}: ~<b>{i['pct']}%</b>{mark}")
    if note:
        lines.append(f"\n<i>{html.escape(note)}</i>")
    await msg.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("giovang"))
async def on_giovang(msg: Message):
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        cur = db.get_setting("happy_hour_range", "20-22")
        pct = db.get_setting("happy_hour_pct", "10")
        hc = db.get_setting("happy_hour_cat", "0")
        on, _, _ = db.happy_hour_active()
        await msg.answer(
            f"⚡ <b>GIỜ VÀNG HIỆN TẠI</b>\nKhung giờ: <b>{cur}</b>\nGiảm: <b>{pct}%</b>\n"
            f"Áp dụng: {'toàn shop' if hc == '0' else f'loại #{hc}'}\n"
            f"Trạng thái: {'🟢 ĐANG DIỄN RA' if on else '⚪ ngoài khung giờ'}\n\n"
            f"Đặt: <code>/giovang &lt;id_loại|0=tất cả&gt; [giờ_bd-giờ_kt] [%giảm]</code>\n"
            f"Tắt: <code>/giovang off</code>",
            parse_mode="HTML")
        return
    if parts[1].lower() == "off":
        db.set_setting("happy_hour_pct", "0")
        await msg.answer("✅ Đã tắt giờ vàng.")
        return
    try:
        cid = int(parts[1])
    except Exception:
        await msg.answer("❌ ID loại phải là số (0 = tất cả).")
        return
    rng = parts[2] if len(parts) > 2 else db.get_setting("happy_hour_range", "20-22")
    pct = parts[3] if len(parts) > 3 else db.get_setting("happy_hour_pct", "10")
    db.set_setting("happy_hour_cat", str(cid))
    db.set_setting("happy_hour_range", rng)
    db.set_setting("happy_hour_pct", str(pct))
    await msg.answer(f"✅ Giờ vàng: giảm <b>{pct}%</b> khung <b>{rng}</b> cho "
                     f"{'toàn shop' if cid == 0 else f'loại #{cid}'}.", parse_mode="HTML")

@router.callback_query(F.data.startswith("accreview:"))
async def on_acc_review(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    parts = cb.data.split(":")
    try:
        order_id = int(parts[1])
    except Exception:
        return
    order = db.acc_get_order(order_id)
    if not order or int(order["tg_id"]) != cb.from_user.id:
        await cb.message.answer("❌ Không tìm thấy đơn.")
        return
    if len(parts) == 2:
        if db.acc_order_reviewed(order_id):
            await cb.message.answer("Bạn đã đánh giá đơn này rồi.")
            return
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⭐" * i, callback_data=f"accreview:{order_id}:{i}")]
            for i in range(1, 6)
        ])
        await cb.message.answer(
            f"⭐ <b>ĐÁNH GIÁ ĐƠN #{order_id}</b>\n"
            f"Chất lượng acc bạn nhận được thế nào?\n"
            f"Đánh giá xong được tặng credits ngay!",
            parse_mode="HTML", reply_markup=kb)
        return
    try:
        stars = int(parts[2])
    except Exception:
        return
    if not db.acc_review_add(order_id, cb.from_user.id, int(order["cat_id"]), stars):
        await cb.message.answer("Bạn đã đánh giá đơn này rồi.")
        return
    try:
        bonus = int(db.get_setting("review_bonus", "20") or 20)
    except Exception:
        bonus = 20
    new_cr = db.add_credits(cb.from_user.id, bonus, f"danh_gia:{order_id}")
    kb2 = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Bỏ qua", callback_data=f"accreview_skip:{order_id}")]
    ])
    await cb.message.answer(
        f"✅ Cảm ơn bạn đã đánh giá {'⭐' * stars}!\n"
        f"🎁 Tặng <b>{bonus} credits</b> (số dư credits: {new_cr}).\n\n"
        f"💬 Bạn có muốn <b>viết vài lời nhận xét</b> về acc không? "
        f"Nhận xét hay sẽ hiện trên shop cho khách khác tham khảo.",
        parse_mode="HTML", reply_markup=kb2)
    await state.update_data(review_order_id=order_id)
    await state.set_state(AccShopState.waiting_for_review_comment)

@router.message(AccShopState.waiting_for_review_comment, F.text)
async def on_review_comment(msg: Message, state: FSMContext):
    _t = (msg.text or "").strip()
    if _t.startswith("/") and _t.split()[0].lower() not in ("/huy", "/cancel"):
        await state.clear()
        await msg.answer("\U0001f6ab \u0110\u00e3 h\u1ee7y thao t\u00e1c \u0111ang nh\u1eadp. B\u1ea1n g\u00f5 l\u1ea1i l\u1ec7nh v\u1eeba r\u1ed3i nh\u00e9.")
        return
    data = await state.get_data()
    order_id = data.get("review_order_id")
    await state.clear()
    if not order_id or not db.acc_order_reviewed(order_id):
        return
    db.acc_review_set_comment(order_id, msg.text or "")
    await msg.answer("💬 Cảm ơn nhận xét của bạn! Lời nhận xét sẽ hiện trên shop cho khách khác tham khảo. 🙏")

@router.callback_query(F.data.startswith("accreview_skip:"))
async def on_acc_review_skip(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    await cb.message.answer("👌 Không sao! Cảm ơn bạn đã đánh giá. 🙏")

@router.message(Command("danhgiancc"))
async def on_danhgiancc(msg: Message):
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 3:
        await msg.answer("⚠️ Cú pháp: <code>/danhgiancc &lt;id&gt; &lt;sao 1-5&gt;</code>",
                         parse_mode="HTML")
        return
    try:
        sid, stars = int(parts[1]), int(parts[2])
    except Exception:
        await msg.answer("❌ ID và sao phải là số.")
        return
    if db.supplier_rate(sid, stars):
        await msg.answer(f"✅ NCC #{sid} được đánh giá {'⭐' * max(1, min(5, stars))}.")
    else:
        await msg.answer("❌ Không tìm thấy NCC.")

@router.message(Command("chamdiem"))
async def on_chamdiem(msg: Message):
    """5.2 Chấm điểm NCC: check mẫu acc các lô nhập >= số_ngày trước."""
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: <code>/chamdiem &lt;id_ncc&gt; [số_ngày=7]</code>",
                         parse_mode="HTML")
        return
    try:
        sid = int(parts[1])
        days = int(parts[2]) if len(parts) > 2 else 7
    except Exception:
        await msg.answer("❌ ID và số ngày phải là số.")
        return
    sup = db.supplier_get(sid)
    if not sup:
        await msg.answer("❌ Không có NCC này.")
        return
    cutoff = int(time.time()) - days * 86400
    batches = db.get_conn().execute(
        "SELECT batch FROM acc_batches WHERE supplier_id=? AND created_at<=?",
        (sid, cutoff)).fetchall()
    if not batches:
        await msg.answer(f"🏭 NCC <b>{html.escape(sup['name'])}</b> chưa có lô nào nhập "
                         f"trước {days} ngày để chấm.", parse_mode="HTML")
        return
    bnames = [b["batch"] for b in batches]
    q = ("SELECT uid, batch FROM acc_stock WHERE batch IN (%s) AND uid != '' "
         "AND status='AVAILABLE' "
         "ORDER BY RANDOM() LIMIT 20" % ",".join("?" * len(bnames)))
    sample = db.get_conn().execute(q, tuple(bnames)).fetchall()
    if not sample:
        await msg.answer("❌ Không lấy được mẫu acc để chấm.")
        return
    wait = await msg.answer(f"🔍 Đang chấm <b>{len(sample)}</b> acc mẫu của NCC "
                            f"<b>{html.escape(sup['name'])}</b>...", parse_mode="HTML")
    from .. import fb as fb_mod
    import asyncio
    sem = asyncio.Semaphore(5)
    alive = 0

    async def _one(uid):
        nonlocal alive
        async with sem:
            try:
                r = await fb_mod.check_uid(str(uid))
                if str(r.get("status", "")).lower() == "live":
                    alive += 1
            except Exception:
                pass

    await asyncio.gather(*[_one(s["uid"]) for s in sample])
    total = len(sample)
    pct = round(alive * 100 / total, 1)
    batch_txt = ", ".join(bnames[:5]) + ("..." if len(bnames) > 5 else "")
    db.supplier_score_add(sid, batch_txt, total, alive,
                          f"chấm sau {days} ngày, mẫu {total} acc")
    verdict = ("Hàng ngon, nên nhập tiếp 👍" if pct >= 80
               else "Hàng trung bình, cân nhắc ⚠️" if pct >= 50
               else "Hàng dở, nên đổi NCC 👎")
    await wait.edit_text(
        f"📊 <b>CHẤM ĐIỂM NCC #{sid} — {html.escape(sup['name'])}</b>\n\n"
        f"🧪 Mẫu: <b>{total}</b> acc (các lô: {html.escape(batch_txt)})\n"
        f"🟢 Sống: <b>{alive}</b> | 🔴 Die: <b>{total - alive}</b>\n"
        f"⭐ <b>Tỉ lệ sống: {pct}%</b>\n\n"
        f"<i>{verdict}</i>",
        parse_mode="HTML")

@router.callback_query(F.data.startswith("accok:"))
async def on_acc_ok(cb: CallbackQuery):
    """5.13 Khách xác nhận acc ổn sau tin hỏi thăm 24h."""
    await cb.answer("Cảm ơn bạn! Chúc bạn dùng acc vui vẻ 🍀")

__all__ = [
    "_doiqua_processing",
    "on_tienich_promo",
    "on_tienich_birthday",
    "on_birthday_cmd",
    "on_promo_input",
    "on_promo",
    "on_taopromo",
    "on_xoapromo",
    "on_dspromo",
    "on_acc_preview",
    "on_quay",
    "on_spin_now",
    "on_randpts",
    "on_doiqua",
    "_doiqua_try_lock",
    "_doiqua_unlock",
    "_doiqua_fail_text",
    "_doiqua_success",
    "on_doiqua_stall",
    "on_doiqua_any",
    "_doiqua_redeem_flow",
    "on_loyaltyrandom",
    "on_mystery",
    "on_mystery_buy",
    "on_hopmu",
    "on_hopmugia",
    "on_hopmutile",
    "on_hopmutilemulti",
    "on_giovang",
    "on_acc_review",
    "on_review_comment",
    "on_acc_review_skip",
    "on_danhgiancc",
    "on_chamdiem",
    "on_acc_ok",
]