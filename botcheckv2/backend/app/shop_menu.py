"""Menu nút /shopadm — quản lý shop acc Facebook + nhà cung cấp bằng nút bấm.

Tái dùng toàn bộ handler lệnh có sẵn trong app/bot.py qua shim message
(không sửa logic nghiệp vụ). Đăng ký sớm qua register_shop_menu(router),
trước on_other — cùng pattern với menu /adm trong app/admin_bot.py.
"""
import html

from aiogram import F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from . import perms as _perms
from . import db


class ShopMenuState(StatesGroup):
    input = State()    # đang chờ admin nhập liệu từng bước
    confirm = State()  # đang chờ xác nhận thao tác nguy hiểm


def _is_admin_sync(tg_id: int) -> bool:
    return _perms.is_admin(tg_id)


def _get_handler(name: str):
    from . import bot as _bmod
    return getattr(_bmod, name)


class _ShopTextShim:
    """Giả lập Message với text tuỳ chỉnh để tái dùng handler lệnh gốc."""
    def __init__(self, msg: Message, text: str, edit_target=None):
        self._msg = msg
        self.text = text
        self._edit_target = edit_target
        self._edited = False

    @property
    def chat(self):
        return self._msg.chat

    @property
    def from_user(self):
        return self._msg.from_user

    @property
    def bot(self):
        return self._msg.bot

    async def answer(self, text, **kwargs):
        if self._edit_target is not None and not self._edited:
            self._edited = True
            kw = {k: v for k, v in kwargs.items()
                  if k in ("parse_mode", "reply_markup", "disable_web_page_preview")}
            try:
                return await self._edit_target.edit_text(text, **kw)
            except Exception:
                pass
        return await self._msg.answer(text, **kwargs)

    async def answer_document(self, *args, **kwargs):
        return await self._msg.answer_document(*args, **kwargs)


# ---------------------------------------------------------------- bàn phím

def _main_kb(tg_id=None):
    rows = []
    for cat, (title, _items) in GROUPS.items():
        if tg_id is not None and not _perms.has_perm(tg_id, cat):
            continue
        label = title.replace("<b>", "").replace("</b>", "")
        rows.append([InlineKeyboardButton(text=label, callback_data=f"shopm:cat_{cat}")])
    if not rows:
        rows.append([InlineKeyboardButton(text="🚫 Không có quyền nào", callback_data="shopm:noop")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _main_text():
    return ("🛒 <b>SHOP ACC & NHÀ CUNG CẤP</b>\n\n"
            "Chọn nhóm thao tác — các lệnh gõ tay vẫn dùng bình thường.")


def _back_kb(cat):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Quay lại nhóm", callback_data=f"shopm:back_{cat}")],
        [InlineKeyboardButton(text="🏠 Menu shop acc", callback_data="shopm:main")],
    ])


def _confirm_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Xác nhận", callback_data="shopmc:yes"),
        InlineKeyboardButton(text="❌ Huỷ", callback_data="shopmc:no"),
    ]])


GROUPS = {
    "kho": ("📦 <b>KHO & LOẠI ACC</b>", [
        ("add_cat", "➕ Thêm loại acc"),
        ("import_file", "📥 Nhập kho (gửi file)"),
        ("import_sheet", "📊 Nhập kho từ Sheet"),
        ("set_sheet", "🔗 Cài đặt Sheet"),
        ("view_stock", "📦 Xem tồn kho"),
        ("export_stock", "📤 Xuất kho (.xlsx)"),
        ("set_cover", "🖼️ Đặt ảnh bìa"),
        ("del_cover", "🗑️ Gỡ ảnh bìa"),
        ("hide_cat", "🙈 Ẩn loại khỏi shop"),
        ("show_cat", "👁️ Hiện lại loại đã ẩn"),
        ("clear_stock", "🧹 Xóa kho (chưa bán)"),
        ("del_cat", "🗑️ Xóa hẳn loại acc"),
        ("recheck", "🔄 Re-check LIVE kho"),
    ]),
    "price": ("💲 <b>GIÁ & KHUYẾN MÃI</b>", [
        ("set_price", "💲 Đổi giá bán"),
        ("set_warranty", "🛡️ Đổi bảo hành"),
        ("credit_bonus", "🎁 Combo tặng credits"),
        ("loyalty_gift", "🎁 Quà đổi điểm"),
        ("loyalty_random", "🎲 Điểm ngẫu nhiên"),
        ("happy_hour", "⚡ Giờ vàng"),
        ("mystery_price", "🎲 Giá hộp mù"),
        ("mystery_toggle", "🎲 Hộp mù: bật/tắt loại"),
        ("mail_app", "📧 Link app mail ảo"),
    ]),
    "orders": ("📋 <b>ĐƠN HÀNG & BẢO HÀNH</b>", [
        ("orders", "🧾 Đơn hàng gần đây"),
        ("warranty_list", "🛡️ BH chờ duyệt"),
        ("warranty_done", "✅ Duyệt BH xong"),
        ("acc_info", "🔍 Truy xuất acc"),
        ("profit", "📊 Lãi theo lô"),
    ]),
    "faq": ("❓ <b>FAQ TỰ ĐỘNG</b>", [
        ("faq_list", "❓ Xem FAQ"),
        ("faq_add", "➕ Thêm câu hỏi"),
        ("faq_del", "➖ Xóa câu hỏi"),
    ]),
    "sup": ("🏭 <b>NHÀ CUNG CẤP</b>", [
        ("sup_add", "➕ Thêm NCC"),
        ("sup_list", "📒 Sổ NCC"),
        ("sup_rate", "⭐ Đánh giá NCC"),
        ("sup_score", "📊 Chấm tỉ lệ sống"),
        ("sup_auto", "🤖 Nhập kho tự động 6h"),
    ]),
}


