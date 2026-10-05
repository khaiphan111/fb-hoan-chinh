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
PAGE_SIZE = 12
RENT_TTL_MIN = 15  # thời gian chờ OTP mỗi lượt thuê

# Quốc gia hỗ trợ (mã ISO cho API ViOTP)
COUNTRIES = [
    ("vn", "🇻🇳 Việt Nam"),
    ("th", "🇹🇭 Thái Lan"),
    ("id", "🇮🇩 Indonesia"),
    ("ph", "🇵🇭 Philippines"),
    ("my", "🇲🇾 Malaysia"),
    ("us", "🇺🇸 Mỹ"),
    ("gb", "🇬🇧 Anh"),
]
DEFAULT_COUNTRY = "vn"


def _country_name(code: str) -> str:
    for c, name in COUNTRIES:
        if c == code:
            return name
    return code.upper()


class ThueSoState(StatesGroup):
    waiting_for_search = State()


def _enabled() -> bool:
    return db.get_setting("viotp_enabled", "1") == "1"


def _token() -> str:
    # Token lấy từ Secure Vault (custom.viotp) trong app/viotp.py
    return ""


def _menu_kb(country: str = DEFAULT_COUNTRY) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🌍 Quốc gia: {_country_name(country)}",
                              callback_data="ts:country")],
        [InlineKeyboardButton(text="🔥 Dịch vụ phổ biến", callback_data="ts:hot")],
        [InlineKeyboardButton(text="📋 Tất cả dịch vụ", callback_data="ts:all:0")],
        [InlineKeyboardButton(text="🔍 Tìm dịch vụ", callback_data="ts:search")],
        [InlineKeyboardButton(text="📋 Đơn thuê của tôi", callback_data="ts:my")],
    ])


def _country_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=name, callback_data=f"ts:ct:{code}")]
            for code, name in COUNTRIES]
    rows.append([InlineKeyboardButton(text="⬅️ Menu thuê số", callback_data="ts:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _back_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Menu thuê số", callback_data="ts:menu")],
    ])


async def _get_country(state) -> str:
    try:
        data = await state.get_data()
        return data.get("ts_country", DEFAULT_COUNTRY)
    except Exception:
        return DEFAULT_COUNTRY


def _disabled_service_ids() -> set:
    import json as _json
    try:
        return set(_json.loads(db.get_setting("viotp_disabled_services", "[]")))
    except Exception:
        return set()


async def _services_or_error(obj, state=None) -> list | None:
    """Lấy danh sách dịch vụ; trả None + báo lỗi nếu không được."""
    country = await _get_country(state) if state else DEFAULT_COUNTRY
    try:
        items = await viotp.get_services(country=country)
        disabled = _disabled_service_ids()
        if disabled:
            items = [s for s in items if s.get("id") not in disabled]
        return items
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
    country = await _get_country(state)
    await cb.answer()
    await cb.message.edit_text(
        "📱 <b>SHOP THUÊ SỐ OTP</b>\nChọn dịch vụ bên dưới nhé:",
        parse_mode="HTML", reply_markup=_menu_kb(country))


@router.callback_query(F.data == "ts:country")
async def on_ts_country(cb: CallbackQuery):
    await cb.answer()
    await cb.message.edit_text(
        "🌍 <b>Chọn quốc gia</b> cho số điện thoại:",
        parse_mode="HTML", reply_markup=_country_kb())


@router.callback_query(F.data.startswith("ts:ct:"))
async def on_ts_country_pick(cb: CallbackQuery, state: FSMContext):
    code = cb.data.split(":")[2]
    if code not in [c for c, _ in COUNTRIES]:
        await cb.answer("Quốc gia không hợp lệ", show_alert=True)
        return
    await state.update_data(ts_country=code)
    viotp.clear_services_cache()
    await cb.answer(f"Đã chọn {_country_name(code)}")
    await cb.message.edit_text(
        "📱 <b>SHOP THUÊ SỐ OTP</b>\nChọn dịch vụ bên dưới nhé:",
        parse_mode="HTML", reply_markup=_menu_kb(code))


@router.callback_query(F.data == "ts:hot")
async def on_ts_hot(cb: CallbackQuery, state: FSMContext):
    items = await _services_or_error(cb, state)
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


