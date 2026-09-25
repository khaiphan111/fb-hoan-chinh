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


# ── 6. Parse nhiều UID 1 lúc (logic trong on_acc_pick_input) ──────
def _parse_uids(text):
    uids = []
    for m in re.findall(r"\d+(?:\.0+)?", text):
        u = re.sub(r"\.0+$", "", m)
        if u and u not in uids:
            uids.append(u)
    return uids[:20]


def test_parse_multi_uids():
    p = _parse_uids
    assert p("61593959792972") == ["61593959792972"]
    assert p("61593959792972, 61593959792973") == ["61593959792972", "61593959792973"]
    assert p("61593959792972\n61593959792973") == ["61593959792972", "61593959792973"]
    assert p("uid: 61593959792972; 61593959792973.0") == ["61593959792972", "61593959792973"]
    assert p("61593959792972 61593959792972") == ["61593959792972"], "loại trùng"
    assert p("61593959792972.00") == ["61593959792972"], "đuôi .00 của Excel"
    assert p("abc") == [], "không có số"
    assert len(p(" ".join(str(900000000000 + i) for i in range(30)))) == 20, "tối đa 20"


def test_multi_uid_add_all_to_cart(tdb):
    db = tdb
    cid = _mkcat(db)
    _add(db, cid, ["970000000001", "970000000002", "970000000003"])
    ids = [db.acc_stock_find_by_uid(u)["id"] for u in
           ["970000000001", "970000000002", "970000000003"]]
    for sid in ids:
        assert db.cart_uid_add(111, cid, sid) == "ok"
    assert db.cart_uid_count(111) == 3
    # mô phỏng multi-buy: bán đúng 3 acc đã chọn
    sold = db.acc_sell_stock_ids(cid, 111, 30000, ids)
    assert sold and len(sold) == 3
    for s in db.get_conn().execute(
            "SELECT status FROM acc_stock WHERE id IN (?,?,?)",
            tuple(ids)).fetchall():
        assert s["status"] == "SOLD"
# ── 5. UID trong giỏ hàng ─────────────────────────────────────────
def test_cart_uid_add_list_remove(tdb):
    db = tdb
    cid = _mkcat(db)
    _add(db, cid, ["950000000001", "950000000002"])
    a1 = db.acc_stock_find_by_uid("950000000001")
    a2 = db.acc_stock_find_by_uid("950000000002")
    assert db.cart_uid_add(111, cid, a1["id"]) == "ok"
    assert db.cart_uid_add(111, cid, a1["id"]) == "exists", "thêm trùng phải báo exists"
    assert db.cart_uid_add(111, cid, a2["id"]) == "ok"
    assert db.cart_uid_add(111, cid, 999999) == "unavailable", "stock không tồn tại"
    lst = db.cart_uid_list(111)
    assert [r["uid"] for r in lst] == ["950000000001", "950000000002"]
    assert db.cart_uid_count(111) == 2
    db.cart_uid_remove(111, a1["id"])
    assert db.cart_uid_count(111) == 1
    db.cart_uid_clear(111)
    assert db.cart_uid_count(111) == 0


def test_cart_uid_sold_elsewhere(tdb):
    db = tdb
    cid = _mkcat(db)
    _add(db, cid, ["960000000001"])
    acc = db.acc_stock_find_by_uid("960000000001")
    assert db.cart_uid_add(111, cid, acc["id"]) == "ok"
    # người khác mua mất trước khi checkout
    sold = db.acc_sell_stock_ids(cid, 222, 10000, [acc["id"]])
    assert sold
    # thêm lại phải báo unavailable
    assert db.cart_uid_add(333, cid, acc["id"]) == "unavailable"


def test_cart_confirm_no_unbound_cat_id():
    """Regression 2026-09-25: on_cart_confirm crash UnboundLocalError khi giỏ
    CHỈ có UID cụ thể (priced rỗng -> biến cat_id trong vòng lặp chưa được gán,
    nhưng vòng giao acc lại dùng _live_line(cat_id)). Khách đã trừ tiền + tạo
    đơn nhưng không nhận được tin giao acc."""
    import inspect
    import re
    from app import bot as botmod
    src = inspect.getsource(botmod.on_cart_confirm)
    uses = re.findall(r"_live_line\(([^)]+)\)", src)
    assert uses, "không tìm thấy _live_line trong on_cart_confirm"
    for u in uses:
        assert u.strip() != "cat_id", (
            "on_cart_confirm dùng _live_line(cat_id) trần -> "
            "UnboundLocalError khi giỏ chỉ có UID cụ thể")


def test_send_merged_acc_file_helper():
    """Mua nhieu acc 1 luc: helper gui file gop phai ton tai va chi gui khi >=2 acc."""
    import asyncio
    import inspect
    from app import bot as botmod
    assert hasattr(botmod, "_send_merged_acc_file"), "thieu helper _send_merged_acc_file"
    src = inspect.getsource(botmod._send_merged_acc_file)
    assert "len(delivered) < 2" in src, "helper phai bo qua khi < 2 acc"
    # _acc_after_purchase phai goi helper (phu luot mua thuong SLN + mua UID + mua nhieu UID)
    src2 = inspect.getsource(botmod._acc_after_purchase)
    assert "_send_merged_acc_file" in src2
    # on_cart_confirm phai goi helper
    src3 = inspect.getsource(botmod.on_cart_confirm)
    assert "_send_merged_acc_file" in src3

    # chay that helper voi 2 acc gia -> phai gui 2 document
    sent = []

    class FakeMsg:
        async def answer_document(self, doc, caption="", parse_mode=None):
            sent.append((doc.filename, caption))

    delivered = [
        {"uid": "111", "password": "p1", "created_date": "d1", "backup_mail": "m1",
         "note": "", "totp": "", "cookie": "", "token": ""},
        {"uid": "222", "password": "p2", "created_date": "d2", "backup_mail": "m2",
         "note": "", "totp": "", "cookie": "", "token": ""},
    ]
    asyncio.run(botmod._send_merged_acc_file(FakeMsg(), delivered))
    assert len(sent) == 2, f"phai gui 2 file (txt+xlsx), nhan {len(sent)}"
    assert sent[0][0].endswith(".txt") and sent[1][0].endswith(".xlsx")

    # 1 acc -> khong gui gi
    sent.clear()
    asyncio.run(botmod._send_merged_acc_file(FakeMsg(), delivered[:1]))
    assert sent == []
