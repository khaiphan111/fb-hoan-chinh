"""📱 Shop thuê số OTP (ViOTP) — thuê số điện thoại nhận mã OTP.

Luồng khách: /thueso → chọn dịch vụ (phổ biến/tìm kiếm) → xem giá
→ xác nhận (trừ VÍ CHÍNH) → nhận số → bot tự poll OTP → báo mã OTP.
"""
import html
import logging

from aiogram import F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from .. import db, viotp
from ..util import vnd
from .core import router

log = logging.getLogger("thueso")

# Dịch vụ phổ biến: ưu tiên hiện khi mở menu (khớp theo tên).
POPULAR_KEYS = [
    "facebook", "gmail", "telegram", "zalo", "tiktok", "whatsapp",
    "instagram", "openai", "shopee", "lazada",
]
PAGE_SIZE = 10
RENT_TTL_MIN = 15  # thời gian chờ OTP mỗi lượt thuê


class ThueSoState(StatesGroup):
    waiting_for_search = State()


def _enabled() -> bool:
    return db.get_setting("viotp_enabled", "1") == "1"


def _token() -> str:
    # Token lấy từ Secure Vault (custom.viotp) trong app/viotp.py
    return ""


def _menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔥 Dịch vụ phổ biến", callback_data="ts:hot")],
        [InlineKeyboardButton(text="🔍 Tìm dịch vụ", callback_data="ts:search")],
        [InlineKeyboardButton(text="📋 Đơn thuê của tôi", callback_data="ts:my")],
    ])


def _back_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Menu thuê số", callback_data="ts:menu")],
    ])


async def _services_or_error(obj) -> list | None:
    """Lấy danh sách dịch vụ; trả None + báo lỗi nếu không được."""
    try:
        return await viotp.get_services()
    except viotp.ViotpError as e:
        txt = f"⚠️ Không lấy được danh sách dịch vụ: {html.escape(str(e))}"
        if isinstance(obj, CallbackQuery):
            await obj.answer("Lỗi kết nối ViOTP", show_alert=True)
            await obj.message.answer(txt)
        else:
            await obj.answer(txt)
        return None


def _popular(items: list) -> list:
    out, seen = [], set()
    low = [(s, s["name"].lower()) for s in items]
    for key in POPULAR_KEYS:
        for s, name in low:
            if s["id"] in seen:
                continue
            if key in name:
                out.append(s)
                seen.add(s["id"])
                break
    return out


