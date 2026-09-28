"""Logic nghiệp vụ ký gửi acc.

10 quyết định chủ shop đã chốt (28/09/2026):
1. Đăng ký công khai. 2. Mọi gian hàng. 3. Phí kết hợp theo loại.
4. Admin quyết định giá bán. 5. Giữ tiền = thời gian BH.
6. KM cần đối tác đồng ý từng chiến dịch. 7. Hạn mức: số acc + tổng giá trị.
8. Tranh chấp bắt buộc ảnh bằng chứng. 9. Rút tiền đủ 4 yếu tố.
10. Quyền nhạy cảm chỉ chủ shop.
"""
import time
from typing import Optional

from . import db


def enabled() -> bool:
    # Mặc định TẮT cho tới khi chủ shop cấu hình xong và bật tay
    return db.get_setting("consign_enabled", "0") == "1"


def is_consignor(tg_id: int) -> bool:
    c = db.consignor_get(tg_id)
    return bool(c and c["status"] == "active")


def calc_fee(category_id: int, sell_price: int) -> tuple:
    """Phí kết hợp theo loại: cố định + %. Trả (fee_fixed, fee_pct, fee_amount)."""
    f = db.consign_fee_get(category_id)
    amt = f["fee_fixed"] + int(sell_price * (f["fee_pct"] or 0) / 100)
    return f["fee_fixed"], f["fee_pct"] or 0, amt


def suggest_price(category_id: int, floor_price: int) -> int:
    """Giá bán gợi ý = giá sàn + phí (tính trên giá sàn)."""
    f = db.consign_fee_get(category_id)
    pct = f["fee_pct"] or 0
    # giá sao cho sau khi trừ phí vẫn >= giá sàn: price = (floor + fixed) / (1 - pct/100)
    if pct >= 100:
        pct = 0
    price = (floor_price + f["fee_fixed"]) / (1 - pct / 100) if pct else (floor_price + f["fee_fixed"])
    return int(price + 0.99)


def check_limits(consignor_id: int, add_items: int = 0, add_value: int = 0) -> tuple:
    """Kiểm tra hạn mức đối tác. Trả (ok, msg)."""
    c = db.get_conn().execute("SELECT * FROM consignors WHERE id=?", (consignor_id,)).fetchone()
    if not c:
        return False, "Không tìm thấy hồ sơ"
    c = dict(c)
    if c["status"] != "active":
        return False, "Tài khoản chưa được duyệt/kích hoạt"
    # Hạn mức = 0 nghĩa là CHƯA cấu hình (user chưa chốt số) -> chặn, báo cấu hình
    if not (c["max_items"] or 0) or not (c["max_value"] or 0):
        return False, "Shop chưa cấu hình hạn mức ký gửi, bạn liên hệ admin nhé"
    cur_items = db.get_conn().execute(
        "SELECT COUNT(*) v FROM consignment_items WHERE consignor_id=? AND status IN ('listed','pending','approved')",
        (consignor_id,)).fetchone()["v"]
    if c["max_items"] > 0 and cur_items + add_items > c["max_items"]:
        return False, f"Vượt hạn mức {c['max_items']} acc đang ký gửi"
    cur_value = db.get_conn().execute(
        "SELECT COALESCE(SUM(floor_price),0) v FROM consignment_batches"
        " WHERE consignor_id=? AND status IN ('submitted','approved')",
        (consignor_id,)).fetchone()["v"]
    if c["max_value"] > 0 and cur_value + add_value > c["max_value"]:
        return False, f"Vượt hạn mức giá trị {c['max_value']:,}đ"
    return True, "OK"


def wallet_text(tg_id: int) -> str:
    c = db.consignor_get(tg_id)
    if not c:
        return "Bạn chưa đăng ký ký gửi."
    w = db.consign_wallets(c["id"])
    return (
        f"👛 <b>Ví ký gửi</b>\n\n"
        f"⏳ Đang chờ bảo hành: <b>{w['pending']:,}đ</b>\n"
        f"✅ Khả dụng (rút được): <b>{w['avail']:,}đ</b>\n"
        f"💸 Đang rút: <b>{w['withdrawing']:,}đ</b>\n"
        f"🔒 Bị giữ (tranh chấp): <b>{w['held']:,}đ</b>"
    )


def batch_summary_text(b: dict) -> str:
    st_map = {"draft": "📝 Nháp", "submitted": "⏳ Chờ duyệt", "approved": "✅ Đã duyệt",
              "rejected": "❌ Từ chối", "listed": "🛒 Đang bán", "closed": "🔒 Đóng"}
    return (
        f"<b>{b['code']}</b> — {st_map.get(b['status'], b['status'])}\n"
        f"🏪 {b['stall'] or ''} | {b['total_items']} acc\n"
        f"💰 Giá sàn: {b['floor_price']:,}đ"
        + (f" → Giá bán: {b['sell_price']:,}đ" if b["sell_price"] else "")
        + f"\n🛡️ BH: {b['warranty_days']} ngày"
    )


async def notify(tg_id: int, text: str, bot=None):
    """Gửi tin cho đối tác, không để lỗi làm chết flow."""
    if not bot:
        return
    try:
        await bot.send_message(tg_id, text, parse_mode="HTML")
    except Exception:
        pass


def audit(by_id: int, by_name: str, action: str, detail: str = ""):
    try:
        db.admin_audit_add(by_id, by_name, f"consign:{action}", detail)
    except Exception:
        pass
