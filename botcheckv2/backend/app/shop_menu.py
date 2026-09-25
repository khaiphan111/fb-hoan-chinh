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
    autoimp = State()  # đang chờ nhập cấu hình nhập kho tự động (chu kỳ/NCC/vốn)


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
        ("add_stall", "🏪 Thêm gian hàng mới"),
        ("import_file", "📥 Nhập kho (gửi file)"),
        ("import_sheet", "📊 Nhập kho từ Sheet"),
        ("update_file", "🔄 Cập nhật kho (gửi file)"),
        ("update_sheet", "🔄 Cập nhật từ Sheet"),
        ("edit_acc", "✏️ Sửa thông tin 1 acc"),
        ("auto_import", "⏰ Nhập kho tự động"),
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
        ("loyalty_gift_money", "💵 Quà đổi điểm (tiền)"),
        ("loyalty_random", "🎲 Điểm ngẫu nhiên"),
        ("happy_hour", "⚡ Giờ vàng"),
        ("mystery_price", "🎲 Giá hộp mù"),
        ("mystery_toggle", "🎲 Hộp mù: bật/tắt loại"),
        ("mystery_weight", "🎲 Hộp mù: tỷ lệ trúng"),
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


def _kho_summary():
    """Tóm tắt tồn kho cho menu KHO & LOẠI ACC: tổng + từng loại kèm ID
    (tiện khi admin không nhớ ID kho)."""
    from . import db
    try:
        cats = db.acc_category_list(active_only=False, include_hidden=True)
    except Exception:
        return ""
    if not cats:
        return "\n<i>Chưa có loại acc nào.</i>"
    lines = []
    total = 0
    last_stall = None
    for c in cats:
        try:
            keys = c.keys()
            cid = int(c["id"])
            name = c["name"] if "name" in keys else f"#{cid}"
            hidden = " 🙈" if ("hidden" in keys and c["hidden"]) else ""
            stall = (c["stall"] if "stall" in keys and c["stall"] else "Acc Facebook")
            n = db.acc_stock_count(cid)
            n_sold = db.acc_stock_sold_count(cid)
        except Exception:
            continue
        total += n
        if stall != last_stall:
            lines.append(f"🏪 <b>{stall}</b>")
            last_stall = stall
        sold_txt = f" • 🛒{n_sold} đã bán" if n_sold else ""
        lines.append(f"• <b>#{cid}</b> {name}{hidden}: {n} acc{sold_txt}")
    head = (f"\n━━━━━━━━━━━━━━━\n"
            f"📊 Tổng: <b>{len(lines)} loại • {total} acc tồn</b>")
    return head + "\n" + "\n".join(lines)


def _group_text(cat):
    title, _ = GROUPS[cat]
    extra = _kho_summary() if cat == "kho" else ""
    return title + extra + "\n\nChọn thao tác:"


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
    mode = db.get_setting("loyalty_redeem_mode", "acc") or "acc"
    pts = db.get_setting("loyalty_redeem_points", "10") or "10"
    if mode == "money":
        amt = db.get_setting("loyalty_redeem_amount", "0") or "0"
        try:
            wallet = db.get_setting("loyalty_redeem_wallet", "main")
            wlbl = db.wallet_label(wallet)
        except Exception:
            wlbl = "ví chính"
            wallet = "main"
        try:
            amt_txt = db.wallet_amount_text(wallet, int(amt))
        except Exception:
            amt_txt = str(amt)
        cur = f"{amt_txt} vào {wlbl}"
    else:
        scope = db.get_setting("loyalty_redeem_scope", "cat") or "cat"
        if scope == "stall":
            cur = "acc bất kỳ (gian hàng Acc Facebook)"
        else:
            cid = db.get_setting("loyalty_redeem_cat", "0") or "0"
            cur = "chưa cài" if cid == "0" else f"loại #{cid}"
    return (f"🎁 <b>QUÀ ĐỔI ĐIỂM LOYALTY</b> (bước 1/2)\n\nHiện tại: <b>{html.escape(cur)}</b> — <b>{html.escape(str(pts))}</b> điểm.\n\n"
            f"Gửi <b>ID loại acc</b> làm quà (<code>0</code> để tắt), hoặc gửi <b>stall</b> để khách được chọn acc bất kỳ trong gian hàng Acc Facebook. Muốn đổi điểm lấy <b>tiền</b> thì dùng nút 💵 bên dưới.")


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
#   | pick_cat (chọn loại acc bằng nút; vẫn gõ tay được)
#   | pick_cat_all (như pick_cat + hiện cả loại đang ẩn, đánh dấu 🙈)
#   | pick_cat_opt (như pick_cat + nút "📦 Toàn bộ" = None)
#   | pick_stall (chọn gian hàng bằng nút; vẫn gõ tay tên được)
#   | pick_supplier / pick_supplier_opt (chọn NCC bằng nút; _opt = thêm "không chọn")
# "run": chạy ngay không cần nhập | "short": {giá_trị: lệnh_chạy_ngay} ở bước 1
# "confirm": hiện màn hình xác nhận trước khi chạy | "needs_state": handler cần FSMContext

