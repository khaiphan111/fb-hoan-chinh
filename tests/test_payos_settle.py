"""Test fix A1: settle_payos_order nguyên tử + job đối soát reconcile_payos_paid.

Chạy: ~/fbvenv/bin/python -m pytest tests/test_payos_settle.py -q
"""
import time

from app import db


def _mk_order(tdb, code, tg_id, amount, target="main"):
    c = tdb.get_conn()
    now = int(time.time())
    c.execute(
        "INSERT INTO payos_orders(order_code, tg_id, amount, status, created_at, updated_at, target)"
        " VALUES(?,?,?,?,?,?,?)",
        (code, tg_id, amount, "PENDING", now, now, target))
    c.commit()
    # tạo user nếu chưa có
    c.execute("INSERT OR IGNORE INTO tg_users(tg_id, balance) VALUES(?, 0)", (tg_id,))
    c.commit()


def test_a1_settle_thanh_cong(tdb):
    """Settle thành công: PAID + ví được cộng."""
    _mk_order(tdb, 900001, 9210001, 50000)
    res = tdb.settle_payos_order(900001)
    assert res["ok"] is True
    c = tdb.get_conn()
    st = c.execute("SELECT status FROM payos_orders WHERE order_code=?", (900001,)).fetchone()
    assert st["status"] == "PAID"
    bal = c.execute("SELECT balance FROM tg_users WHERE tg_id=?", (9210001,)).fetchone()
    assert int(bal["balance"]) == 50000


def test_a1_settle_idempotent(tdb):
    """Settle 2 lần: lần 2 trả ok=False, ví chỉ cộng 1 lần."""
    _mk_order(tdb, 900002, 9210002, 30000)
    assert tdb.settle_payos_order(900002)["ok"] is True
    assert tdb.settle_payos_order(900002)["ok"] is False
    c = tdb.get_conn()
    bal = c.execute("SELECT balance FROM tg_users WHERE tg_id=?", (9210002,)).fetchone()
    assert int(bal["balance"]) == 30000


def test_a1_settle_loi_giua_chung_rollback(tdb, monkeypatch):
    """A1: lỗi khi cộng tiền -> đơn KHÔNG thành PAID (rollback toàn bộ)."""
    _mk_order(tdb, 900003, 9210003, 70000)

    def _boom(*a, **k):
        raise RuntimeError("giả lập tunnel rớt giữa chừng")

    monkeypatch.setattr(tdb, "_credit_topup_raw", _boom)
    try:
        tdb.settle_payos_order(900003)
        assert False, "phải raise"
    except RuntimeError:
        pass
    c = tdb.get_conn()
    st = c.execute("SELECT status FROM payos_orders WHERE order_code=?", (900003,)).fetchone()
    assert st["status"] == "PENDING", f"A1 CHƯA FIX: đơn đã {st['status']} nhưng tiền chưa cộng!"
    bal = c.execute("SELECT balance FROM tg_users WHERE tg_id=?", (9210003,)).fetchone()
    assert int(bal["balance"]) == 0


def test_a1_reconcile_bu_tien_thieu(tdb):
    """reconcile_payos_paid: đơn PAID thiếu txn -> cộng bù; đủ txn -> bỏ qua."""
    c = tdb.get_conn()
    now = int(time.time())
    # Đơn 1: PAID nhưng không có txn -> cần bù
    c.execute(
        "INSERT INTO payos_orders(order_code, tg_id, amount, status, created_at, updated_at, target)"
        " VALUES(?,?,?,?,?,?,?)",
        (900011, 9210011, 40000, "PAID", now, now, "main"))
    c.execute("INSERT OR IGNORE INTO tg_users(tg_id, balance) VALUES(?, 0)", (9210011,))
    # Đơn 2: PAID đã có txn -> bỏ qua
    c.execute(
        "INSERT INTO payos_orders(order_code, tg_id, amount, status, created_at, updated_at, target)"
        " VALUES(?,?,?,?,?,?,?)",
        (900012, 9210012, 25000, "PAID", now, now, "main"))
    c.execute("INSERT OR IGNORE INTO tg_users(tg_id, balance) VALUES(?, 25000)", (9210012,))
    c.execute("INSERT INTO txns(ts, tg_id, amount, reason) VALUES(?,?,?,?)",
              (now, 9210012, 25000, "payos"))
    c.commit()
    res = tdb.reconcile_payos_paid()
    assert res["fixed"] == 1, res
    bal = c.execute("SELECT balance FROM tg_users WHERE tg_id=?", (9210011,)).fetchone()
    assert int(bal["balance"]) == 40000
    bal2 = c.execute("SELECT balance FROM tg_users WHERE tg_id=?", (9210012,)).fetchone()
    assert int(bal2["balance"]) == 25000, "đơn đã có txn bị cộng 2 lần!"


def test_a2_khong_ghi_de_don_paid(tdb):
    """A2: mark_payos_status KHÔNG ghi đè đơn đã PAID."""
    c = tdb.get_conn()
    now = int(time.time())
    c.execute(
        "INSERT INTO payos_orders(order_code, tg_id, amount, status, created_at, updated_at)"
        " VALUES(?,?,?,?,?,?)",
        (900021, 9210021, 50000, "PAID", now, now))
    c.commit()
    assert tdb.mark_payos_status(900021, "CANCELLED") is False
    st = c.execute("SELECT status FROM payos_orders WHERE order_code=?", (900021,)).fetchone()
    assert st["status"] == "PAID"


def test_a2_huy_don_pending_ok(tdb):
    """A2: đơn PENDING vẫn hủy được bình thường."""
    c = tdb.get_conn()
    now = int(time.time())
    c.execute(
        "INSERT INTO payos_orders(order_code, tg_id, amount, status, created_at, updated_at)"
        " VALUES(?,?,?,?,?,?)",
        (900022, 9210022, 50000, "PENDING", now, now))
    c.commit()
    assert tdb.mark_payos_status(900022, "CANCELLED") is True
    st = c.execute("SELECT status FROM payos_orders WHERE order_code=?", (900022,)).fetchone()
    assert st["status"] == "CANCELLED"