def _svc_rows(items: list, page: int, prefix: str) -> InlineKeyboardMarkup:
    total = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total - 1))
    chunk = items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    rows = [[InlineKeyboardButton(
        text=f"{s['name'][:28]} — {vnd(db.viotp_sell_price(s['price']))}đ",
        callback_data=f"ts:svc:{s['id']}:{page}:{prefix}")] for s in chunk]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Trước",
                                        callback_data=f"ts:page:{page-1}:{prefix}"))
    if page < total - 1:
        nav.append(InlineKeyboardButton(text="Tiếp ➡️",
                                        callback_data=f"ts:page:{page+1}:{prefix}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ Menu thuê số", callback_data="ts:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _svc_by_id(items: list, sid: int) -> dict:
    for s in items:
        if s["id"] == sid:
            return s
    return {}


# ---------------------------------------------------------------- menu

@router.message(Command("thueso"))
async def on_thueso_cmd(msg: Message, state: FSMContext):
    await state.clear()
    if not _enabled():
        await msg.answer("📱 Shop thuê số hiện đang tạm tắt. Vui lòng quay lại sau.")
        return
    await msg.answer(
        "📱 <b>SHOP THUÊ SỐ OTP</b>\n"
        "Thuê số điện thoại nhận mã xác minh (Facebook, Gmail, Telegram, Zalo...).\n"
        "Chọn dịch vụ bên dưới nhé:",
        parse_mode="HTML", reply_markup=_menu_kb())


@router.callback_query(F.data == "ts:menu")
async def on_ts_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    await cb.message.edit_text(
        "📱 <b>SHOP THUÊ SỐ OTP</b>\nChọn dịch vụ bên dưới nhé:",
        parse_mode="HTML", reply_markup=_menu_kb())


@router.callback_query(F.data == "ts:hot")
async def on_ts_hot(cb: CallbackQuery):
    items = await _services_or_error(cb)
    if items is None:
        return
    hot = _popular(items)
    if not hot:
        await cb.answer("Chưa có danh sách, thử tìm kiếm nhé", show_alert=True)
        return
    await cb.answer()
    await cb.message.edit_text(
        "🔥 <b>Dịch vụ phổ biến</b> — chọn để xem giá và thuê số:",
        parse_mode="HTML", reply_markup=_svc_rows(hot, 0, "hot"))


@router.callback_query(F.data == "ts:search")
async def on_ts_search(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(ThueSoState.waiting_for_search)
    await cb.message.edit_text(
        "🔍 Nhập tên dịch vụ cần tìm (vd: <i>facebook, gmail, telegram</i>):",
        parse_mode="HTML", reply_markup=_back_menu_kb())


@router.message(StateFilter(ThueSoState.waiting_for_search))
async def on_ts_search_input(msg: Message, state: FSMContext):
    await state.clear()
    q = (msg.text or "").strip().lower()
    if not q:
        await msg.answer("Bạn chưa nhập gì cả. Gõ /thueso để thử lại nhé.")
        return
    items = await _services_or_error(msg)
    if items is None:
        return
    found = [s for s in items if q in s["name"].lower()][:50]
    if not found:
        await msg.answer(f"🔍 Không tìm thấy dịch vụ nào khớp \"{html.escape(msg.text.strip())}\".",
                         parse_mode="HTML", reply_markup=_back_menu_kb())
        return
    # Lưu kết quả vào FSM để phân trang
    await state.update_data(search_results=[s["id"] for s in found])
    await msg.answer(
        f"🔍 Tìm thấy <b>{len(found)}</b> dịch vụ khớp:",
        parse_mode="HTML", reply_markup=_svc_rows(found, 0, "search"))


# ---------------------------------------------------------------- chi tiết + thuê

@router.callback_query(F.data.startswith("ts:svc:"))
async def on_ts_svc(cb: CallbackQuery):
    try:
        _, _, sid_s, page_s, prefix = cb.data.split(":")
        sid, page = int(sid_s), int(page_s)
    except (ValueError, IndexError):
        await cb.answer("Dữ liệu không hợp lệ", show_alert=True)
        return
    items = await _services_or_error(cb)
    if items is None:
        return
    s = _svc_by_id(items, sid)
    if not s:
        await cb.answer("Dịch vụ không còn", show_alert=True)
        return
    cost = s["price"]
    sell = db.viotp_sell_price(cost)
    await cb.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Thuê số — {vnd(sell)}đ",
                              callback_data=f"ts:rent:{sid}")],
        [InlineKeyboardButton(text="⬅️ Quay lại", callback_data=f"ts:page:{page}:{prefix}")],
    ])
    await cb.message.edit_text(
        f"📱 <b>{html.escape(s['name'])}</b>\n\n"
        f"💰 Giá thuê: <b>{vnd(sell)}đ</b>\n"
        f"⏳ Số dùng trong {RENT_TTL_MIN} phút, bot tự chờ mã OTP.\n"
        f"💳 Trừ vào <b>ví chính</b>.",
        parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("ts:page:"))
async def on_ts_page(cb: CallbackQuery, state: FSMContext):
    try:
        _, _, page_s, prefix = cb.data.split(":")
        page = int(page_s)
    except (ValueError, IndexError):
        await cb.answer("Dữ liệu không hợp lệ", show_alert=True)
        return
    items = await _services_or_error(cb)
    if items is None:
        return
    if prefix == "hot":
        lst, title = _popular(items), "🔥 <b>Dịch vụ phổ biến</b> — chọn để xem giá và thuê số:"
    else:
        data = await state.get_data()
        ids = data.get("search_results") or []
        by_id = {s["id"]: s for s in items}
        lst = [by_id[i] for i in ids if i in by_id]
        title = "🔍 <b>Kết quả tìm kiếm:</b>"
    await cb.answer()
    await cb.message.edit_text(title, parse_mode="HTML",
                               reply_markup=_svc_rows(lst, page, prefix))


