"""Test /daban — đánh dấu acc bán thủ công. SQLite TẠM, không đụng data.db thật.

- Tìm acc theo UID (kể cả UID dính đuôi '.0' do Excel).
- Đánh dấu AVAILABLE -> SOLD, giữ sheet_marked=0 để poller đẩy '🛒 ĐÃ BÁN' lên Sheet.
- Từ chối: acc không tồn tại, acc đã SOLD/DIE, đánh dấu 2 lần (race).
- Acc đánh dấu tay lọt vào danh sách poller quét (acc_sold_unmarked_sheet).

Chạy:  ~/fbvenv/bin/python -m pytest tests/test_daban.py -q
(cwd = ~/workspace/fb-hoan-chinh)
"""
import time


def _mkcat(db, name="Daban Test"):
    return db.acc_category_add(name, 10000, 24, "mô tả")


def _add(db, cid, uids):
    rows = [{"uid": u, "password": "p"} for u in uids]
    added, _ = db.acc_stock_add_batch(cid, rows, batch="test")
    assert added == len(uids)


def _row(db, uid):
    r = db.get_conn().execute(
        "SELECT id, uid, status, sold_at, sheet_marked FROM acc_stock WHERE uid=?",
        (uid,)).fetchone()
    return dict(r) if r else None


# ── 1. Tìm theo UID ─────────────────────────────────────────────────
def test_find_by_uid(tdb):
    db = tdb
    cid = _mkcat(db)
    _add(db, cid, ["61593959792972"])
    acc = db.acc_stock_find_by_uid("61593959792972")
    assert acc and acc["uid"] == "61593959792972"
    # UID dính đuôi .0 do Excel -> vẫn tìm ra
    acc2 = db.acc_stock_find_by_uid("61593959792972.0")
    assert acc2 and acc2["uid"] == "61593959792972"
    assert db.acc_stock_find_by_uid("khong_ton_tai") is None
    assert db.acc_stock_find_by_uid("") is None


# ── 2. Đánh dấu AVAILABLE -> SOLD ────────────────────────────────────
def test_mark_sold_manual_ok(tdb):
    db = tdb
    cid = _mkcat(db)
    _add(db, cid, ["m1"])
    acc = db.acc_stock_find_by_uid("m1")
    ok, why = db.acc_mark_sold_manual(acc["id"])
    assert ok and why == "ok"
    r = _row(db, "m1")
    assert r["status"] == "SOLD", "phải chuyển sang SOLD"
    assert int(r["sold_at"]) > 0, "phải ghi giờ bán"
    assert int(r["sheet_marked"]) == 0, "giữ sheet_marked=0 để poller đẩy lên Sheet"
    # Không còn tính vào tồn kho bán được
    assert db.acc_stock_count(cid) == 0
    assert db.acc_stock_sold_count(cid) == 1


# ── 3. Từ chối các trường hợp sai ────────────────────────────────────
def test_mark_sold_manual_rejects(tdb):
    db = tdb
    cid = _mkcat(db)
    _add(db, cid, ["r1", "r2"])
    acc1 = db.acc_stock_find_by_uid("r1")
    # id không tồn tại
    ok, why = db.acc_mark_sold_manual(999999)
    assert not ok and why == "not_found"
    # đánh dấu 2 lần -> lần 2 bị từ chối
    assert db.acc_mark_sold_manual(acc1["id"])[0] is True
    ok2, why2 = db.acc_mark_sold_manual(acc1["id"])
    assert not ok2 and why2 == "status_SOLD", "đánh dấu lặp phải bị chặn"
    # acc DIE cũng không đánh dấu được
    db.get_conn().execute("UPDATE acc_stock SET status='DIE' WHERE uid='r2'")
    db.get_conn().commit()
    acc2 = db.acc_stock_find_by_uid("r2")
    ok3, why3 = db.acc_mark_sold_manual(acc2["id"])
    assert not ok3 and why3 == "status_DIE"


# ── 4. Poller sẽ đẩy dấu lên Sheet ────────────────────────────────────
def test_marked_acc_picked_up_by_sheet_poller(tdb):
    db = tdb
    cid = _mkcat(db)
    _add(db, cid, ["s1"])
    acc = db.acc_stock_find_by_uid("s1")
    # Giả lập acc nhập từ Sheet (có sheet_ref)
    db.get_conn().execute(
        "UPDATE acc_stock SET sheet_ref='NhapKho:29' WHERE id=?", (acc["id"],))
    db.get_conn().commit()
    assert db.acc_mark_sold_manual(acc["id"])[0] is True
    pending = db.acc_sold_unmarked_sheet(200)
    ids = [p["id"] for p in pending]
    assert acc["id"] in ids, "poller 5 phút phải nhặt được acc này để ghi cột J"
    assert pending[0]["tab"] == "NhapKho" and pending[0]["row"] == 29


# ── 5. Không ảnh hưởng acc khác ──────────────────────────────────────
def test_mark_only_target_acc(tdb):
    db = tdb
    cid = _mkcat(db)
    _add(db, cid, ["t1", "t2", "t3"])
    acc = db.acc_stock_find_by_uid("t2")
    assert db.acc_mark_sold_manual(acc["id"])[0] is True
    assert _row(db, "t1")["status"] == "AVAILABLE"
    assert _row(db, "t2")["status"] == "SOLD"
    assert _row(db, "t3")["status"] == "AVAILABLE"
