"""📱 Shop thuê số OTP (ViOTP) — thuê số điện thoại nhận mã OTP.

Luồng khách: /thueso → chọn dịch vụ (phổ biến/tìm kiếm) → xem giá
→ xác nhận (trừ VÍ CHÍNH) → nhận số → bot tự poll OTP → báo mã OTP.
"""
import html
import logging
import time

# Chống bấm trùng nút khi mạng delay
_proc_lock: dict = {}

def _acquire_lock(uid: int, key: str) -> bool:
    now = time.time()
    k = (uid, key)
    ts = _proc_lock.get(k)
    if ts and now - ts < 30:
        return False
    _proc_lock[k] = now
    for kk in list(_proc_lock.keys()):
        if now - _proc_lock[kk] > 60:
            del _proc_lock[kk]
    return True

from aiogram import F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from .. import db
from ..rental_providers import default_provider, RentalProviderError
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

# Tên hiển thị nhà cung cấp cho khách (user yêu cầu 2026-10-14: đánh số 1/2,
# khách tự chọn, nhà đã chọn lỗi thì tự chuyển sang nhà còn lại)
PROVIDER_LABELS = {
    "viotp": "Nhà cung cấp 1",
    "otpvn": "Nhà cung cấp 2",
}


def _provider_label(pname: str) -> str:
    return PROVIDER_LABELS.get(pname, pname)

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
    # GĐ3: shop mở khi còn ít nhất 1 nhà cung cấp đang bật
    from ..rental_providers.merge import any_provider_enabled
    return any_provider_enabled()


def _prov():
    # GĐ1: shop /thueso luôn dùng provider mặc định (ViOTP) — hành vi không đổi.
    # GĐ3 sẽ chọn provider theo cấu hình (tự động rẻ-trước / khách chọn / admin gán).
    return default_provider()


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
    """Danh sách dịch vụ GỘP 2 nhà (GĐ3: tự động chọn nhà rẻ trước).
    Mỗi mục: {key, name, providers: {pname: {service_id, cost}}}.
    Trả None + báo lỗi nếu không lấy được nhà nào."""
    country = await _get_country(state) if state else DEFAULT_COUNTRY
    try:
        from ..rental_providers.merge import get_merged_services
        items = await get_merged_services(country=country)
        if not items:
            raise RentalProviderError("cả 2 nhà cung cấp đều không phản hồi")
        disabled = _disabled_service_ids()
        if disabled:
            items = [s for s in items
                     if s["providers"].get("viotp", {}).get("service_id") not in disabled]
        return items
    except RentalProviderError as e:
        txt = f"⚠️ Không lấy được danh sách dịch vụ: {html.escape(str(e))}"
        if isinstance(obj, CallbackQuery):
            await obj.answer("Lỗi kết nối nhà cung cấp", show_alert=True)
            await obj.message.answer(txt)
        else:
            await obj.answer(txt)
        return None


def _popular(items: list) -> list:
    out, seen = [], set()
    low = [(s, s["name"].lower()) for s in items]
    for key in POPULAR_KEYS:
        for s, name in low:
            if s["key"] in seen:
                continue
            if key in name:
                out.append(s)
                seen.add(s["key"])
                break
    return out


def _svc_rows(items: list, page: int, prefix: str) -> InlineKeyboardMarkup:
    from ..rental_providers.merge import best_sell
    total = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total - 1))
    chunk = items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    rows = [[InlineKeyboardButton(
        text=f"{s['name'][:28]} — {vnd(best_sell(s))}đ",
        callback_data=f"ts:svc:{s['key']}:{page}:{prefix}")] for s in chunk]
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


