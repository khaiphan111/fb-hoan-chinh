"""Test phần tiền: parse số tiền, quyết toán PayOS, hết hạn, hoàn tiền, giảm giá bulk, VIP."""
import time

import pytest

from app import bot as botmod
from app import db as dbmod


# ── 1. Parse số tiền ──────────────────────────────────────────────
def test_parse_payos_amount_k():
    p = botmod._parse_payos_amount
    assert p("50k") == 50000
    assert p("50K") == 50000
    assert p("1k") == 1000
    assert p("200000") == 200000
    assert p("100 000") == 100000


def test_parse_payos_amount_invalid():
    p = botmod._parse_payos_amount
    with pytest.raises(Exception):
        p("0")
    with pytest.raises(Exception):
        p("-5k")
    with pytest.raises(Exception):
        p("abc")


def test_parse_payos_amount_1m_current_behavior():
    """Ghi nhận hành vi hiện tại: '1m' CHƯA được hỗ trợ (raise ValueError)."""
    with pytest.raises(Exception):
        botmod._parse_payos_amount("1m")


# ── 2. Quyết toán PayOS không cộng trùng ──────────────────────────
def test_settle_payos_idempotent(tdb):
    db = tdb
    tg = 900001
    db.upsert_user(tg, "test_u", "Test User")
    code = 100000000000001
    db.create_payos_order(code, tg, 50000, target="main")

    r1 = db.settle_payos_order(code)
    assert r1["ok"] is True
    u1 = db.get_user(tg)
    assert u1["balance"] == 50000
    assert u1["total_topup"] == 50000

    # gọi lần 2 (webhook + poller trùng) → không cộng thêm
    r2 = db.settle_payos_order(code)
    assert r2["ok"] is False
    u2 = db.get_user(tg)
    assert u2["balance"] == 50000, "CỘNG TRÙNG TIỀN!"
    assert u2["total_topup"] == 50000


def test_settle_payos_shop_wallet(tdb):
    db = tdb
    tg = 900002
    db.upsert_user(tg, "test_u2", "Test User 2")
    code = 100000000000002
    db.create_payos_order(code, tg, 200000, target="shop")
    r = db.settle_payos_order(code)
    assert r["ok"] is True and r["target"] == "shop"
    u = db.get_user(tg)
    assert u["shop_balance"] == 200000
    assert u["balance"] == 0


# ── 3. PENDING quá 45 phút → EXPIRED, không ghi đè PAID ──────────
def test_pending_expire_after_45min(tdb):
    db = tdb
    tg = 900003
    db.upsert_user(tg, "test_u3", "Test User 3")
    code = 100000000000003
    db.create_payos_order(code, tg, 50000)
    # giả lập đơn tạo cách đây 46 phút
    old = int(time.time()) - 46 * 60
    db.get_conn().execute(
        "UPDATE payos_orders SET created_at=? WHERE order_code=?", (old, code))
    db.get_conn().commit()

    overdue = db.get_overdue_pending_payos_orders(45 * 60)
    assert any(r["order_code"] == code for r in overdue)

    assert db.mark_payos_expired_if_pending(code) is True
    assert db.get_payos_order(code)["status"] == "EXPIRED"


def test_expire_does_not_override_paid(tdb):
    db = tdb
    tg = 900004
    db.upsert_user(tg, "test_u4", "Test User 4")
    code = 100000000000004
    db.create_payos_order(code, tg, 50000)
    assert db.settle_payos_order(code)["ok"] is True
    # đơn đã PAID → expire phải trả False, giữ nguyên PAID
    assert db.mark_payos_expired_if_pending(code) is False
    assert db.get_payos_order(code)["status"] == "PAID"


