"""Panel ⚙️ CÀI ĐẶT BOT — gom mọi cấu hình vào bot admin, chỉnh bằng nút bấm.

Chỉ chủ shop (is_super) dùng được. Đọc/ghi thẳng setting trong DB nên
có hiệu lực ngay (db.set_setting tự refresh cache), mọi thay đổi đều
ghi nhật ký admin (admin_audit_add).

Đăng ký bằng register_settings(router) — gọi trong register_adm_menu()
của admin_bot.py nên chạy được trên cả bot chính lẫn bot admin.
"""

import html
import json
import logging
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from . import db
from . import perms as _perms
from .util import vnd as _vnd

log = logging.getLogger("admin_settings")


class AdmSetState(StatesGroup):
    value = State()
    banks_add = State()       # nhập "Tên | STK | Chủ TK"
    bank_qr = State()         # chờ gửi ảnh QR cho 1 ngân hàng
    photo = State()           # chờ gửi ảnh cho setting kiểu "photo"
    prize_label = State()     # nhập tên giải
    prize_kind = State()      # chờ bấm nút chọn loại giải
    prize_value = State()     # nhập giá trị giải (sau khi chọn loại)
    prize_weight = State()    # nhập trọng số giải


# ─────────────────────────────────────────────────────────────
# Định nghĩa setting: key | label | type | hint (cách dùng) | default
# type: toggle | int | money | pct | ratio | text | select | packs
# ─────────────────────────────────────────────────────────────

def _S(key, label, typ, hint, default="", **kw):
    d = {"key": key, "label": label, "type": typ, "hint": hint,
         "default": default}
    d.update(kw)
    return d