FLOWS = {
    # ── Kho & loại acc ──
    "add_cat": {
        "cat": "kho", "handler": "on_themloai",
        "steps": [
            ("➕ <b>THÊM LOẠI ACC</b> (bước 1/5)\n\nChọn <b>gian hàng</b> cho loại acc mới (hoặc gõ tên gian hàng).", "pick_stall"),
            ("➕ <b>THÊM LOẠI ACC</b> (bước 2/5)\n\nGửi <b>tên loại</b> (VD: Via Việt).", "text"),
            ("➕ <b>THÊM LOẠI ACC</b> (bước 3/5)\n\nGửi <b>giá bán</b> (VD: 25000).", "price"),
            ("➕ <b>THÊM LOẠI ACC</b> (bước 4/5)\n\nGửi <b>bảo hành</b>: <code>30p</code> | <code>24h</code> | <code>2 ngày</code> | <code>1 tuần</code>.", "text"),
            ("➕ <b>THÊM LOẠI ACC</b> (bước 5/5)\n\nGửi <b>mô tả</b> (gõ <code>-</code> để bỏ qua).", "opt_text"),
        ],
        "build": lambda v: f"/themloai {v[1]} | {v[2]} | {v[3]} | {'' if v[4] == '-' else v[4]} | {v[0]}",
    },
    "add_stall": {
        "cat": "kho", "handler": "on_themstall",
        "steps": [
            ("🏪 <b>THÊM GIAN HÀNG</b> (bước 1/5)\n\nGửi <b>tên gian hàng</b> (VD: Gmail).", "text"),
            ("🏪 <b>THÊM GIAN HÀNG</b> (bước 2/5)\n\nGửi <b>tên loại hàng đầu tiên</b> trong gian hàng (VD: Gmail Edu).", "text"),
            ("🏪 <b>THÊM GIAN HÀNG</b> (bước 3/5)\n\nGửi <b>giá bán</b> (VD: 15000).", "price"),
            ("🏪 <b>THÊM GIAN HÀNG</b> (bước 4/5)\n\nGửi <b>bảo hành</b>: <code>30p</code> | <code>24h</code> | <code>2 ngày</code> | <code>1 tuần</code>.", "text"),
            ("🏪 <b>THÊM GIAN HÀNG</b> (bước 5/5)\n\nGửi <b>mô tả</b> (gõ <code>-</code> để bỏ qua).", "opt_text"),
        ],
        "build": lambda v: f"/themstall {v[0]} | {v[1]} | {v[2]} | {v[3]} | {'' if v[4] == '-' else v[4]}",
    },
    "import_file": {
        "cat": "kho", "handler": "on_themacc", "needs_state": True,
        "steps": [
            ("📥 <b>NHẬP KHO</b> (bước 1/3)\n\nChọn <b>loại acc</b> bên dưới (hoặc gõ ID).", "pick_cat"),
            ("📥 <b>NHẬP KHO</b> (bước 2/3)\n\nChọn <b>NCC</b> bên dưới (hoặc gõ ID, trống = không chọn).", "pick_supplier_opt"),
            ("📥 <b>NHẬP KHO</b> (bước 3/3)\n\nGửi <b>giá vốn</b>/acc (trống = 0).", "opt_price"),
        ],
        "build": lambda v: "/themacc " + str(v[0]) + (f" {v[1]}" if v[1] else "") + (f" {v[2]}" if v[2] else ""),
    },
    "import_sheet": {
        "cat": "kho", "handler": "on_nhapkhosheet",
        "steps": [
            ("📊 <b>NHẬP KHO TỪ SHEET</b> (bước 1/3)\n\nChọn <b>loại acc</b> bên dưới (hoặc gõ ID).", "pick_cat"),
            ("📊 <b>NHẬP KHO TỪ SHEET</b> (bước 2/3)\n\nChọn <b>NCC</b> bên dưới (hoặc gõ ID, trống = không chọn).", "pick_supplier_opt"),
            ("📊 <b>NHẬP KHO TỪ SHEET</b> (bước 3/3)\n\nGửi <b>giá vốn</b>/acc (trống = 0).", "opt_price"),
        ],
        "build": lambda v: "/nhapkhosheet " + str(v[0]) + (f" {v[1]}" if v[1] else "") + (f" {v[2]}" if v[2] else ""),
    },
    "update_file": {
        "cat": "kho", "handler": "on_capnhatacc", "needs_state": True,
        "steps": [("🔄 <b>CẬP NHẬT KHO TỪ FILE</b>\n\nChọn <b>loại acc</b> bên dưới (hoặc gõ ID).", "pick_cat")],
        "build": lambda v: f"/capnhatacc {v[0]}",
    },
    "update_sheet": {
        "cat": "kho", "handler": "on_capnhatsheet",
        "steps": [("🔄 <b>CẬP NHẬT TỪ SHEET</b>\n\nChọn <b>loại acc</b> bên dưới (hoặc gõ ID).", "pick_cat")],
        "build": lambda v: f"/capnhatsheet {v[0]}",
    },
    "edit_acc": {"cat": "kho", "handler": "on_suaacc", "run": "/suaacc"},
    "set_sheet": {
        "cat": "kho", "handler": "on_setsheet",
        "steps": [
            (_p_set_sheet, "text"),
            ("🔗 <b>CÀI ĐẶT SHEET</b> (bước 2/2)\n\nGửi <b>tên tab</b> (trống = NhapKho).", "opt_text"),
        ],
        "build": lambda v: f"/setsheet {v[0]}" + (f" {v[1]}" if v[1] else ""),
    },
    # Màn hình cấu hình riêng (không theo flow tuyến tính): nhập kho tự động từng sạp
    "auto_import": {"cat": "kho", "custom": "autoimp"},
    "view_stock": {"cat": "kho", "handler": "on_kho", "run": "/kho"},
    "export_stock": {
        "cat": "kho", "handler": "on_xuatkho",
        "steps": [("📤 <b>XUẤT KHO</b>\n\nChọn <b>loại acc</b> bên dưới (hoặc gõ ID, trống = toàn bộ).", "pick_cat_opt")],
        "build": lambda v: "/xuatkho" + (f" {v[0]}" if v[0] is not None else ""),
    },
    "set_cover": {
        "cat": "kho", "handler": "on_anhbia", "needs_state": True,
        "steps": [("🖼️ <b>ĐẶT ẢNH BÌA</b>\n\nChọn <b>loại acc</b> bên dưới (hoặc gõ ID), rồi gửi 1 ảnh ở tin tiếp theo.", "pick_cat")],
        "build": lambda v: f"/anhbia {v[0]}",
    },
    "del_cover": {
        "cat": "kho", "handler": "on_anhbia",
        "steps": [("🗑️ <b>GỠ ẢNH BÌA</b>\n\nChọn <b>loại acc</b> cần gỡ ảnh bên dưới (hoặc gõ ID).", "pick_cat")],
        "build": lambda v: f"/anhbia {v[0]} xoa",
    },
    "hide_cat": {
        "cat": "kho", "handler": "on_xoaloai",
        "steps": [("🙈 <b>ẨN LOẠI KHỎI SHOP</b>\n\nChọn <b>loại</b> cần ẩn bên dưới (hoặc gõ ID).", "pick_cat")],
        "build": lambda v: f"/xoaloai {v[0]}",
    },
    "show_cat": {
        "cat": "kho", "handler": "on_hienloai",
        "steps": [("👁️ <b>HIỆN LẠI LOẠI ĐÃ ẨN</b>\n\nChọn <b>loại</b> cần hiện lại bên dưới (🙈 = đang ẩn).", "pick_cat_all")],
        "build": lambda v: f"/hienloai {v[0]}",
    },
    "clear_stock": {
        "cat": "kho", "handler": "on_xoakho", "confirm": True,
        "steps": [("🧹 <b>XÓA KHO</b>\n\nChọn <b>loại</b> cần xóa toàn bộ acc CHƯA BÁN (hoặc gõ ID).", "pick_cat_all")],
        "summary": lambda v: f"🧹 <b>XÓA KHO</b>\n\nXóa toàn bộ acc <b>CHƯA BÁN</b> của loại <b>#{v[0]}</b>?\n<i>Không khôi phục được.</i>",
        "build": lambda v: f"/xoakho {v[0]} yes",
    },
    "del_cat": {
        "cat": "kho", "handler": "on_xoahan", "confirm": True,
        "steps": [("🗑️ <b>XÓA HẲN LOẠI ACC</b>\n\nChọn <b>loại</b> cần xóa hẳn bên dưới (hoặc gõ ID).", "pick_cat_all")],
        "summary": lambda v: f"🗑️ <b>XÓA HẲN LOẠI ACC</b>\n\nXóa hẳn loại <b>#{v[0]}</b> khỏi DB?\n<i>Không khôi phục được. Nếu còn acc đã bán, loại sẽ đổi tên + ẩn để giữ lịch sử.</i>",
        "build": lambda v: f"/xoahan {v[0]} yes",
    },
    "recheck": {"cat": "kho", "handler": "on_recheck", "run": "/recheck"},

    # ── Giá & khuyến mãi ──
    "set_price": {
        "cat": "price", "handler": "on_gia",
        "steps": [
            ("💲 <b>ĐỔI GIÁ BÁN</b> (bước 1/2)\n\nChọn <b>loại acc</b> bên dưới (hoặc gõ ID).", "pick_cat"),
            ("💲 <b>ĐỔI GIÁ BÁN</b> (bước 2/2)\n\nGửi <b>giá mới</b> (VD: 30000).", "price"),
        ],
        "build": lambda v: f"/gia {v[0]} {v[1]}",
    },
    "set_warranty": {
        "cat": "price", "handler": "on_suabh",
        "steps": [
            ("🛡️ <b>ĐỔI BẢO HÀNH</b> (bước 1/2)\n\nChọn <b>loại acc</b> bên dưới (hoặc gõ ID).", "pick_cat"),
            ("🛡️ <b>ĐỔI BẢO HÀNH</b> (bước 2/2)\n\nGửi <b>bảo hành mới</b>: <code>30p</code> | <code>24h</code> | <code>2 ngày</code> | <code>1 tuần</code>.", "text"),
        ],
        "build": lambda v: f"/suabh {v[0]} {v[1]}",
    },
    "credit_bonus": {
        "cat": "price", "handler": "on_creditbonus",
        "steps": [
            ("🎁 <b>COMBO TẶNG CREDITS</b> (bước 1/2)\n\nChọn <b>loại acc</b> bên dưới (hoặc gõ ID).", "pick_cat"),
            ("🎁 <b>COMBO TẶNG CREDITS</b> (bước 2/2)\n\nGửi <b>số credits tặng</b> khi mua acc loại này.", "int"),
        ],
        "build": lambda v: f"/creditbonus {v[0]} {v[1]}",
    },
    "loyalty_gift": {
        "cat": "price", "handler": "on_quadoi",
        "steps": [
            (_p_loyalty, "text"),
            ("🎁 <b>QUÀ ĐỔI ĐIỂM LOYALTY</b> (bước 2/2)\n\nGửi <b>số điểm</b> cần để đổi quà (<code>0</code> = giữ nguyên).", "opt_int"),
        ],
        "build": lambda v: f"/quadoi {v[0]}" + (f" {v[1]}" if v[1] else ""),
    },
    "loyalty_gift_money": {
        "cat": "price", "handler": "on_quadoi",
        "steps": [
            ("💵 <b>QUÀ ĐỔI ĐIỂM (TIỀN)</b> (bước 1/3)\n\nGửi <b>số tiền</b> thưởng cho mỗi lần đổi (VD: 50000).", "price"),
            ("💵 <b>QUÀ ĐỔI ĐIỂM (TIỀN)</b> (bước 2/3)\n\nTiền thưởng cộng vào ví nào? Gửi <b>chinh</b> (ví chính), <b>shop</b> (ví shop) hoặc <b>credits</b> (cộng lượt credits).", "wallet"),
            ("💵 <b>QUÀ ĐỔI ĐIỂM (TIỀN)</b> (bước 3/3)\n\nGửi <b>số điểm</b> cần để đổi (<code>0</code> = giữ nguyên).", "opt_int"),
        ],
        "build": lambda v: f"/quadoi tien {v[0]} {v[1]}" + (f" {v[2]}" if v[2] else ""),
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
        "steps": [("🎲 <b>HỘP MÙ: BẬT/TẮT LOẠI</b>\n\nChọn <b>loại acc</b> bên dưới (bật ↔ tắt).", "pick_cat")],
        "build": lambda v: f"/hopmu {v[0]}",
    },
    "mystery_weight": {
        "cat": "price", "handler": "on_hopmutile",
        "steps": [
            ("🎲 <b>HỘP MÙ: TỶ LỆ TRÚNG</b>\n\nChọn <b>loại acc</b> bên dưới — loại mới chưa tham gia thì bấm vào là tự bật luôn.", "pick_mystery_cat"),
            ("Gõ <b>% trúng</b> mik muốn cho loại này (1–99), VD: <b>60</b>.\n"
             "Các loại còn lại bot tự chia phần % còn lại theo đúng tỷ lệ cũ của chúng.",
             "pick_weight"),
        ],
        "build": lambda v: f"/hopmutile {v[0]} {v[1]}%",
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
        "steps": [("✅ <b>DUYỆT BH XONG</b>\n\nChọn <b>khiếu nại đang chờ</b> bên dưới (⚠️ = cần xem lại, hoặc gõ ID).", "pick_claim")],
        "build": lambda v: f"/bhdone {v[0]}",
    },
    "acc_info": {
        "cat": "orders", "handler": "on_accinfo",
        "steps": [("🔍 <b>TRUY XUẤT ACC</b>\n\nGửi <b>UID</b> cần tra hành trình.", "text")],
        "build": lambda v: f"/accinfo {v[0]}",
    },
    "profit": {
        "cat": "orders", "handler": "on_lo", "super_only": True,
        "steps": [("📊 <b>LÃI THEO LÔ</b>\n\nChọn <b>loại acc</b> bên dưới (hoặc gõ ID).", "pick_cat")],
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
            ("⭐ <b>ĐÁNH GIÁ NCC</b> (bước 1/2)\n\nChọn <b>NCC</b> bên dưới (hoặc gõ ID).", "pick_supplier"),
            ("⭐ <b>ĐÁNH GIÁ NCC</b> (bước 2/2)\n\nGửi <b>số sao</b> (1-5).", "int"),
        ],
        "build": lambda v: f"/danhgiancc {v[0]} {v[1]}",
    },
    "sup_score": {
        "cat": "sup", "handler": "on_chamdiem",
        "steps": [
            ("📊 <b>CHẤM TỈ LỆ SỐNG</b> (bước 1/2)\n\nChọn <b>NCC</b> bên dưới (hoặc gõ ID).", "pick_supplier"),
            ("📊 <b>CHẤM TỈ LỆ SỐNG</b> (bước 2/2)\n\nGửi <b>số ngày</b> quét (trống = 7).", "opt_int"),
        ],
        "build": lambda v: f"/chamdiem {v[0]}" + (f" {v[1]}" if v[1] is not None else ""),
    },
    "sup_auto": {
        "cat": "sup", "handler": "on_nccauto",
        "short": {"off": "/nccauto off"},
        "steps": [
            (_p_sup_auto, "text"),
            ("🤖 <b>NHẬP KHO TỰ ĐỘNG</b> (bước 2/3)\n\nChọn <b>loại acc</b> sẽ nhập vào (hoặc gõ ID).", "pick_cat"),
            ("🤖 <b>NHẬP KHO TỰ ĐỘNG</b> (bước 3/3)\n\nChọn <b>NCC</b> bên dưới (hoặc gõ ID, trống = không chọn).", "pick_supplier_opt"),
        ],
        "build": lambda v: f"/nccauto {v[0]} {v[1]}" + (f" {v[2]}" if v[2] else ""),
    },
}


