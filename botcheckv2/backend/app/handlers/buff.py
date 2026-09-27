"""🛍️ Shop buff tương tác — khách đặt đơn buff MXH, bot tự đặt hộ trên panel.

Luồng khách: /buff → chọn nền tảng → loại dịch vụ → gói → nhập link
→ nhập số lượng → xác nhận (trừ VÍ BUFF) → đơn vào hàng đợi worker.
Admin: /buffadm — sửa giá, bật/tắt gói, xem đơn, cài tài khoản panel,
cộng/trừ ví buff, test đăng nhập panel.
"""

import html
import logging
import math

from aiogram import F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from .. import db
from ..util import vnd
from ..perms import has_perm, is_super
from .core import _is_admin, router
from .common import _notify_admin_smart

log = logging.getLogger("buff")


class BuffState(StatesGroup):
    waiting_for_link = State()
    waiting_for_qty = State()


class BuffAdmState(StatesGroup):
    waiting_for_price = State()        # data: sid
    waiting_for_panel_user = State()
    waiting_for_panel_pass = State()
    waiting_for_topup_uid = State()
    waiting_for_topup_amount = State()  # data: uid


_BUFF_STATUS_LABEL = {
    "pending": "⏳ Chờ đặt",
    "placing": "🔄 Đang đặt",
    "running": "🔄 Đang chạy",
    "done": "✅ Hoàn thành",
    "failed": "❌ Lỗi",
    "refunded": "💸 Đã hoàn tiền",
}