SETTING_GROUPS = [
    {
        "id": "money", "title": "💰 Tiền tệ & Ngân hàng",
        "desc": "Tài khoản nhận tiền, cảnh báo sửa giá, quà sinh nhật.",
        "settings": [
            _S("bank_name", "Tên ngân hàng", "text",
               "Hiện khi khách chọn nạp qua chuyển khoản tay.",
               "", ph="VD: Vietcombank"),
            _S("bank_account", "Số tài khoản", "text",
               "Số tài khoản nhận tiền nạp tay.", "", ph="VD: 0123456789"),
            _S("bank_owner", "Chủ tài khoản", "text",
               "Tên chủ tài khoản hiện kèm STK.", "", ph="VD: NGUYEN VAN A"),
            _S("bank_qr", "Ảnh QR (1 TK)", "photo",
               "Ảnh QR cho TK ở 3 ô trên (khi không dùng danh sách nhiều TK).",
               ""),
            _S("banks_list", "Danh sách ngân hàng", "banks",
               "Nhiều TK ngân hàng cho khách chọn khi nạp tay. "
               "TK đơn ở dưới vẫn hiện kèm (trùng STK thì hiện 1 lần).", ""),
            _S("price_warn_pct", "Cảnh báo chênh giá (%)", "pct",
               "Sửa giá chênh quá X% so với giá cũ → bot hỏi xác nhận.",
               "50", min=0, max=100),
            _S("birthday_gift_amount", "Quà sinh nhật", "money",
               "Tự tặng vào ví chính vào đúng ngày sinh nhật khách.",
               "50000"),
        ],
    },
    {
        "id": "shop", "title": "🛒 Shop Acc",
        "desc": "Đặt cọc, cảnh báo hết hàng, quét kho, hộp mù, happy hour.",
        "settings": [
            _S("deposit_pct", "Đặt cọc giữ acc (%)", "pct",
               "% giá trị đơn khi khách đặt trước acc hết hàng.",
               "0", min=0, max=100),
            _S("acc_low_stock_warn", "Báo sắp hết hàng khi ≤", "int",
               "Tồn kho loại acc còn ≤ X → báo cho admin.", "5",
               min=0, max=10000, unit="acc"),
            _S("stock_recheck_days", "Quét lại kho mỗi", "int",
               "Chu kỳ tự kiểm tra LIVE toàn kho FB.", "2",
               min=1, max=30, unit="ngày"),
            _S("stock_recheck_hour", "Giờ quét kho", "int",
               "Giờ trong ngày chạy quét kho (giờ VN).", "3",
               min=0, max=23, unit="giờ"),
            _S("mystery_price", "Giá hộp mù", "money",
               "Giá mỗi lượt mở hộp mù acc.", "0"),
            _S("happy_hour_pct", "Happy hour giảm", "pct",
               "Giảm giá shop acc trong khung giờ vàng.", "0",
               min=0, max=100),
            _S("happy_hour_range", "Khung giờ vàng", "text",
               "Dạng GIỜ-GIỜ (giờ VN). Để trống = tắt.",
               "", ph="VD: 18-20"),
            _S("happy_hour_cat", "Giờ vàng áp cho loại", "int",
               "ID loại acc được giảm giờ vàng. 0 = toàn shop. "
               "Xem ID loại ở /kho.",
               "0", min=0, ph="VD: 0"),
            _S("warranty_max_week", "BH tối đa", "int",
               "Số tuần bảo hành tối đa cho phép khi nhập kho.",
               "3", min=1, max=52, unit="tuần"),
            _S("warranty_remind_hours", "Nhắc hết BH trước", "int",
               "Nhắc khách trước khi hết bảo hành từng này giờ.",
               "12", min=1, max=168, unit="giờ"),
            _S("review_bonus", "Thưởng đánh giá", "int",
               "Tặng credit khi khách đánh giá sao đơn mua acc.",
               "20", min=0, unit="credit"),
            _S("upsell_pct", "Upsell giảm", "pct",
               "Giảm giá khi gợi ý mua acc sau check bulk ra nhiều DIE.",
               "0", min=0, max=100),
            _S("upsell_window_min", "Hiện upsell trong", "int",
               "Gợi ý mua acc hiện trong X phút sau khi check.",
               "30", min=1, max=1440, unit="phút"),
        ],
    },
    {
        "id": "loyalty", "title": "🎲 Điểm thưởng",
        "desc": "Tích điểm, random sau đơn, đổi quà.",
        "settings": [
            _S("loyalty_per_vnd", "VND = 1 điểm", "money",
               "Mua mỗi Xđ được cộng 1 điểm thưởng.", "100000"),
            _S("loyalty_random_min", "Random tối thiểu", "int",
               "Điểm ngẫu nhiên ít nhất sau mỗi đơn đủ điều kiện.",
               "0", min=0, unit="điểm"),
            _S("loyalty_random_max", "Random tối đa", "int",
               "Điểm ngẫu nhiên nhiều nhất sau mỗi đơn.", "0",
               min=0, unit="điểm"),
            _S("loyalty_random_cap", "Trần random / user", "int",
               "Mỗi user tối đa X điểm random (0 = tắt random).", "0",
               min=0, unit="điểm"),
            _S("loyalty_random_min_order", "Đơn tối thiểu được random", "money",
               "Đơn ≥ Xđ mới được nhận điểm ngẫu nhiên.", "0"),
            _S("loyalty_random_daily_max", "Random tối đa / ngày", "int",
               "Mỗi user tối đa X lượt random mỗi ngày.", "0",
               min=0, unit="lượt"),
            _S("loyalty_redeem_points", "Điểm cần để đổi quà", "int",
               "Số điểm khách cần tích để đổi 1 phần quà.", "10",
               min=1, unit="điểm"),
            _S("loyalty_redeem_mode", "Loại quà đổi điểm", "select",
               "Quà khi đủ điểm: tặng acc hay tặng tiền ví.",
               "acc", choices=[("acc", "🎁 Tặng acc"),
                               ("money", "💵 Tặng tiền ví")]),
            _S("loyalty_redeem_amount", "Quà tiền ví", "money",
               "Số tiền tặng vào ví khi đổi điểm (chỉ khi quà = tiền ví).",
               "0"),
            _S("loyalty_redeem_wallet", "Ví nhận quà tiền", "select",
               "Quà tiền ví sẽ vào ví nào.", "main",
               choices=[("main", "💰 Ví chính"), ("shop", "🛒 Ví shop"),
                        ("buff", "👛 Ví buff"), ("rent", "📱 Ví thuê số")]),
            _S("loyalty_redeem_scope", "Phạm vi quà acc", "select",
               "Khi quà = acc: khách chọn trong phạm vi nào.", "cat",
               choices=[("cat", "📦 Theo loại acc"),
                        ("stall", "🏪 Theo gian hàng")]),
            _S("spin_prizes", "Giải thưởng vòng quay", "prizes",
               "Các giải của vòng quay may mắn. "
               "Để trống = dùng 6 giải mặc định.", ""),
        ],
    },
    {
        "id": "plans", "title": "💳 Gói & VIP",
        "desc": "Giá gói ngày/tuần/tháng, credit, dùng thử, điểm danh.",
        "settings": [
            _S("price_1d", "Giá gói 1 ngày", "money",
               "Giá thuê bot 1 ngày.", "5000"),
            _S("price_3d", "Giá gói 3 ngày", "money",
               "Giá thuê bot 3 ngày.", "0"),
            _S("price_5d", "Giá gói 5 ngày", "money",
               "Giá thuê bot 5 ngày.", "0"),
            _S("price_7d", "Giá gói 7 ngày", "money",
               "Giá thuê bot 7 ngày.", "20000"),
            _S("price_1m", "Giá gói 1 tháng", "money",
               "Giá thuê bot 1 tháng.", "50000"),
            _S("price_per_month", "Giá tự gia hạn tháng", "money",
               "Trừ ví chính khi khách bật tự gia hạn gói tháng.",
               "50000"),
            _S("bulk_credit_cost", "Credit / UID check bulk", "int",
               "Mỗi UID khi check bulk trừ X credit.", "1",
               min=1, unit="credit"),
            _S("credit_packs", "Các gói credit", "packs",
               "Dạng: lượt=giá, cách nhau dấu phẩy. VD: 500=50000, 900=200000",
               ""),
            _S("sub_credit_ratio", "Tặng credit gói tháng", "ratio",
               "% số lượt của gói credit tương đương khi mua gói tháng.",
               "0.9"),
            _S("enable_free_trial", "Cho dùng thử", "toggle",
               "Bật/tắt cho khách mới dùng thử miễn phí.", "1"),
            _S("free_trial_days", "Số ngày dùng thử", "int",
               "Khách mới được dùng thử X ngày.", "3",
               min=1, max=30, unit="ngày"),
            _S("daily_reward_base", "Điểm danh: tiền cơ bản", "money",
               "Điểm danh ngày thường nhận Xđ vào ví chính.", "0"),
            _S("daily_reward_7d", "Điểm danh: 7 ngày liên tiếp", "money",
               "Thưởng thêm khi điểm danh 7 ngày liên tiếp.", "0"),
            _S("daily_reward_30d", "Điểm danh: 30 ngày liên tiếp", "money",
               "Thưởng thêm khi điểm danh 30 ngày liên tiếp.", "0"),
            _S("daily_credit_base", "Điểm danh: lượt cơ bản", "int",
               "Điểm danh ngày thường nhận X lượt check.", "2",
               min=0, unit="lượt"),
            _S("daily_credit_7d", "Điểm danh: lượt 7 ngày", "int",
               "Thưởng lượt khi điểm danh 7 ngày liên tiếp.", "10",
               min=0, unit="lượt"),
            _S("daily_credit_30d", "Điểm danh: lượt 30 ngày", "int",
               "Thưởng lượt khi điểm danh 30 ngày liên tiếp.", "30",
               min=0, unit="lượt"),
            _S("low_credit_warn", "Báo credit thấp khi ≤", "int",
               "Credit còn ≤ X → nhắc khách nạp thêm.", "0",
               min=0, unit="credit"),
            _S("ref_shop_pct", "Hoa hồng giới thiệu", "pct",
               "% hoa hồng F1 khi người được giới thiệu mua ở shop.",
               "10", min=0, max=100),
        ],
    },
    {
        "id": "rent", "title": "📱 Thuê số OTP",
        "desc": "Bật/tắt shop thuê số, % lãi, dịch vụ ẩn.",
        "settings": [
            _S("viotp_enabled", "Bật shop thuê số", "toggle",
               "Tắt → khách không thuê số được nữa.", "1"),
            _S("viotp_markup_pct", "% lãi thuê số", "pct",
               "% cộng thêm vào giá vốn ViOTP ra giá bán.",
               "50", min=0, max=500),
            _S("viotp_disabled_services", "Dịch vụ đang ẩn", "text",
               "Mã dịch vụ ẩn khỏi shop, cách nhau dấu phẩy.",
               "", ph="VD: fb, zalo"),
        ],
    },
    {
        "id": "buff", "title": "🚀 Shop Buff",
        "desc": "Bật/tắt shop buff, tài khoản panel.",
        "settings": [
            _S("buff_enabled", "Bật shop buff", "toggle",
               "Tắt → khách không đặt buff được nữa.", "1"),
            _S("buff_panel_user", "Tài khoản panel buff", "text",
               "User đăng nhập panel buff. Mật khẩu đổi riêng, không hiện ở đây.",
               "", ph="VD: username panel"),
        ],
    },
    {
        "id": "loan", "title": "💰 Công nợ ứng tiền",
        "desc": "Điều kiện cho ứng, hạn trả, tự trừ nợ, nhắc nợ.",
        "settings": [
            _S("loan_enabled", "Bật cho ứng tiền", "toggle",
               "Tắt → khách không xin ứng được nữa.", "1"),
            _S("loan_min_acc", "Mua tối thiểu (acc)", "int",
               "Khách phải mua ≥ X acc mới được xin ứng.", "5",
               min=0, unit="acc"),
            _S("loan_max_per_request", "Ứng tối đa / lần", "money",
               "Mỗi lần xin ứng không quá Xđ.", "50000"),
            _S("loan_max_total", "Tổng nợ tối đa", "money",
               "Mỗi khách nợ không quá Xđ (mọi khoản cộng lại).",
               "100000"),
            _S("loan_due_days", "Hạn trả (ngày)", "int",
               "Khoản ứng phải trả trong X ngày.", "7",
               min=1, max=90, unit="ngày"),
            _S("loan_auto_deduct", "Tự trừ nợ khi nạp", "toggle",
               "Bật → khách nạp ví shop thì tự trừ vào nợ cũ.",
               "1"),
            _S("loan_remind", "Nhắc nợ tự động", "toggle",
               "Nhắc trước hạn 1 ngày + mỗi ngày khi quá hạn (9h sáng).",
               "1"),
        ],
    },
    {
        "id": "consign", "title": "🏷️ Ký gửi",
        "desc": "Bật/tắt ký gửi, rút tiền, hạn mức đối tác mới.",
        "settings": [
            _S("consign_enabled", "Bật ký gửi", "toggle",
               "Tắt → ẩn toàn bộ tính năng ký gửi.", "0"),
            _S("consign_min_withdraw", "Rút tối thiểu", "money",
               "Đối tác rút tiền phải ≥ Xđ.", "0"),
            _S("consign_withdraw_fee", "Phí rút tiền", "money",
               "Phí mỗi lần đối tác rút tiền.", "0"),
            _S("consign_withdraw_schedule", "Lịch xử lý rút tiền", "text",
               "Thông báo cho đối tác biết khi nào được xử lý.",
               "", ph="VD: Xử lý T2-T6 hàng tuần"),
            _S("consign_default_max_items", "Hạn mức acc (đối tác mới)", "int",
               "Đối tác mới tối đa X acc ký gửi.", "0",
               min=0, unit="acc"),
            _S("consign_default_max_value", "Hạn mức giá trị (đối tác mới)",
               "money", "Tổng giá trị acc đối tác mới được ký gửi.", "0"),
        ],
    },
    {
        "id": "system", "title": "🔧 Hệ thống",
        "desc": "Link web/mini app, mail ảo, hướng dẫn sau mua, job tự động.",
        "settings": [
            _S("web_domain", "Domain web admin", "text",
               "Domain trỏ tới web quản trị (không có https://).",
               "", ph="VD: arikakhai.com"),
            _S("miniapp_url", "Link Mini App", "text",
               "Link mở Mini App Telegram.", "", ph="VD: https://t.me/..."),
            _S("mail_app_link", "Link tải app mail ảo", "text",
               "Hiện trong tin giao acc cho khách.", ""),
            _S("postbuy_guide", "Hướng dẫn sau mua", "text",
               "Đoạn hướng dẫn thêm vào tin giao acc. Để trống = dùng mặc định.",
               ""),
            _S("poll_interval", "Chu kỳ job nền (giây)", "int",
               "Bot quét việc nền mỗi X giây.", "60",
               min=10, max=3600, unit="giây"),
            _S("review_nudge_minutes", "Nhắc đánh giá sau", "int",
               "Sau mua acc từng này phút mà khách chưa đánh giá thì bot nhắc.",
               "90", min=10, max=10080, unit="phút"),
            _S("stale_days", "Báo tồn kho lâu", "int",
               "Acc nằm kho quá X ngày thì báo tồn lâu cho admin.",
               "30", min=1, max=365, unit="ngày"),
            _S("clean_stock_days", "Tự dọn kho cũ", "int",
               "Tự dọn acc tồn kho quá X ngày (0 = tắt).",
               "60", min=0, max=365, unit="ngày"),
            _S("spike_threshold", "Ngưỡng báo video hot", "int",
               "Video TikTok đang theo dõi tăng X view/ngày thì báo lên xu hướng.",
               "10000", min=100),
        ],
        "jobs": [
            ("morning_report", "Báo cáo sáng 7h"),
            ("revenue_report", "Báo cáo doanh thu"),
            ("birthday", "Tặng quà sinh nhật"),
            ("followup_24h", "Hỏi thăm sau 24h"),
            ("review_nudge", "Nhắc đánh giá"),
            ("loan_remind", "Nhắc nợ 9h sáng"),
            ("stock_recheck", "Quét lại kho"),
            ("stock_backup", "Backup tồn kho 3h"),
            ("db_backup", "Backup DB 4h"),
            ("backup_telegram", "Gửi backup qua Telegram"),
            ("clean_stock", "Dọn kho cũ"),
            ("stale_stock", "Cảnh báo hàng tồn lâu"),
            ("cookie_clean", "Dọn cookie hết hạn"),
            ("supplier_import", "Nhập kho NCC tự động"),
            ("stall_auto_import", "Nhập kho sạp tự động"),
            ("fraud_scan", "Quét gian lận"),
            ("warranty_remind", "Nhắc bảo hành"),
            ("consign_release", "Giải ngân ký gửi"),
            ("consign_dispute_auto", "Tự xử lý tranh chấp"),
            ("consign_sale_notify", "Báo đơn ký gửi mới"),
            ("sheet_consign_wh", "Đồng bộ Sheet ký gửi"),
            ("sheet_linkwh", "Đồng bộ Sheet link"),
            ("sheet_sold_push", "Đẩy đơn bán lên Sheet"),
        ],
    },
    {
        "id": "notify", "title": "🔔 Bot báo tin",
        "desc": "Mỗi kênh thông báo đi về 1 bot riêng — xem ở màn hình riêng.",
        "custom": "notify_panel",
    },
]