def _svc_by_key(items: list, key: str) -> dict:
    for s in items:
        if s["key"] == key:
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
    _prov().clear_services_cache()
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
    # Chuẩn hóa từ viết tắt phổ biến
    _abbr = {"fb": "facebook", "ig": "instagram", "tele": "telegram",
             "ytb": "youtube", "yt": "youtube", "gg": "google",
             "zalo": "zalo", "tiktok": "tiktok"}
    q_norm = _abbr.get(q, q)
    # Khớp: chuỗi con chính xác, hoặc tất cả ký tự của q xuất hiện theo thứ tự trong tên
    # (vd: "fb" khớp "facebook" qua f...b)
    def _match(name_low: str) -> bool:
        if q_norm in name_low:
            return True
        # fuzzy: các ký tự q xuất hiện theo đúng thứ tự
        it = iter(name_low)
        return all(ch in it for ch in q_norm)
    found = [s for s in items if _match(s["name"].lower())][:50]
    if not found:
        await msg.answer(f"🔍 Không tìm thấy dịch vụ nào khớp \"{html.escape(msg.text.strip())}\".",
                         parse_mode="HTML", reply_markup=_back_menu_kb())
        return
    # Lưu kết quả vào FSM để phân trang
    await state.update_data(search_results=[s["key"] for s in found])
    await msg.answer(
        f"🔍 Tìm thấy <b>{len(found)}</b> dịch vụ khớp:",
        parse_mode="HTML", reply_markup=_svc_rows(found, 0, "search"))


# ---------------------------------------------------------------- chi tiết + thuê

@router.callback_query(F.data.startswith("ts:svc:"))
async def on_ts_svc(cb: CallbackQuery, state: FSMContext):
    from ..rental_providers.merge import best_sell
    try:
        _, _, key, page_s, prefix = cb.data.split(":")
        page = int(page_s)
    except (ValueError, IndexError):
        await cb.answer("Dữ liệu không hợp lệ", show_alert=True)
        return
    items = await _services_or_error(cb, state)
    if items is None:
        return
    s = _svc_by_key(items, key)
    if not s:
        await cb.answer("Dịch vụ không còn", show_alert=True)
        return
    sell = best_sell(s)
    await cb.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Thuê số — {vnd(sell)}đ",
                              callback_data=f"ts:rent:{key}")],
        [InlineKeyboardButton(text="⬅️ Quay lại", callback_data=f"ts:page:{page}:{prefix}")],
    ])
    await cb.message.edit_text(
        f"📱 <b>{html.escape(s['name'])}</b>\n\n"
        f"💰 Giá thuê: <b>{vnd(sell)}đ</b> (giá tốt nhất)\n"
        f"⏳ Số dùng trong {RENT_TTL_MIN} phút, bot tự chờ mã OTP.\n"
        f"💸 Hết {RENT_TTL_MIN} phút không có mã → tự hoàn tiền vào ví thuê số.\n"
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
        keys = data.get("search_results") or []
        by_key = {s["key"]: s for s in items}
        lst = [by_key[k] for k in keys if k in by_key]
        title = "🔍 <b>Kết quả tìm kiếm:</b>"
    await cb.answer()
    await cb.message.edit_text(title, parse_mode="HTML",
                               reply_markup=_svc_rows(lst, page, prefix))


@router.callback_query(F.data.startswith("ts:rent:"))
async def on_ts_rent(cb: CallbackQuery, state: FSMContext):
    from ..rental_providers.merge import offers_sorted
    try:
        key = cb.data.split(":")[2]
    except IndexError:
        await cb.answer("Dữ liệu không hợp lệ", show_alert=True)
        return
    items = await _services_or_error(cb, state)
    if items is None:
        return
    s = _svc_by_key(items, key)
    if not s:
        await cb.answer("Dịch vụ không còn", show_alert=True)
        return
    offers = offers_sorted(s)
    if not offers:
        await cb.answer("Dịch vụ hiện không có nhà cung cấp nào.", show_alert=True)
        return
    bal = db.rent_get_balance(cb.from_user.id)
    kb_rows = []
    for pname, psid, cost, sell in offers:
        kb_rows.append([InlineKeyboardButton(
            text=f"🏠 {_provider_label(pname)} — {vnd(sell)}đ",
            callback_data=f"ts:prov:{key}:{pname}")])
    kb_rows.append([InlineKeyboardButton(text="⬅️ Quay lại", callback_data=f"ts:svc:{key}:0:hot")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await cb.answer()
    await cb.message.edit_text(
        f"📱 <b>{html.escape(s['name'])}</b>\n\n"
        f"👇 Chọn nhà cung cấp:\n"
        f"👛 Số dư ví thuê số: <b>{vnd(bal)}đ</b>\n\n"
        f"Nếu nhà bạn chọn không có số, bot sẽ tự chuyển sang nhà còn lại.",
        parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("ts:prov:"))
async def on_ts_provider(cb: CallbackQuery, state: FSMContext):
    from ..rental_providers.merge import offers_sorted
    try:
        _, _, key, pname = cb.data.split(":")
    except (ValueError, IndexError):
        await cb.answer("Dữ liệu không hợp lệ", show_alert=True)
        return
    items = await _services_or_error(cb, state)
    if items is None:
        return
    s = _svc_by_key(items, key)
    if not s:
        await cb.answer("Dịch vụ không còn", show_alert=True)
        return
    offers = offers_sorted(s)
    chosen = next((o for o in offers if o[0] == pname), None)
    if not chosen:
        await cb.answer("Nhà cung cấp này hiện không có dịch vụ.", show_alert=True)
        return
    _, _, _, sell = chosen
    bal = db.rent_get_balance(cb.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Xác nhận thuê — {vnd(sell)}đ",
                              callback_data=f"ts:confirm:{key}:{pname}")],
        [InlineKeyboardButton(text="⬅️ Chọn nhà khác", callback_data=f"ts:rent:{key}")],
    ])
    await cb.answer()
    await cb.message.edit_text(
        f"🧾 <b>Xác nhận thuê số</b>\n\n"
        f"📱 Dịch vụ: <b>{html.escape(s['name'])}</b>\n"
        f"🏠 Nhà cung cấp: <b>{_provider_label(pname)}</b>\n"
        f"💰 Giá: <b>{vnd(sell)}đ</b> (trừ ví thuê số)\n"
        f"👛 Số dư ví thuê số: <b>{vnd(bal)}đ</b>\n\n"
        f"Sau khi thuê, bạn sẽ nhận số điện thoại và bot tự chờ mã OTP trong {RENT_TTL_MIN} phút.\n"
        f"💸 Hết {RENT_TTL_MIN} phút không có mã → tự hoàn tiền vào ví thuê số.\n"
        f"⚠️ Nếu {_provider_label(pname)} không có số, bot tự chuyển sang nhà còn lại.",
        parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("ts:confirm:"))
