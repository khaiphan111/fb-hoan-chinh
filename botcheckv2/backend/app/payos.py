"""Tích hợp PayOS — nạp tiền tự động.

Luồng hoạt động (không cần webhook public, không cần public URL):
  1. User gõ /nap <số tiền> -> tạo payment link qua PayOS API, lưu đơn vào DB.
  2. User quét QR / mở link thanh toán, chuyển khoản.
  3. Vòng poll nền (mỗi 20s) hỏi trạng thái đơn qua PayOS API.
     Đơn PAID -> quyết toán nguyên tử (db.settle_payos_order),
     báo cho user + admin, kiểm tra lên VIP.
  4. Đơn quá 45 phút chưa thanh toán -> tự hủy qua API, đánh dấu EXPIRED.

Ngoài ra có sẵn handler webhook đã verify chữ ký
(POST /payos/webhook) để dùng khi backend có public HTTPS sau này.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import random
import time

import httpx

log = logging.getLogger("payos")

API_BASE = "https://api-merchant.payos.vn"
POLL_INTERVAL = 20          # giây giữa 2 lần quét đơn chờ
ORDER_TTL = 45 * 60         # đơn chờ tối đa 45 phút
PAYOS_MIN_AMOUNT = 2000     # số tiền tối thiểu PayOS chấp nhận


class PayOSError(Exception):
    pass


# ---------------------------------------------------------------- ký HMAC
def _sign_create_payment(data: dict, checksum_key: str) -> str:
    """Ký request tạo payment link.

    Chuẩn PayOS: template cố định đúng thứ tự
    amount=..&cancelUrl=..&description=..&orderCode=..&returnUrl=..
    """
    raw = (
        f"amount={data['amount']}"
        f"&cancelUrl={data['cancelUrl']}"
        f"&description={data['description']}"
        f"&orderCode={data['orderCode']}"
        f"&returnUrl={data['returnUrl']}"
    )
    return hmac.new(
        checksum_key.encode(), raw.encode(), hashlib.sha256
    ).hexdigest()


def _canon_value(v):
    if v is None:
        return ""
    if isinstance(v, dict):
        inner = "&".join(f"{k}={_canon_value(v[k])}" for k in sorted(v.keys()))
        return "{" + inner + "}"
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(_canon_value(x) for x in v) + "]"
    return str(v)


def sign_object(data: dict, checksum_key: str) -> str:
    """Ký/verify generic: key sắp xếp a->z, nối key=value bằng &."""
    raw = "&".join(f"{k}={_canon_value(data[k])}" for k in sorted(data.keys()))
    return hmac.new(
        checksum_key.encode(), raw.encode(), hashlib.sha256
    ).hexdigest()


def verify_webhook_signature(payload: dict, checksum_key: str) -> bool:
    sig = payload.get("signature", "")
    data = payload.get("data") or {}
    if not sig or not isinstance(data, dict):
        return False
    expected = sign_object(data, checksum_key)
    return hmac.compare_digest(expected, sig)


# ---------------------------------------------------------------- cấu hình
def _get_db():
    from . import db
    return db


def get_creds() -> tuple:
    """(client_id, api_key, checksum_key) — chuỗi rỗng nếu chưa cấu hình."""
    db = _get_db()
    return (
        db.get_setting("payos_client_id", "").strip(),
        db.get_setting("payos_api_key", "").strip(),
        db.get_setting("payos_checksum_key", "").strip(),
    )


def is_configured() -> bool:
    return all(get_creds())


def get_return_urls() -> tuple:
    db = _get_db()
    ret = db.get_setting("payos_return_url", "https://t.me/check_live_diebot").strip()
    cancel = db.get_setting("payos_cancel_url", "https://t.me/check_live_diebot").strip()
    return ret or "https://t.me/check_live_diebot", cancel or "https://t.me/check_live_diebot"


def new_order_code(tg_id: int = 0) -> int:
    # Tối đa 15 chữ số (an toàn dưới 2^53 của PayOS):
    # 10 số giây hiện tại + 4 số cuối tg_id + 1 số ngẫu nhiên.
    # 2 user cùng giây hiếm khi trùng cả 4 số cuối tg_id + số ngẫu nhiên.
    return (int(time.time()) % 10_000_000_000) * 100_000 \
        + (abs(int(tg_id)) % 10_000) * 10 + random.randint(0, 9)


# ---------------------------------------------------------------- API calls
def _headers(client_id: str, api_key: str, signature: str | None = None) -> dict:
    h = {"x-client-id": client_id, "x-api-key": api_key,
         "Content-Type": "application/json"}
    if signature:
        h["x-signature"] = signature
    return h


async def create_payment_link(order_code: int, amount: int, description: str,
                              return_url: str, cancel_url: str) -> dict:
    client_id, api_key, checksum_key = get_creds()
    if not all([client_id, api_key, checksum_key]):
        raise PayOSError("PayOS chưa được cấu hình (thiếu Client ID / API Key / Checksum Key).")
    body = {
        "orderCode": order_code,
        "amount": amount,
        "description": description,
        "returnUrl": return_url,
        "cancelUrl": cancel_url,
    }
    # PayOS yêu cầu chữ ký HMAC nằm TRONG body (field "signature"),
    # ký trên chuỗi: amount=..&cancelUrl=..&description=..&orderCode=..&returnUrl=..
    body["signature"] = _sign_create_payment(body, checksum_key)
    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.post(
            f"{API_BASE}/v2/payment-requests",
            headers=_headers(client_id, api_key),
            json=body,
        )
    try:
        resp = r.json()
    except Exception:
        raise PayOSError(f"PayOS trả về không phải JSON (HTTP {r.status_code}).")
    if resp.get("code") != "00" or not resp.get("data"):
        raise PayOSError(f"PayOS từ chối: {resp.get('desc') or resp.get('code')}")
    return resp["data"]


async def get_payment_info(order_code: int) -> dict:
    client_id, api_key, _ = get_creds()
    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.get(
            f"{API_BASE}/v2/payment-requests/{order_code}",
            headers=_headers(client_id, api_key),
        )
    resp = r.json()
    if resp.get("code") != "00" or not resp.get("data"):
        raise PayOSError(f"Không đọc được trạng thái đơn: {resp.get('desc') or resp.get('code')}")
    return resp["data"]


async def cancel_payment_link(order_code: int, reason: str = "User hủy") -> dict:
    client_id, api_key, _ = get_creds()
    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.post(
            f"{API_BASE}/v2/payment-requests/{order_code}/cancel",
            headers=_headers(client_id, api_key),
            json={"cancellationReason": reason[:200]},
        )
    resp = r.json()
    if resp.get("code") not in ("00", "20"):
        raise PayOSError(f"Hủy đơn thất bại: {resp.get('desc') or resp.get('code')}")
    return resp.get("data") or {}


# ---------------------------------------------------------------- cộng tiền
async def _notify_paid(tg_id: int, amount: int, order_code: int):
    """Báo user + admin khi đơn được thanh toán."""
    from .bot import manager, vnd
    from . import db
    msg_text = (
        "✅ <b>NẠP TIỀN THÀNH CÔNG</b>\n\n"
        f"💰 Số tiền: <b>{vnd(amount)}</b>\n"
        f"🧾 Mã đơn: <code>{order_code}</code>\n"
        "⚡ Tiền đã được cộng tự động qua PayOS.\n"
        "Cảm ơn bạn đã sử dụng dịch vụ!"
    )
    try:
        upgraded, new_vip, is_lifetime = db.check_vip_upgrade(tg_id)
    except Exception:
        upgraded, new_vip, is_lifetime = False, 0, False
    if upgraded and new_vip > 0:
        try:
            limit = db.get_setting(f"vip{new_vip}_limit", "10")
        except Exception:
            limit = "10"
        msg_text += (
            f"\n\n🎉 <b>CHÚC MỪNG BẠN ĐÃ LÊN VIP {new_vip}!</b> 🎉\n\n"
            "🎁 <b>Đặc quyền mới:</b>\n"
            f"- Mức độ theo dõi tối đa: <b>{limit} mục</b>/nền tảng\n"
        )
        msg_text += "- Hạn sử dụng: <b>VĨNH VIỄN</b>\n\n" if is_lifetime else "\n"
    if manager.running and manager.bot:
        try:
            await manager.bot.send_message(tg_id, msg_text, parse_mode="HTML")
        except Exception as e:
            log.error("Không gửi được tin báo nạp cho %s: %s", tg_id, e)
    # Báo admin qua bot admin
    try:
        from .admin_bot import manager as admin_manager
        sender = admin_manager.bot if getattr(admin_manager, "running", False) else None
        if not sender and manager.running:
            sender = manager.bot
        if sender:
            admins = []
            for key in ("admin_tg_id", "admin_tg_group_id"):
                try:
                    v = db.get_setting(key, "")
                    if v:
                        admins.append(int(v))
                except Exception:
                    pass
            for admin_id in admins:
                try:
                    await sender.send_message(
                        admin_id,
                        "💳 <b>PAYOS: KHÁCH NẠP TIỀN TỰ ĐỘNG</b>\n\n"
                        f"🆔 ID: <code>{tg_id}</code>\n"
                        f"💰 Số tiền: <b>{vnd(amount)}</b>\n"
                        f"🧾 Mã đơn: <code>{order_code}</code>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
    except Exception as e:
        log.error("Không báo admin được: %s", e)


async def settle_order(order: dict) -> bool:
    """Quyết toán 1 đơn đã PAID. Trả True nếu quyết toán thành công.

    db.settle_payos_order() làm tất cả trong 1 transaction duy nhất:
    PENDING/EXPIRED -> PAID + cộng balance/total_topup + hoa hồng F1/F2.
    Webhook và poller gọi trùng nhau -> bên thua nhận {'ok': False}, bỏ qua,
    không có cộng thừa nên không cần rollback.
    """
    from . import db
    order_code = int(order["order_code"])
    tg_id = int(order["tg_id"])
    amount = int(order["amount"])
    try:
        res = db.settle_payos_order(order_code, tg_id, amount)
    except Exception as e:
        log.error("settle_payos_order thất bại cho đơn %s: %s", order_code, e)
        return False
    if not res.get("ok"):
        log.warning("PayOS: đơn %s đã được xử lý trước đó, bỏ qua", order_code)
        return False
    for key, level in (("f1", 1), ("f2", 2)):
        info = res.get(key)
        if info:
            db.notify_commission_bonus(info[0], info[1], level)
    log.info("PayOS: đã cộng %s cho user %s (đơn %s)", amount, tg_id, order_code)
    await _notify_paid(tg_id, amount, order_code)
    return True


async def check_pending_once() -> dict:
    """Quét 1 lượt các đơn chờ. Trả về thống kê."""
    from . import db
    stats = {"paid": 0, "cancelled": 0, "expired": 0, "errors": 0}
    if not is_configured():
        return stats
    now = int(time.time())
    for row in db.get_pending_payos_orders(ORDER_TTL):
        order = dict(row)
        order_code = int(order["order_code"])
        try:
            info = await get_payment_info(order_code)
        except Exception as e:
            log.warning("Không đọc được đơn %s: %s", order_code, e)
            stats["errors"] += 1
            continue
        status = (info.get("status") or "").upper()
        try:
            if status == "PAID":
                if await settle_order(order):
                    stats["paid"] += 1
            elif status in ("CANCELLED", "EXPIRED"):
                db.mark_payos_status(order_code, status)
                stats["cancelled"] += 1
            else:  # PENDING / PROCESSING
                if now - int(order["created_at"]) > ORDER_TTL:
                    try:
                        await cancel_payment_link(order_code, "Hết hạn thanh toán")
                    except Exception:
                        pass
                    db.mark_payos_status(order_code, "EXPIRED")
                    stats["expired"] += 1
                else:
                    db.touch_payos_order(order_code)
        except Exception as e:
            log.error("Xử lý đơn %s lỗi: %s", order_code, e)
            stats["errors"] += 1
    # Đơn PENDING quá TTL: get_pending_payos_orders() không trả về nữa nên quét
    # riêng ở đây, đánh dấu EXPIRED để vòng "kiểm tra lần cuối" bên dưới vẫn
    # bắt được trường hợp khách trả trễ.
    for row in db.get_overdue_pending_payos_orders(ORDER_TTL):
        order = dict(row)
        order_code = int(order["order_code"])
        try:
            try:
                await cancel_payment_link(order_code, "Hết hạn thanh toán")
            except Exception:
                pass
            if db.mark_payos_expired_if_pending(order_code):
                stats["expired"] += 1
        except Exception as e:
            log.error("Xử lý đơn quá hạn %s lỗi: %s", order_code, e)
            stats["errors"] += 1
    # Kiểm tra lần cuối đơn EXPIRED trong 24h: khách trả trễ nhưng PayOS vẫn ghi PAID
    for row in db.get_recently_expired_payos_orders(24 * 3600):
        order = dict(row)
        order_code = int(order["order_code"])
        try:
            info = await get_payment_info(order_code)
        except Exception as e:
            log.warning("Không đọc được đơn hết hạn %s: %s", order_code, e)
            continue
        try:
            if (info.get("status") or "").upper() == "PAID":
                if await settle_order(order):
                    stats["paid"] += 1
        except Exception as e:
            log.error("Xử lý đơn hết hạn %s lỗi: %s", order_code, e)
            stats["errors"] += 1
        finally:
            db.mark_payos_final_checked(order_code)
    return stats


# ---------------------------------------------------------------- vòng poll nền
_task = None


async def _poll_loop():
    log.info("PayOS poll loop started")
    while True:
        try:
            stats = await check_pending_once()
            if any(stats.values()):
                log.info("PayOS poll: %s", stats)
        except Exception as e:
            log.error("PayOS poll loop lỗi: %s", e)
        await asyncio.sleep(POLL_INTERVAL)


def start():
    global _task
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_poll_loop())


async def stop():
    global _task
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None


# ---------------------------------------------------------------- webhook
async def handle_webhook(payload: dict) -> tuple:
    """Xử lý webhook PayOS đã verify chữ ký. Trả (ok: bool, message: str)."""
    from . import db
    _, _, checksum_key = get_creds()
    if not checksum_key:
        return False, "PayOS chưa được cấu hình"
    if not verify_webhook_signature(payload, checksum_key):
        log.warning("PayOS webhook sai chữ ký")
        return False, "Sai chữ ký"
    data = payload.get("data") or {}
    if payload.get("code") != "00" and data.get("code") != "00":
        return True, "Đơn chưa thanh toán"
    order_code = data.get("orderCode")
    if not order_code:
        return False, "Thiếu orderCode"
    order = db.get_payos_order(int(order_code))
    if not order:
        return True, "Không tìm thấy đơn (có thể tạo ngoài bot)"
    if str(order["status"]).upper() == "PAID":
        return True, "Đơn đã xử lý"
    await settle_order(dict(order))
    return True, "OK"