@router.callback_query(F.data.startswith("ts:all:"))
async def on_ts_all(cb: CallbackQuery, state: FSMContext):
    try:
        page = int(cb.data.split(":")[2])
    except (ValueError, IndexError):
        page = 0
    items = await _services_or_error(cb, state)
    if items is None:
        return
    # Sắp xếp theo tên cho dễ tìm
    items = sorted(items, key=lambda s: s["name"].lower())
    await cb.answer()
    await cb.message.edit_text(
        f"📋 <b>Tất cả dịch vụ</b> ({len(items)} dịch vụ) — chọn để xem giá và thuê số:",
        parse_mode="HTML", reply_markup=_svc_rows(items, page, "all"))


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
    items = await _services_or_error(msg, state)
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
async def on_ts_svc(cb: CallbackQuery, state: FSMContext):
    try:
        _, _, sid_s, page_s, prefix = cb.data.split(":")
        sid, page = int(sid_s), int(page_s)
    except (ValueError, IndexError):
        await cb.answer("Dữ liệu không hợp lệ", show_alert=True)
        return
    items = await _services_or_error(cb, state)
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
        f"💳 Trừ vào <b>ví thuê số</b>.",
        parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("ts:page:"))
async def on_ts_page(cb: CallbackQuery, state: FSMContext):
    try:
        _, _, page_s, prefix = cb.data.split(":")
        page = int(page_s)
    except (ValueError, IndexError):
        await cb.answer("Dữ liệu không hợp lệ", show_alert=True)
        return
    items = await _services_or_error(cb, state)
    if items is None:
        return
    if prefix == "hot":
        lst, title = _popular(items), "🔥 <b>Dịch vụ phổ biến</b> — chọn để xem giá và thuê số:"
    elif prefix == "all":
        lst = sorted(items, key=lambda s: s["name"].lower())
        title = f"📋 <b>Tất cả dịch vụ</b> ({len(lst)} dịch vụ) — chọn để xem giá và thuê số:"
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
async def on_ts_rent(cb: CallbackQuery, state: FSMContext):
    try:
        sid = int(cb.data.split(":")[2])
    except (ValueError, IndexError):
        await cb.answer("Dữ liệu không hợp lệ", show_alert=True)
        return
    items = await _services_or_error(cb, state)
    if items is None:
        return
    s = _svc_by_id(items, sid)
    if not s:
        await cb.answer("Dịch vụ không còn", show_alert=True)
        return
    sell = db.viotp_sell_price(s["price"])
    bal = db.rent_get_balance(cb.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Xác nhận thuê — {vnd(sell)}đ",
                              callback_data=f"ts:confirm:{sid}")],
        [InlineKeyboardButton(text="⬅️ Quay lại", callback_data=f"ts:svc:{sid}:0:hot")],
    ])
    await cb.answer()
    await cb.message.edit_text(
        f"🧾 <b>Xác nhận thuê số</b>\n\n"
        f"📱 Dịch vụ: <b>{html.escape(s['name'])}</b>\n"
        f"💰 Giá: <b>{vnd(sell)}đ</b> (trừ ví thuê số)\n"
        f"👛 Số dư ví thuê số: <b>{vnd(bal)}đ</b>\n\n"
        f"Sau khi thuê, bạn sẽ nhận số điện thoại và bot tự chờ mã OTP trong {RENT_TTL_MIN} phút.",
        parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("ts:confirm:"))