def _plat_kb() -> InlineKeyboardMarkup:
    plats = db.buff_platforms()
    rows, row = [], []
    for p in plats:
        row.append(InlineKeyboardButton(
            text=f"{p['icon']} {p['name']}",
            callback_data=f"buff:plat:{p['key']}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="📋 Đơn của tôi",
                                     callback_data="buff:myorders")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _cat_kb(pkey: str) -> InlineKeyboardMarkup:
    cats = db.buff_categories(pkey)
    rows = [[InlineKeyboardButton(text=c["category_name"],
                                  callback_data=f"buff:cat:{pkey}:{c['category_key']}")]
            for c in cats]
    rows.append([InlineKeyboardButton(text="⬅️ Chọn nền tảng",
                                      callback_data="buff:back:plats")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _pkg_kb(pkey: str, ckey: str) -> InlineKeyboardMarkup:
    pkgs = db.buff_services_list(pkey, ckey)
    rows = [[InlineKeyboardButton(
        text=f"{p['name']} — {vnd(p['sell_price'])}đ/1k",
        callback_data=f"buff:pkg:{p['id']}")] for p in pkgs]
    rows.append([InlineKeyboardButton(text="⬅️ Quay lại",
                                      callback_data=f"buff:back:cats:{pkey}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _calc_total(qty: int, sell_price: int) -> int:
    return math.ceil(qty / 1000 * sell_price)


# ─────────────────────────── KHÁCH ───────────────────────────

@router.message(Command("buff"))
@router.message(Command("shopbuff"))
async def on_buff(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(
        "🛍️ <b>SHOP BUFF TƯƠNG TÁC</b>\n"
        "━━━━━━━━━━━━━━\n"
        "Tăng like, follow, view... cho mọi nền tảng.\n"
        "Chọn nền tảng bạn muốn buff:",
        parse_mode="HTML", reply_markup=_plat_kb())


@router.callback_query(F.data.startswith("buff:"))
async def on_buff_cb(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    uid = cb.from_user.id
    parts = cb.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "back":
        where = parts[2] if len(parts) > 2 else "plats"
        if where == "plats":
            await cb.message.edit_text(
                "🛍️ <b>SHOP BUFF TƯƠNG TÁC</b>\n"
                "━━━━━━━━━━━━━━\n"
                "Chọn nền tảng bạn muốn buff:",
                parse_mode="HTML", reply_markup=_plat_kb())
        elif where == "cats" and len(parts) > 3:
            await cb.message.edit_text(
                "Chọn loại dịch vụ:", parse_mode="HTML",
                reply_markup=_cat_kb(parts[3]))
        return

    if action == "plat" and len(parts) > 2:
        pkey = parts[2]
        plats = {p["key"]: p for p in db.buff_platforms()}
        p = plats.get(pkey)
        if not p:
            await cb.message.answer("❌ Nền tảng không tồn tại.")
            return
        await cb.message.edit_text(
            f"{p['icon']} <b>{p['name']}</b>\nChọn loại dịch vụ:",
            parse_mode="HTML", reply_markup=_cat_kb(pkey))
        return

    if action == "cat" and len(parts) > 3:
        pkey, ckey = parts[2], parts[3]
        pkgs = db.buff_services_list(pkey, ckey)
        if not pkgs:
            await cb.answer("Chưa có gói nào.", show_alert=True)
            return
        await cb.message.edit_text(
            "Chọn gói <i>(giá /1000 đơn vị)</i>:",
            parse_mode="HTML", reply_markup=_pkg_kb(pkey, ckey))
        return

    if action == "pkg" and len(parts) > 2:
        try:
            sid = int(parts[2])
        except ValueError:
            return
        svc = db.buff_service_get(sid)
        if not svc or not int(svc.get("enabled") or 0):
            await cb.answer("❌ Gói này hiện không bán.", show_alert=True)
            return
        await state.update_data(buff_sid=sid)
        await state.set_state(BuffState.waiting_for_link)
        bal = db.buff_get_balance(uid)
        await cb.message.edit_text(
            f"📦 <b>{html.escape(svc['name'])}</b> — {html.escape(svc['platform_name'])}\n"
            f"💰 Giá: <b>{vnd(svc['sell_price'])}đ/1000</b>\n"
            f"👛 Ví buff của bạn: <b>{vnd(bal)}</b>\n"
            f"📝 <i>{html.escape(svc['description'] or '')}</i>\n\n"
            "🔗 Gửi <b>link</b> cần buff (bài viết/video/trang, phải công khai):",
            parse_mode="HTML")
        return

    if action == "myorders":
        orders = db.buff_orders_by_user(uid, 10)
        if not orders:
            await cb.message.edit_text(
                "📋 Bạn chưa có đơn buff nào.\nGõ /buff để đặt đơn đầu tiên nhé!",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🛍️ Đặt đơn ngay",
                                         callback_data="buff:back:plats")]]))
            return
        lines = ["📋 <b>ĐƠN BUFF CỦA BẠN</b> (10 gần nhất)\n"]
        for o in orders:
            st = _BUFF_STATUS_LABEL.get(o["status"], o["status"])
            lines.append(
                f"• <code>{o['code']}</code> — {html.escape(o.get('service_name') or '')}\n"
                f"  🔢 {vnd(o['quantity'])} • 💵 {vnd(o['total_price'])} • {st}")
        await cb.message.edit_text("\n".join(lines), parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(
                                       inline_keyboard=[[
                                           InlineKeyboardButton(
                                               text="⬅️ Về shop buff",
                                               callback_data="buff:back:plats")]]))
        return

    if action == "confirm":
        data = await state.get_data()
        sid, link, qty = data.get("buff_sid"), data.get("buff_link"), data.get("buff_qty")
        svc = db.buff_service_get(sid) if sid else None
        if not svc or not link or not qty:
            await cb.message.answer("❌ Đơn đã hết hạn, gõ /buff đặt lại nhé.")
            await state.clear()
            return
        if not int(svc.get("enabled") or 0):
            await cb.message.answer("❌ Gói này hiện không bán.")
            await state.clear()
            return
        total = _calc_total(int(qty), int(svc["sell_price"]))
        bal = db.buff_get_balance(uid)
        if bal < total:
            await state.clear()
            await cb.message.edit_text(
                "❌ <b>Ví buff không đủ.</b>\n\n"
                f"💵 Cần: <b>{vnd(total)}</b>\n"
                f"👛 Ví buff: <b>{vnd(bal)}</b>\n\n"
                f"Nạp thêm bằng <code>/napbuff {vnd(total - bal)}</code> rồi đặt lại nhé.",
                parse_mode="HTML")
            return
        # Tạo đơn trước, trừ tiền nguyên tử sau (tránh race).
        order = db.buff_order_create(uid, int(svc["id"]), link, int(qty),
                                     total, _calc_total(int(qty), int(svc["cost_price"])))
        ok = db.buff_adjust_balance(uid, -total, f"buff:{order['code']}")
        if not ok:
            db.buff_order_update(order["id"], status="failed")
            await state.clear()
            await cb.message.edit_text(
                "❌ Trừ tiền thất bại (số dư vừa thay đổi).\n"
                "Nạp thêm bằng /napbuff rồi thử lại nhé.",
                parse_mode="HTML")
            return
        await state.clear()
        try:
            await _notify_admin_smart(
                cb.bot,
                f"🛍️ <b>ĐƠN BUFF MỚI</b> <code>{order['code']}</code>\n"
                f"👤 {uid} • 📦 {html.escape(svc['platform_name'])} — {html.escape(svc['name'])}\n"
                f"🔢 {vnd(qty)} • 💵 {vnd(total)} (vốn {vnd(order['total_cost'])})")
        except Exception:
            pass
        await cb.message.edit_text(
            f"✅ <b>ĐÃ NHẬN ĐƠN <code>{order['code']}</code></b>\n\n"
            f"📦 {html.escape(svc['name'])} — {html.escape(svc['platform_name'])}\n"
            f"🔢 Số lượng: <b>{vnd(qty)}</b>\n"
            f"💵 Đã trừ ví buff: <b>{vnd(total)}</b>\n"
            f"👛 Ví buff còn: <b>{vnd(bal - total)}</b>\n\n"
            "⏳ Bot đang đặt đơn trên hệ thống, xong sẽ báo bạn ngay.",
            parse_mode="HTML")
        return

    if action == "cancel":
        await state.clear()
        await cb.message.edit_text("❌ Đã hủy đơn.", reply_markup=None)
        return


@router.message(BuffState.waiting_for_link)
async def on_buff_link(msg: Message, state: FSMContext):
    link = (msg.text or "").strip()
    if not (link.startswith("http://") or link.startswith("https://")):
        await msg.answer("❌ Link chưa đúng. Gửi link bắt đầu bằng <b>http</b> nhé "
                         "(ví dụ: https://...)",
                         parse_mode="HTML")
        return
    data = await state.get_data()
    svc = db.buff_service_get(data.get("buff_sid"))
    if not svc:
        await msg.answer("❌ Gói đã hết hạn, gõ /buff đặt lại nhé.")
        await state.clear()
        return
    await state.update_data(buff_link=link)
    await state.set_state(BuffState.waiting_for_qty)
    await msg.answer(
        f"🔗 Link: <code>{html.escape(link[:60])}</code>\n\n"
        f"🔢 Nhập <b>số lượng</b> (tối thiểu {vnd(svc['min_qty'])} — "
        f"tối đa {vnd(svc['max_qty'])}):",
        parse_mode="HTML")


@router.message(BuffState.waiting_for_qty)
async def on_buff_qty(msg: Message, state: FSMContext):
    data = await state.get_data()
    svc = db.buff_service_get(data.get("buff_sid"))
    if not svc:
        await msg.answer("❌ Gói đã hết hạn, gõ /buff đặt lại nhé.")
        await state.clear()
        return
    try:
        qty = int((msg.text or "").strip().replace(".", "").replace(",", ""))
    except ValueError:
        qty = 0
    mn, mx = int(svc["min_qty"]), int(svc["max_qty"])
    if qty < mn or qty > mx:
        await msg.answer(f"❌ Số lượng phải từ <b>{vnd(mn)}</b> đến <b>{vnd(mx)}</b>. "
                         "Nhập lại nhé:", parse_mode="HTML")
        return
    total = _calc_total(qty, int(svc["sell_price"]))
    bal = db.buff_get_balance(msg.from_user.id)
    await state.update_data(buff_qty=qty)
    link = data.get("buff_link", "")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Xác nhận đặt đơn",
                              callback_data="buff:confirm"),
         InlineKeyboardButton(text="❌ Hủy", callback_data="buff:cancel")],
    ])
    await msg.answer(
        "🧾 <b>XÁC NHẬN ĐƠN BUFF</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"📦 {html.escape(svc['name'])} — {html.escape(svc['platform_name'])}\n"
        f"🔗 <code>{html.escape(link[:50])}</code>\n"
        f"🔢 Số lượng: <b>{vnd(qty)}</b>\n"
        f"💰 Đơn giá: {vnd(svc['sell_price'])}đ/1000\n"
        f"💵 <b>Tổng: {vnd(total)}</b>\n"
        f"👛 Ví buff: <b>{vnd(bal)}</b>"
        + ("" if bal >= total
           else f"\n\n⚠️ <b>Ví buff không đủ</b> — nạp thêm bằng <code>/napbuff {vnd(total - bal)}</code>"),
        parse_mode="HTML", reply_markup=kb)