async def on_ts_confirm(cb: CallbackQuery, state: FSMContext):
    from ..rental_providers import get_provider
    from ..rental_providers.merge import offers_sorted
    try:
        parts = cb.data.split(":")
        key = parts[2]
        chosen_pname = parts[3] if len(parts) > 3 else None
    except IndexError:
        await cb.answer("Dữ liệu không hợp lệ", show_alert=True)
        return
    tg_id = cb.from_user.id
    # Chống bấm trùng khi mạng delay
    if not _acquire_lock(tg_id, f"tsconfirm:{key}"):
        await cb.answer("⏳ Đơn đang được xử lý, vui lòng đợi...", show_alert=False)
        return
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await cb.answer()
    items = await _services_or_error(cb, state)
    if items is None:
        return
    s = _svc_by_key(items, key)
    if not s:
        await cb.answer("Dịch vụ không còn", show_alert=True)
        return
    # Khách chọn nhà cung cấp: thử nhà đã chọn trước, lỗi thì chuyển sang nhà còn lại.
    offers = offers_sorted(s)
    if chosen_pname:
        offers = sorted(offers, key=lambda o: 0 if o[0] == chosen_pname else 1)
    if not offers:
        await cb.answer("Dịch vụ hiện không có nhà cung cấp nào.", show_alert=True)
        return
    country = await _get_country(state)
    # 1. Trừ ví thuê số theo giá rẻ nhất (nguyên tử, không đủ -> False)
    first_sell = offers[0][3]
    if not db.rent_adjust_balance(tg_id, -first_sell, f"thue_so:{s['name'][:40]}"):
        await cb.answer("Ví thuê số không đủ tiền. Nạp thêm bằng /napthueso nhé.",
                        show_alert=True)
        return
    deducted = first_sell
    rent, used = None, None
    for pname, psid, cost, sell in offers:
        # Điều chỉnh số tiền đã trừ cho khớp giá của nhà đang thử
        diff = sell - deducted
        if diff > 0:
            if not db.rent_adjust_balance(tg_id, -diff, f"thue_so_chenh_lech:{pname}"):
                db.rent_adjust_balance(tg_id, deducted, "hoan_tien_thue_so_loi")
                log.warning("thue so tg=%s: khong du tien cho nha %s", tg_id, pname)
                await cb.answer(
                    "Thuê số thất bại (không đủ tiền cho nhà còn lại), đã hoàn tiền.",
                    show_alert=True)
                return
        elif diff < 0:
            db.rent_adjust_balance(tg_id, -diff, "hoan_tien_chenh_lech_gia")
        deducted = sell
        try:
            rent = await get_provider(pname).rent_number(psid, country=country)
            used = (pname, psid, cost, sell)
            break
        except Exception as e:
            log.warning("thue so nha %s that bai, thu nha khac (tg=%s): %s",
                        pname, tg_id, e)
            continue
    # 2. Cả 2 nhà đều lỗi -> HOÀN TIỀN
    if rent is None or used is None:
        db.rent_adjust_balance(tg_id, deducted, "hoan_tien_thue_so_loi")
        log.error("thue so that bai ca 2 nha sau khi tru tien tg=%s", tg_id)
        await cb.answer("Thuê số thất bại, đã hoàn tiền vào ví thuê số.", show_alert=True)
        await cb.message.edit_text(
            "⚠️ Thuê số thất bại (cả 2 nhà cung cấp đều lỗi). "
            f"Đã hoàn <b>{vnd(deducted)}đ</b> vào ví thuê số.",
            parse_mode="HTML", reply_markup=_back_menu_kb())
        return
    pname, psid, cost, sell = used
    # 3. Lưu đơn thuê — lỗi ở đây cũng HOÀN TIỀN (2026-10-05: từng mất tiền
    # vì viotp_rentals.id không tự tăng trên Postgres, tiền trừ mà đơn không tạo)
    try:
        rid = db.viotp_rental_create(
            tg_id, rent["request_id"], rent["phone_number"], psid,
            s["name"], country, cost, sell, provider=pname)
    except Exception as e:
        db.rent_adjust_balance(tg_id, deducted, "hoan_tien_tao_don_loi")
        log.error("tao don thue so that bai sau khi tru tien tg=%s: %s", tg_id, e)
        await cb.answer("Lỗi tạo đơn thuê, đã hoàn tiền vào ví thuê số.",
                        show_alert=True)
        await cb.message.edit_text(
            "⚠️ Lỗi tạo đơn thuê số. "
            f"Đã hoàn <b>{vnd(deducted)}đ</b> vào ví thuê số, bạn thử lại nhé.",
            parse_mode="HTML", reply_markup=_back_menu_kb())
        return
    if not rid:
        db.rent_adjust_balance(tg_id, deducted, "hoan_tien_tao_don_loi")
        log.error("tao don thue so tra ve rid=0 tg=%s", tg_id)
        await cb.answer("Lỗi tạo đơn thuê, đã hoàn tiền vào ví thuê số.",
                        show_alert=True)
        return
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
        f"Có mã sẽ báo ngay cho bạn.\n"
        f"💸 Hết {RENT_TTL_MIN} phút không có mã → tự hoàn tiền vào ví thuê số.",
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
        if r["status"] == "refunded":
            await cb.answer("Đơn này đã được hoàn tiền vào ví thuê số.", show_alert=True)
        else:
            await cb.answer("Đơn này đã kết thúc.", show_alert=True)
        return
    await cb.answer("Đang kiểm tra...")
    try:
        from ..rental_providers import get_provider
        prov = get_provider(r.get("provider") or "viotp")
        sess = await prov.get_session(r["request_id"])
    except RentalProviderError as e:
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
    waiting_for_price_search = State()
    waiting_for_price_input = State()


