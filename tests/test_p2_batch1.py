"""Test P2 batch 1: B1 (giải link trước kiểm trùng), B2 (DIE gỡ khỏi shop +
trả hàng dead), B3 (đối soát bù bút toán), D1 (đóng sạch Playwright).

Chạy: ~/fbvenv/bin/python -m pytest tests/test_p2_batch1.py -q
(cwd = ~/workspace/fb-hoan-chinh; DB SQLite tạm, không đụng dữ liệu thật)
"""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import db


# ---------------------------------------------------------------- B1
class _FakeWait:
    async def edit_text(self, *a, **k):
        pass

    async def delete(self, *a, **k):
        pass


def test_b1_giai_link_truoc_khi_kiem_trung(tdb, monkeypatch):
    """B1: link FB và UID số của cùng 1 acc -> sau giải đều thành UID số,
    nên vòng kiểm trùng (so UID số) bắt được."""
    from app.handlers import common as _common

    async def _fake_resolve(link):
        assert "facebook.com" in link
        return ("777888999", "Test User", {})

    monkeypatch.setattr("app.fb.resolve_fb_uid", _fake_resolve)
    rows = [
        {"uid": "https://www.facebook.com/777888999", "password": "p1"},
        {"uid": "777888999", "password": "p2"},
    ]
    resolved, failed, failed_lines, _, _ = asyncio.run(
        _common._resolve_stock_links(rows, _FakeWait(), action_text="ký gửi"))
    # chỉ dòng link được tính vào resolved; dòng UID số giữ nguyên
    assert resolved == 1 and failed == 0, (resolved, failed)
    uids = [r["uid"] for r in rows]
    assert uids == ["777888999", "777888999"], uids
    # mô phỏng vòng kiểm trùng trong _kg_finish_items: 2 dòng cùng UID số
    assert len(set(uids)) == 1, "B1 CHƯA FIX: link chưa giải ra UID số trước kiểm trùng"