# ─────────────────────────── ADMIN: /buffadm ───────────────────────────

def _buffadm_menu_kb(tg_id: int = 0) -> InlineKeyboardMarkup:
    plats = db.buff_platforms()
    rows, row = [], []
    can_price = has_perm(tg_id, "price")
    can_orders = has_perm(tg_id, "orders")
    can_tien = has_perm(tg_id, "tien")
    super_only = is_super(tg_id)
    if can_price:
        for p in plats:
            row.append(InlineKeyboardButton(
                text=f"{p['icon']} {p['name']}",
                callback_data=f"buffadm:plat:{p['key']}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
    if can_orders:
        rows.append(
            [InlineKeyboardButton(text="📋 10 đơn mới nhất",
                                  callback_data="buffadm:orders")])
    if super_only:
        rows += [
            [InlineKeyboardButton(text="⚙️ Tài khoản panel",
                                  callback_data="buffadm:panel"),
             InlineKeyboardButton(text="🧪 Test đăng nhập",
                                  callback_data="buffadm:testlogin")],
        ]
    if can_tien:
        rows.append(
            [InlineKeyboardButton(text="💰 Cộng/trừ ví buff user",
                                  callback_data="buffadm:topup")])
    if not rows:
        rows.append([InlineKeyboardButton(text="⛔ Không có quyền nào",
                                          callback_data="buffadm:noop")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("buffadm"))
async def on_buffadm(msg: Message, state: FSMContext):
    if not _is_admin(msg.from_user.id):
        return
    await state.clear()
    n_svc = db.get_conn().execute(
        "SELECT COUNT(*) n FROM buff_services").fetchone()["n"]
    n_pend = db.get_conn().execute(
        "SELECT COUNT(*) n FROM buff_orders WHERE status='pending'").fetchone()["n"]
    await msg.answer(
        "🛍️ <b>QUẢN LÝ SHOP BUFF</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"📦 Dịch vụ: <b>{n_svc}</b> • ⏳ Đơn chờ: <b>{n_pend}</b>\n\n"
        "Chọn nền tảng để xem/sửa giá:",
        parse_mode="HTML", reply_markup=_buffadm_menu_kb(msg.from_user.id))


@router.callback_query(F.data.startswith("buffadm:"))
async def on_buffadm_cb(cb: CallbackQuery, state: FSMContext):
    if not _is_admin(cb.from_user.id):
        await cb.answer("⛔ Không có quyền.", show_alert=True)
        return
    await cb.answer()
    parts = cb.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    _uid = cb.from_user.id

    async def _need(perm: str) -> bool:
        if has_perm(_uid, perm):
            return True
        await cb.message.answer("⛔ Bạn không có quyền này.")
        return False

    async def _need_super() -> bool:
        if is_super(_uid):
            return True
        await cb.message.answer("⛔ Chỉ chủ shop mới dùng được.")
        return False

    if action == "menu":
        await cb.message.edit_text("🛍️ <b>QUẢN LÝ SHOP BUFF</b>\nChọn nền tảng:",
                                   parse_mode="HTML",
                                   reply_markup=_buffadm_menu_kb(cb.from_user.id))
        return

    if action == "plat" and len(parts) > 2:
        if not await _need("price"):
            return
        pkey = parts[2]
        rows = db.get_conn().execute(
            "SELECT * FROM buff_services WHERE platform_key=? ORDER BY category_key, sell_price",
            (pkey,)).fetchall()
        kb_rows = []
        cur_cat = None
        for r in rows:
            r = dict(r)
            if r["category_key"] != cur_cat:
                cur_cat = r["category_key"]
                kb_rows.append([InlineKeyboardButton(
                    text=f"── {r['category_name']} ──",
                    callback_data="buffadm:noop")])
            mark = "✅" if int(r["enabled"]) else "🚫"
            kb_rows.append([InlineKeyboardButton(
                text=f"{mark} {r['name']} — vốn {vnd(r['cost_price'])}/bán {vnd(r['sell_price'])}",
                callback_data=f"buffadm:pkg:{r['id']}")])
        kb_rows.append([InlineKeyboardButton(text="⬅️ Menu",
                                             callback_data="buffadm:menu")])
        await cb.message.edit_text(
            "Bấm vào gói để sửa giá / bật-tắt:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
        return

    if action == "pkg" and len(parts) > 2:
        if not await _need("price"):
            return
        try:
            sid = int(parts[2])
        except ValueError:
            return
        svc = db.buff_service_get(sid)
        if not svc:
            return
        st = "✅ Đang bán" if int(svc["enabled"]) else "🚫 Đang tắt"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Sửa giá bán",
                                  callback_data=f"buffadm:price:{sid}"),
             InlineKeyboardButton(
                 text="🔛 Bật" if not int(svc["enabled"]) else "🔴 Tắt",
                 callback_data=f"buffadm:toggle:{sid}")],
            [InlineKeyboardButton(text="⬅️ Quay lại",
                                  callback_data=f"buffadm:plat:{svc['platform_key']}")],
        ])
        await cb.message.edit_text(
            f"📦 <b>{html.escape(svc['name'])}</b> ({html.escape(svc['platform_name'])})\n"
            f"🆔 Panel ID: <code>{svc['panel_service_id']}</code>\n"
            f"💰 Giá vốn: {vnd(svc['cost_price'])}đ/1k\n"
            f"💵 Giá bán: <b>{vnd(svc['sell_price'])}đ/1k</b>\n"
            f"📊 Trạng thái: {st}\n"
            f"🔢 SL: {vnd(svc['min_qty'])} – {vnd(svc['max_qty'])}",
            parse_mode="HTML", reply_markup=kb)
        return

    if action == "price" and len(parts) > 2:
        if not await _need("price"):
            return
        try:
            sid = int(parts[2])
        except ValueError:
            return
        await state.update_data(buffadm_sid=sid)
        await state.set_state(BuffAdmState.waiting_for_price)
        await cb.message.answer("✏️ Nhập <b>giá bán mới</b> (đ/1000, ví dụ: 5000):",
                                parse_mode="HTML")
        return

    if action == "toggle" and len(parts) > 2:
        if not await _need("price"):
            return
        try:
            sid = int(parts[2])
        except ValueError:
            return
        now_on = db.buff_service_toggle(sid)
        svc = db.buff_service_get(sid)
        try:
            db.add_audit_log(cb.from_user.id, "buff_toggle",
                             f"service:{sid}",
                             f"{svc['name']} -> {'BẬT' if now_on else 'TẮT'}")
        except Exception:
            pass
        await cb.answer(f"Đã {'bật' if now_on else 'tắt'} gói.", show_alert=True)
        # Vẽ lại màn hình gói
        svc = db.buff_service_get(sid)
        st = "✅ Đang bán" if int(svc["enabled"]) else "🚫 Đang tắt"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Sửa giá bán",
                                  callback_data=f"buffadm:price:{sid}"),
             InlineKeyboardButton(
                 text="🔛 Bật" if not int(svc["enabled"]) else "🔴 Tắt",
                 callback_data=f"buffadm:toggle:{sid}")],
            [InlineKeyboardButton(text="⬅️ Quay lại",
                                  callback_data=f"buffadm:plat:{svc['platform_key']}")],
        ])
        await cb.message.edit_text(
            f"📦 <b>{html.escape(svc['name'])}</b> ({html.escape(svc['platform_name'])})\n"
            f"🆔 Panel ID: <code>{svc['panel_service_id']}</code>\n"
            f"💰 Giá vốn: {vnd(svc['cost_price'])}đ/1k\n"
            f"💵 Giá bán: <b>{vnd(svc['sell_price'])}đ/1k</b>\n"
            f"📊 Trạng thái: {st}\n"
            f"🔢 SL: {vnd(svc['min_qty'])} – {vnd(svc['max_qty'])}",
            parse_mode="HTML", reply_markup=kb)
        return

    if action == "orders":
        if not await _need("orders"):
            return
        rows = db.get_conn().execute(
            "SELECT o.*, s.name AS service_name FROM buff_orders o"
            " LEFT JOIN buff_services s ON s.id=o.service_id"
            " ORDER BY o.id DESC LIMIT 10").fetchall()
        if not rows:
            txt = "📋 Chưa có đơn buff nào."
        else:
            lines = ["📋 <b>10 ĐƠN BUFF MỚI NHẤT</b>\n"]
            for r in rows:
                r = dict(r)
                st = _BUFF_STATUS_LABEL.get(r["status"], r["status"])
                lines.append(
                    f"• <code>{r['code']}</code> — {html.escape(r.get('service_name') or '')}\n"
                    f"  👤 <code>{r['tg_id']}</code> • 🔢 {vnd(r['quantity'])}\n"
                    f"  💵 {vnd(r['total_price'])} (vốn {vnd(r['total_cost'])}) • {st}")
            txt = "\n".join(lines)
        await cb.message.edit_text(
            txt, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⬅️ Menu",
                                     callback_data="buffadm:menu")]]))
        return

    if action == "panel":
        if not await _need_super():
            return
        user = db.get_setting("buff_panel_user", "")
        has_pass = bool(db.get_setting("buff_panel_pass", ""))
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👤 Đổi username",
                                  callback_data="buffadm:paneluser"),
             InlineKeyboardButton(text="🔑 Đổi mật khẩu",
                                  callback_data="buffadm:panelpass")],
            [InlineKeyboardButton(text="⬅️ Menu",
                                  callback_data="buffadm:menu")],
        ])
        await cb.message.edit_text(
            "⚙️ <b>TÀI KHOẢN PANEL</b>\n"
            "━━━━━━━━━━━━━━\n"
            f"🌐 Web: <code>tuongtactg.pro</code>\n"
            f"👤 Username: <code>{html.escape(user or '(chưa có)')}</code>\n"
            f"🔑 Mật khẩu: <b>{'✅ đã lưu' if has_pass else '❌ chưa có'}</b>\n\n"
            "<i>Mật khẩu nhập xong sẽ bị xóa tin nhắn ngay.</i>",
            parse_mode="HTML", reply_markup=kb)
        return

    if action == "paneluser":
        if not await _need_super():
            return
        await state.set_state(BuffAdmState.waiting_for_panel_user)
        await cb.message.answer("👤 Nhập <b>username</b> panel mới:",
                                parse_mode="HTML")
        return

    if action == "panelpass":
        if not await _need_super():
            return
        await state.set_state(BuffAdmState.waiting_for_panel_pass)
        await cb.message.answer("🔑 Nhập <b>mật khẩu</b> panel mới "
                                "(tin nhắn sẽ bị xóa ngay sau khi gửi):",
                                parse_mode="HTML")
        return

    if action == "testlogin":
        if not await _need_super():
            return
        await cb.message.answer("🧪 Đang test đăng nhập panel, đợi chút...")
        try:
            from .. import buff_worker
            ok, info = await buff_worker.test_panel_login()
        except Exception as e:
            ok, info = False, f"worker lỗi: {e}"
        await cb.message.answer(
            f"🧪 <b>TEST ĐĂNG NHẬP PANEL</b>\n\n{'✅ ' if ok else '❌ '}"
            f"{html.escape(str(info))}",
            parse_mode="HTML")
        return

    if action == "topup":
        if not await _need("tien"):
            return
        await state.set_state(BuffAdmState.waiting_for_topup_uid)
        await cb.message.answer("💰 Nhập <b>Telegram ID</b> của user cần cộng/trừ ví buff:",
                                parse_mode="HTML")
        return

    if action == "noop":
        await cb.answer()
        return