def _thueoadm_kb() -> InlineKeyboardMarkup:
    en_viotp = db.get_setting("viotp_enabled", "1") == "1"
    en_otpvn = db.get_setting("otpvn_enabled", "1") == "1"
    markup_viotp = db.get_setting("viotp_markup_pct", "50")
    markup_otpvn = db.get_setting("otpvn_markup_pct", "50")
    auto_refund = db.get_setting("viotp_auto_refund", "1") == "1"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"ViOTP: {'🟢 BẬT' if en_viotp else '🔴 TẮT'} — bấm để đổi",
            callback_data="tsadm:toggle:viotp"),
         InlineKeyboardButton(
            text=f"OTPVN: {'🟢 BẬT' if en_otpvn else '🔴 TẮT'} — bấm để đổi",
            callback_data="tsadm:toggle:otpvn")],
        [InlineKeyboardButton(text=f"💰 Lãi ViOTP: {markup_viotp}% — bấm để đổi",
                              callback_data="tsadm:markup:viotp"),
         InlineKeyboardButton(text=f"💰 Lãi OTPVN: {markup_otpvn}% — bấm để đổi",
                              callback_data="tsadm:markup:otpvn")],
        [InlineKeyboardButton(
            text=f"💸 Tự hoàn hết hạn: {'🟢 BẬT' if auto_refund else '🔴 TẮT'} — bấm để đổi",
            callback_data="tsadm:refund")],
        [InlineKeyboardButton(text="📋 10 đơn gần nhất", callback_data="tsadm:recent")],
        [InlineKeyboardButton(text="🏷️ Giá từng dịch vụ", callback_data="tsadm:prices")],
        [InlineKeyboardButton(text="🤖 Bot báo riêng", callback_data="tsadm:bot")],
        [InlineKeyboardButton(text="🔄 Làm mới", callback_data="tsadm:menu")],
    ])