# ---------------------------------------------------------------- B2
def _mk_consignor(tdb, tg_id=9210051):
    c = tdb.get_conn()
    now = int(time.time())
    c.execute(
        "INSERT INTO consignors(tg_id, name, status, level, max_items, max_value, created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (tg_id, "Test ĐT", "approved", "new", 20, 10000000, now))
    c.commit()
    return c.execute("SELECT id FROM consignors WHERE tg_id=?", (tg_id,)).fetchone()["id"]


def _mk_batch(tdb, consignor_id, status="approved"):
    c = tdb.get_conn()
    now = int(time.time())
    c.execute(
        "INSERT INTO consignment_batches(code, consignor_id, status, floor_price, sell_price,"
        " warranty_days, category_id, created_at) VALUES(?,?,?,?,?,?,?,?)",
        (f"TEST-{now}", consignor_id, status, 100000, 150000, 7, 1, now))
    c.commit()
    return c.execute("SELECT id FROM consignment_batches WHERE code=?",
                     (f"TEST-{now}",)).fetchone()["id"]


def test_b2_tra_hang_item_dead_va_stock_die(tdb):
    """B2: duyệt trả hàng -> item 'dead' cũng được trả; acc_stock 'DIE' -> 'RETURNED'."""
    c = tdb.get_conn()
    now = int(time.time())
    cid = _mk_consignor(tdb)
    bid = _mk_batch(tdb, cid, status="return_requested")
    # acc trong kho ở trạng thái DIE (đã gỡ khỏi shop)
    c.execute(
        "INSERT INTO acc_stock(cat_id, uid, password, status, added_at) VALUES(?,?,?,?,?)",
        (1, "dead_acc_1", "pw", "DIE", now))
    sid = c.execute("SELECT id FROM acc_stock WHERE uid=?", ("dead_acc_1",)).fetchone()["id"]
    c.execute(
        "INSERT INTO consignment_items(batch_id, consignor_id, uid, status, acc_stock_id, created_at)"
        " VALUES(?,?,?,?,?,?)",
        (bid, cid, "dead_acc_1", "dead", sid, now))
    c.commit()

    assert tdb.consign_batch_do_return(bid, approve=True, by_id=1) is True
    st = c.execute("SELECT status FROM consignment_items WHERE batch_id=?", (bid,)).fetchone()
    assert st["status"] == "returned", f"item dead không được trả: {st['status']}"
    sst = c.execute("SELECT status FROM acc_stock WHERE id=?", (sid,)).fetchone()
    assert sst["status"] == "RETURNED", f"acc DIE không được trả về: {sst['status']}"


# ---------------------------------------------------------------- B3
def test_b3_reconcile_bu_but_toan_thieu(tdb):
    """B3: acc SOLD nguồn ký gửi nhưng thiếu consignment_orders -> tạo bù;
    chạy lại -> fixed=0 (idempotent)."""
    c = tdb.get_conn()
    now = int(time.time())
    cid = _mk_consignor(tdb, tg_id=9210052)
    bid = _mk_batch(tdb, cid)
    c.execute(
        "INSERT INTO acc_stock(cat_id, uid, password, status, consign_item_id, added_at)"
        " VALUES(?,?,?,?,?,?)",
        (1, "sold_acc_1", "pw", "SOLD", 0, now))
    sid = c.execute("SELECT id FROM acc_stock WHERE uid=?", ("sold_acc_1",)).fetchone()["id"]
    c.execute(
        "INSERT INTO consignment_items(batch_id, consignor_id, uid, status, acc_stock_id, created_at)"
        " VALUES(?,?,?,?,?,?)",
        (bid, cid, "sold_acc_1", "listed", sid, now))
    iid = c.execute("SELECT id FROM consignment_items WHERE uid=?",
                    ("sold_acc_1",)).fetchone()["id"]
    c.execute("UPDATE acc_stock SET consign_item_id=? WHERE id=?", (iid, sid))
    # đơn bán đã có trong acc_orders nhưng CHƯA có consignment_orders
    c.execute(
        "INSERT INTO acc_orders(tg_id, stock_id, cat_id, price, created_at) VALUES(?,?,?,?,?)",
        (9210099, sid, 1, 150000, now))
    oid = c.execute("SELECT id FROM acc_orders WHERE stock_id=?", (sid,)).fetchone()["id"]
    c.commit()

    r1 = tdb.reconcile_consign_sales()
    assert r1["fixed"] == 1, f"không bù bút toán: {r1}"
    co = c.execute("SELECT * FROM consignment_orders WHERE order_ref=?",
                   (f"ACC-{oid}",)).fetchone()
    assert co is not None, "thiếu consignment_orders sau bù"
    lg = c.execute("SELECT COUNT(*) v FROM consignment_ledger WHERE note LIKE ?",
                   (f"%{f'ACC-{oid}'}%",)).fetchone()
    assert lg["v"] >= 1, "thiếu bút toán ledger sau bù"
    # chạy lại: không tạo trùng
    r2 = tdb.reconcile_consign_sales()
    assert r2["fixed"] == 0, f"bù trùng bút toán! {r2}"
    n = c.execute("SELECT COUNT(*) v FROM consignment_orders WHERE order_ref=?",
                  (f"ACC-{oid}",)).fetchone()
    assert n["v"] == 1


# ---------------------------------------------------------------- D1
def test_d1_dong_sach_playwright_khi_loi():
    """D1: get_panel_order_progress lỗi giữa chừng -> vẫn đóng ctx, browser, apw."""
    from app import buff_tracker as _bt

    ctx = AsyncMock()
    browser = AsyncMock()
    apw = MagicMock()
    apw.stop = AsyncMock()

    async def _boom(*a, **k):
        raise RuntimeError("panel sập giữa chừng")

    # buff_tracker import buff_worker lazy trong hàm -> patch module gốc
    from app import buff_worker as _bw
    with patch.object(_bw, "_new_context",
                      AsyncMock(return_value=(apw, browser, ctx))), \
         patch.object(_bw, "_ensure_login", _boom):
        res = asyncio.run(_bt.get_panel_order_progress("12345"))
    assert res["ok"] is False
    ctx.close.assert_awaited()
    browser.close.assert_awaited()
    apw.stop.assert_awaited()


def test_fix2_buff_rent_tru_nguyen_tu(tdb):
    """Fix 2: trừ ví buff/rent khi không đủ -> False, ví không đổi (không âm).

    Điều kiện nằm trong SQL (AND balance + ? >= 0) nên nguyên tử cả khi
    2 tiến trình trừ đồng thời — bản cũ SELECT rồi UPDATE riêng -> ví âm.
    """
    c = tdb.get_conn()
    c.execute("INSERT OR IGNORE INTO tg_users(tg_id, buff_balance, rent_balance)"
              " VALUES(?, 50000, 50000)", (9210061,))
    c.commit()
    # trừ vừa đủ -> True
    assert tdb.buff_adjust_balance(9210061, -50000, "test") is True
    assert tdb.rent_adjust_balance(9210061, -30000, "test") is True
    # trừ quá số dư -> False, ví giữ nguyên
    assert tdb.buff_adjust_balance(9210061, -1, "test") is False
    assert tdb.rent_adjust_balance(9210061, -20001, "test") is False
    row = c.execute("SELECT buff_balance, rent_balance FROM tg_users WHERE tg_id=?",
                    (9210061,)).fetchone()
    assert int(row["buff_balance"]) == 0, "ví buff bị âm!"
    assert int(row["rent_balance"]) == 20000, "ví rent bị trừ sai!"
    # cộng vẫn bình thường
    assert tdb.buff_adjust_balance(9210061, 10000, "test") is True
    row = c.execute("SELECT buff_balance FROM tg_users WHERE tg_id=?",
                    (9210061,)).fetchone()
    assert int(row["buff_balance"]) == 10000
    # user không tồn tại -> False (không tạo ví ma)
    assert tdb.buff_adjust_balance(999999999, -100, "test") is False


def test_fix3_khong_ban_trung_1_acc(tdb):
    """Fix 3: 2 lần bán cùng acc -> lần 2 thua race (None), chỉ 1 đơn tồn tại.

    Mô phỏng 2 tiến trình cùng SELECT thấy AVAILABLE: tiến trình 1 claim
    SOLD thành công; tiến trình 2 (dùng row cũ) phải raise AccAlreadySold
    thay vì tạo đơn thứ 2 như bản cũ.
    """
    import time
    c = tdb.get_conn()
    now = int(time.time())
    c.execute(
        "INSERT INTO acc_stock(cat_id, uid, password, status, added_at)"
        " VALUES(?,?,?, 'AVAILABLE', ?)",
        (7, "race_acc_1", "pw", now))
    c.commit()
    sid = c.execute("SELECT id FROM acc_stock WHERE uid=?", ("race_acc_1",)).fetchone()["id"]

    # tiến trình 1: bán thành công
    r1 = tdb.acc_sell_one(7, 9210071, 100000)
    assert r1 is not None, "lần bán đầu phải thành công"
    # tiến trình 2: SELECT cũ vẫn thấy row, nhưng claim SOLD phải thua
    row2 = c.execute("SELECT * FROM acc_stock WHERE id=?", (sid,)).fetchone()
    try:
        tdb._acc_order_create(c, 9210072, row2, 7, 100000, now)
        assert False, "bản cũ: tạo đơn thứ 2 cho cùng 1 acc!"
    except tdb.AccAlreadySold:
        pass
    n = c.execute("SELECT COUNT(*) v FROM acc_orders WHERE stock_id=?", (sid,)).fetchone()
    assert n["v"] == 1, f"có {n['v']} đơn cho 1 acc!"
    st = c.execute("SELECT status, sold_to FROM acc_stock WHERE id=?", (sid,)).fetchone()
    assert st["status"] == "SOLD" and int(st["sold_to"]) == 9210071
    # acc_sell_one lần nữa -> hết hàng (None), không tạo đơn ma
    assert tdb.acc_sell_one(7, 9210073, 100000) is None


def test_fix4_khong_huy_oan_khi_payos_loi(tdb, monkeypatch):
    """Fix 4: PayOS API lỗi giữa chừng -> đơn cũ giữ PENDING (không CANCELLED oan).

    Bản cũ: get_payment_info throw -> except pass -> vẫn đánh CANCELLED ->
    tiền user đã trả kẹt vĩnh viễn (settle/poller/reconciler đều bỏ qua CANCELLED).
    """
    import asyncio
    import time
    from app.handlers import wallet as _w
    from app import payos as _pm

    now = int(time.time())
    c = tdb.get_conn()
    c.execute(
        "INSERT INTO payos_orders(order_code, tg_id, amount, status, created_at, updated_at,"
        " target, checkout_url) VALUES(?,?,?,?,?,?,?,?)",
        (910001, 9210081, 50000, "PENDING", now, now, "main", "https://payos.link/old"))
    c.execute("INSERT OR IGNORE INTO tg_users(tg_id, balance) VALUES(?, 0)", (9210081,))
    c.commit()

    async def _raise(*a, **k):
        raise RuntimeError("mạng chập chờn")

    async def _noop_cancel(*a, **k):
        return None

    monkeypatch.setattr(_pm, "is_configured", lambda: True)
    monkeypatch.setattr(_pm, "get_payment_info", _raise)
    monkeypatch.setattr(_pm, "cancel_payment_link", _noop_cancel)
    monkeypatch.setattr(_pm, "new_order_code", lambda tg: 910002)
    monkeypatch.setattr(_pm, "get_return_urls", lambda: ("https://r/", "https://c/"))
    async def _mk_link(*a, **k):
        return {"paymentLinkId": "PL2", "checkoutUrl": "https://payos.link/new", "qrCode": "QR2"}
    monkeypatch.setattr(_pm, "create_payment_link", _mk_link)

    oc, url, qr, reused = asyncio.run(_w._make_payos_order(9210081, 99999, "main"))
    assert oc == 910002 and reused is False
    st = c.execute("SELECT status FROM payos_orders WHERE order_code=910001").fetchone()
    assert st["status"] == "PENDING", f"đơn bị hủy oan: {st['status']}"


def test_fix4_huy_that_khi_xac_minh_chua_tra(tdb, monkeypatch):
    """Fix 4: xác minh được đơn PENDING thật -> vẫn hủy cũ, tạo mới như cũ."""
    import asyncio
    import time
    from app.handlers import wallet as _w
    from app import payos as _pm

    now = int(time.time())
    c = tdb.get_conn()
    c.execute(
        "INSERT INTO payos_orders(order_code, tg_id, amount, status, created_at, updated_at,"
        " target, checkout_url) VALUES(?,?,?,?,?,?,?,?)",
        (910011, 9210082, 50000, "PENDING", now, now, "main", "https://payos.link/old"))
    c.execute("INSERT OR IGNORE INTO tg_users(tg_id, balance) VALUES(?, 0)", (9210082,))
    c.commit()

    cancelled = []

    async def _info_pending(*a, **k):
        return {"status": "PENDING"}

    async def _cancel(code, *a, **k):
        cancelled.append(code)

    monkeypatch.setattr(_pm, "is_configured", lambda: True)
    monkeypatch.setattr(_pm, "get_payment_info", _info_pending)
    monkeypatch.setattr(_pm, "cancel_payment_link", _cancel)
    monkeypatch.setattr(_pm, "new_order_code", lambda tg: 910012)
    monkeypatch.setattr(_pm, "get_return_urls", lambda: ("https://r/", "https://c/"))
    async def _mk_link(*a, **k):
        return {"paymentLinkId": "PL3", "checkoutUrl": "https://payos.link/new2", "qrCode": "QR3"}
    monkeypatch.setattr(_pm, "create_payment_link", _mk_link)

    oc, url, qr, reused = asyncio.run(_w._make_payos_order(9210082, 77777, "main"))
    assert oc == 910012
    st = c.execute("SELECT status FROM payos_orders WHERE order_code=910011").fetchone()
    assert st["status"] == "CANCELLED", f"đơn PENDING thật phải bị hủy: {st['status']}"
    assert cancelled == [910011]


def test_fix5_db_mode_bao_dung_che_do(tdb):
    """Fix 5: db.db_mode() phân biệt postgres/sqlite để health báo đúng.

    Trước đây rớt SQLite im lặng mà health vẫn 200 -> watchdog tưởng khỏe.
    """
    # test DB là SQLite -> phải báo sqlite
    assert tdb.db_mode() == "sqlite"
    assert isinstance(tdb.get_conn(), tdb.SqliteConnection)
    # giả lập Postgres -> phải báo postgres
    class _FakePg(tdb.PgConnection):
        def __init__(self):
            pass
    assert isinstance(_FakePg(), tdb.PgConnection)
    import unittest.mock as _mock
    with _mock.patch.object(tdb, "get_conn", return_value=_FakePg()):
        assert tdb.db_mode() == "postgres"