@router.message(BuffAdmState.waiting_for_price)
async def on_buffadm_price_input(msg: Message, state: FSMContext):
    if not _is_admin(msg.from_user.id):
        await state.clear()
        return
    if not has_perm(msg.from_user.id, "price"):
        await state.clear()
        return
    data = await state.get_data()
    sid = data.get("buffadm_sid")
    try:
        price = int((msg.text or "").strip().replace(".", "").replace(",", ""))
        assert price > 0
    except (ValueError, AssertionError):
        await msg.answer("❌ Giá không hợp lệ. Nhập số nguyên dương (đ/1000):")
        return
    ok = db.buff_service_set_price(sid, price)
    svc = db.buff_service_get(sid)
    await state.clear()
    if ok:
        try:
            db.add_audit_log(msg.from_user.id, "buff_set_price",
                             f"service:{sid}",
                             f"{svc['name']}: giá bán -> {vnd(price)}đ/1k")
        except Exception:
            pass
        await msg.answer(f"✅ Đã đổi giá bán gói <b>{html.escape(svc['name'])}</b> "
                         f"thành <b>{vnd(price)}đ/1k</b>.", parse_mode="HTML")
    else:
        await msg.answer("❌ Không tìm thấy gói.")


@router.message(BuffAdmState.waiting_for_panel_user)
async def on_buffadm_panel_user_input(msg: Message, state: FSMContext):
    if not _is_admin(msg.from_user.id):
        await state.clear()
        return
    if not is_super(msg.from_user.id):
        await state.clear()
        return
    user = (msg.text or "").strip()
    if not user:
        await msg.answer("❌ Username trống. Nhập lại:")
        return
    db.set_setting("buff_panel_user", user)
    try:
        db.add_audit_log(msg.from_user.id, "buff_panel_user", "panel",
                         f"đổi username -> {user}")
    except Exception:
        pass
    await state.clear()
    await msg.answer(f"✅ Đã lưu username panel: <code>{html.escape(user)}</code>",
                     parse_mode="HTML")


