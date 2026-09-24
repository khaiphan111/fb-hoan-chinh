"""Test đánh dấu SOLD khi bán acc — chạy trên SQLite TẠM, không đụng data.db thật.

Chạy:  ~/fbvenv/bin/python -m pytest tests/test_sold_mark.py -q
(cwd = ~/workspace/fb-hoan-chinh)
"""
import time


def _mkcat(db, name="Via Test"):
    return db.acc_category_add(name, 10000, 24, "mô tả")


def _add(db, cid, uids):
    rows = [{"uid": u, "password": "p"} for u in uids]
    added, _ = db.acc_stock_add_batch(cid, rows, batch="test")
    assert added == len(uids)


def _status(db, uid):
    r = db.get_conn().execute(
        "SELECT status, sold_to, sold_at, price_sold FROM acc_stock WHERE uid=?",
        (uid,)).fetchone()
    return dict(r) if r else None


def test_sell_one_marks_sold(tdb):
    db = tdb
    cid = _mkcat(db)
    _add(db, cid, ["uid1", "uid2"])
    oid, _row = db.acc_sell_one(cid, 111, 10000)
    assert oid
    st = _status(db, "uid1")
    assert st["status"] == "SOLD"
    assert int(st["sold_to"]) == 111
    assert int(st["sold_at"]) > 0
    assert int(st["price_sold"]) == 10000
    # Hàng đã bán không còn tính vào tồn kho bán được
    assert db.acc_stock_count(cid) == 1
    assert db.acc_stock_sold_count(cid) == 1


def test_sell_many_marks_sold(tdb):
    db = tdb
    cid = _mkcat(db)
    _add(db, cid, ["a1", "a2", "a3"])
    out = db.acc_sell_many(cid, 222, 25000, 2)
    assert out and len(out) == 2
    for oid, _ in out:
        assert oid
    assert db.acc_stock_count(cid) == 1
    assert db.acc_stock_sold_count(cid) == 2
    st = _status(db, "a1")
    assert st["status"] == "SOLD" and int(st["sold_to"]) == 222


def test_sell_stock_ids_marks_sold(tdb):
    db = tdb
    cid = _mkcat(db)
    _add(db, cid, ["b1", "b2"])
    ids = [r["id"] for r in db.get_conn().execute(
        "SELECT id FROM acc_stock WHERE cat_id=? ORDER BY id", (cid,)).fetchall()]
    out = db.acc_sell_stock_ids(cid, 333, 20000, ids)
    assert out and len(out) == 2
    assert db.acc_stock_count(cid) == 0
    assert db.acc_stock_sold_count(cid) == 2


def test_mystery_sell_marks_sold(tdb):
    db = tdb
    cid = _mkcat(db, "HopMu Test")
    # bật cờ hộp mù trực tiếp
    db.get_conn().execute(
        "UPDATE acc_categories SET mystery_eligible=1 WHERE id=?", (cid,))
    db.get_conn().commit()
    _add(db, cid, ["m1"])
    out = db.acc_mystery_sell(444, 5000)
    assert out
    _oid, _row, _name = out
    assert db.acc_stock_sold_count(cid) == 1
    assert db.acc_stock_count(cid) == 0


def test_auto_replace_marks_sold(tdb):
    db = tdb
    cid = _mkcat(db)
    _add(db, cid, ["w1"])
    out = db.acc_auto_replace(1, 999999, cid, 555)
    assert out
    _oid, _row = out
    st = _status(db, "w1")
    assert st["status"] == "SOLD"
    # giá 0đ (bảo hành đổi acc)
    assert int(st["price_sold"]) == 0


def test_sold_uid_can_be_reimported(tdb):
    """Acc đã bán được nhập lại cùng UID (chống trùng chỉ chặn AVAILABLE/DIE)."""
    db = tdb
    cid = _mkcat(db)
    _add(db, cid, ["dup1"])
    db.acc_sell_one(cid, 111, 10000)
    # mô phỏng logic chống trùng mới trong _import_stock_rows
    existing = {r[0] for r in db.get_conn().execute(
        "SELECT uid FROM acc_stock WHERE cat_id=? AND status IN ('AVAILABLE','DIE')",
        (cid,)).fetchall()}
    assert "dup1" not in existing
    _add(db, cid, ["dup1"])
    assert db.acc_stock_count(cid) == 1


def test_migration_adds_sold_columns(tdb):
    db = tdb
    cols = {r[1] for r in db.get_conn().execute(
        "PRAGMA table_info(acc_stock)").fetchall()}
    assert {"sold_to", "sold_at", "price_sold"} <= cols


def test_accinfo_sees_sold_row(tdb):
    """acc_stock_by_uid thấy hàng đã bán (kèm đơn bán)."""
    db = tdb
    cid = _mkcat(db)
    _add(db, cid, ["info1"])
    oid, _ = db.acc_sell_one(cid, 777, 10000)
    s = db.acc_stock_by_uid("info1")
    assert s and s["status"] == "SOLD"
    orders = db.acc_stock_orders(s["id"])
    assert orders and orders[0]["id"] == oid
    assert int(orders[0]["tg_id"]) == 777