async def on_ts_confirm(cb: CallbackQuery, state: FSMContext):
    try:
        sid = int(cb.data.split(":")[2])
    except (ValueError, IndexError):
        await cb.answer("Dữ liệu không hợp lệ", show_alert=True)
        return
    tg_id = cb.from_user.id
    items = await _services_or_error(cb, state)
    if items is None:
        return
    s = _svc_by_id(items, sid)
    if not s:
        await cb.answer("Dịch vụ không còn", show_alert=True)
        return
    cost, sell = s["price"], db.viotp_sell_price(s["price"])
    # 1. Trừ ví thuê số (nguyên tử, không đủ -> False)
    if not db.rent_adjust_balance(tg_id, -sell, f"thue_so:{s['name'][:40]}"):
        await cb.answer("Ví thuê số không đủ tiền. Nạp thêm bằng /napthueso nhé.",
                        show_alert=True)
        return
    # 2. Gọi API thuê số — lỗi sau khi trừ tiền thì HOÀN TIỀN (chống mất tiền)
    try:
        country = await _get_country(state)
        rent = await viotp.rent_number(_token(), sid, country=country)
    except Exception as e:
        db.rent_adjust_balance(tg_id, sell, "hoan_tien_thue_so_loi")
        log.error("thue so that bai sau khi tru tien tg=%s: %s", tg_id, e)
        await cb.answer("Thuê số thất bại, đã hoàn tiền vào ví thuê số.", show_alert=True)
        await cb.message.edit_text(
            "⚠️ Thuê số thất bại (lỗi nhà cung cấp). "
            f"Đã hoàn <b>{vnd(sell)}đ</b> vào ví thuê số.",
            parse_mode="HTML", reply_markup=_back_menu_kb())
        return
    rid = db.viotp_rental_create(
        tg_id, rent["request_id"], rent["phone_number"], sid,
        s["name"], country, cost, sell)
    await cb.answer("Thuê số thành công!")
    # Báo bot riêng cho admin
    try:
        from .. import viotp_notify as _vn
        r = db.viotp_rental_get(rid)
        username = (cb.from_user.username or "")
        from .. import notify_router as _nr
        await _nr.send("rent", _vn.rental_notify_text(r, username))
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
        f"💰 Đã trừ: <b>{vnd(sell)}đ</b> (ví thuê số)\n\n"
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
        # Báo bot riêng cho admin (đồng nhất với poller nền)
        try:
            from . import viotp_notify as _vn
            rr = db.viotp_rental_get(rid)
            from .. import notify_router as _nr
            await _nr.send("rent", _vn.otp_notify_text(rr))
        except Exception:
            pass
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


# ---------------------------------------------------------------- admin: /thueoadm

class ThueSoAdmState(StatesGroup):
    waiting_for_markup = State()


def _thueoadm_kb() -> InlineKeyboardMarkup:
    enabled = db.get_setting("viotp_enabled", "1") == "1"
    markup = db.get_setting("viotp_markup_pct", "50")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{'🟢 Đang BẬT' if enabled else '🔴 Đang TẮT'} — bấm để đổi",
            callback_data="tsadm:toggle")],
        [InlineKeyboardButton(text=f"💰 Lãi thêm: {markup}% — bấm để đổi",
                              callback_data="tsadm:markup")],
        [InlineKeyboardButton(text="📋 10 đơn gần nhất", callback_data="tsadm:recent")],
        [InlineKeyboardButton(text="🤖 Bot báo riêng", callback_data="tsadm:bot")],
        [InlineKeyboardButton(text="🔄 Làm mới", callback_data="tsadm:menu")],
    ])


def _thueoadm_text() -> str:
    st = db.viotp_stats()
    enabled = db.get_setting("viotp_enabled", "1") == "1"
    markup = db.get_setting("viotp_markup_pct", "50")
    return (
        "📱 <b>QUẢN LÝ SHOP THUÊ SỐ</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"📊 Tổng đơn: <b>{st['total']}</b> • "
        f"Doanh thu: <b>{vnd(st['revenue'])}đ</b>\n"
        f"💵 Lãi: <b>{vnd(st['profit'])}đ</b> • "
        f"⏳ Đang chờ OTP: <b>{st['waiting']}</b>\n\n"
        f"Trạng thái: {'🟢 BẬT' if enabled else '🔴 TẮT'} • Lãi thêm: {markup}%\n"
        f"🤖 Bot báo riêng: {_viotp_bot_status()}"
    )


def _viotp_bot_status() -> str:
    try:
        from ..viotp_notify import manager
        return "🟢 đang chạy" if manager.running else "🔴 chưa chạy"
    except Exception:
        return "❓ không rõ"


def _thueoadm_bot_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Gửi tin test", callback_data="tsadm:bottest")],
        [InlineKeyboardButton(text="⬅️ Quay lại", callback_data="tsadm:menu")],
    ])


@router.message(Command("thuesoadm"))
async def on_thueoadm(msg: Message, state: FSMContext):
    from .core import _is_admin
    if not _is_admin(msg.from_user.id):
        return
    await state.clear()
    await msg.answer(_thueoadm_text(), parse_mode="HTML", reply_markup=_thueoadm_kb())