@router.message(BuffAdmState.waiting_for_panel_pass)
async def on_buffadm_panel_pass_input(msg: Message, state: FSMContext):
    if not _is_admin(msg.from_user.id):
        await state.clear()
        return
    if not is_super(msg.from_user.id):
        await state.clear()
        return
    pw = (msg.text or "").strip()
    try:
        await msg.delete()
    except Exception:
        pass
    if not pw:
        await msg.answer("❌ Mật khẩu trống.")
        await state.clear()
        return
    db.set_setting("buff_panel_pass", pw)
    # Xóa session cũ để worker đăng nhập lại với pass mới
    try:
        from .. import buff_worker
        buff_worker.clear_session()
    except Exception:
        pass
    try:
        db.add_audit_log(msg.from_user.id, "buff_panel_pass", "panel",
                         "đổi mật khẩu panel")
    except Exception:
        pass
    await state.clear()
    await msg.answer("✅ Đã lưu mật khẩu panel (tin nhắn chứa pass đã bị xóa).")


@router.message(BuffAdmState.waiting_for_topup_uid)
async def on_buffadm_topup_uid_input(msg: Message, state: FSMContext):
    if not _is_admin(msg.from_user.id):
        await state.clear()
        return
    if not has_perm(msg.from_user.id, "tien"):
        await state.clear()
        return
    try:
        uid = int((msg.text or "").strip())
    except ValueError:
        await msg.answer("❌ ID không hợp lệ. Nhập Telegram ID (số):")
        return
    u = db.get_user(uid)
    if not u:
        await msg.answer("❌ Chưa thấy user này (user phải /start bot trước).")
        return
    await state.update_data(buffadm_topup_uid=uid)
    await state.set_state(BuffAdmState.waiting_for_topup_amount)
    bal = db.buff_get_balance(uid)
    await msg.answer(
        f"👤 User <code>{uid}</code> — ví buff hiện có: <b>{vnd(bal)}</b>\n"
        "💰 Nhập <b>số tiền</b> (số dương = cộng, số âm = trừ, ví dụ: <code>50000</code> hoặc <code>-20000</code>):",
        parse_mode="HTML")