# ------------------------------------------------------------------ chạy handler

def _parse_step(kind, raw):
    t = (raw or "").strip()
    if kind == "text":
        return (True, t, "") if t else (False, None, "⚠️ Không được để trống, gửi lại nhé.")
    if kind == "pick_stall":
        # Chọn gian hàng bằng nút; gõ tay tên gian hàng vẫn được (không phân biệt hoa/thường)
        if not t:
            return False, None, "⚠️ Bấm nút chọn gian hàng bên dưới, hoặc gõ tên gian hàng nhé."
        try:
            stalls = [(s.get("stall") or "").strip() for s in db.acc_stall_list()]
            stalls = [s for s in stalls if s]
        except Exception:
            stalls = []
        for s in stalls:
            if s.lower() == t.lower():
                return True, s, ""
        return False, None, "⚠️ Chưa có gian hàng này. Bấm nút bên dưới, hoặc tạo sạp mới (🏪 Thêm gian hàng mới) nhé."
    if kind == "int":
        try:
            return True, int(t), ""
        except Exception:
            return False, None, "⚠️ Phải là số, gửi lại nhé."
    if kind == "pick_weight":
        # % trúng hộp mù: bấm nút preset hoặc gõ tay 1–99
        t = (raw or "").strip().rstrip("%").strip()
        try:
            p = int(t)
        except Exception:
            return False, None, "⚠️ % phải là số 1–99, bấm nút hoặc gửi lại nhé."
        if not (1 <= p <= 99):
            return False, None, "⚠️ % phải từ 1 đến 99, gửi lại nhé."
        return True, p, ""
    if kind.startswith("pick_"):
        # Chọn bằng nút bấm (loại acc / NCC); gõ tay ID vẫn được
        t = (raw or "").strip()
        if kind.endswith("_opt") and not t:
            return True, None, ""
        try:
            return True, int(t), ""
        except Exception:
            return False, None, "⚠️ Bấm nút chọn bên dưới, hoặc gõ ID nhé."
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
    if kind == "wallet":
        t2 = t.lower()
        if t2 in ("main", "chinh", "ví chính", "vi chinh"):
            return True, "main", ""
        if t2 in ("shop", "vishop", "ví shop", "vi shop"):
            return True, "shop", ""
        if t2 in ("credits", "credit"):
            return True, "credits", ""
        return False, None, "⚠️ Gửi <b>chinh</b> (ví chính), <b>shop</b> (ví shop) hoặc <b>credits</b> nhé."
    return False, None, "⚠️ Lỗi nội bộ."


