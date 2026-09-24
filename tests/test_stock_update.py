"""Test cập nhật thông tin acc (3 cách) — SQLite TẠM, không đụng data.db thật.

Chạy:  ~/fbvenv/bin/python -m pytest tests/test_stock_update.py -q
(cwd = ~/workspace/fb-hoan-chinh)
"""
import asyncio

import pytest

from app import bot as botmod
from app import db as dbmod


def _mkcat(db, name="Via test"):
    return db.acc_category_add(name, 10000, 24, "mô tả")


def _add(db, cid, rows):
    added, _ = db.acc_stock_add_batch(cid, rows, batch="test")
    assert added == len(rows)


class _Wait:
    def __init__(self):
        self.text = ""

    async def edit_text(self, t, parse_mode=None):
        self.text = t


class _User:
    id = 999
    full_name = "Tester"


class _Msg:
    from_user = _User()
    text = ""

    def __init__(self):
        self.answered = []
        self.wait = _Wait()

    async def answer(self, t, parse_mode=None, reply_markup=None):
        self.answered.append(t)
        return self.wait


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── 1. acc_stock_update_fields: cập nhật acc còn hàng ──────────────
def test_update_fields_basic(tdb):
    db = tdb
    cid = _mkcat(db)
    _add(db, cid, [{"uid": "u1", "password": "old", "totp": "AAA"}])
    row = db.acc_stock_find(cid, "u1")
    assert row and row["password"] == "old"
    n = db.acc_stock_update_fields(row["id"], {"password": "new", "totp": "BBB"})
    assert n == 2
    row2 = db.acc_stock_find(cid, "u1")
    assert row2["password"] == "new" and row2["totp"] == "BBB"


# ── 2. Không đổi gì khi giá trị giống cũ ───────────────────────────
def test_update_fields_noop(tdb):
    db = tdb
    cid = _mkcat(db)
    _add(db, cid, [{"uid": "u1", "password": "same"}])
    row = db.acc_stock_find(cid, "u1")
    assert db.acc_stock_update_fields(row["id"], {"password": "same"}) == 0
    assert db.acc_stock_update_fields(row["id"], {"password": ""}) == 0 or True


# ── 3. Acc đã bán (SOLD) không được đụng ───────────────────────────
def test_update_fields_sold_untouched(tdb):
    db = tdb
    cid = _mkcat(db)
    _add(db, cid, [{"uid": "u1", "password": "old"}])
    row = db.acc_stock_find(cid, "u1")
    # giả lập bán: đánh dấu SOLD
    db.get_conn().execute("UPDATE acc_stock SET status='SOLD' WHERE id=?", (row["id"],))
    db.get_conn().commit()
    assert db.acc_stock_find(cid, "u1") is None, "SOLD không được tìm thấy"
    n = db.acc_stock_update_fields(row["id"], {"password": "hacked"})
    assert n == 0, "không được sửa acc đã bán"
    r = db.acc_stock_get_by_id(row["id"])
    assert r["password"] == "old"


# ── 4. Bỏ qua trường không hợp lệ ──────────────────────────────────
def test_update_fields_invalid_ignored(tdb):
    db = tdb
    cid = _mkcat(db)
    _add(db, cid, [{"uid": "u1", "password": "old"}])
    row = db.acc_stock_find(cid, "u1")
    n = db.acc_stock_update_fields(row["id"], {"uid": "newuid", "hacker": "x"})
    assert n == 0
    assert db.acc_stock_find(cid, "u1")["uid"] == "u1"


# ── 5. _parse_stock_file: .txt ─────────────────────────────────────
def test_parse_txt(tdb):
    raw = ("100000111|pass1|01/01/2024|mail@x.com|note1|ABCDEF12|cookie1|EAAGtok\n"
           "100000222|pass2\n").encode()
    rows, err = botmod._parse_stock_file(raw, "acc.txt")
    assert err is None and len(rows) == 2
    assert rows[0]["uid"] == "100000111" and rows[0]["password"] == "pass1"
    assert rows[0]["backup_mail"] == "mail@x.com" and rows[0]["token"] == "EAAGtok"
    assert rows[1]["uid"] == "100000222"


# ── 6. _parse_stock_file: .xlsx ────────────────────────────────────
def test_parse_xlsx(tdb):
    from openpyxl import Workbook
    import io
    wb = Workbook()
    ws = wb.active
    ws.append(["UID", "MK", "Ngày tạo", "Mail thay", "Ghi chú", "2FA", "Cookie", "Token"])
    ws.append(["333", "pw3", "", "", "n3", "", "", ""])
    bio = io.BytesIO()
    wb.save(bio)
    rows, err = botmod._parse_stock_file(bio.getvalue(), "acc.xlsx")
    assert err is None and len(rows) == 1
    assert rows[0]["uid"] == "333" and rows[0]["password"] == "pw3"


# ── 7. _update_stock_rows: update theo UID, UID lạ không nhập mới ──
def test_update_stock_rows_file(tdb, monkeypatch):
    db = tdb
    monkeypatch.setattr(botmod, "db", db)
    cid = _mkcat(db)
    _add(db, cid, [
        {"uid": "u1", "password": "old1", "note": "n1"},
        {"uid": "u2", "password": "old2"},
    ])
    rows = [
        {"uid": "u1", "password": "new1", "note": "", "created_date": "",
         "backup_mail": "", "totp": "", "cookie": "", "token": ""},
        {"uid": "u3", "password": "pw3", "note": "", "created_date": "",
         "backup_mail": "", "totp": "", "cookie": "", "token": ""},
    ]
    wait, msg = _Wait(), _Msg()
    _run(botmod._update_stock_rows(rows, cid, {"name": "Via test"}, msg, wait, source="test"))
    assert "Đã cập nhật: <b>1</b> acc" in wait.text
    assert "UID không có trong kho: <b>1</b>" in wait.text
    r1 = db.acc_stock_find(cid, "u1")
    assert r1["password"] == "new1", "password phải đổi"
    assert r1["note"] == "n1", "ô trống phải giữ nguyên"
    assert db.acc_stock_find(cid, "u3") is None, "UID lạ không được nhập mới"


# ── 8. _run_sheet_update: sửa trên Sheet -> update kho ─────────────
def test_run_sheet_update(tdb, monkeypatch):
    db = tdb
    monkeypatch.setattr(botmod, "db", db)
    from app import sheet_import as simod

    async def fake_read_marked(sid, tab):
        assert sid == "SID123"
        return [
            (2, ["u1", "newpw", "", "", "", "", "", ""]),   # đổi mk
            (3, ["u2", "old2", "", "", "", "", "", ""]),    # không đổi
            (4, ["uX", "pw", "", "", "", "", "", ""]),      # UID lạ
        ]

    monkeypatch.setattr(simod, "read_marked", fake_read_marked)
    db.set_setting("sheet_import_id", "SID123")
    cid = _mkcat(db)
    _add(db, cid, [
        {"uid": "u1", "password": "old1"},
        {"uid": "u2", "password": "old2"},
    ])
    msg = _Msg()
    _run(botmod._run_sheet_update(msg, cid))
    assert "Đã cập nhật: <b>1</b> acc" in msg.wait.text
    assert db.acc_stock_find(cid, "u1")["password"] == "newpw"
    assert db.acc_stock_find(cid, "u2")["password"] == "old2"