@router.message(BuffAdmState.waiting_for_topup_amount)
async def on_buffadm_topup_amount_input(msg: Message, state: FSMContext):
    if not _is_admin(msg.from_user.id):
        await state.clear()
        return
    if not has_perm(msg.from_user.id, "tien"):
        await state.clear()
        return
    data = await state.get_data()
    uid = data.get("buffadm_topup_uid")
    try:
        amount = int((msg.text or "").strip().replace(".", "").replace(",", ""))
        assert amount != 0
    except (ValueError, AssertionError):
        await msg.answer("❌ Số tiền không hợp lệ (khác 0). Nhập lại:")
        return
    ok = db.buff_adjust_balance(uid, amount, f"buff_admin:{msg.from_user.id}")
    await state.clear()
    if not ok:
        await msg.answer("❌ Trừ thất bại — số dư ví buff của user không đủ.")
        return
    try:
        db.add_audit_log(msg.from_user.id, "buff_topup", f"user:{uid}",
                         f"{'+' if amount > 0 else ''}{vnd(amount)} ví buff")
    except Exception:
        pass
    bal = db.buff_get_balance(uid)
    await msg.answer(
        f"✅ Đã {'cộng' if amount > 0 else 'trừ'} <b>{vnd(abs(amount))}</b> "
        f"ví buff cho <code>{uid}</code>.\n"
        f"👛 Số dư ví buff hiện tại: <b>{vnd(bal)}</b>",
        parse_mode="HTML")
