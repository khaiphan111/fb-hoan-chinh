"""Test mua acc theo UID cu the (khach tu chon UID).

- Phan trang danh sach UID con hang (_pick_uid_page trich tu bot.py).
- Mua dung stock id da chon: acc_sell_stock_ids nguyen tu.
- Race: 2 nguoi mua cung UID -> chi 1 nguoi duoc, nguoi kia duoc hoan tien (None).
- UID nhap tay dinh duoi '.0' (Excel) -> van tim ra.

SQLite TAM, khong dung data.db that.
"""
import re


def _mkcat(db, name="Pick UID Test"):
    return db.acc_category_add(name, 10000, 24, "mô tả")


def _add(db, cid, uids):
    rows = [{"uid": u, "password": "p"} for u in uids]
    added, _ = db.acc_stock_add_batch(cid, rows, batch="test")
    assert added == len(uids)


def _load_pick_fn():
    src = open("botcheckv2/backend/app/bot.py", encoding="utf-8").read()
    m = re.search(
        r"(def _pick_uid_page\(cat_id: int, page: int\):.*?)(?=\n\n\ndef |\n\n@router)",
        src, re.S)
    assert m, "khong tim thay _pick_uid_page"
    return m.group(1)


# ── 1. Phân trang UID ─────────────────────────────────────────────
def test_pick_uid_pagination(tdb):
    db = tdb
    cid = _mkcat(db)
    uids = [f"900000000{i:03d}" for i in range(25)]
    _add(db, cid, uids)
    ns = {"db": db, "_PICK_PAGE_SIZE": 10}
    exec(_load_pick_fn(), ns)
    page = ns["_pick_uid_page"]
    r0, nxt0 = page(cid, 0)
    r1, nxt1 = page(cid, 1)
    r2, nxt2 = page(cid, 2)
    assert len(r0) == 10 and nxt0 is True
    assert len(r1) == 10 and nxt1 is True
    assert len(r2) == 5 and nxt2 is False
    # dung thu tu, khong trung
    all_ids = [r["id"] for r in r0 + r1 + r2]
    assert len(set(all_ids)) == 25
    assert r0[0]["uid"] == uids[0]


def test_pick_uid_excludes_sold(tdb):
    db = tdb
    cid = _mkcat(db)
    _add(db, cid, ["910000000001", "910000000002", "910000000003"])
    acc = db.acc_stock_find_by_uid("910000000002")
    ok, _ = db.acc_mark_sold_manual(acc["id"])
    assert ok
    ns = {"db": db, "_PICK_PAGE_SIZE": 10}
    exec(_load_pick_fn(), ns)
    rows, nxt = ns["_pick_uid_page"](cid, 0)
    got = [r["uid"] for r in rows]
    assert got == ["910000000001", "910000000003"], got
    assert nxt is False


# ── 2. Mua đúng UID đã chọn ───────────────────────────────────────
def test_buy_specific_uid(tdb):
    db = tdb
    cid = _mkcat(db)
    _add(db, cid, ["920000000001", "920000000002"])
    acc = db.acc_stock_find_by_uid("920000000002")
    sold = db.acc_sell_stock_ids(cid, 111, 10000, [acc["id"]])
    assert sold and len(sold) == 1
    oid, _row = sold[0]
    order = dict(db.acc_get_order(oid))
    assert order["uid"] == "920000000002", "phai giao dung UID khach chon"
    # acc kia van con hang
    assert db.acc_stock_count(cid) == 1


# ── 3. Race: 2 người mua cùng UID ──────────────────────────────────
def test_buy_uid_race(tdb):
    db = tdb
    cid = _mkcat(db)
    _add(db, cid, ["930000000001"])
    acc = db.acc_stock_find_by_uid("930000000001")
    first = db.acc_sell_stock_ids(cid, 111, 10000, [acc["id"]])
    assert first, "nguoi dau tien mua duoc"
    second = db.acc_sell_stock_ids(cid, 222, 10000, [acc["id"]])
    assert second is None, "nguoi thu 2 phai that bai (de hoan tien)"
    assert db.acc_stock_count(cid) == 0


# ── 4. UID nhập tay dính '.0' ─────────────────────────────────────
def test_pick_uid_dot_zero(tdb):
    db = tdb
    cid = _mkcat(db)
    _add(db, cid, ["940000000001"])
    # mo phong logic chuan hoa trong on_acc_pick_input
    _t = "940000000001.0"
    _m = re.fullmatch(r"(\d+)(?:\.0+)?", _t.strip())
    uid = _m.group(1) if _m else _t
    r = db.get_conn().execute(
        "SELECT id, uid, status FROM acc_stock WHERE cat_id=? AND uid=?",
        (cid, uid)).fetchone()
    assert r and r["uid"] == "940000000001"