@router.callback_query(F.data.startswith("ts:rent:"))
async def on_ts_rent(cb: CallbackQuery):
    try:
        sid = int(cb.data.split(":")[2])
    except (ValueError, IndexError):
        await cb.answer("Dữ liệu không hợp lệ", show_alert=True)
        return
    items = await _services_or_error(cb)
    if items is None:
        return
    s = _svc_by_id(items, sid)
    if not s:
        await cb.answer("Dịch vụ không còn", show_alert=True)
        return
    sell = db.viotp_sell_price(s["price"])
    u = db.get_user(cb.from_user.id)
    bal = int(dict(u).get("balance", 0)) if u else 0
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Xác nhận thuê — {vnd(sell)}đ",
                              callback_data=f"ts:confirm:{sid}")],
        [InlineKeyboardButton(text="⬅️ Quay lại", callback_data=f"ts:svc:{sid}:0:hot")],
    ])
    await cb.answer()
    await cb.message.edit_text(
        f"🧾 <b>Xác nhận thuê số</b>\n\n"
        f"📱 Dịch vụ: <b>{html.escape(s['name'])}</b>\n"
        f"💰 Giá: <b>{vnd(sell)}đ</b> (trừ ví chính)\n"
        f"👛 Số dư ví chính: <b>{vnd(bal)}đ</b>\n\n"
        f"Sau khi thuê, bạn sẽ nhận số điện thoại và bot tự chờ mã OTP trong {RENT_TTL_MIN} phút.",
        parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("ts:confirm:"))
async def on_ts_confirm(cb: CallbackQuery):
    try:
        sid = int(cb.data.split(":")[2])
    except (ValueError, IndexError):
        await cb.answer("Dữ liệu không hợp lệ", show_alert=True)
        return
    tg_id = cb.from_user.id
    items = await _services_or_error(cb)
    if items is None:
        return
    s = _svc_by_id(items, sid)
    if not s:
        await cb.answer("Dịch vụ không còn", show_alert=True)
        return
    cost, sell = s["price"], db.viotp_sell_price(s["price"])
    # 1. Trừ ví chính (nguyên tử, không đủ -> False)
    if not db.adjust_balance(tg_id, -sell, f"thue_so:{s['name'][:40]}"):
        await cb.answer("Ví chính không đủ tiền. Nạp thêm bằng /nap nhé.",
                        show_alert=True)
        return
    # 2. Gọi API thuê số — lỗi sau khi trừ tiền thì HOÀN TIỀN (chống mất tiền)
    try:
        rent = await viotp.rent_number(_token(), sid)
    except Exception as e:
        db.adjust_balance(tg_id, sell, "hoan_tien_thue_so_loi")
        log.error("thue so that bai sau khi tru tien tg=%s: %s", tg_id, e)
        await cb.answer("Thuê số thất bại, đã hoàn tiền vào ví chính.", show_alert=True)
        await cb.message.edit_text(
            "⚠️ Thuê số thất bại (lỗi nhà cung cấp). "
            f"Đã hoàn <b>{vnd(sell)}đ</b> vào ví chính.",
            parse_mode="HTML", reply_markup=_back_menu_kb())
        return
    rid = db.viotp_rental_create(
        tg_id, rent["request_id"], rent["phone_number"], sid,
        s["name"], "vn", cost, sell)
    await cb.answer("Thuê số thành công!")
    # Báo bot riêng cho admin
    try:
        from .. import viotp_notify as _vn
        r = db.viotp_rental_get(rid)
        username = (cb.from_user.username or "")
        await _vn.manager.send_to_privileged(_vn.rental_notify_text(r, username))
    except Exception as e:
        log.warning("bao admin thue so that bai: %s", e)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Kiểm tra OTP ngay",
                              callback_data=f"ts:check:{rid}")],
        [InlineKeyboardButton(text="📋 Đơn thuê của tôi", callback_data="ts:my")],
        [InlineKeyboardButton(text="⬅️ Menu thuê số", callback_data="ts:menu")],
    ])
    await cb.message.edit_text(
        f"✅ <b>Thuê số thành công!</b>\n\n"
        f"📱 Dịch vụ: <b>{html.escape(s['name'])}</b>\n"
        f"📞 Số: <code>{html.escape(rent['phone_number'])}</code>\n"
        f"💰 Đã trừ: <b>{vnd(sell)}đ</b> (ví chính)\n\n"
        f"⏳ Bot đang tự chờ mã OTP trong {RENT_TTL_MIN} phút. "
        f"Có mã sẽ báo ngay cho bạn.",
        parse_mode="HTML", reply_markup=kb)


# ---------------------------------------------------------------- OTP: kiểm tra tay + xem đơn