def _thueoadm_text() -> str:
    st = db.viotp_stats()
    en_viotp = db.get_setting("viotp_enabled", "1") == "1"
    en_otpvn = db.get_setting("otpvn_enabled", "1") == "1"
    markup_viotp = db.get_setting("viotp_markup_pct", "50")
    markup_otpvn = db.get_setting("otpvn_markup_pct", "50")
    auto_refund = db.get_setting("viotp_auto_refund", "1") == "1"
    return (
        "📱 <b>QUẢN LÝ SHOP THUÊ SỐ</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"📊 Tổng đơn: <b>{st['total']}</b> • "
        f"Doanh thu: <b>{vnd(st['revenue'])}đ</b>\n"
        f"💵 Lãi: <b>{vnd(st['profit'])}đ</b> • "
        f"⏳ Đang chờ OTP: <b>{st['waiting']}</b>\n\n"
        f"ViOTP: {'🟢 BẬT' if en_viotp else '🔴 TẮT'} • "
        f"OTPVN: {'🟢 BẬT' if en_otpvn else '🔴 TẮT'}\n"
        f"💰 Lãi ViOTP: {markup_viotp}% • Lãi OTPVN: {markup_otpvn}%\n"
        f"💸 Tự hoàn khi hết hạn: {'🟢 BẬT' if auto_refund else '🔴 TẮT'}\n"
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
        # tsadm:toggle:viotp | tsadm:toggle:otpvn (cũ: tsadm:toggle = ViOTP)
        parts = cb.data.split(":")
        pname = parts[2] if len(parts) > 2 else "viotp"
        if pname not in ("viotp", "otpvn"):
            pname = "viotp"
        key = f"{pname}_enabled"
        cur = db.get_setting(key, "1") == "1"
        db.set_setting(key, "0" if cur else "1")
        await cb.message.edit_text(_thueoadm_text(), parse_mode="HTML",
                                   reply_markup=_thueoadm_kb())
        return

    if action == "refund":
        if not is_super(cb.from_user.id):
            await cb.message.answer("⛔ Chỉ chủ shop mới dùng được.")
            return
        cur = db.get_setting("viotp_auto_refund", "1") == "1"
        db.set_setting("viotp_auto_refund", "0" if cur else "1")
        await cb.answer(
            "💸 Đã BẬT tự hoàn: hết hạn không mã → hoàn full vào ví thuê số."
            if not cur else
            "💸 Đã TẮT tự hoàn: đơn hết hạn chỉ đánh dấu, admin xử lý tay.",
            show_alert=True)
        await cb.message.edit_text(_thueoadm_text(), parse_mode="HTML",
                                   reply_markup=_thueoadm_kb())
        return

    if action == "markup":
        if not is_super(cb.from_user.id):
            await cb.message.answer("⛔ Chỉ chủ shop mới dùng được.")
            return
        parts = cb.data.split(":")
        provider = parts[2] if len(parts) > 2 and parts[2] in ("viotp", "otpvn") else "viotp"
        await state.update_data(markup_provider=provider)
        await state.set_state(ThueSoAdmState.waiting_for_markup)
        name = "OTPVN" if provider == "otpvn" else "ViOTP"
        await cb.message.answer(f"💰 Nhập % lãi thêm mới cho {name} (0–500, vd: 50):")
        return

    if action == "prices":
        if not is_super(cb.from_user.id):
            await cb.message.answer("⛔ Chỉ chủ shop mới dùng được.")
            return
        parts = cb.data.split(":")
        if len(parts) == 2:
            # Chọn nhà cung cấp
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="ViOTP", callback_data="tsadm:prices:viotp"),
                 InlineKeyboardButton(text="OTPVN", callback_data="tsadm:prices:otpvn")],
                [InlineKeyboardButton(text="⬅️ Quay lại", callback_data="tsadm:menu")],
            ])
            await cb.message.edit_text(
                "🏷️ <b>ĐẶT GIÁ TỪNG DỊCH VỤ</b>\nChọn nhà cung cấp:",
                parse_mode="HTML", reply_markup=kb)
            return
        provider = parts[2] if parts[2] in ("viotp", "otpvn") else "viotp"
        name = "OTPVN" if provider == "otpvn" else "ViOTP"
        overrides = db.rent_price_list(provider)
        lines = [f"🏷️ <b>GIÁ ĐÃ ĐẶT — {name}</b> ({len(overrides)} dịch vụ)\n"]
        if overrides:
            try:
                from ..rental_providers import get_provider as _gp
                items = await _gp(provider).get_services()
                names = {s["id"]: s["name"] for s in items}
            except Exception:
                names = {}
            for o in overrides[:30]:
                nm = names.get(o["service_id"], f"ID {o['service_id']}")
                lines.append(f"• {html.escape(nm[:30])}: <b>{vnd(o['sell_price'])}đ</b>")
            if len(overrides) > 30:
                lines.append(f"<i>…và {len(overrides) - 30} dịch vụ nữa</i>")
        else:
            lines.append("<i>Chưa đặt giá riêng dịch vụ nào — đang dùng % lãi.</i>")
        lines.append("\nNhập tên dịch vụ cần đặt giá (vd: <i>facebook</i>), hoặc /thuesoadm để thoát.")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Quay lại", callback_data="tsadm:menu")],
        ])
        await state.update_data(price_provider=provider)
        await state.set_state(ThueSoAdmState.waiting_for_price_search)
        await cb.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=kb)
        return

    if action == "pricepick":
        if not is_super(cb.from_user.id):
            await cb.message.answer("⛔ Chỉ chủ shop mới dùng được.")
            return
        parts = cb.data.split(":")
        if len(parts) < 4:
            return
        provider, sid = parts[2], parts[3]
        if provider not in ("viotp", "otpvn"):
            return
        try:
            from ..rental_providers import get_provider as _gp2
            items = await _gp2(provider).get_services()
            s = next((x for x in items if str(x["id"]) == str(sid)), None)
        except Exception:
            s = None
        if not s:
            await cb.answer("Dịch vụ không còn", show_alert=True)
            return
        cur = db.rent_price_get(provider, int(sid))
        cur_txt = f"{vnd(cur)}đ" if cur else "chưa đặt (dùng % lãi)"
        await state.update_data(price_provider=provider, price_sid=int(sid),
                                price_name=s["name"])
        await state.set_state(ThueSoAdmState.waiting_for_price_input)
        await cb.message.answer(
            f"🏷️ <b>{html.escape(s['name'])}</b> ({'OTPVN' if provider == 'otpvn' else 'ViOTP'})\n"
            f"Giá vốn: {vnd(s['price'])}đ • Giá hiện tại: {cur_txt}\n\n"
            f"Nhập giá bán mới (đ, vd: 5000). Nhập <b>0</b> để xóa về % lãi.",
            parse_mode="HTML")
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
    try:
        data = await state.get_data()
        provider = data.get("markup_provider", "viotp")
    except Exception:
        provider = "viotp"
    if provider not in ("viotp", "otpvn"):
        provider = "viotp"
    db.set_setting(f"{provider}_markup_pct", str(pct))
    await state.clear()
    name = "OTPVN" if provider == "otpvn" else "ViOTP"
    await msg.answer(f"✅ Đã đặt lãi thêm {name} <b>{pct}%</b>.", parse_mode="HTML",
                     reply_markup=_thueoadm_kb())