@router.callback_query(F.data.startswith("tsadm:"))
async def on_tsadm_cb(cb: CallbackQuery, state: FSMContext):
    from .core import _is_admin
    from ..perms import is_super
    if not _is_admin(cb.from_user.id):
        await cb.answer("⛔ Không có quyền.", show_alert=True)
        return
    await cb.answer()
    action = cb.data.split(":")[1] if ":" in cb.data else ""

    if action == "menu":
        await state.clear()
        await cb.message.edit_text(_thueoadm_text(), parse_mode="HTML",
                                   reply_markup=_thueoadm_kb())
        return

    if action == "toggle":
        if not is_super(cb.from_user.id):
            await cb.message.answer("⛔ Chỉ chủ shop mới dùng được.")
            return
        cur = db.get_setting("viotp_enabled", "1") == "1"
        db.set_setting("viotp_enabled", "0" if cur else "1")
        await cb.message.edit_text(_thueoadm_text(), parse_mode="HTML",
                                   reply_markup=_thueoadm_kb())
        return

    if action == "markup":
        if not is_super(cb.from_user.id):
            await cb.message.answer("⛔ Chỉ chủ shop mới dùng được.")
            return
        await state.set_state(ThueSoAdmState.waiting_for_markup)
        await cb.message.answer("💰 Nhập % lãi thêm mới (0–500, vd: 50):")
        return

    if action == "bot":
        status = _viotp_bot_status()
        try:
            from ..viotp_notify import privileged_ids
            pids = ", ".join(f"<code>{p}</code>" for p in sorted(privileged_ids()))
        except Exception:
            pids = "?"
        await cb.message.edit_text(
            f"🤖 <b>BOT BÁO RIÊNG THUÊ SỐ</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"Trạng thái: <b>{status}</b>\n"
            f"Người nhận: {pids}\n\n"
            f"Bot này báo đơn thuê mới + OTP về cho admin.",
            parse_mode="HTML", reply_markup=_thueoadm_bot_kb())
        return

    if action == "bottest":
        try:
            from ..viotp_notify import manager
            ok = await manager.send_to_privileged(
                "🧪 <b>Tin test bot báo thuê số</b>\nBot đang hoạt động bình thường.")
        except Exception:
            ok = False
        await cb.answer("✅ Đã gửi!" if ok else "❌ Gửi thất bại (bot chưa chạy).",
                        show_alert=True)
        return

    if action == "recent":
        rows = db.viotp_rental_list_all(10)
        if not rows:
            await cb.message.answer("📭 Chưa có đơn thuê số nào.")
            return
        lines = ["📋 <b>10 ĐƠN THUÊ GẦN NHẤT</b>", ""]
        for r in rows:
            label = db.VIOTP_STATUS_LABEL.get(r["status"], r["status"])
            otp = f" — OTP: <code>{html.escape(r['otp_code'])}</code>" if r["otp_code"] else ""
            lines.append(
                f"• #{r['id']} {html.escape(r['service_name'])} — "
                f"<b>{vnd(r['sell_price'])}đ</b> — {label}{otp}\n"
                f"  📞 <code>{html.escape(r['phone_number'])}</code> "
                f"👤 <code>{r['tg_id']}</code>"
            )
        await cb.message.answer("\n".join(lines), parse_mode="HTML")
        return


@router.message(StateFilter(ThueSoAdmState.waiting_for_markup))
async def on_tsadm_markup_input(msg: Message, state: FSMContext):
    from .core import _is_admin
    from ..perms import is_super
    if not _is_admin(msg.from_user.id) or not is_super(msg.from_user.id):
        await state.clear()
        return
    try:
        pct = int((msg.text or "").strip())
        if not 0 <= pct <= 500:
            raise ValueError
    except ValueError:
        await msg.answer("⚠️ Nhập số 0–500 thôi nhé.")
        return
    db.set_setting("viotp_markup_pct", str(pct))
    await state.clear()
    await msg.answer(f"✅ Đã đặt lãi thêm <b>{pct}%</b>.", parse_mode="HTML",
                     reply_markup=_thueoadm_kb())