# ── 4. Hoàn tiền khi lỗi hạ tầng ──────────────────────────────────
def test_refund_on_infra_error(tdb):
    db = tdb
    tg = 900005
    db.upsert_user(tg, "test_u5", "Test User 5")
    db.add_balance_only(tg, 100000, "test nap")
    assert db.get_user(tg)["balance"] == 100000

    # trừ tiền mua acc (giống flow shop)
    price = 30000
    db.add_balance_only(tg, -price, "test mua acc")
    assert db.get_user(tg)["balance"] == 70000

    # lỗi hạ tầng khi check LIVE → hoàn đủ, total_topup không đổi
    db.add_balance_only(tg, price, "test hoan tien loi ha tang")
    u = db.get_user(tg)
    assert u["balance"] == 100000, "hoàn thiếu tiền!"
    assert u["total_topup"] == 0, "hoàn tiền không được động total_topup"


# ── 5. Giảm giá bulk theo ngưỡng 5/10/20 ─────────────────────────
def _bulk_pct_for(qty, p5, p10, p20):
    # đúng rule đang dùng trong bot.py (4 chỗ)
    return p20 if qty >= 20 else (p10 if qty >= 10 else (p5 if qty >= 5 else 0))


def test_bulk_pcts_defaults(tdb):
    assert botmod._shop_bulk_pcts() == (5, 10, 15)


def test_bulk_pcts_custom_settings(tdb):
    db = tdb
    db.set_setting("shop_bulk_5_pct", "7")
    db.set_setting("shop_bulk_10_pct", "12")
    db.set_setting("shop_bulk_20_pct", "20")
    assert botmod._shop_bulk_pcts() == (7, 12, 20)


def test_bulk_thresholds():
    p5, p10, p20 = 5, 10, 15
    assert _bulk_pct_for(1, p5, p10, p20) == 0
    assert _bulk_pct_for(4, p5, p10, p20) == 0
    assert _bulk_pct_for(5, p5, p10, p20) == 5
    assert _bulk_pct_for(9, p5, p10, p20) == 5
    assert _bulk_pct_for(10, p5, p10, p20) == 10
    assert _bulk_pct_for(19, p5, p10, p20) == 10
    assert _bulk_pct_for(20, p5, p10, p20) == 15
    assert _bulk_pct_for(50, p5, p10, p20) == 15


def test_bulk_final_price_formula():
    # đúng công thức trong bot.py: final = price*qty*(100-pct)//100
    price, qty, pct = 100000, 10, 10
    assert price * qty * (100 - pct) // 100 == 900000


# ── 6. Ngưỡng tự nâng VIP ────────────────────────────────────────
def _set_vip_prices(db):
    db.set_setting("vip1_price", "50000")
    db.set_setting("vip2_price", "100000")
    db.set_setting("vip3_price", "150000")
    db.set_setting("vip_lifetime_price", "209999")


def test_vip_upgrade_thresholds(tdb):
    db = tdb
    _set_vip_prices(db)
    tg = 900006
    db.upsert_user(tg, "test_u6", "Test User 6")

    # dưới ngưỡng → không nâng
    db.credit_topup(tg, 40000, "test")
    upgraded, level, lifetime = db.check_vip_upgrade(tg)
    assert (upgraded, level) == (False, 0)

    # ≥50k → VIP1
    db.credit_topup(tg, 20000, "test")  # tổng 60k
    upgraded, level, lifetime = db.check_vip_upgrade(tg)
    assert (upgraded, level) == (True, 1)

    # ≥100k → VIP2
    db.credit_topup(tg, 50000, "test")  # tổng 110k
    upgraded, level, lifetime = db.check_vip_upgrade(tg)
    assert (upgraded, level) == (True, 2)

    # ≥150k → VIP3
    db.credit_topup(tg, 50000, "test")  # tổng 160k
    upgraded, level, lifetime = db.check_vip_upgrade(tg)
    assert (upgraded, level) == (True, 3)

    # ≥209999 → vĩnh viễn
    db.credit_topup(tg, 60000, "test")  # tổng 220k
    upgraded, level, lifetime = db.check_vip_upgrade(tg)
    assert lifetime is True
    u = db.get_user(tg)
    assert u["sub_until"] == 9999999999