@router.callback_query(F.data.startswith("ts:check:"))
async def on_ts_check(cb: CallbackQuery):
    try:
        rid = int(cb.data.split(":")[2])
    except (ValueError, IndexError):
        await cb.answer("Dữ liệu không hợp lệ", show_alert=True)
        return
    r = db.viotp_rental_get(rid)
    if not r or r["tg_id"] != cb.from_user.id:
        await cb.answer("Đơn không tồn tại", show_alert=True)
        return
    if r["status"] == "done":
        await cb.answer(f"Mã OTP: {r['otp_code']}", show_alert=True)
        return
    if r["status"] != "waiting":
        await cb.answer("Đơn này đã kết thúc.", show_alert=True)
        return
    await cb.answer("Đang kiểm tra...")
    try:
        sess = await viotp.get_session(_token(), r["request_id"])
    except viotp.ViotpError as e:
        await cb.message.answer(f"⚠️ Lỗi kiểm tra OTP: {html.escape(str(e))}")
        return
    if sess["status"] == 1 and sess["code"]:
        db.viotp_rental_set_status(rid, "done", sess["code"])
        await cb.message.answer(
            f"🎉 <b>Đã nhận mã OTP!</b>\n\n"
            f"📱 {html.escape(r['service_name'])}\n"
            f"🔑 Mã: <code>{html.escape(sess['code'])}</code>\n\n"
            f"Chạm vào mã để copy.",
            parse_mode="HTML")
    elif sess["status"] == 2:
        db.viotp_rental_set_status(rid, "expired")
        await cb.message.answer("⌛ Số đã hết hạn, chưa nhận được OTP.")
    else:
        await cb.answer("Chưa có mã OTP, bot vẫn đang chờ...", show_alert=True)


def _rental_kb(rid: int, status: str) -> InlineKeyboardMarkup:
    rows = []
    if status == "waiting":
        rows.append([InlineKeyboardButton(text="🔄 Kiểm tra OTP ngay",
                                          callback_data=f"ts:check:{rid}")])
    rows.append([InlineKeyboardButton(text="⬅️ Đơn của tôi", callback_data="ts:my")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "ts:my")
async def on_ts_my(cb: CallbackQuery):
    rows = db.viotp_rental_list(cb.from_user.id, 10)
    await cb.answer()
    if not rows:
        await cb.message.edit_text("📋 Bạn chưa thuê số nào. Gõ /thueso để bắt đầu nhé.",
                                   reply_markup=_back_menu_kb())
        return
    kb_rows = []
    for r in rows:
        label = db.VIOTP_STATUS_LABEL.get(r["status"], r["status"])
        kb_rows.append([InlineKeyboardButton(
            text=f"{label} — {r['service_name'][:22]}",
            callback_data=f"ts:detail:{r['id']}")])
    kb_rows.append([InlineKeyboardButton(text="⬅️ Menu thuê số", callback_data="ts:menu")])
    await cb.message.edit_text("📋 <b>Đơn thuê số của bạn</b> (10 đơn gần nhất):",
                               parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


@router.callback_query(F.data.startswith("ts:detail:"))
async def on_ts_detail(cb: CallbackQuery):
    try:
        rid = int(cb.data.split(":")[2])
    except (ValueError, IndexError):
        await cb.answer("Dữ liệu không hợp lệ", show_alert=True)
        return
    r = db.viotp_rental_get(rid)
    if not r or r["tg_id"] != cb.from_user.id:
        await cb.answer("Đơn không tồn tại", show_alert=True)
        return
    label = db.VIOTP_STATUS_LABEL.get(r["status"], r["status"])
    txt = (f"🧾 <b>Đơn thuê số #{r['id']}</b>\n\n"
           f"📱 Dịch vụ: <b>{html.escape(r['service_name'])}</b>\n"
           f"📞 Số: <code>{html.escape(r['phone_number'])}</code>\n"
           f"💰 Giá: <b>{vnd(r['sell_price'])}đ</b>\n"
           f"📊 Trạng thái: {label}\n")
    if r["status"] == "done" and r["otp_code"]:
        txt += f"🔑 Mã OTP: <code>{html.escape(r['otp_code'])}</code>\n"
    await cb.answer()
    await cb.message.edit_text(txt, parse_mode="HTML",
                               reply_markup=_rental_kb(rid, r["status"]))