_GROUP_BY_ID = {g["id"]: g for g in SETTING_GROUPS}
_SPEC_BY_KEY = {}
for _g in SETTING_GROUPS:
    for _s in _g.get("settings", []):
        _SPEC_BY_KEY[_s["key"]] = (_g["id"], _s)


# ─────────────────────────────────────────────────────────────
# Đọc / hiển thị / parse giá trị
# ─────────────────────────────────────────────────────────────

def _raw(key: str, default: str = "") -> str:
    try:
        return str(db.get_setting(key, default) or "")
    except Exception:
        return default


def _fmt_money(raw: str) -> str:
    try:
        return f"{_vnd(int(float(str(raw).strip() or 0)))}đ"
    except Exception:
        return (raw or "0") + "đ"


def _fmt(spec: dict, raw: str) -> str:
    t = spec["type"]
    r = (raw or "").strip()
    if t == "toggle":
        return "🟢 Bật" if r == "1" else "🔴 Tắt"
    if t == "money":
        return _fmt_money(r)
    if t == "pct":
        return f"{r or spec['default']}%"
    if t == "ratio":
        try:
            return f"{float(r or spec['default']) * 100:.0f}%"
        except Exception:
            return r
    if t == "packs":
        try:
            packs = json.loads(r) if r else []
            if packs:
                return ", ".join(
                    f"{p.get('credits')}/{_vnd(int(p.get('price', 0)))}đ"
                    for p in packs)
        except Exception:
            pass
        return "mặc định (500/50k, 900/200k, 2000/350k)"
    if t == "banks":
        try:
            banks = json.loads(r) if r else []
            if banks:
                return f"{len(banks)} ngân hàng: " + ", ".join(
                    str(b.get("name", "?")) for b in banks)
        except Exception:
            pass
        return "<i>dùng 1 TK ở 3 ô trên</i>"
    if t == "prizes":
        try:
            prizes = json.loads(r) if r else []
            if prizes:
                return f"{len(prizes)} giải: " + ", ".join(
                    str(p.get("label", "?")) for p in prizes)
        except Exception:
            pass
        return "<i>6 giải mặc định</i>"
    if t == "photo":
        return "🖼️ <i>đã có ảnh</i>" if r else "<i>chưa có ảnh</i>"
    if t == "select":
        for v, label in spec.get("choices", []):
            if v == r:
                return label
        return r or "—"
    if t == "int" and spec.get("unit"):
        return f"{r or spec['default']} {spec['unit']}"
    return r if r else "<i>trống</i>"


def _parse_money(text: str):
    t = (text or "").strip().lower().replace("đ", "").replace("vnd", "")
    t = t.replace(".", "").replace(",", "").replace(" ", "")
    mult = 1
    if t.endswith("tr"):
        mult, t = 1_000_000, t[:-2]
    elif t.endswith("k"):
        mult, t = 1000, t[:-1]
    try:
        v = int(float(t) * mult)
    except Exception:
        return None
    return v if v >= 0 else None


def _parse(spec: dict, text: str):
    """Trả về (ok, value_str | thông_báo_lỗi)."""
    t = spec["type"]
    txt = (text or "").strip()
    if t == "toggle":
        return False, "Bấm nút Bật/Tắt, không cần nhập."
    if t == "money":
        v = _parse_money(txt)
        if v is None:
            return False, "❌ Nhập số tiền (VD: 50000, 50k, 1tr)."
        return True, str(v)
    if t == "packs":
        try:
            packs = []
            for part in txt.split(","):
                part = part.strip()
                if not part:
                    continue
                m = re.match(r"^(\d+)\s*[:=]\s*(.+)$", part)
                if not m:
                    return False, "❌ Sai định dạng. VD: 500=50000, 900=200000"
                credits = int(m.group(1))
                price = _parse_money(m.group(2))
                if price is None or credits <= 0:
                    return False, "❌ Sai định dạng. VD: 500=50000, 900=200000"
                packs.append({"credits": credits, "price": price,
                              "label": f"{credits} lượt"})
            if not packs:
                return False, "❌ Nhập ít nhất 1 gói. VD: 500=50000"
            return True, json.dumps(packs, ensure_ascii=False)
        except Exception:
            return False, "❌ Sai định dạng. VD: 500=50000, 900=200000"
    if t in ("int", "pct"):
        v = _parse_money(txt)
        if v is None:
            return False, "❌ Nhập một số nguyên (VD: 50)."
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None and v < lo:
            return False, f"❌ Giá trị phải ≥ {lo}."
        if hi is not None and v > hi:
            return False, f"❌ Giá trị phải ≤ {hi}."
        return True, str(v)
    if t == "ratio":
        x = txt.replace("%", "").strip()
        try:
            f = float(x)
        except Exception:
            return False, "❌ Nhập % (VD: 90) hoặc thập phân (VD: 0.9)."
        if f > 1:
            f = f / 100.0
        if not (0 <= f <= 1):
            return False, "❌ Tỉ lệ phải từ 0% tới 100%."
        return True, str(round(f, 4))
    if t == "text":
        return True, txt
    return False, "❌ Loại cài đặt chưa hỗ trợ nhập tay."