def _group_kb(cat, tg_id=None):
    _, items = GROUPS[cat]
    vis = []
    for key, label in items:
        fl = FLOWS.get(key) or {}
        if fl.get("super_only") and tg_id and not _perms.is_super(tg_id):
            continue
        vis.append((key, label))
    rows = []
    for i in range(0, len(vis), 2):
        row = [InlineKeyboardButton(text=vis[i][1],
                                    callback_data=f"shopm:go_{vis[i][0]}")]
        if i + 1 < len(vis):
            row.append(InlineKeyboardButton(text=vis[i + 1][1],
                                            callback_data=f"shopm:go_{vis[i + 1][0]}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🏠 Menu shop acc", callback_data="shopm:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _group_text(cat):
    title, _ = GROUPS[cat]
    return title + "\n\nChọn thao tác:"


# ------------------------------------------------- prompt động (kèm hd + giá trị hiện tại)

def _p_happy_hour():
    from . import db
    cur = db.get_setting("happy_hour_range", "20-22")
    pct = db.get_setting("happy_hour_pct", "10")
    hc = db.get_setting("happy_hour_cat", "0")
    dest = "toàn shop" if hc == "0" else f"loại #{hc}"
    return (f"⚡ <b>GIỜ VÀNG</b> (bước 1/3)\n\nHiện tại: khung <b>{html.escape(str(cur))}</b>, "
            f"giảm <b>{html.escape(str(pct))}%</b>, áp dụng: <b>{html.escape(dest)}</b>.\n\n"
            f"Gửi <b>ID loại</b> (<code>0</code> = toàn shop) hoặc gõ <code>off</code> để tắt.")


def _p_sup_auto():
    from . import db
    cur = db.get_setting("supplier_auto_url", "") or "chưa cài"
    return (f"🤖 <b>NHẬP KHO TỰ ĐỘNG 6H</b> (bước 1/3)\n\nHiện tại: <code>{html.escape(cur)}</code>\n\n"
            f"Gửi <b>URL file .txt</b> của NCC hoặc gõ <code>off</code> để tắt.")


def _p_set_sheet():
    from . import db
    sid = db.get_setting("sheet_import_id", "") or "chưa cài"
    tab = db.get_setting("sheet_import_tab", "") or "NhapKho"
    return (f"🔗 <b>CÀI ĐẶT SHEET NHẬP KHO</b> (bước 1/2)\n\n"
            f"Hiện tại: <code>{html.escape(sid)}</code> (tab {html.escape(tab)})\n\n"
            f"Gửi <b>link hoặc ID</b> Google Sheet.")


def _p_mail_app():
    from . import db
    cur = db.get_setting("mail_app_link", "") or "chưa cài"
    return (f"📧 <b>LINK APP MAIL ẢO</b>\n\nHiện tại: {html.escape(cur)}\n\n"
            f"Gửi <b>link mới</b> hoặc gõ <code>xoa</code> để gỡ.")


def _p_mystery_price():
    from . import db
    cur = db.get_setting("mystery_price", "0") or "0"
    return (f"🎲 <b>GIÁ HỘP MÙ</b>\n\nHiện tại: <b>{html.escape(str(cur))}</b>đ (0 = đang tắt).\n\n"
            f"Gửi <b>giá mới</b> (<code>0</code> để tắt hộp mù).")


def _p_loyalty():
    from . import db
    cid = db.get_setting("loyalty_redeem_cat", "0") or "0"
    pts = db.get_setting("loyalty_redeem_points", "10") or "10"
    cur = "chưa cài" if cid == "0" else f"loại #{cid}"
    return (f"🎁 <b>QUÀ ĐỔI ĐIỂM LOYALTY</b> (bước 1/2)\n\nHiện tại: <b>{html.escape(cur)}</b> — <b>{html.escape(str(pts))}</b> điểm.\n\n"
            f"Gửi <b>ID loại</b> làm quà (<code>0</code> để tắt).")


def _p_loyalty_random():
    from . import db
    try:
        lo = int(db.get_setting("loyalty_random_min", "1") or 1)
        hi = int(db.get_setting("loyalty_random_max", "5") or 5)
        cap = int(db.get_setting("loyalty_random_cap", "0") or 0)
        mo = int(db.get_setting("loyalty_random_min_order", "0") or 0)
        dm = int(db.get_setting("loyalty_random_daily_max", "0") or 0)
    except Exception:
        lo, hi, cap, mo, dm = 1, 5, 0, 0, 0
    cur = "đang tắt" if cap <= 0 else f"{lo}–{hi} điểm/lần, tối đa {cap} điểm/user"
    if cap > 0:
        if mo > 0:
            cur += f", đơn ≥ {mo}đ"
        if dm > 0:
            cur += f", {dm} lượt/ngày"
    return (f"🎲 <b>ĐIỂM NGẪU NHIÊN SAU MUA</b> (bước 1/5)\n\nHiện tại: <b>{html.escape(cur)}</b>.\n\n"
            f"Gửi <b>điểm thấp nhất</b> mỗi lần random (VD: 1).")


# ------------------------------------------------------------------ định nghĩa flow
# kind bước nhập: text | int | price | opt_text | opt_int | opt_price
# "run": chạy ngay không cần nhập | "short": {giá_trị: lệnh_chạy_ngay} ở bước 1
# "confirm": hiện màn hình xác nhận trước khi chạy | "needs_state": handler cần FSMContext

FLOWS = {
    # ── Kho & loại acc ──
    "add_cat": {
        "cat": "kho", "handler": "on_themloai",
        "steps": [
            ("➕ <b>THÊM LOẠI ACC</b> (bước 1/4)\n\nGửi <b>tên loại</b> (VD: Via Việt).", "text"),
            ("➕ <b>THÊM LOẠI ACC</b> (bước 2/4)\n\nGửi <b>giá bán</b> (VD: 25000).", "price"),
            ("➕ <b>THÊM LOẠI ACC</b> (bước 3/4)\n\nGửi <b>bảo hành</b>: <code>30p</code> | <code>24h</code> | <code>2 ngày</code> | <code>1 tuần</code>.", "text"),
            ("➕ <b>THÊM LOẠI ACC</b> (bước 4/4)\n\nGửi <b>mô tả</b> (gõ <code>-</code> để bỏ qua).", "opt_text"),
        ],
        "build": lambda v: f"/themloai {v[0]} | {v[1]} | {v[2]} | {'' if v[3] == '-' else v[3]}",
    },
    "import_file": {
        "cat": "kho", "handler": "on_themacc", "needs_state": True,
        "steps": [
            ("📥 <b>NHẬP KHO</b> (bước 1/3)\n\nGửi <b>ID loại acc</b> (xem ở /kho).", "int"),
            ("📥 <b>NHẬP KHO</b> (bước 2/3)\n\nGửi <b>ID NCC</b> (trống = không chọn).", "opt_int"),
            ("📥 <b>NHẬP KHO</b> (bước 3/3)\n\nGửi <b>giá vốn</b>/acc (trống = 0).", "opt_price"),
        ],
        "build": lambda v: "/themacc " + str(v[0]) + (f" {v[1]}" if v[1] else "") + (f" {v[2]}" if v[2] else ""),
    },
    "import_sheet": {
        "cat": "kho", "handler": "on_nhapkhosheet",
        "steps": [
            ("📊 <b>NHẬP KHO TỪ SHEET</b> (bước 1/3)\n\nGửi <b>ID loại acc</b> (xem ở /kho).", "int"),
            ("📊 <b>NHẬP KHO TỪ SHEET</b> (bước 2/3)\n\nGửi <b>ID NCC</b> (trống = không chọn).", "opt_int"),
            ("📊 <b>NHẬP KHO TỪ SHEET</b> (bước 3/3)\n\nGửi <b>giá vốn</b>/acc (trống = 0).", "opt_price"),
        ],
        "build": lambda v: "/nhapkhosheet " + str(v[0]) + (f" {v[1]}" if v[1] else "") + (f" {v[2]}" if v[2] else ""),
    },
    "set_sheet": {
        "cat": "kho", "handler": "on_setsheet",
        "steps": [
            (_p_set_sheet, "text"),
            ("🔗 <b>CÀI ĐẶT SHEET</b> (bước 2/2)\n\nGửi <b>tên tab</b> (trống = NhapKho).", "opt_text"),
        ],
        "build": lambda v: f"/setsheet {v[0]}" + (f" {v[1]}" if v[1] else ""),
    },
    "view_stock": {"cat": "kho", "handler": "on_kho", "run": "/kho"},
    "export_stock": {
        "cat": "kho", "handler": "on_xuatkho",
        "steps": [("📤 <b>XUẤT KHO</b>\n\nGửi <b>ID loại</b> (trống = xuất toàn bộ).", "opt_int")],
        "build": lambda v: "/xuatkho" + (f" {v[0]}" if v[0] is not None else ""),
    },
    "set_cover": {
        "cat": "kho", "handler": "on_anhbia", "needs_state": True,
        "steps": [("🖼️ <b>ĐẶT ẢNH BÌA</b>\n\nGửi <b>ID loại acc</b>, rồi gửi 1 ảnh ở tin tiếp theo.", "int")],
        "build": lambda v: f"/anhbia {v[0]}",
    },
    "del_cover": {
        "cat": "kho", "handler": "on_anhbia",
        "steps": [("🗑️ <b>GỠ ẢNH BÌA</b>\n\nGửi <b>ID loại acc</b> cần gỡ ảnh.", "int")],
        "build": lambda v: f"/anhbia {v[0]} xoa",
    },
    "hide_cat": {
        "cat": "kho", "handler": "on_xoaloai",
        "steps": [("🙈 <b>ẨN LOẠI KHỎI SHOP</b>\n\nGửi <b>ID loại</b> cần ẩn (tên vẫn giữ trong DB).", "int")],
        "build": lambda v: f"/xoaloai {v[0]}",
    },
    "show_cat": {
        "cat": "kho", "handler": "on_hienloai",
        "steps": [("👁️ <b>HIỆN LẠI LOẠI ĐÃ ẨN</b>\n\nGửi <b>ID loại</b> cần hiện lại.", "int")],
        "build": lambda v: f"/hienloai {v[0]}",
    },
    "clear_stock": {
        "cat": "kho", "handler": "on_xoakho", "confirm": True,
        "steps": [("🧹 <b>XÓA KHO</b>\n\nGửi <b>ID loại</b> cần xóa toàn bộ acc CHƯA BÁN.", "int")],
        "summary": lambda v: f"🧹 <b>XÓA KHO</b>\n\nXóa toàn bộ acc <b>CHƯA BÁN</b> của loại <b>#{v[0]}</b>?\n<i>Không khôi phục được.</i>",
        "build": lambda v: f"/xoakho {v[0]} yes",
    },
    "del_cat": {
        "cat": "kho", "handler": "on_xoahan", "confirm": True,
        "steps": [("🗑️ <b>XÓA HẲN LOẠI ACC</b>\n\nGửi <b>ID loại</b> cần xóa hẳn.", "int")],
        "summary": lambda v: f"🗑️ <b>XÓA HẲN LOẠI ACC</b>\n\nXóa hẳn loại <b>#{v[0]}</b> khỏi DB?\n<i>Không khôi phục được. Nếu còn acc đã bán, loại sẽ đổi tên + ẩn để giữ lịch sử.</i>",
        "build": lambda v: f"/xoahan {v[0]} yes",
    },
    "recheck": {"cat": "kho", "handler": "on_recheck", "run": "/recheck"},

    # ── Giá & khuyến mãi ──
    "set_price": {
        "cat": "price", "handler": "on_gia",
        "steps": [
            ("💲 <b>ĐỔI GIÁ BÁN</b> (bước 1/2)\n\nGửi <b>ID loại acc</b>.", "int"),
            ("💲 <b>ĐỔI GIÁ BÁN</b> (bước 2/2)\n\nGửi <b>giá mới</b> (VD: 30000).", "price"),
        ],
        "build": lambda v: f"/gia {v[0]} {v[1]}",
    },
    "set_warranty": {
        "cat": "price", "handler": "on_suabh",
        "steps": [
            ("🛡️ <b>ĐỔI BẢO HÀNH</b> (bước 1/2)\n\nGửi <b>ID loại acc</b>.", "int"),
            ("🛡️ <b>ĐỔI BẢO HÀNH</b> (bước 2/2)\n\nGửi <b>bảo hành mới</b>: <code>30p</code> | <code>24h</code> | <code>2 ngày</code> | <code>1 tuần</code>.", "text"),
        ],
        "build": lambda v: f"/suabh {v[0]} {v[1]}",
    },
    "credit_bonus": {
        "cat": "price", "handler": "on_creditbonus",
        "steps": [
            ("🎁 <b>COMBO TẶNG CREDITS</b> (bước 1/2)\n\nGửi <b>ID loại acc</b>.", "int"),
            ("🎁 <b>COMBO TẶNG CREDITS</b> (bước 2/2)\n\nGửi <b>số credits tặng</b> khi mua acc loại này.", "int"),
        ],
        "build": lambda v: f"/creditbonus {v[0]} {v[1]}",
    },
    "loyalty_gift": {
        "cat": "price", "handler": "on_quadoi",
        "steps": [
            (_p_loyalty, "int"),
            ("🎁 <b>QUÀ ĐỔI ĐIỂM LOYALTY</b> (bước 2/2)\n\nGửi <b>số điểm</b> cần để đổi quà (<code>0</code> = giữ nguyên).", "opt_int"),
        ],
        "build": lambda v: f"/quadoi {v[0]}" + (f" {v[1]}" if v[1] else ""),
    },
    "loyalty_random": {
        "cat": "price", "handler": "on_loyaltyrandom",
        "steps": [
            (_p_loyalty_random, "int"),
            ("🎲 <b>ĐIỂM NGẪU NHIÊN SAU MUA</b> (bước 2/5)\n\nGửi <b>điểm cao nhất</b> mỗi lần random (VD: 5).", "int"),
            ("🎲 <b>ĐIỂM NGẪU NHIÊN SAU MUA</b> (bước 3/5)\n\nGửi <b>tổng điểm tối đa</b> mỗi user được nhận (<code>0</code> = tắt tính năng).", "int"),
            ("🎲 <b>ĐIỂM NGẪU NHIÊN SAU MUA</b> (bước 4/5)\n\nGửi <b>giá trị đơn tối thiểu</b> (VNĐ) để được random — chống cày điểm bằng acc rẻ (<code>0</code> = không giới hạn).", "int"),
            ("🎲 <b>ĐIỂM NGẪU NHIÊN SAU MUA</b> (bước 5/5)\n\nGửi <b>số lượt random tối đa mỗi ngày</b> cho mỗi user (<code>0</code> = không giới hạn).", "int"),
        ],
        "build": lambda v: f"/loyaltyrandom {v[0]} {v[1]} {v[2]} {v[3]} {v[4]}",
    },
    "happy_hour": {
        "cat": "price", "handler": "on_giovang",
        "short": {"off": "/giovang off"},
        "steps": [
            (_p_happy_hour, "text"),
            ("⚡ <b>GIỜ VÀNG</b> (bước 2/3)\n\nGửi <b>khung giờ</b> (VD: 20-22, trống = giữ nguyên).", "opt_text"),
            ("⚡ <b>GIỜ VÀNG</b> (bước 3/3)\n\nGửi <b>% giảm</b> (trống = giữ nguyên).", "opt_text"),
        ],
        "build": lambda v: f"/giovang {v[0]}" + (f" {v[1]}" if v[1] else "") + (f" {v[2]}" if v[2] else ""),
    },
    "mystery_price": {
        "cat": "price", "handler": "on_hopmugia",
        "steps": [(_p_mystery_price, "price")],
        "build": lambda v: f"/hopmugia {v[0]}",
    },
    "mystery_toggle": {
        "cat": "price", "handler": "on_hopmu",
        "steps": [("🎲 <b>HỘP MÙ: BẬT/TẮT LOẠI</b>\n\nGửi <b>ID loại acc</b> (bật ↔ tắt).", "int")],
        "build": lambda v: f"/hopmu {v[0]}",
    },
    "mail_app": {
        "cat": "price", "handler": "on_setmailapp",
        "steps": [(_p_mail_app, "text")],
        "build": lambda v: f"/setmailapp {v[0]}",
    },

    # ── Đơn hàng & bảo hành ──
    "orders": {"cat": "orders", "handler": "on_donhang", "run": "/donhang"},
    "warranty_list": {"cat": "orders", "handler": "on_bhdon", "run": "/bhdon"},
    "warranty_done": {
        "cat": "orders", "handler": "on_bhdone",
        "steps": [("✅ <b>DUYỆT BH XONG</b>\n\nGửi <b>ID khiếu nại</b> (xem ở mục BH chờ duyệt).", "int")],
        "build": lambda v: f"/bhdone {v[0]}",
    },
    "acc_info": {
        "cat": "orders", "handler": "on_accinfo",
        "steps": [("🔍 <b>TRUY XUẤT ACC</b>\n\nGửi <b>UID</b> cần tra hành trình.", "text")],
        "build": lambda v: f"/accinfo {v[0]}",
    },
    "profit": {
        "cat": "orders", "handler": "on_lo", "super_only": True,
        "steps": [("📊 <b>LÃI THEO LÔ</b>\n\nGửi <b>ID loại acc</b>.", "int")],
        "build": lambda v: f"/lo {v[0]}",
    },

    # ── FAQ ──
    "faq_list": {"cat": "faq", "handler": "on_faq", "run": "/faq"},
    "faq_add": {
        "cat": "faq", "handler": "on_themcauhoi",
        "steps": [
            ("➕ <b>THÊM CÂU HỎI</b> (bước 1/2)\n\nGửi <b>từ khóa</b> (nhiều từ khóa cách nhau bằng dấu phẩy).", "text"),
            ("➕ <b>THÊM CÂU HỎI</b> (bước 2/2)\n\nGửi <b>câu trả lời</b> sẽ gửi cho khách.", "text"),
        ],
        "build": lambda v: f"/themcauhoi {v[0]} | {v[1]}",
    },
    "faq_del": {
        "cat": "faq", "handler": "on_xoacauhoi",
        "steps": [("➖ <b>XÓA CÂU HỎI</b>\n\nGửi <b>số thứ tự</b> câu tự thêm (xem ở mục Xem FAQ).", "int")],
        "build": lambda v: f"/xoacauhoi {v[0]}",
    },

    # ── Nhà cung cấp ──
    "sup_add": {
        "cat": "sup", "handler": "on_themncc",
        "steps": [
            ("➕ <b>THÊM NCC</b> (bước 1/2)\n\nGửi <b>tên NCC</b>.", "text"),
            ("➕ <b>THÊM NCC</b> (bước 2/2)\n\nGửi <b>liên hệ</b> (trống = bỏ qua).", "opt_text"),
        ],
        "build": lambda v: f"/themncc {v[0]}" + (f" | {v[1]}" if v[1] else ""),
    },
    "sup_list": {"cat": "sup", "handler": "on_ncc", "run": "/ncc"},
    "sup_rate": {
        "cat": "sup", "handler": "on_danhgiancc",
        "steps": [
            ("⭐ <b>ĐÁNH GIÁ NCC</b> (bước 1/2)\n\nGửi <b>ID NCC</b> (xem ở Sổ NCC).", "int"),
            ("⭐ <b>ĐÁNH GIÁ NCC</b> (bước 2/2)\n\nGửi <b>số sao</b> (1-5).", "int"),
        ],
        "build": lambda v: f"/danhgiancc {v[0]} {v[1]}",
    },
    "sup_score": {
        "cat": "sup", "handler": "on_chamdiem",
        "steps": [
            ("📊 <b>CHẤM TỈ LỆ SỐNG</b> (bước 1/2)\n\nGửi <b>ID NCC</b>.", "int"),
            ("📊 <b>CHẤM TỈ LỆ SỐNG</b> (bước 2/2)\n\nGửi <b>số ngày</b> quét (trống = 7).", "opt_int"),
        ],
        "build": lambda v: f"/chamdiem {v[0]}" + (f" {v[1]}" if v[1] is not None else ""),
    },
    "sup_auto": {
        "cat": "sup", "handler": "on_nccauto",
        "short": {"off": "/nccauto off"},
        "steps": [
            (_p_sup_auto, "text"),
            ("🤖 <b>NHẬP KHO TỰ ĐỘNG</b> (bước 2/3)\n\nGửi <b>ID loại acc</b> sẽ nhập vào.", "int"),
            ("🤖 <b>NHẬP KHO TỰ ĐỘNG</b> (bước 3/3)\n\nGửi <b>ID NCC</b> (trống = không chọn).", "opt_int"),
        ],
        "build": lambda v: f"/nccauto {v[0]} {v[1]}" + (f" {v[2]}" if v[2] else ""),
    },
}


# ------------------------------------------------------------------ chạy handler

def _parse_step(kind, raw):
    t = (raw or "").strip()
    if kind == "text":
        return (True, t, "") if t else (False, None, "⚠️ Không được để trống, gửi lại nhé.")
    if kind == "int":
        try:
            return True, int(t), ""
        except Exception:
            return False, None, "⚠️ Phải là số, gửi lại nhé."
    if kind == "price":
        try:
            return True, int(t.replace(".", "").replace(",", "").replace(" ", "")), ""
        except Exception:
            return False, None, "⚠️ Giá phải là số, gửi lại nhé."
    if kind == "opt_text":
        return True, t, ""
    if kind == "opt_int":
        if not t:
            return True, None, ""
        try:
            return True, int(t), ""
        except Exception:
            return False, None, "⚠️ Phải là số (trống = bỏ qua), gửi lại nhé."
    if kind == "opt_price":
        if not t:
            return True, None, ""
        try:
            return True, int(t.replace(".", "").replace(",", "").replace(" ", "")), ""
        except Exception:
            return False, None, "⚠️ Giá phải là số (trống = bỏ qua), gửi lại nhé."
    return False, None, "⚠️ Lỗi nội bộ."


def _render_prompt(step):
    p = step[0]
    return p() if callable(p) else p


async def _exec_handler(shim, state, flow):
    fn = _get_handler(flow["handler"])
    await state.clear()
    if flow.get("needs_state"):
        await fn(shim, state)
    else:
        await fn(shim)


async def _exec_via_cb(cb: CallbackQuery, state: FSMContext, flow, cmd_text: str):
    """Chạy lệnh từ nút bấm — kết quả hiện ngay tại tin menu."""
    try:
        db.admin_audit_add(cb.from_user.id, cb.from_user.full_name,
                           "menu_shopadm", (cmd_text or "")[:200])
    except Exception:
        pass
    shim = _ShopTextShim(cb.message, cmd_text, edit_target=cb.message)
    await _exec_handler(shim, state, flow)
    if await state.get_state() is None:
        try:
            await cb.message.edit_reply_markup(reply_markup=_back_kb(flow["cat"]))
        except Exception:
            pass
    await cb.answer()


async def _exec_via_msg(msg: Message, state: FSMContext, flow, cmd_text: str):
    """Chạy lệnh sau khi admin nhập liệu xong."""
    try:
        db.admin_audit_add(msg.from_user.id, msg.from_user.full_name,
                           "menu_shopadm", (cmd_text or "")[:200])
    except Exception:
        pass
    shim = _ShopTextShim(msg, cmd_text)
    await _exec_handler(shim, state, flow)


# ------------------------------------------------------------------ đăng ký

def register_shop_menu(target_router):
    """Gắn toàn bộ menu nút /shopadm (callback + nhập liệu FSM) vào router cho trước."""

    @target_router.message(Command("shopadm"))
    async def _on_shopadm(msg: Message, state: FSMContext):
        if not _is_admin_sync(msg.from_user.id):
            return
        await state.clear()
        await msg.answer(_main_text(), parse_mode="HTML",
                         reply_markup=_main_kb(msg.from_user.id))

    @target_router.callback_query(F.data.startswith("shopm:"))
    async def _on_shopm_cb(cb: CallbackQuery, state: FSMContext):
        if not _is_admin_sync(cb.from_user.id):
            await cb.answer("🚫 Không có quyền.", show_alert=True)
            return
        action = (cb.data or "")[6:]

        if action == "noop":
            await cb.answer()
            return

        if action == "main":
            await state.clear()
            await cb.message.edit_text(_main_text(), parse_mode="HTML",
                                       reply_markup=_main_kb(cb.from_user.id))
            await cb.answer()
            return

        if action.startswith("cat_") or action.startswith("back_"):
            cat = action[4:] if action.startswith("cat_") else action[5:]
            if cat not in GROUPS or not _perms.has_perm(cb.from_user.id, cat):
                if cat in GROUPS:
                    await cb.answer("🚫 Bạn không có quyền nhóm này.", show_alert=True)
                await state.clear()
                await cb.message.edit_text(_main_text(), parse_mode="HTML",
                                           reply_markup=_main_kb(cb.from_user.id))
            else:
                await cb.message.edit_text(_group_text(cat), parse_mode="HTML",
                                            reply_markup=_group_kb(cat, cb.from_user.id))
            await cb.answer()
            return

        if action.startswith("go_"):
            flow = FLOWS.get(action[3:])
            if not flow:
                await cb.answer()
                return
            if not _perms.has_perm(cb.from_user.id, flow["cat"]):
                await cb.answer("🚫 Bạn không có quyền thao tác này.", show_alert=True)
                return
            if flow.get("super_only") and not _perms.is_super(cb.from_user.id):
                await cb.answer("🚫 Chỉ chủ shop mới xem được mục này.", show_alert=True)
                return
            if "run" in flow:
                await _exec_via_cb(cb, state, flow, flow["run"])
                return
            await state.update_data(shopm_flow=action[3:], shopm_step=0,
                                    shopm_vals=[], shopm_cat=flow["cat"])
            await state.set_state(ShopMenuState.input)
            prompt = _render_prompt(flow["steps"][0])
            await cb.message.edit_text(prompt + "\n\nGõ /huy để huỷ.", parse_mode="HTML",
                                       reply_markup=_back_kb(flow["cat"]))
            await cb.answer()
            return

        await cb.answer()

    @target_router.message(ShopMenuState.input)
    async def _on_shopm_input(msg: Message, state: FSMContext):
        if not _is_admin_sync(msg.from_user.id):
            await state.clear()
            return
        if (msg.text or "").strip().lower() in ("/huy", "/cancel"):
            await state.clear()
            await msg.answer("Đã huỷ thao tác.")
            return
        data = await state.get_data()
        flow = FLOWS.get(data.get("shopm_flow") or "")
        if not flow:
            await state.clear()
            return
        if not _perms.has_perm(msg.from_user.id, flow["cat"]):
            await state.clear()
            await msg.answer("🚫 Bạn không có quyền thao tác này.")
            return
        step_idx = int(data.get("shopm_step") or 0)
        vals = data.get("shopm_vals") or []
        if step_idx >= len(flow["steps"]) or not msg.text:
            if not msg.text:
                await msg.answer("⚠️ Vui lòng gửi dạng chữ nhé. Gõ /huy để huỷ.")
                return
            await state.clear()
            return
        ok, val, err = _parse_step(flow["steps"][step_idx][1], msg.text)
        if not ok:
            await msg.answer(err)
            return
        vals = vals + [val]
        short = flow.get("short") or {}
        if step_idx == 0 and str(val).strip().lower() in short:
            await _exec_via_msg(msg, state, flow, short[str(val).strip().lower()])
            return
        if step_idx + 1 < len(flow["steps"]):
            await state.update_data(shopm_step=step_idx + 1, shopm_vals=vals)
            prompt = _render_prompt(flow["steps"][step_idx + 1])
            await msg.answer(prompt + "\n\nGõ /huy để huỷ.", parse_mode="HTML")
            return
        cmd_text = flow["build"](vals)
        if flow.get("confirm"):
            await state.update_data(shopm_cmd=cmd_text)
            await state.set_state(ShopMenuState.confirm)
            await msg.answer(flow["summary"](vals) + "\n\nXác nhận thực hiện?",
                             parse_mode="HTML", reply_markup=_confirm_kb())
            return
        await _exec_via_msg(msg, state, flow, cmd_text)

    @target_router.callback_query(F.data.startswith("shopmc:"))
    async def _on_shopm_confirm(cb: CallbackQuery, state: FSMContext):
        if not _is_admin_sync(cb.from_user.id):
            await cb.answer("🚫 Không có quyền.", show_alert=True)
            return
        data = await state.get_data()
        flow = FLOWS.get(data.get("shopm_flow") or "")
        cmd = data.get("shopm_cmd")
        if cb.data != "shopmc:yes" or not flow or not cmd:
            await state.clear()
            try:
                await cb.message.edit_text(
                    "Đã huỷ thao tác.",
                    reply_markup=_back_kb(data.get("shopm_cat") or "kho"))
            except Exception:
                pass
            await cb.answer()
            return
        if not _perms.has_perm(cb.from_user.id, flow["cat"]):
            await state.clear()
            await cb.answer("🚫 Bạn không có quyền thao tác này.", show_alert=True)
            return
        await _exec_via_cb(cb, state, flow, cmd)