@router.message(StateFilter(ThueSoAdmState.waiting_for_price_search))
async def on_tsadm_price_search(msg: Message, state: FSMContext):
    from .core import _is_admin
    from ..perms import is_super
    if not _is_admin(msg.from_user.id) or not is_super(msg.from_user.id):
        await state.clear()
        return
    try:
        data = await state.get_data()
        provider = data.get("price_provider", "viotp")
    except Exception:
        provider = "viotp"
    if provider not in ("viotp", "otpvn"):
        provider = "viotp"
    q = (msg.text or "").strip().lower()
    if not q:
        await msg.answer("Bạn chưa nhập gì cả.")
        return
    try:
        from ..rental_providers import get_provider as _gp3
        items = await _gp3(provider).get_services()
    except Exception as e:
        await state.clear()
        await msg.answer(f"⚠️ Không lấy được dịch vụ: {html.escape(str(e))}")
        return
    _abbr = {"fb": "facebook", "ig": "instagram", "tele": "telegram",
             "ytb": "youtube", "yt": "youtube", "gg": "google",
             "zalo": "zalo", "tiktok": "tiktok"}
    q_norm = _abbr.get(q, q)

    def _match(name_low: str) -> bool:
        if q_norm in name_low:
            return True
        it = iter(name_low)
        return all(ch in it for ch in q_norm)

    found = [s for s in items if _match(s["name"].lower())][:10]
    if not found:
        await msg.answer(f"🔍 Không tìm thấy dịch vụ nào khớp \"{html.escape(q)}\". Thử từ khác nhé.")
        return
    rows = []
    for s in found:
        cur = db.rent_price_get(provider, s["id"])
        cur_txt = f" — {vnd(cur)}đ" if cur else ""
        rows.append([InlineKeyboardButton(
            text=f"{s['name'][:28]} (vốn {vnd(s['price'])}đ){cur_txt}",
            callback_data=f"tsadm:pricepick:{provider}:{s['id']}")])
    rows.append([InlineKeyboardButton(text="⬅️ Quay lại", callback_data="tsadm:menu")])
    await msg.answer("🔍 Kết quả — bấm để đặt giá:",
                     reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(StateFilter(ThueSoAdmState.waiting_for_price_input))
async def on_tsadm_price_input(msg: Message, state: FSMContext):
    from .core import _is_admin
    from ..perms import is_super
    if not _is_admin(msg.from_user.id) or not is_super(msg.from_user.id):
        await state.clear()
        return
    try:
        price = int((msg.text or "").strip().replace(".", "").replace(",", ""))
        if price < 0 or price > 1000000:
            raise ValueError
    except ValueError:
        await msg.answer("⚠️ Nhập số tiền 0–1.000.000 thôi nhé (0 = xóa về % lãi).")
        return
    try:
        data = await state.get_data()
        provider = data.get("price_provider", "viotp")
        sid = int(data.get("price_sid", 0))
        sname = data.get("price_name", f"ID {sid}")
    except Exception:
        await state.clear()
        return
    db.rent_price_set(provider, sid, price)
    await state.clear()
    name = "OTPVN" if provider == "otpvn" else "ViOTP"
    if price <= 0:
        txt = f"✅ Đã xóa giá riêng <b>{html.escape(sname)}</b> ({name}) — về % lãi."
    else:
        txt = f"✅ Đã đặt giá <b>{html.escape(sname)}</b> ({name}) = <b>{vnd(price)}đ</b>."
    await msg.answer(txt, parse_mode="HTML", reply_markup=_thueoadm_kb())
