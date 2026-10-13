"""Test fix 2026-10-13: đơn PayOS quá hạn KHÔNG bị đánh EXPIRED oan khi lỗi mạng.

Bối cảnh: đơn 179181907691984 — khách trả 8.116đ lúc 22:31:49 12/10 nhưng tối đó
mạng VM chập chờn -> get_payment_info lỗi liên tục -> vòng quét chờ bỏ qua,
vòng quét quá hạn đánh EXPIRED mà KHÔNG hỏi PayOS, vòng kiểm tra lần cuối cũng
lỗi mạng rồi finally đánh final_checked=1 đóng hồ sơ vĩnh viễn. Tiền khách mất oan.

Luật mới trong check_pending_once():
1. Đơn quá hạn: hỏi PayOS trước. PAID -> settle; lỗi mạng -> GIỮ PENDING.
2. Kiểm tra lần cuối: chỉ final_checked=1 khi đã đọc được trạng thái thật.

Chạy: ~/fbvenv/bin/python -m pytest tests/test_payos_expire_guard.py -q
"""
import asyncio
import time

import pytest

from app import db as _real_db
from app import payos


def _mk_pending(tdb, code, tg_id, amount, age_sec, target="shop"):
    """Tạo đơn PENDING đã tạo age_sec giây trước."""
    now = int(time.time())
    c = tdb.get_conn()
    c.execute(
        "INSERT INTO payos_orders(order_code, tg_id, amount, status, created_at, updated_at, target)"
        " VALUES(?,?,?,?,?,?,?)",
        (code, tg_id, amount, "PENDING", now - age_sec, now - age_sec, target))
    c.commit()
    c.execute("INSERT OR IGNORE INTO tg_users(tg_id, balance, shop_balance) VALUES(?, 0, 0)", (tg_id,))
    c.commit()


def _mk_expired(tdb, code, tg_id, amount, age_sec, target="shop"):
    _mk_pending(tdb, code, tg_id, amount, age_sec, target)
    c = tdb.get_conn()
    c.execute("UPDATE payos_orders SET status='EXPIRED' WHERE order_code=?", (code,))
    c.commit()


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def _patched(tdb, monkeypatch):
    """check_pending_once chạy trên DB tạm, PayOS được giả lập."""
    monkeypatch.setattr(payos, "is_configured", lambda: True)
    # reconcile 30 phút/lần: ép không chạy trong test cho nhanh
    monkeypatch.setattr(time, "monotonic", lambda: 0.0)
    state = {"pay_status": {}, "fail_codes": set(), "settled": [], "cancelled": []}

    async def _fake_info(code):
        if code in state["fail_codes"]:
            raise payos.PayOSError("giả lập mạng chập chờn")
        return {"status": state["pay_status"].get(code, "PENDING")}

    async def _fake_settle(order):
        # mô phỏng settle thật qua db.settle_payos_order (nguyên tử)
        res = _real_db.settle_payos_order(int(order["order_code"]))
        if res.get("ok"):
            state["settled"].append(int(order["order_code"]))
            return True
        return False

    async def _fake_cancel(code, reason=""):
        state["cancelled"].append(code)
        return {}

    monkeypatch.setattr(payos, "get_payment_info", _fake_info)
    monkeypatch.setattr(payos, "settle_order", _fake_settle)
    monkeypatch.setattr(payos, "cancel_payment_link", _fake_cancel)
    return state


def _status(tdb, code):
    c = tdb.get_conn()
    r = c.execute("SELECT status, final_checked FROM payos_orders WHERE order_code=?", (code,)).fetchone()
    return r["status"], r["final_checked"]


def test_overdue_paid_duoc_settle(tdb, _patched):
    """Đơn quá hạn nhưng PayOS báo PAID -> cộng tiền, KHÔNG đánh EXPIRED."""
    _mk_pending(tdb, 910001, 9210001, 8116, age_sec=payos.ORDER_TTL + 60)
    _patched["pay_status"][910001] = "PAID"
    stats = _run(payos.check_pending_once())
    assert stats["paid"] == 1
    st, _ = _status(tdb, 910001)
    assert st == "PAID", f"đơn phải PAID, đang {st}"
    c = tdb.get_conn()
    bal = c.execute("SELECT shop_balance FROM tg_users WHERE tg_id=?", (9210001,)).fetchone()
    assert int(bal["shop_balance"]) == 8116


def test_overdue_loi_mang_giu_pending(tdb, _patched):
    """LỖI MẠNG khi hỏi PayOS -> đơn quá hạn GIỮ NGUYÊN PENDING, không EXPIRED oan."""
    _mk_pending(tdb, 910002, 9210002, 9000, age_sec=payos.ORDER_TTL + 60)
    _patched["fail_codes"].add(910002)
    stats = _run(payos.check_pending_once())
    st, _ = _status(tdb, 910002)
    assert st == "PENDING", f"lỗi mạng phải giữ PENDING, đang {st}"
    assert stats["errors"] >= 1
    assert 910002 not in _patched["cancelled"], "lỗi mạng thì không được hủy link"


def test_overdue_pending_that_bi_expired(tdb, _patched):
    """PayOS xác nhận vẫn PENDING quá TTL thật -> hủy link + EXPIRED như cũ."""
    _mk_pending(tdb, 910003, 9210003, 9000, age_sec=payos.ORDER_TTL + 60)
    _patched["pay_status"][910003] = "PENDING"
    stats = _run(payos.check_pending_once())
    st, _ = _status(tdb, 910003)
    assert st == "EXPIRED"
    assert stats["expired"] == 1
    assert 910003 in _patched["cancelled"]


def test_final_check_loi_mang_khong_dong_ho_so(tdb, _patched):
    """Vòng kiểm tra lần cuối lỗi mạng -> final_checked VẪN 0, để lần sau thử lại."""
    _mk_expired(tdb, 910004, 9210004, 9000, age_sec=3600)
    _patched["fail_codes"].add(910004)
    _run(payos.check_pending_once())
    st, final = _status(tdb, 910004)
    assert st == "EXPIRED"
    assert int(final or 0) == 0, "lỗi mạng thì không được đóng hồ sơ"


def test_final_check_paid_duoc_settle(tdb, _patched):
    """Khách trả trễ: vòng cuối thấy PAID -> cộng tiền."""
    _mk_expired(tdb, 910005, 9210005, 12000, age_sec=3600)
    _patched["pay_status"][910005] = "PAID"
    stats = _run(payos.check_pending_once())
    assert stats["paid"] == 1
    st, final = _status(tdb, 910005)
    assert st == "PAID"
    c = tdb.get_conn()
    bal = c.execute("SELECT shop_balance FROM tg_users WHERE tg_id=?", (9210005,)).fetchone()
    assert int(bal["shop_balance"]) == 12000


def test_final_check_khong_paid_dong_ho_so(tdb, _patched):
    """Đọc được trạng thái thật (không PAID) -> đóng hồ sơ như cũ."""
    _mk_expired(tdb, 910006, 9210006, 9000, age_sec=3600)
    _patched["pay_status"][910006] = "CANCELLED"
    _run(payos.check_pending_once())
    st, final = _status(tdb, 910006)
    assert st == "EXPIRED"
    assert int(final or 0) == 1
