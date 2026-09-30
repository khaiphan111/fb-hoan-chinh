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


async def notify(tg_id: int, text: str, bot=None, consignor_id: int = 0, kind: str = "", ref_id: int = 0):
    """Gửi tin cho đối tác, không để lỗi làm chết flow. Ghi log vào DB."""
    if not bot:
        return False
    try:
        await bot.send_message(tg_id, text, parse_mode="HTML")
        if consignor_id:
            db.consign_notif_log(consignor_id, kind, ref_id, "main", True)
        return True
    except Exception as e:
        print(f"[CONSIGN-NOTIFY] Gửi tin cho {tg_id} thất bại: {e}", flush=True)
        if consignor_id:
            db.consign_notif_log(consignor_id, kind, ref_id, "main", False, str(e))
        return False


async def notify_consignor_on_sale(order_id: int, bot) -> bool:
    """Báo đối tác khi acc ký gửi bán được. Dùng bot riêng nếu đã cấu hình,
    fallback bot chính. Trả True nếu đã gửi (qua bot nào đó).
    Idempotency: chỉ gửi 1 lần theo consignment_orders.notified."""
    try:
        sale_info = db.consign_get_sale_info(f"ACC-{order_id}")
        if not sale_info or not (sale_info.get("notify_sale", 1) or 0):
            return False
        # Đã báo rồi thì thôi (chống gửi trùng khi retry)
        if sale_info.get("notified"):
            return True
        txt = (
            f"💰 <b>Acc ký gửi của bạn vừa bán được!</b>\n"
            f"🧾 Đơn: <b>{sale_info['order_ref']}</b>\n"
            f"💵 Giá bán: <b>{sale_info['sell_price']:,}đ</b>\n"
            f"💸 Phí shop: <b>{sale_info['fee_amount']:,}đ</b>\n"
            f"✅ Tiền về ví (chờ BH): <b>{sale_info['net_amount']:,}đ</b>\n"
            f"⏳ Hết BH {sale_info['warranty_days']} ngày sẽ khả dụng."
        )
        ptoken = (sale_info.get("notify_bot_token") or "").strip()
        sent = False
        via = "main"
        if ptoken:
            ok, _ = await notify_via_partner_bot(sale_info["consignor_tg_id"], txt, ptoken)
            sent = ok
            via = "partner"
        if not sent:
            sent = await notify(sale_info["consignor_tg_id"], txt, bot,
                                sale_info.get("consignor_id", 0), "sale", order_id)
        else:
            db.consign_notif_log(sale_info.get("consignor_id", 0), "sale", order_id, via, True)
        # Đánh dấu đã báo (idempotency)
        try:
            db.consign_mark_notified(f"ACC-{order_id}")
        except Exception as e:
            print(f"[CONSIGN-NOTIFY] Mark notified ACC-{order_id} thất bại: {e}", flush=True)
        return sent
    except Exception as e:
        print(f"[CONSIGN-NOTIFY] notify_consignor_on_sale #{order_id} thất bại: {e}", flush=True)
        return False


async def notify_via_partner_bot(tg_id: int, text: str, bot_token: str) -> tuple:
    """Gửi tin qua bot riêng của đối tác (token do đối tác cấu hình).
    Trả (ok, err): ok=True nếu gửi thành công."""
    if not bot_token:
        return False, "chưa cấu hình bot"
    try:
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode
        b = Bot(token=bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        try:
            await b.send_message(tg_id, text, parse_mode="HTML")
            return True, ""
        finally:
            await b.session.close()
    except Exception as e:
        return False, str(e)[:100]


async def validate_bot_token(bot_token: str) -> tuple:
    """Kiểm tra token bot hợp lệ qua getMe. Trả (ok, username_or_err)."""
    bot_token = (bot_token or "").strip()
    if not bot_token or ":" not in bot_token:
        return False, "Token không đúng định dạng (phải có dạng 123456:ABC...)"
    try:
        from aiogram import Bot
        b = Bot(token=bot_token)
        try:
            me = await b.get_me()
            return True, f"@{me.username}" if me.username else me.first_name
        finally:
            await b.session.close()
    except Exception:
        return False, "Token không hợp lệ hoặc không kết nối được Telegram. Kiểm tra lại token nhé."


def audit(by_id: int, by_name: str, action: str, detail: str = ""):
    try:
        db.admin_audit_add(by_id, by_name, f"consign:{action}", detail)
    except Exception:
        pass