# ─────────────────────────────────────────────────────────────
# Giao diện
# ─────────────────────────────────────────────────────────────

def main_text() -> str:
    return (
        "⚙️ <b>CÀI ĐẶT BOT</b>\n"
        "━━━━━━━━━━━━\n\n"
        "Mọi cấu hình của bot gom ở đây — bấm vào nhóm, chọn mục,\n"
        "nhập giá trị mới là xong, có hiệu lực <b>ngay lập tức</b>.\n\n"
        "📝 <i>Cách dùng: mỗi mục đều có 1 dòng ghi chú giải thích.</i>"
    )


def main_kb() -> InlineKeyboardMarkup:
    rows = []
    gs = SETTING_GROUPS
    for i in range(0, len(gs), 2):
        row = [InlineKeyboardButton(text=gs[i]["title"],
                                    callback_data=f"admset:g:{gs[i]['id']}")]
        if i + 1 < len(gs):
            row.append(InlineKeyboardButton(
                text=gs[i + 1]["title"],
                callback_data=f"admset:g:{gs[i + 1]['id']}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="◀️ Quay lại menu Admin",
                                      callback_data="admm:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _job_line(name: str, label: str) -> str:
    on = _raw(f"job_{name}", "1") == "1"
    return f"{'🟢' if on else '🔴'} {label}"


def group_text(gid: str) -> str:
    g = _GROUP_BY_ID[gid]
    lines = [f"<b>{g['title']}</b>", "━━━━━━━━━━━━", "", g["desc"], "",
             "Bấm vào từng mục để xem chi tiết và sửa:",
             "📝 <i>Mỗi mục có 1 dòng ghi chú cách dùng.</i>"]
    return "\n".join(lines)


def group_kb(gid: str) -> InlineKeyboardMarkup:
    g = _GROUP_BY_ID[gid]
    rows = []
    for s in g.get("settings", []):
        val = _fmt(s, _raw(s["key"], s["default"]))
        # rút gọn hiển thị: bỏ tag HTML khi đưa vào nút
        plain = re.sub(r"<[^>]+>", "", val)
        if len(plain) > 22:
            plain = plain[:21] + "…"
        rows.append([InlineKeyboardButton(
            text=f"{s['label']}: {plain}",
            callback_data=f"admset:s:{s['key']}")])
    for name, label in g.get("jobs", []):
        on = _raw(f"job_{name}", "1") == "1"
        rows.append([InlineKeyboardButton(
            text=f"{'🟢' if on else '🔴'} {label}",
            callback_data=f"admset:j:{name}")])
    rows.append([InlineKeyboardButton(text="◀️ Cài đặt",
                                      callback_data="admset:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def detail_text(key: str) -> str:
    gid, spec = _SPEC_BY_KEY[key]
    g = _GROUP_BY_ID[gid]
    cur = _fmt(spec, _raw(key, spec["default"]))
    return (
        f"⚙️ <b>{html.escape(spec['label'])}</b>\n"
        f"━━━━━━━━━━━━\n\n"
        f"📌 Giá trị hiện tại: <b>{cur}</b>\n\n"
        f"📝 <i>Cách dùng:</i> {html.escape(spec['hint'])}"
    )


def _back_to_group_kb(gid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="◀️ Quay lại", callback_data=f"admset:g:{gid}")
    ]])


def detail_kb(key: str) -> InlineKeyboardMarkup:
    gid, spec = _SPEC_BY_KEY[key]
    t = spec["type"]
    rows = []
    if t == "toggle":
        cur = _raw(key, spec["default"]) == "1"
        rows.append([
            InlineKeyboardButton(
                text="✅ 🟢 Bật" if cur else "🟢 Bật",
                callback_data=f"admset:tg:{key}:1"),
            InlineKeyboardButton(
                text="✅ 🔴 Tắt" if not cur else "🔴 Tắt",
                callback_data=f"admset:tg:{key}:0"),
        ])
    elif t == "select":
        cur = _raw(key, spec["default"])
        for v, label in spec.get("choices", []):
            mark = "✅ " if v == cur else ""
            rows.append([InlineKeyboardButton(
                text=f"{mark}{label}",
                callback_data=f"admset:ch:{key}:{v}")])
    elif t == "banks":
        rows.append([InlineKeyboardButton(
            text="➕ Thêm ngân hàng",
            callback_data="admset:banks:add")])
        rows.append([InlineKeyboardButton(
            text="🖼️ Ảnh QR từng ngân hàng",
            callback_data="admset:banks:qrlist")])
        rows.append([InlineKeyboardButton(
            text="🗑️ Xóa ngân hàng",
            callback_data="admset:banks:dellist")])
        rows.append([InlineKeyboardButton(
            text="↺ Về 1 TK mặc định (xóa hết)",
            callback_data="admset:banks:clear")])
    elif t == "prizes":
        rows.append([InlineKeyboardButton(
            text="➕ Thêm giải",
            callback_data="admset:prizes:add")])
        rows.append([InlineKeyboardButton(
            text="🗑️ Xóa giải",
            callback_data="admset:prizes:dellist")])
        rows.append([InlineKeyboardButton(
            text="↺ Về 6 giải mặc định",
            callback_data="admset:prizes:reset")])
    elif t == "photo":
        rows.append([InlineKeyboardButton(
            text="📤 Gửi ảnh mới",
            callback_data=f"admset:photo:{key}")])
        if _raw(key, spec["default"]):
            rows.append([InlineKeyboardButton(
                text="❌ Xóa ảnh",
                callback_data=f"admset:photodel:{key}")])
    else:
        rows.append([InlineKeyboardButton(
            text="✏️ Nhập giá trị mới",
            callback_data=f"admset:edit:{key}")])
    rows.append([InlineKeyboardButton(text="◀️ Quay lại",
                                      callback_data=f"admset:g:{gid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def job_text(name: str, label: str) -> str:
    on = _raw(f"job_{name}", "1") == "1"
    return (
        f"⏰ <b>{html.escape(label)}</b>\n"
        f"━━━━━━━━━━━━\n\n"
        f"📌 Trạng thái: <b>{'🟢 Đang bật' if on else '🔴 Đang tắt'}</b>\n\n"
        f"📝 <i>Cách dùng:</i> công việc tự động chạy nền của bot. "
        f"Tắt khi muốn dừng hẳn việc đó."
    )


def job_kb(name: str) -> InlineKeyboardMarkup:
    on = _raw(f"job_{name}", "1") == "1"
    rows = [[
        InlineKeyboardButton(text="✅ 🟢 Bật" if on else "🟢 Bật",
                             callback_data=f"admset:jt:{name}:1"),
        InlineKeyboardButton(text="✅ 🔴 Tắt" if not on else "🔴 Tắt",
                             callback_data=f"admset:jt:{name}:0"),
    ], [InlineKeyboardButton(text="◀️ Quay lại",
                             callback_data="admset:g:system")]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ─────────────────────────────────────────────────────────────
# Danh sách ngân hàng (banks_list) — UI riêng, không nhập JSON
# ─────────────────────────────────────────────────────────────

def _banks() -> list:
    try:
        raw = _raw("banks_list", "")
        banks = json.loads(raw) if raw else []
        return banks if isinstance(banks, list) else []
    except Exception:
        return []


def _save_banks(tg_id: int, name: str, banks: list):
    db.set_setting("banks_list", json.dumps(banks, ensure_ascii=False))
    try:
        db.admin_audit_add(tg_id, name or "", "doi_cai_dat",
                           f"Danh sách ngân hàng: {len(banks)} TK")
    except Exception:
        pass


def banks_text() -> str:
    banks = _banks()
    lines = ["🏦 <b>DANH SÁCH NGÂN HÀNG</b>", "━━━━━━━━━━━━", ""]
    if banks:
        for i, b in enumerate(banks, 1):
            qr = " 🖼️ <i>có QR</i>" if b.get("qr") else ""
            lines.append(
                f"<b>{i}.</b> {html.escape(str(b.get('name', '?')))}{qr}\n"
                f"    STK: <code>{html.escape(str(b.get('account', '')))}</code>\n"
                f"    Chủ TK: {html.escape(str(b.get('owner', '')))}")
    else:
        lines.append("<i>Chưa có — đang dùng 1 TK ở 3 ô "
                     "Tên ngân hàng / Số tài khoản / Chủ tài khoản.</i>")
    lines += ["",
              "📝 <i>Cách dùng:</i> khách nạp tay sẽ thấy đủ các TK này "
              "để chọn chuyển khoản."]
    return "\n".join(lines)


def banks_dellist_kb() -> InlineKeyboardMarkup:
    rows = []
    for i, b in enumerate(_banks()):
        rows.append([InlineKeyboardButton(
            text=f"❌ {i + 1}. {b.get('name', '?')}",
            callback_data=f"admset:banks:del:{i}")])
    rows.append([InlineKeyboardButton(text="◀️ Quay lại",
                                      callback_data="admset:s:banks_list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def banks_qrlist_kb() -> InlineKeyboardMarkup:
    rows = []
    for i, b in enumerate(_banks()):
        mark = "🖼️" if b.get("qr") else "◻️"
        rows.append([
            InlineKeyboardButton(
                text=f"{mark} {i + 1}. {b.get('name', '?')}",
                callback_data=f"admset:banks:qrset:{i}"),
            InlineKeyboardButton(
                text="❌ Xóa QR",
                callback_data=f"admset:banks:qrdel:{i}"),
        ])
    rows.append([InlineKeyboardButton(text="◀️ Quay lại",
                                      callback_data="admset:s:banks_list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def banks_qrlist_text() -> str:
    banks = _banks()
    lines = ["🖼️ <b>ẢNH QR TỪNG NGÂN HÀNG</b>", "━━━━━━━━━━━━", "",
             "Bấm vào ngân hàng để <b>gửi ảnh QR</b> mới (chụp/gửi ảnh).",
             "Ảnh QR sẽ hiện kèm STK khi khách nạp tay.", ""]
    for i, b in enumerate(banks, 1):
        st = "✅ đã có" if b.get("qr") else "◻️ chưa có"
        lines.append(f"<b>{i}.</b> {html.escape(str(b.get('name', '?')))} — {st}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Giải thưởng vòng quay (spin_prizes) — UI riêng, không nhập JSON
# ─────────────────────────────────────────────────────────────

_PRIZE_KINDS = {"credits": "🎁 Credit", "balance": "💵 Tiền ví",
                "none": "😅 Trượt"}

def _prizes() -> list:
    try:
        raw = _raw("spin_prizes", "")
        prizes = json.loads(raw) if raw else []
        return prizes if isinstance(prizes, list) else []
    except Exception:
        return []


def _save_prizes(tg_id: int, name: str, prizes: list):
    db.set_setting("spin_prizes", json.dumps(prizes, ensure_ascii=False))
    try:
        db.admin_audit_add(tg_id, name or "", "doi_cai_dat",
                           f"Giải vòng quay: {len(prizes)} giải")
    except Exception:
        pass


def prizes_text() -> str:
    prizes = _prizes()
    lines = ["🎡 <b>GIẢI THƯỞNG VÒNG QUAY</b>", "━━━━━━━━━━━━", ""]
    if prizes:
        total = sum(max(0, int(p.get("weight", 0) or 0)) for p in prizes) or 1
        for i, p in enumerate(prizes, 1):
            w = max(0, int(p.get("weight", 0) or 0))
            kind = _PRIZE_KINDS.get(p.get("kind"), str(p.get("kind", "")))
            lines.append(
                f"<b>{i}.</b> {html.escape(str(p.get('label', '?')))}\n"
                f"    {kind} — tỉ lệ ~{w * 100 // total}%")
    else:
        lines.append("<i>Chưa tự đặt — đang dùng 6 giải mặc định của bot.</i>")
    lines += ["",
              "📝 <i>Cách dùng:</i> trọng số càng cao càng dễ trúng. "
              "Tổng tỉ lệ tự chia theo trọng số."]
    return "\n".join(lines)


def prizes_dellist_kb() -> InlineKeyboardMarkup:
    rows = []
    for i, p in enumerate(_prizes()):
        label = str(p.get("label", "?"))
        if len(label) > 24:
            label = label[:23] + "…"
        rows.append([InlineKeyboardButton(
            text=f"❌ {i + 1}. {label}",
            callback_data=f"admset:prizes:del:{i}")])
    rows.append([InlineKeyboardButton(text="◀️ Quay lại",
                                      callback_data="admset:s:spin_prizes")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def prizes_kind_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(
        text=label, callback_data=f"admset:prizes:kind:{kind}")]
        for kind, label in _PRIZE_KINDS.items()]
    rows.append([InlineKeyboardButton(text="🚫 Hủy",
                                      callback_data="admset:s:spin_prizes")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ─────────────────────────────────────────────────────────────
# Lưu + audit
# ─────────────────────────────────────────────────────────────

def _save(tg_id: int, name: str, key: str, new_val: str, label: str,
          fmt_new: str = None, fmt_old: str = None):
    old_raw = _raw(key, "")
    db.set_setting(key, new_val)
    try:
        db.admin_audit_add(
            tg_id, name or "", "doi_cai_dat",
            f"{label}: {fmt_old or old_raw} → {fmt_new or new_val}")
    except Exception:
        pass


def _deny(cb: CallbackQuery) -> bool:
    return not _perms.is_super(cb.from_user.id)


# ─────────────────────────────────────────────────────────────
# Đăng ký handler
# ─────────────────────────────────────────────────────────────

def register_settings(target_router):
    @target_router.callback_query(F.data.startswith("admset:"))
    async def _on_admset_cb(cb: CallbackQuery, state: FSMContext):
        if _deny(cb):
            await cb.answer("🚫 Chỉ chủ shop mới dùng được.",
                            show_alert=True)
            return
        action = (cb.data or "")[7:]

        async def _ans(text=""):
            try:
                await cb.answer(text)
            except Exception:
                pass

        # ── Danh sách nhóm ──
        if action == "main":
            await state.clear()
            await _ans()
            await cb.message.edit_text(main_text(), parse_mode="HTML",
                                       reply_markup=main_kb())
            return

        # ── Vào nhóm ──
        if action.startswith("g:"):
            gid = action[2:]
            if gid not in _GROUP_BY_ID:
                await _ans("❌ Nhóm không tồn tại.")
                return
            if _GROUP_BY_ID[gid].get("custom") == "notify_panel":
                from . import admin_notify_panel as _np
                await state.clear()
                await _ans()
                await cb.message.edit_text(
                    _np.main_text(), parse_mode="HTML",
                    reply_markup=_np.main_kb())
                return
            await state.clear()
            await _ans()
            await cb.message.edit_text(group_text(gid), parse_mode="HTML",
                                       reply_markup=group_kb(gid))
            return

        # ── Chi tiết 1 setting ──
        if action.startswith("s:"):
            key = action[2:]
            if key not in _SPEC_BY_KEY:
                await _ans("❌ Mục không tồn tại.")
                return
            await state.clear()
            await _ans()
            # 2 danh sách đặc biệt có màn hình riêng (không nhập JSON)
            if key == "banks_list":
                await cb.message.edit_text(
                    banks_text(), parse_mode="HTML",
                    reply_markup=detail_kb(key))
                return
            if key == "spin_prizes":
                await cb.message.edit_text(
                    prizes_text(), parse_mode="HTML",
                    reply_markup=detail_kb(key))
                return
            await cb.message.edit_text(detail_text(key), parse_mode="HTML",
                                       reply_markup=detail_kb(key))
            return

        # ── Danh sách ngân hàng: thêm ──
        if action == "banks:add":
            await state.set_state(AdmSetState.banks_add)
            await _ans()
            await cb.message.edit_text(
                "🏦 <b>THÊM NGÂN HÀNG</b>\n"
                "━━━━━━━━━━━━\n\n"
                "Gửi 1 tin theo mẫu:\n"
                "<code>Tên ngân hàng | Số tài khoản | Chủ tài khoản</code>\n\n"
                "VD: <code>Vietcombank | 0123456789 | NGUYEN VAN A</code>\n\n"
                "Gõ /huy để huỷ.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="◀️ Quay lại",
                                         callback_data="admset:s:banks_list")]]))
            return

        # ── Danh sách ngân hàng: chọn xóa ──
        if action == "banks:dellist":
            if not _banks():
                await _ans("Chưa có ngân hàng nào để xóa.",
                           show_alert=True)
                return
            await _ans()
            await cb.message.edit_text(
                "🗑️ <b>XÓA NGÂN HÀNG</b>\n━━━━━━━━━━━━\n\n"
                "Bấm vào ngân hàng muốn xóa:",
                parse_mode="HTML", reply_markup=banks_dellist_kb())
            return

        # ── Danh sách ngân hàng: xóa 1 ──
        if action.startswith("banks:del:"):
            try:
                idx = int(action.split(":")[2])
            except Exception:
                await _ans("❌ Không hợp lệ.")
                return
            banks = _banks()
            if not (0 <= idx < len(banks)):
                await _ans("❌ Không tìm thấy.")
                return
            gone = banks.pop(idx)
            _save_banks(cb.from_user.id, cb.from_user.full_name or "", banks)
            await _ans(f"✅ Đã xóa {gone.get('name', '?')}")
            await cb.message.edit_text(banks_text(), parse_mode="HTML",
                                       reply_markup=detail_kb("banks_list"))
            return

        # ── Danh sách ngân hàng: xóa hết (về 1 TK mặc định) ──
        if action == "banks:clear":
            _save_banks(cb.from_user.id, cb.from_user.full_name or "", [])
            await _ans("✅ Đã về 1 TK mặc định.")
            await cb.message.edit_text(banks_text(), parse_mode="HTML",
                                       reply_markup=detail_kb("banks_list"))
            return

        # ── Ảnh QR: danh sách ──
        if action == "banks:qrlist":
            if not _banks():
                await _ans("Chưa có ngân hàng nào.", show_alert=True)
                return
            await state.clear()
            await _ans()
            await cb.message.edit_text(banks_qrlist_text(), parse_mode="HTML",
                                       reply_markup=banks_qrlist_kb())
            return

        # ── Ảnh QR: đặt cho 1 ngân hàng (chờ gửi ảnh) ──
        if action.startswith("banks:qrset:"):
            try:
                idx = int(action.split(":")[2])
            except Exception:
                await _ans("❌ Không hợp lệ.")
                return
            banks = _banks()
            if not (0 <= idx < len(banks)):
                await _ans("❌ Không tìm thấy.")
                return
            await state.update_data(bank_qr_idx=idx)
            await state.set_state(AdmSetState.bank_qr)
            await _ans()
            await cb.message.edit_text(
                f"🖼️ <b>ĐẶT ẢNH QR</b>\n━━━━━━━━━━━━\n\n"
                f"Ngân hàng: <b>{html.escape(str(banks[idx].get('name', '?')))}</b>\n\n"
                f"Gửi <b>ảnh QR</b> vào đây (chụp màn hình hoặc gửi file ảnh).\n\n"
                f"Gõ /huy để huỷ.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="◀️ Quay lại",
                                         callback_data="admset:banks:qrlist")]]))
            return

        # ── Ảnh QR: xóa của 1 ngân hàng ──
        if action.startswith("banks:qrdel:"):
            try:
                idx = int(action.split(":")[2])
            except Exception:
                await _ans("❌ Không hợp lệ.")
                return
            banks = _banks()
            if not (0 <= idx < len(banks)):
                await _ans("❌ Không tìm thấy.")
                return
            banks[idx].pop("qr", None)
            _save_banks(cb.from_user.id, cb.from_user.full_name or "", banks)
            await _ans("✅ Đã xóa ảnh QR.")
            await cb.message.edit_text(banks_qrlist_text(), parse_mode="HTML",
                                       reply_markup=banks_qrlist_kb())
            return

        # ── Vòng quay: thêm giải (bước 1: tên) ──
        if action == "prizes:add":
            await state.set_state(AdmSetState.prize_label)
            await _ans()
            await cb.message.edit_text(
                "🎡 <b>THÊM GIẢI THƯỞNG</b> — bước 1/4\n"
                "━━━━━━━━━━━━\n\n"
                "Gửi <b>tên giải</b> (khách sẽ nhìn thấy):\n"
                "VD: <code>🎁 +10 credits</code>\n\n"
                "Gõ /huy để huỷ.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="◀️ Quay lại",
                                         callback_data="admset:s:spin_prizes")]]))
            return

        # ── Vòng quay: chọn loại giải (bước 2) ──
        if action.startswith("prizes:kind:"):
            kind = action.split(":")[2]
            if kind not in _PRIZE_KINDS:
                await _ans("❌ Loại không hợp lệ.")
                return
            data = await state.get_data()
            if not data.get("prize_label"):
                await _ans("❌ Phiên đã hết hạn, làm lại nhé.")
                await state.clear()
                return
            await state.update_data(prize_kind=kind)
            await _ans()
            if kind == "none":
                # giải trượt: giá trị = 0, hỏi thẳng trọng số
                await state.update_data(prize_value=0)
                await state.set_state(AdmSetState.prize_weight)
                await cb.message.edit_text(
                    f"🎡 <b>THÊM GIẢI</b> — bước 4/4\n"
                    f"━━━━━━━━━━━━\n\n"
                    f"Giải: {html.escape(data['prize_label'])}\n"
                    f"Loại: {_PRIZE_KINDS[kind]} (không có giá trị)\n\n"
                    f"Gửi <b>trọng số</b> (số càng cao càng dễ trúng):\n"
                    f"VD: <code>20</code>\n\nGõ /huy để huỷ.",
                    parse_mode="HTML")
            else:
                await state.set_state(AdmSetState.prize_value)
                unit = "credit" if kind == "credits" else "đồng"
                await cb.message.edit_text(
                    f"🎡 <b>THÊM GIẢI</b> — bước 3/4\n"
                    f"━━━━━━━━━━━━\n\n"
                    f"Giải: {html.escape(data['prize_label'])}\n"
                    f"Loại: {_PRIZE_KINDS[kind]}\n\n"
                    f"Gửi <b>giá trị</b> ({unit}, VD: <code>10</code>):\n\n"
                    f"Gõ /huy để huỷ.",
                    parse_mode="HTML")
            return

        # ── Vòng quay: chọn xóa ──
        if action == "prizes:dellist":
            if not _prizes():
                await _ans("Chưa có giải nào để xóa.",
                           show_alert=True)
                return
            await _ans()
            await cb.message.edit_text(
                "🗑️ <b>XÓA GIẢI THƯỞNG</b>\n━━━━━━━━━━━━\n\n"
                "Bấm vào giải muốn xóa:",
                parse_mode="HTML", reply_markup=prizes_dellist_kb())
            return

        # ── Vòng quay: xóa 1 ──
        if action.startswith("prizes:del:"):
            try:
                idx = int(action.split(":")[2])
            except Exception:
                await _ans("❌ Không hợp lệ.")
                return
            prizes = _prizes()
            if not (0 <= idx < len(prizes)):
                await _ans("❌ Không tìm thấy.")
                return
            gone = prizes.pop(idx)
            _save_prizes(cb.from_user.id, cb.from_user.full_name or "",
                         prizes)
            await _ans(f"✅ Đã xóa {gone.get('label', '?')}")
            await cb.message.edit_text(prizes_text(), parse_mode="HTML",
                                       reply_markup=detail_kb("spin_prizes"))
            return

        # ── Vòng quay: về mặc định ──
        if action == "prizes:reset":
            _save_prizes(cb.from_user.id, cb.from_user.full_name or "", [])
            await _ans("✅ Đã về 6 giải mặc định.")
            await cb.message.edit_text(prizes_text(), parse_mode="HTML",
                                       reply_markup=detail_kb("spin_prizes"))
            return

        # ── Ảnh (kiểu photo): chờ gửi ảnh ──
        if action.startswith("photo:"):
            key = action[6:]
            if key not in _SPEC_BY_KEY or _SPEC_BY_KEY[key][1]["type"] != "photo":
                await _ans("❌ Mục không tồn tại.")
                return
            gid, spec = _SPEC_BY_KEY[key]
            await state.update_data(photo_key=key)
            await state.set_state(AdmSetState.photo)
            await _ans()
            await cb.message.edit_text(
                f"🖼️ <b>{html.escape(spec['label'])}</b>\n"
                f"━━━━━━━━━━━━\n\n"
                f"Gửi <b>ảnh</b> vào đây.\n\n"
                f"Gõ /huy để huỷ.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="◀️ Quay lại",
                                         callback_data=f"admset:s:{key}")]]))
            return

        # ── Ảnh (kiểu photo): xóa ──
        if action.startswith("photodel:"):
            key = action[9:]
            if key not in _SPEC_BY_KEY or _SPEC_BY_KEY[key][1]["type"] != "photo":
                await _ans("❌ Mục không tồn tại.")
                return
            gid, spec = _SPEC_BY_KEY[key]
            _save(cb.from_user.id, cb.from_user.full_name or "", key, "",
                  spec["label"], fmt_new="đã xóa",
                  fmt_old=_fmt(spec, _raw(key, spec["default"])))
            await _ans("✅ Đã xóa ảnh.")
            await cb.message.edit_text(detail_text(key), parse_mode="HTML",
                                       reply_markup=detail_kb(key))
            return

        # ── Bật/tắt nhanh ──
        if action.startswith("tg:"):
            _, key, val = action.split(":", 2)
            if key not in _SPEC_BY_KEY:
                await _ans("❌ Mục không tồn tại.")
                return
            gid, spec = _SPEC_BY_KEY[key]
            new_val = "1" if val == "1" else "0"
            _save(cb.from_user.id, cb.from_user.full_name or "", key,
                  new_val, spec["label"],
                  fmt_new="🟢 Bật" if new_val == "1" else "🔴 Tắt",
                  fmt_old=_fmt(spec, _raw(key, spec["default"])))
            await _ans("✅ Đã lưu!")
            await cb.message.edit_text(detail_text(key), parse_mode="HTML",
                                       reply_markup=detail_kb(key))
            return

        # ── Chọn 1 trong nhiều (select) ──
        if action.startswith("ch:"):
            _, key, val = action.split(":", 2)
            if key not in _SPEC_BY_KEY:
                await _ans("❌ Mục không tồn tại.")
                return
            gid, spec = _SPEC_BY_KEY[key]
            valid = [v for v, _ in spec.get("choices", [])]
            if val not in valid:
                await _ans("❌ Lựa chọn không hợp lệ.")
                return
            _save(cb.from_user.id, cb.from_user.full_name or "", key,
                  val, spec["label"],
                  fmt_new=_fmt(spec, val),
                  fmt_old=_fmt(spec, _raw(key, spec["default"])))
            await _ans("✅ Đã lưu!")
            await cb.message.edit_text(detail_text(key), parse_mode="HTML",
                                       reply_markup=detail_kb(key))
            return

        # ── Xin nhập giá trị mới ──
        if action.startswith("edit:"):
            key = action[5:]
            if key not in _SPEC_BY_KEY:
                await _ans("❌ Mục không tồn tại.")
                return
            gid, spec = _SPEC_BY_KEY[key]
            await state.update_data(set_key=key)
            await state.set_state(AdmSetState.value)
            await _ans()
            cur = _fmt(spec, _raw(key, spec["default"]))
            ph = spec.get("ph")
            await cb.message.edit_text(
                f"✏️ <b>{html.escape(spec['label'])}</b>\n"
                f"━━━━━━━━━━━━\n\n"
                f"📌 Hiện tại: <b>{cur}</b>\n"
                f"📝 {html.escape(spec['hint'])}\n\n"
                + (f"💡 <i>Gợi ý:</i> {html.escape(ph)}\n\n" if ph else "")
                + "Gửi giá trị mới vào đây.\nGõ /huy để huỷ.",
                parse_mode="HTML",
                reply_markup=_back_to_group_kb(gid))
            return

        # ── Chi tiết 1 job ──
        if action.startswith("j:"):
            name = action[2:]
            g = _GROUP_BY_ID["system"]
            label = dict(g.get("jobs", [])).get(name)
            if not label:
                await _ans("❌ Job không tồn tại.")
                return
            await state.clear()
            await _ans()
            await cb.message.edit_text(job_text(name, label),
                                       parse_mode="HTML",
                                       reply_markup=job_kb(name))
            return

        # ── Bật/tắt job ──
        if action.startswith("jt:"):
            _, name, val = action.split(":", 2)
            g = _GROUP_BY_ID["system"]
            label = dict(g.get("jobs", [])).get(name)
            if not label:
                await _ans("❌ Job không tồn tại.")
                return
            new_val = "1" if val == "1" else "0"
            old = "🟢 Bật" if _raw(f"job_{name}", "1") == "1" else "🔴 Tắt"
            _save(cb.from_user.id, cb.from_user.full_name or "",
                  f"job_{name}", new_val, f"Job: {label}",
                  fmt_new="🟢 Bật" if new_val == "1" else "🔴 Tắt",
                  fmt_old=old)
            await _ans("✅ Đã lưu!")
            await cb.message.edit_text(job_text(name, label),
                                       parse_mode="HTML",
                                       reply_markup=job_kb(name))
            return

        await _ans()

    @target_router.message(AdmSetState.value)
    async def _on_admset_value(msg: Message, state: FSMContext):
        if not _perms.is_super(msg.from_user.id):
            await state.clear()
            return
        txt = (msg.text or "").strip()
        if txt.lower() in ("/huy", "/cancel"):
            data = await state.get_data()
            key = data.get("set_key")
            await state.clear()
            if key and key in _SPEC_BY_KEY:
                gid, _ = _SPEC_BY_KEY[key]
                await msg.answer("🚫 Đã huỷ, không đổi gì.",
                                 reply_markup=_back_to_group_kb(gid))
            else:
                await msg.answer("🚫 Đã huỷ.")
            return
        data = await state.get_data()
        key = data.get("set_key")
        if not key or key not in _SPEC_BY_KEY:
            await state.clear()
            await msg.answer("❌ Phiên đã hết hạn, vào lại ⚙️ Cài đặt nhé.")
            return
        gid, spec = _SPEC_BY_KEY[key]
        ok, val = _parse(spec, txt)
        if not ok:
            await msg.answer(f"{val}\n\nGửi lại giá trị khác hoặc /huy để huỷ.")
            return
        _save(msg.from_user.id, msg.from_user.full_name or "", key,
              val, spec["label"],
              fmt_new=_fmt(spec, val),
              fmt_old=_fmt(spec, _raw(key, spec["default"])))
        await state.clear()
        await msg.answer(
            f"✅ <b>Đã lưu {html.escape(spec['label'])}</b>\n\n"
            f"📌 Giá trị mới: <b>{_fmt(spec, val)}</b>\n"
            f"<i>Có hiệu lực ngay.</i>",
            parse_mode="HTML",
            reply_markup=_back_to_group_kb(gid))

    async def _cancel_flow(msg: Message, state: FSMContext,
                           back_cb: str) -> bool:
        """Trả True nếu user gõ /huy (đã xử lý xong)."""
        if (msg.text or "").strip().lower() in ("/huy", "/cancel"):
            await state.clear()
            await msg.answer(
                "🚫 Đã huỷ, không đổi gì.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="◀️ Quay lại",
                                         callback_data=back_cb)]]))
            return True
        return False

    @target_router.message(AdmSetState.banks_add)
    async def _on_banks_add(msg: Message, state: FSMContext):
        if not _perms.is_super(msg.from_user.id):
            await state.clear()
            return
        if await _cancel_flow(msg, state, "admset:s:banks_list"):
            return
        parts = [(p or "").strip() for p in (msg.text or "").split("|")]
        if len(parts) != 3 or not all(parts):
            await msg.answer(
                "❌ Sai mẫu. Nhập đúng dạng:\n"
                "<code>Tên ngân hàng | Số tài khoản | Chủ tài khoản</code>\n\n"
                "VD: <code>Vietcombank | 0123456789 | NGUYEN VAN A</code>\n\n"
                "Gõ /huy để huỷ.", parse_mode="HTML")
            return
        banks = _banks()
        banks.append({"name": parts[0], "account": parts[1],
                      "owner": parts[2]})
        _save_banks(msg.from_user.id, msg.from_user.full_name or "", banks)
        await state.clear()
        await msg.answer(
            f"✅ <b>Đã thêm {html.escape(parts[0])}</b> "
            f"({len(banks)} ngân hàng).",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀️ Danh sách ngân hàng",
                                     callback_data="admset:s:banks_list")]]))

    @target_router.message(AdmSetState.bank_qr)
    async def _on_bank_qr(msg: Message, state: FSMContext):
        if not _perms.is_super(msg.from_user.id):
            await state.clear()
            return
        if await _cancel_flow(msg, state, "admset:banks:qrlist"):
            return
        file_id = None
        if msg.photo:
            file_id = msg.photo[-1].file_id  # ảnh nét nhất
        elif msg.document and (msg.document.mime_type or "").startswith("image/"):
            file_id = msg.document.file_id
        if not file_id:
            await msg.answer("❌ Gửi 1 <b>ảnh</b> nhé (chụp màn hình QR hoặc file ảnh).\n"
                             "Gõ /huy để huỷ.", parse_mode="HTML")
            return
        data = await state.get_data()
        idx = data.get("bank_qr_idx")
        banks = _banks()
        if idx is None or not (0 <= idx < len(banks)):
            await state.clear()
            await msg.answer("❌ Phiên đã hết hạn, làm lại nhé.")
            return
        banks[idx]["qr"] = file_id
        _save_banks(msg.from_user.id, msg.from_user.full_name or "", banks)
        await state.clear()
        await msg.answer(
            f"✅ <b>Đã đặt ảnh QR</b> cho "
            f"{html.escape(str(banks[idx].get('name', '?')))}.\n"
            f"Khách nạp tay sẽ thấy ảnh này kèm STK.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀️ Ảnh QR từng ngân hàng",
                                     callback_data="admset:banks:qrlist")]]))

    @target_router.message(AdmSetState.photo)
    async def _on_photo_msg(msg: Message, state: FSMContext):
        if not _perms.is_super(msg.from_user.id):
            await state.clear()
            return
        data = await state.get_data()
        key = data.get("photo_key")
        if not key or key not in _SPEC_BY_KEY:
            await state.clear()
            await msg.answer("❌ Phiên đã hết hạn, vào lại ⚙️ Cài đặt nhé.")
            return
        gid, spec = _SPEC_BY_KEY[key]
        if await _cancel_flow(msg, state, f"admset:s:{key}"):
            return
        file_id = None
        if msg.photo:
            file_id = msg.photo[-1].file_id
        elif msg.document and (msg.document.mime_type or "").startswith("image/"):
            file_id = msg.document.file_id
        if not file_id:
            await msg.answer("❌ Gửi 1 <b>ảnh</b> nhé.\nGõ /huy để huỷ.",
                             parse_mode="HTML")
            return
        _save(msg.from_user.id, msg.from_user.full_name or "", key,
              file_id, spec["label"],
              fmt_new="🖼️ đã có ảnh",
              fmt_old=_fmt(spec, _raw(key, spec["default"])))
        await state.clear()
        await msg.answer(
            f"✅ <b>Đã lưu ảnh {html.escape(spec['label'])}</b>.\n"
            f"<i>Có hiệu lực ngay.</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀️ Quay lại",
                                     callback_data=f"admset:s:{key}")]]))

    @target_router.message(AdmSetState.prize_label)
    async def _on_prize_label(msg: Message, state: FSMContext):
        if not _perms.is_super(msg.from_user.id):
            await state.clear()
            return
        if await _cancel_flow(msg, state, "admset:s:spin_prizes"):
            return
        label = (msg.text or "").strip()
        if not label or len(label) > 60:
            await msg.answer("❌ Tên giải từ 1–60 ký tự, gửi lại nhé.\n"
                             "Gõ /huy để huỷ.")
            return
        await state.update_data(prize_label=label)
        await state.set_state(AdmSetState.prize_kind)
        await msg.answer(
            f"🎡 <b>THÊM GIẢI</b> — bước 2/4\n━━━━━━━━━━━━\n\n"
            f"Giải: {html.escape(label)}\n\n"
            f"Chọn <b>loại giải</b>:",
            parse_mode="HTML", reply_markup=prizes_kind_kb())

    @target_router.message(AdmSetState.prize_kind)
    async def _on_prize_kind_msg(msg: Message, state: FSMContext):
        # user gửi chữ thay vì bấm nút chọn loại -> nhắc bấm nút
        if not _perms.is_super(msg.from_user.id):
            await state.clear()
            return
        if await _cancel_flow(msg, state, "admset:s:spin_prizes"):
            return
        data = await state.get_data()
        await msg.answer(
            f"👆 Bấm nút chọn <b>loại giải</b> cho "
            f"“{html.escape(data.get('prize_label', '?'))}” nhé.\n"
            f"Gõ /huy để huỷ.",
            parse_mode="HTML", reply_markup=prizes_kind_kb())

    @target_router.message(AdmSetState.prize_value)
    async def _on_prize_value(msg: Message, state: FSMContext):
        if not _perms.is_super(msg.from_user.id):
            await state.clear()
            return
        if await _cancel_flow(msg, state, "admset:s:spin_prizes"):
            return
        v = _parse_money((msg.text or "").strip())
        if v is None or v <= 0:
            await msg.answer("❌ Nhập số > 0 (VD: 10).\nGõ /huy để huỷ.")
            return
        await state.update_data(prize_value=v)
        await state.set_state(AdmSetState.prize_weight)
        await msg.answer(
            "🎡 <b>THÊM GIẢI</b> — bước 4/4\n━━━━━━━━━━━━\n\n"
            "Gửi <b>trọng số</b> (số càng cao càng dễ trúng):\n"
            "VD: <code>20</code>\n\nGõ /huy để huỷ.",
            parse_mode="HTML")

    @target_router.message(AdmSetState.prize_weight)
    async def _on_prize_weight(msg: Message, state: FSMContext):
        if not _perms.is_super(msg.from_user.id):
            await state.clear()
            return
        if await _cancel_flow(msg, state, "admset:s:spin_prizes"):
            return
        w = _parse_money((msg.text or "").strip())
        if w is None or w <= 0 or w > 1000000:
            await msg.answer("❌ Nhập số > 0 (VD: 20).\nGõ /huy để huỷ.")
            return
        data = await state.get_data()
        label = data.get("prize_label") or "?"
        kind = data.get("prize_kind") or "none"
        value = int(data.get("prize_value") or 0)
        prizes = _prizes()
        prizes.append({"label": label, "kind": kind, "value": value,
                       "weight": int(w)})
        _save_prizes(msg.from_user.id, msg.from_user.full_name or "",
                     prizes)
        await state.clear()
        await msg.answer(
            f"✅ <b>Đã thêm giải {html.escape(label)}</b> "
            f"({_PRIZE_KINDS.get(kind, kind)}, trọng số {int(w)}).",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀️ Giải thưởng vòng quay",
                                     callback_data="admset:s:spin_prizes")]]))
