"""Test cấu hình nhập kho tự động theo gian hàng + bỏ tự ẩn khi hết hàng.

Chạy trên SQLite TẠM, tuyệt đối không đụng data.db thật.
Chạy:  ~/fbvenv/bin/python -m pytest tests/test_auto_import_cfg.py -q
(cwd = ~/workspace/fb-hoan-chinh)
"""
import time


def test_cfg_default_off(tdb):
    db = tdb
    cfg = db.stall_import_cfg_get("kho gmail")
    assert cfg["enabled"] == 0
    assert cfg["interval_min"] == 60
    assert cfg["cat_id"] == 0
    assert cfg["last_run"] == 0


def test_cfg_set_and_due(tdb):
    db = tdb
    db.stall_import_cfg_set("kho gmail", enabled=1, interval_min=30, cat_id=5,
                            supplier_id=2, cost=3000)
    cfg = db.stall_import_cfg_get("kho gmail")
    assert cfg["enabled"] == 1
    assert cfg["interval_min"] == 30
    assert cfg["cat_id"] == 5
    assert cfg["supplier_id"] == 2
    assert cfg["cost"] == 3000
    now = int(time.time())
    # chưa chạy lần nào -> đến giờ quét
    assert any(d["stall"] == "kho gmail" for d in db.stall_import_cfg_due(now))
    # vừa chạy -> chưa đến giờ
    db.stall_import_cfg_set("kho gmail", last_run=now)
    assert not any(d["stall"] == "kho gmail"
                   for d in db.stall_import_cfg_due(now))
    # quá chu kỳ -> đến giờ
    db.stall_import_cfg_set("kho gmail", last_run=now - 31 * 60)
    assert any(d["stall"] == "kho gmail" for d in db.stall_import_cfg_due(now))


def test_cfg_min_interval_5min(tdb):
    db = tdb
    now = int(time.time())
    db.stall_import_cfg_set("sạp nhanh", enabled=1, interval_min=1,
                            last_run=now - 4 * 60)
    # interval < 5 phút bị kẹp lên 5 -> chưa đến giờ
    assert not any(d["stall"] == "sạp nhanh"
                   for d in db.stall_import_cfg_due(now))
    db.stall_import_cfg_set("sạp nhanh", last_run=now - 6 * 60)
    assert any(d["stall"] == "sạp nhanh"
               for d in db.stall_import_cfg_due(now))


def test_cfg_disabled_not_due(tdb):
    db = tdb
    db.stall_import_cfg_set("sạp tắt", enabled=0, last_run=0)
    assert not any(d["stall"] == "sạp tắt"
                   for d in db.stall_import_cfg_due(int(time.time())))


def test_no_autohide_when_sold_out(tdb):
    # Bán hết acc cuối -> loại acc KHÔNG bị tự ẩn nữa (fix 2026-09-24)
    db = tdb
    cid = db.acc_category_add("Via Test", 10000, 24, "mô tả")
    added, _ = db.acc_stock_add_batch(cid, [{"uid": "u1", "password": "p"}],
                                        batch="t")
    assert added == 1
    oid, _row = db.acc_sell_one(cid, 111, 10000)
    assert oid
    assert db.acc_stock_count(cid) == 0
    r = db.get_conn().execute(
        "SELECT hidden FROM acc_categories WHERE id=?", (cid,)).fetchone()
    assert int(r["hidden"]) == 0