def _render_prompt(step):
    p = step[0]
    return p() if callable(p) else p


def _cat_pick_kb(flow_key: str, step_idx: int, cat: str,
                 include_hidden: bool = False, optional: bool = False):
    """Bàn phím chọn loại acc (theo thứ tự ID) cho bước pick_cat*."""
    try:
        cats = db.acc_category_list(include_hidden=include_hidden)
    except Exception:
        cats = []
    rows = []
    for c in cats:
        try:
            cid = int(c["id"])
        except Exception:
            continue
        nm = (c["name"] or "").strip()[:28] or f"Loại {cid}"
        hid = ""
        try:
            hid = " 🙈" if int(c["hidden"] or 0) else ""
        except Exception:
            pass
        rows.append([InlineKeyboardButton(
            text=f"#{cid} {nm}{hid}",
            callback_data=f"shopm:pick:{flow_key}:{step_idx}:{cid}")])
    if not rows and not optional:
        return None
    if optional:
        rows.append([InlineKeyboardButton(
            text="📦 Toàn bộ",
            callback_data=f"shopm:pick:{flow_key}:{step_idx}:all")])
    rows.append([InlineKeyboardButton(text="◀️ Quay lại nhóm",
                                      callback_data=f"shopm:back_{cat}")])
    rows.append([InlineKeyboardButton(text="🏠 Menu shop acc",
                                      callback_data="shopm:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _mystery_cat_pick_kb(flow_key: str, step_idx: int, cat: str):
    """Bàn phím chọn loại acc setup hộp mù: hiện % trúng nếu đang tham gia,
    loại mới chưa tham gia thì bấm vào là tự bật + đặt tỷ trọng luôn."""
    try:
        items = db.acc_mystery_setup_list()
    except Exception:
        items = []
    rows = []
    for i in items:
        nm = (i.get("name") or "").strip()[:24] or f"Loại {i['id']}"
        stock = f" ({i['stock']} acc)" if i.get("stock") else " (hết hàng)"
        if i.get("eligible"):
            label = f"#{i['id']} {nm} — ~{i['pct']}%{stock}"
        else:
            label = f"#{i['id']} {nm} — ➕ chưa tham gia{stock}"
        rows.append([InlineKeyboardButton(
            text=label[:60],
            callback_data=f"shopm:pick:{flow_key}:{step_idx}:{i['id']}")])
    if not rows:
        rows.append([InlineKeyboardButton(text="📭 Chưa có loại acc nào",
                                          callback_data="shopm:noop")])
    rows.append([InlineKeyboardButton(text="◀️ Quay lại nhóm",
                                      callback_data=f"shopm:back_{cat}")])
    rows.append([InlineKeyboardButton(text="🏠 Menu shop acc",
                                      callback_data="shopm:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


_WEIGHT_PRESETS = [
    (5, "5% · 🌱"),
    (10, "10%"),
    (20, "20%"),
    (30, "30% · ⭐"),
    (50, "50% · 🔥"),
]


def _weight_pick_kb(flow_key: str, step_idx: int, cat: str):
    """Bàn phím chọn % trúng hộp mù (gõ số % mik muốn)."""
    rows, row = [], []
    for p, label in _WEIGHT_PRESETS:
        row.append(InlineKeyboardButton(
            text=label,
            callback_data=f"shopm:pick:{flow_key}:{step_idx}:{p}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="◀️ Quay lại nhóm",
                                      callback_data=f"shopm:back_{cat}")])
    rows.append([InlineKeyboardButton(text="🏠 Menu shop acc",
                                      callback_data="shopm:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _claim_pick_kb(flow_key: str, step_idx: int, cat: str):
    """Bàn phím chọn khiếu nại BH đang chờ (theo thứ tự ID tăng dần)."""
    try:
        claims = [dict(r) for r in db.acc_warranty_pending(30)]
    except Exception:
        return None
    rows = []
    for r in sorted(claims, key=lambda x: int(x.get("id") or 0)):
        try:
            cid = int(r["id"])
        except Exception:
            continue
        cn = (r.get("cat_name") or "").strip()[:26]
        uid = r.get("uid") or ""
        label = f"#{cid} {cn} — UID {uid}".strip()
        if r.get("status") == "NEEDS_REVIEW":
            label = "⚠️ " + label
        rows.append([InlineKeyboardButton(
            text=label[:60],
            callback_data=f"shopm:pick:{flow_key}:{step_idx}:{cid}")])
    if not rows:
        rows.append([InlineKeyboardButton(text="📭 Không có khiếu nại đang chờ",
                                          callback_data="shopm:noop")])
    rows.append([InlineKeyboardButton(text="◀️ Quay lại nhóm",
                                      callback_data=f"shopm:back_{cat}")])
    rows.append([InlineKeyboardButton(text="🏠 Menu shop acc",
                                      callback_data="shopm:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _sup_pick_kb(flow_key: str, step_idx: int, cat: str,
                 optional: bool = False):
    """Bàn phím chọn NCC (theo thứ tự ID) cho bước pick_supplier*."""
    try:
        sups = db.supplier_list()
    except Exception:
        sups = []
    rows = []
    for s in sorted(sups, key=lambda r: int(r["id"] or 0)):
        try:
            sid = int(s["id"])
        except Exception:
            continue
        nm = (s["name"] or "").strip()[:28] or f"NCC {sid}"
        rows.append([InlineKeyboardButton(
            text=f"#{sid} {nm}",
            callback_data=f"shopm:pick:{flow_key}:{step_idx}:{sid}")])
    if not rows and not optional:
        return None
    if optional:
        rows.append([InlineKeyboardButton(
            text="➖ Không chọn NCC",
            callback_data=f"shopm:pick:{flow_key}:{step_idx}:none")])
    rows.append([InlineKeyboardButton(text="◀️ Quay lại nhóm",
                                      callback_data=f"shopm:back_{cat}")])
    rows.append([InlineKeyboardButton(text="🏠 Menu shop acc",
                                      callback_data="shopm:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _stall_pick_kb(flow_key: str, step_idx: int, cat: str):
    """Bàn phím chọn gian hàng cho bước pick_stall."""
    try:
        stalls = db.acc_stall_list()
    except Exception:
        stalls = []
    rows = []
    for s in stalls:
        nm = (s.get("stall") or "").strip()[:28]
        if not nm:
            continue
        try:
            n = int(s.get("n") or 0)
        except Exception:
            n = 0
        rows.append([InlineKeyboardButton(
            text=f"🏪 {nm} ({n} loại)",
            callback_data=f"shopm:pick:{flow_key}:{step_idx}:{nm}")])
    if not rows:
        return None
    rows.append([InlineKeyboardButton(text="◀️ Quay lại nhóm",
                                      callback_data=f"shopm:back_{cat}")])
    rows.append([InlineKeyboardButton(text="🏠 Menu shop acc",
                                      callback_data="shopm:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _step_kb(flow, flow_key: str, step_idx: int):
    """Bàn phím cho bước nhập liệu: nút chọn loại acc / NCC nếu là pick_*."""
    kind = flow["steps"][step_idx][1]
    if kind == "pick_cat":
        return _cat_pick_kb(flow_key, step_idx, flow["cat"]) or _back_kb(flow["cat"])
    if kind == "pick_cat_all":
        return (_cat_pick_kb(flow_key, step_idx, flow["cat"], include_hidden=True)
                or _back_kb(flow["cat"]))
    if kind == "pick_cat_opt":
        return (_cat_pick_kb(flow_key, step_idx, flow["cat"], optional=True)
                or _back_kb(flow["cat"]))
    if kind == "pick_stall":
        return _stall_pick_kb(flow_key, step_idx, flow["cat"]) or _back_kb(flow["cat"])
    if kind == "pick_supplier":
        return _sup_pick_kb(flow_key, step_idx, flow["cat"]) or _back_kb(flow["cat"])
    if kind == "pick_supplier_opt":
        return (_sup_pick_kb(flow_key, step_idx, flow["cat"], optional=True)
                or _back_kb(flow["cat"]))
    if kind == "pick_claim":
        return _claim_pick_kb(flow_key, step_idx, flow["cat"]) or _back_kb(flow["cat"])
    if kind == "pick_weight":
        return _weight_pick_kb(flow_key, step_idx, flow["cat"]) or _back_kb(flow["cat"])
    if kind == "pick_mystery_cat":
        return (_mystery_cat_pick_kb(flow_key, step_idx, flow["cat"])
                or _back_kb(flow["cat"]))
    return _back_kb(flow["cat"])


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

# ================== NHẬP KHO TỰ ĐỘNG THEO GIAN HÀNG ==================
def _autoimp_fmt_ts(ts: int) -> str:
    import time as _t
    try:
        ts = int(ts or 0)
        if ts <= 0:
            return "—"
        return _t.strftime("%d/%m %H:%M", _t.localtime(ts))
    except Exception:
        return "—"


def _autoimp_detail_text(stall: str) -> str:
    from . import sheet_import as _si
    cfg = db.stall_import_cfg_get(stall)
    iv = max(5, min(10080, int(cfg.get("interval_min") or 60)))
    on = int(cfg.get("enabled") or 0) == 1
    cat_id = int(cfg.get("cat_id") or 0)
    sup_id = int(cfg.get("supplier_id") or 0)
    cost = int(cfg.get("cost") or 0)
    cat_txt = "⚠️ <i>chưa chọn</i>"
    if cat_id:
        c = db.acc_category_get(cat_id)
        cat_txt = (f"<b>{html.escape(c['name'])}</b> (#{cat_id})" if c
                   else f"⚠️ loại #{cat_id} không còn")
    sup_txt = "—"
    if sup_id:
        s = db.supplier_get(sup_id)
        sup_txt = f"#{sup_id} {html.escape(s['name'])}" if s else f"⚠️ #{sup_id} không còn"
    try:
        tab = _si.tab_for_stall(stall, db.get_setting("sheet_import_tab") or "NhapKho")
    except Exception:
        tab = stall
    last = int(cfg.get("last_run") or 0)
    nxt = last + iv * 60 if (on and last) else 0
    lines = [
        "⏰ <b>NHẬP KHO TỰ ĐỘNG</b>",
        f"🏪 <b>{html.escape(stall)}</b> <i>(tab Sheet: <code>{html.escape(tab)}</code>)</i>",
        "━━━━━━━━━━━━━━",
        f"🔄 Trạng thái: {'🟢 <b>BẬT</b>' if on else '🔴 <b>TẮT</b>'}",
        f"⏱️ Chu kỳ quét: mỗi <b>{iv}</b> phút",
        f"📦 Loại acc: {cat_txt}",
        f"🏭 NCC: {sup_txt}",
        f"💰 Giá vốn: <b>{cost:,}</b>đ/acc".replace(",", "."),
        f"🕐 Quét cuối: {_autoimp_fmt_ts(last)}",
        f"⏭️ Quét tiếp: ~{_autoimp_fmt_ts(nxt)}" if nxt else "⏭️ Quét tiếp: —",
        "",
        "<i>Bot tự đọc các dòng chưa đánh dấu trong tab Sheet của sạp "
        "rồi nhập kho (chống trùng, ghi trạng thái ngược như nhập tay).</i>",
    ]
    return "\n".join(lines)


def _autoimp_detail_kb(stall: str):
    cfg = db.stall_import_cfg_get(stall)
    on = int(cfg.get("enabled") or 0) == 1
    rows = [
        [InlineKeyboardButton(
            text="🔴 Tắt nhập tự động" if on else "🟢 Bật nhập tự động",
            callback_data=f"shopm:autoimp:toggle:{stall}")],
        [InlineKeyboardButton(text="⏱️ Đổi chu kỳ",
                              callback_data=f"shopm:autoimp:int:{stall}"),
         InlineKeyboardButton(text="📦 Loại acc",
                              callback_data=f"shopm:autoimp:cat:{stall}")],
        [InlineKeyboardButton(text="🏭 NCC",
                              callback_data=f"shopm:autoimp:sup:{stall}"),
         InlineKeyboardButton(text="💰 Giá vốn",
                              callback_data=f"shopm:autoimp:cost:{stall}")],
        [InlineKeyboardButton(text="▶️ Chạy ngay 1 lần",
                              callback_data=f"shopm:autoimp:run:{stall}")],
        [InlineKeyboardButton(text="◀️ Danh sách sạp",
                              callback_data="shopm:autoimp"),
         InlineKeyboardButton(text="🏠 Menu shop acc",
                              callback_data="shopm:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _autoimp_list_kb():
    rows = []
    try:
        stalls = db.acc_stall_list()
    except Exception:
        stalls = []
    for s in stalls:
        st = s.get("stall") or "Acc Facebook"
        cfg = db.stall_import_cfg_get(st)
        on = int(cfg.get("enabled") or 0) == 1
        iv = max(5, min(10080, int(cfg.get("interval_min") or 60)))
        mark = f"🟢 {iv}p" if on else "🔴 tắt"
        rows.append([InlineKeyboardButton(
            text=f"🏪 {st} ({mark})",
            callback_data=f"shopm:autoimp:stall:{st}")])
    rows.append([InlineKeyboardButton(text="◀️ Quay lại nhóm",
                                      callback_data="shopm:back_kho")])
    rows.append([InlineKeyboardButton(text="🏠 Menu shop acc",
                                      callback_data="shopm:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _autoimp_list_text():
    return ("⏰ <b>NHẬP KHO TỰ ĐỘNG THEO GIAN HÀNG</b>\n"
            "━━━━━━━━━━━━━━\n"
            "Mỗi sạp có tab Sheet riêng — bot sẽ tự quét tab đó theo chu kỳ "
            "bạn đặt, nhập các dòng mới vào kho.\n\n"
            "Chọn sạp để cấu hình:")


def register_shop_menu(target_router):
    """Gắn toàn bộ menu nút /shopadm (callback + nhập liệu FSM) vào router cho trước."""

    @target_router.message(Command("shopadm"))
    async def _on_shopadm(msg: Message, state: FSMContext):
        if not _is_admin_sync(msg.from_user.id):
            return
        await state.clear()
        await msg.answer(_main_text(), parse_mode="HTML",
                         reply_markup=_main_kb(msg.from_user.id))

    @target_router.callback_query(F.data.startswith("shopm:pick:"))
    async def _on_shopm_pick(cb: CallbackQuery, state: FSMContext):
        # Bấm nút chọn loại acc ở bước pick_cat (thay cho gõ ID).
        # Đăng ký TRƯỚC handler "shopm:" để không bị nuốt callback.
        if not _is_admin_sync(cb.from_user.id):
            await cb.answer("🚫 Không có quyền.", show_alert=True)
            return
        try:
            # maxsplit=4 để tên gian hàng (pick_stall) có chứa ":" cũng không vỡ
            _, _, flow_key, step_s, cat_s = (cb.data or "").split(":", 4)
            step_idx = int(step_s)
        except Exception:
            await cb.answer()
            return
        data = await state.get_data()
        flow = FLOWS.get(flow_key)
        kind = (flow["steps"][step_idx][1] if flow
                and step_idx < len(flow["steps"]) else "")
        if (not flow or data.get("shopm_flow") != flow_key
                or int(data.get("shopm_step") or 0) != step_idx
                or not kind.startswith("pick_")):
            await cb.answer("Hết phiên, thử lại.", show_alert=True)
            return
        if cat_s in ("all", "none"):
            if not kind.endswith("_opt"):
                await cb.answer("Hết phiên, thử lại.", show_alert=True)
                return
            val = None
        elif kind == "pick_stall":
            val = (cat_s or "").strip()
            if not val:
                await cb.answer()
                return
        else:
            try:
                val = int(cat_s)
            except Exception:
                await cb.answer()
                return
        if not _perms.has_perm(cb.from_user.id, flow["cat"]):
            await state.clear()
            await cb.answer("🚫 Bạn không có quyền thao tác này.", show_alert=True)
            return
        vals = (data.get("shopm_vals") or []) + [val]
        if step_idx + 1 < len(flow["steps"]):
            await state.update_data(shopm_step=step_idx + 1, shopm_vals=vals)
            prompt = _render_prompt(flow["steps"][step_idx + 1])
            await cb.message.edit_text(
                prompt + "\n\nGõ /huy để huỷ.", parse_mode="HTML",
                reply_markup=_step_kb(flow, flow_key, step_idx + 1))
            await cb.answer()
            return
        cmd_text = flow["build"](vals)
        if flow.get("confirm"):
            await state.update_data(shopm_cmd=cmd_text)
            await state.set_state(ShopMenuState.confirm)
            await cb.message.edit_text(
                flow["summary"](vals) + "\n\nXác nhận thực hiện?",
                parse_mode="HTML", reply_markup=_confirm_kb())
            await cb.answer()
            return
        await _exec_via_cb(cb, state, flow, cmd_text)

    @target_router.callback_query(F.data.startswith("shopm:autoimp"))
    async def _on_shopm_autoimp(cb: CallbackQuery, state: FSMContext):
        # Màn hình cấu hình nhập kho tự động từng gian hàng.
        # Đăng ký TRƯỚC handler "shopm:" để không bị nuốt callback.
        if not _is_admin_sync(cb.from_user.id):
            await cb.answer("🚫 Không có quyền.", show_alert=True)
            return
        if not _perms.has_perm(cb.from_user.id, "kho"):
            await cb.answer("🚫 Bạn không có quyền 📦 Kho & loại acc.", show_alert=True)
            return
        body = (cb.data or "")[len("shopm:autoimp"):]
        parts = body.strip(":").split(":") if body.strip(":") else []
        sub = parts[0] if parts else ""
        stall = ":".join(parts[1:]) if len(parts) > 1 else ""

        async def _show_detail(st):
            await cb.message.edit_text(_autoimp_detail_text(st), parse_mode="HTML",
                                       reply_markup=_autoimp_detail_kb(st))

        def _audit(action, detail):
            try:
                db.admin_audit_add(cb.from_user.id, cb.from_user.full_name,
                                   action, detail)
            except Exception:
                pass

        if not sub:
            await state.clear()
            await cb.message.edit_text(_autoimp_list_text(), parse_mode="HTML",
                                       reply_markup=_autoimp_list_kb())
            await cb.answer()
            return
        if sub == "stall" and stall:
            await _show_detail(stall)
            await cb.answer()
            return
        if sub == "toggle" and stall:
            cfg = db.stall_import_cfg_get(stall)
            new = 0 if int(cfg.get("enabled") or 0) else 1
            db.stall_import_cfg_set(stall, enabled=new)
            _audit("auto_import", f"{stall}: {'BẬT' if new else 'TẮT'} nhập tự động")
            if new and not int(cfg.get("cat_id") or 0):
                await cb.answer("⚠️ Đã bật — nhớ chọn loại acc mặc định nhé!",
                                show_alert=True)
            else:
                await cb.answer("🟢 Đã bật nhập tự động" if new else "🔴 Đã tắt")
            await _show_detail(stall)
            return
        if sub == "int" and stall:
            await state.update_data(ai_field="int", ai_stall=stall)
            await state.set_state(ShopMenuState.autoimp)
            await cb.message.edit_text(
                f"⏱️ <b>CHU KỲ QUÉT — {html.escape(stall)}</b>\n\n"
                "Gửi số <b>phút</b> giữa 2 lần quét "
                "(tối thiểu 5, tối đa 10080 = 7 ngày).\n"
                "Gõ /huy để huỷ.", parse_mode="HTML")
            await cb.answer()
            return
        if sub == "sup" and stall:
            await state.update_data(ai_field="sup", ai_stall=stall)
            await state.set_state(ShopMenuState.autoimp)
            await cb.message.edit_text(
                f"🏭 <b>NCC MẶC ĐỊNH — {html.escape(stall)}</b>\n\n"
                "Gửi <b>ID NCC</b> (xem: /ncc) — gửi <b>0</b> = không gắn NCC.\n"
                "Gõ /huy để huỷ.", parse_mode="HTML")
            await cb.answer()
            return
        if sub == "cost" and stall:
            await state.update_data(ai_field="cost", ai_stall=stall)
            await state.set_state(ShopMenuState.autoimp)
            await cb.message.edit_text(
                f"💰 <b>GIÁ VỐN MẶC ĐỊNH — {html.escape(stall)}</b>\n\n"
                "Gửi <b>giá vốn</b>/acc (vd 5000) — dùng để tính lãi theo lô.\n"
                "Gõ /huy để huỷ.", parse_mode="HTML")
            await cb.answer()
            return
        if sub == "cat" and stall:
            cats = [c for c in db.acc_category_list(active_only=False,
                                                   include_hidden=True)
                    if (((c["stall"] if "stall" in c.keys() else "")
                         or "Acc Facebook")) == stall]
            if not cats:
                await cb.answer("Sạp này chưa có loại acc nào.", show_alert=True)
                return
            rows = []
            for cc in cats:
                dd = dict(cc)
                name = (dd.get("name") or f"Loại {dd['id']}")[:24]
                rows.append([InlineKeyboardButton(
                    text=f"#{dd['id']} {name}",
                    callback_data=f"shopm:autoimp:catset:{stall}:{dd['id']}")])
            rows.append([InlineKeyboardButton(
                text="◀️ Quay lại",
                callback_data=f"shopm:autoimp:stall:{stall}")])
            await cb.message.edit_text(
                f"📦 <b>LOẠI ACC MẶC ĐỊNH — {html.escape(stall)}</b>\n\n"
                "Các dòng mới trong tab Sheet sẽ được nhập vào loại này:",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
            await cb.answer()
            return
        if sub == "catset" and len(parts) >= 3:
            try:
                cat_id = int(parts[-1])
            except Exception:
                await cb.answer()
                return
            st = ":".join(parts[1:-1])
            c = db.acc_category_get(cat_id)
            if not c:
                await cb.answer("❌ Loại acc không còn.", show_alert=True)
                return
            db.stall_import_cfg_set(st, cat_id=cat_id)
            _audit("auto_import", f"{st}: loại mặc định -> #{cat_id}")
            await cb.answer(f"✅ Loại mặc định: {c['name']}")
            await _show_detail(st)
            return
        if sub == "run" and stall:
            cfg = db.stall_import_cfg_get(stall)
            cat_id = int(cfg.get("cat_id") or 0)
            if not cat_id:
                await cb.answer("⚠️ Chưa chọn loại acc mặc định.", show_alert=True)
                return
            await cb.answer("⏳ Đang quét Sheet...")
            try:
                fn = _get_handler("auto_import_stall")
                res = await fn(stall, cat_id, int(cfg.get("supplier_id") or 0),
                               int(cfg.get("cost") or 0), cb.bot)
            except Exception as e:
                res = {"error": str(e)[:200]}
            if res.get("error"):
                head = f"❌ {html.escape(str(res['error']))}"
            elif int(res.get("added") or 0) > 0:
                head = (f"✅ <b>Đã nhập {res['added']} acc mới</b> vào kho "
                        f"(đọc {res.get('rows') or 0} dòng).")
                _audit("auto_import", f"{stall}: chạy tay, +{res['added']} acc")
            else:
                head = "📭 Sheet không có dòng mới nào."
            await cb.message.edit_text(
                head + "\n\n" + _autoimp_detail_text(stall), parse_mode="HTML",
                reply_markup=_autoimp_detail_kb(stall))
            return
        await cb.answer()

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
            if flow.get("custom") == "autoimp":
                await state.clear()
                await cb.message.edit_text(_autoimp_list_text(), parse_mode="HTML",
                                           reply_markup=_autoimp_list_kb())
                await cb.answer()
                return
            await state.update_data(shopm_flow=action[3:], shopm_step=0,
                                    shopm_vals=[], shopm_cat=flow["cat"])
            await state.set_state(ShopMenuState.input)
            prompt = _render_prompt(flow["steps"][0])
            await cb.message.edit_text(prompt + "\n\nGõ /huy để huỷ.", parse_mode="HTML",
                                       reply_markup=_step_kb(flow, action[3:], 0))
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
            await msg.answer(prompt + "\n\nGõ /huy để huỷ.", parse_mode="HTML",
                             reply_markup=_step_kb(flow, data.get("shopm_flow") or "", step_idx + 1))
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

    @target_router.message(ShopMenuState.autoimp)
    async def _on_shopm_autoimp_input(msg: Message, state: FSMContext):
        # Nhập liệu cho cấu hình nhập kho tự động: chu kỳ / NCC / giá vốn.
        if not _is_admin_sync(msg.from_user.id):
            await state.clear()
            return
        if (msg.text or "").strip().lower() in ("/huy", "/cancel"):
            await state.clear()
            await msg.answer("Đã huỷ.")
            return
        if not _perms.has_perm(msg.from_user.id, "kho"):
            await state.clear()
            await msg.answer("🚫 Bạn không có quyền 📦 Kho & loại acc.")
            return
        data = await state.get_data()
        field, stall = data.get("ai_field"), data.get("ai_stall")
        if not field or not stall or not msg.text:
            await state.clear()
            return
        t = msg.text.strip()
        if field == "int":
            try:
                iv = int(t)
            except Exception:
                await msg.answer("⚠️ Phải là số phút, gửi lại nhé.")
                return
            iv = max(5, min(10080, iv))
            db.stall_import_cfg_set(stall, interval_min=iv)
            saved = f"⏱️ Chu kỳ quét: mỗi <b>{iv}</b> phút"
        elif field == "sup":
            try:
                sid = int(t)
            except Exception:
                await msg.answer("⚠️ NCC phải là ID số (0 = không chọn), gửi lại nhé.")
                return
            if sid < 0:
                await msg.answer("⚠️ ID NCC không hợp lệ, gửi lại nhé.")
                return
            if sid and not db.supplier_get(sid):
                await msg.answer("❌ Không có NCC này. Xem: /ncc")
                return
            db.stall_import_cfg_set(stall, supplier_id=sid)
            saved = "🏭 NCC mặc định: —" if not sid else f"🏭 NCC mặc định: #{sid}"
        elif field == "cost":
            try:
                cost = int(t.replace(".", "").replace(",", "").replace(" ", ""))
                if cost < 0:
                    raise ValueError
            except Exception:
                await msg.answer("⚠️ Giá vốn phải là số, gửi lại nhé.")
                return
            db.stall_import_cfg_set(stall, cost=cost)
            saved = f"💰 Giá vốn: <b>{cost:,}</b>đ/acc".replace(",", ".")
        else:
            await state.clear()
            return
        await state.clear()
        try:
            db.admin_audit_add(msg.from_user.id, msg.from_user.full_name,
                               "auto_import", f"{stall}: {field} -> {t}")
        except Exception:
            pass
        await msg.answer(f"✅ Đã lưu — {saved}\n\n{_autoimp_detail_text(stall)}",
                         parse_mode="HTML",
                         reply_markup=_autoimp_detail_kb(stall))


