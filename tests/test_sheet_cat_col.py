"""Test cột L 'Loại / Gian hàng' trên Google Sheet — chạy trên SQLite TẠM.

- Ghi nhãn loại/gian hàng lúc nhập kho (write-back _import_stock_rows).
- Refresh nhãn mỗi lần re-check kho (poller._push_cat_to_sheet) cho MỌI gian hàng.

Chạy:  ~/fbvenv/bin/python -m pytest tests/ -q
(cwd = ~/workspace/fb-hoan-chinh)
"""
import asyncio
import json
import os
import sys

os.environ.pop("SUPABASE_DB_URL", None)

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "botcheckv2", "backend")
sys.path.insert(0, os.path.normpath(_BACKEND))

import app.bot as _bot  # noqa: E402
from app import db as _db  # noqa: E402
from app import sheet_import as _si  # noqa: E402


def test_cat_label():
    assert _si.cat_label(17, "Acc đầu 1000x", "Acc Facebook") == \
        "#17 Acc đầu 1000x · 🏪 Acc Facebook"
    # fallback khi thiếu tên/sạp
    assert _si.cat_label(5, "", "") == "#5 loại #5 · 🏪 Acc Facebook"


def test_build_cat_items():
    qrows = [
        {"uid": "100001", "sheet_ref": "NhapKho:2", "cat_id": 17,
         "cat_name": "Acc đầu 1000x", "stall": "Acc Facebook"},
        {"uid": "200001", "sheet_ref": "kho gmail:2", "cat_id": 3,
         "cat_name": "Gmail new", "stall": "Gmail"},
        {"uid": "100002", "sheet_ref": "NhapKho:2", "cat_id": 17,  # trùng dòng
         "cat_name": "Acc đầu 1000x", "stall": "Acc Facebook"},
        {"uid": "100003", "sheet_ref": "sai-format", "cat_id": 17,
         "cat_name": "X", "stall": "Acc Facebook"},
        {"uid": "100004", "sheet_ref": "", "cat_id": 17,
         "cat_name": "X", "stall": "Acc Facebook"},
    ]
    items = _si.build_cat_items(qrows)
    assert len(items) == 2
    by_tab = {(i["tab"], i["row"]): i["label"] for i in items}
    assert by_tab[("NhapKho", 2)] == "#17 Acc đầu 1000x · 🏪 Acc Facebook"
    assert by_tab[("kho gmail", 2)] == "#3 Gmail new · 🏪 Gmail"


def _fake_cli(calls, col_a):
    async def _run(args, payload=None):
        calls.append((args, payload))
        if "batchGet" in args:
            rngs = json.loads(args[-1])["ranges"]
            vrs = []
            for rg in rngs:
                tab, cell = rg.split("!")
                v = col_a.get((tab, int(cell[1:])), "")
                vrs.append({"range": rg, "values": [[v]] if v else []})
            return {"valueRanges": vrs}
        return {}
    return _run


def test_push_cat_marks(monkeypatch):
    calls = []
    col_a = {("NhapKho", 2): "100001", ("NhapKho", 3): "100002",
             ("kho gmail", 2): "200001"}
    monkeypatch.setattr(_si, "_cli", _fake_cli(calls, col_a))
    items = [
        {"uid": "100001", "tab": "NhapKho", "row": 2,
         "label": "#17 Acc đầu 1000x · 🏪 Acc Facebook"},
        {"uid": "999999", "tab": "NhapKho", "row": 3,  # UID không khớp
         "label": "#17 Acc đầu 1000x · 🏪 Acc Facebook"},
        {"uid": "200001", "tab": "kho gmail", "row": 2,
         "label": "#3 Gmail new · 🏪 Gmail"},
        {"uid": "100001", "tab": "NhapKho", "row": 2, "label": ""},  # nhãn rỗng
    ]
    done, skip = asyncio.run(_si.push_cat_marks("sheet123", items))
    assert done == 2
    assert skip == 1
    updates = [p for a, p in calls if p and "batchUpdate" in a]
    assert len(updates) == 2  # 2 tab
    vals = {d["range"]: d["values"][0][0] for p in updates for d in p["data"]}
    assert vals["NhapKho!L1:L1"] == "Loại / Gian hàng"
    assert vals["kho gmail!L1:L1"] == "Loại / Gian hàng"
    assert vals["NhapKho!L2:L2"] == "#17 Acc đầu 1000x · 🏪 Acc Facebook"
    assert vals["kho gmail!L2:L2"] == "#3 Gmail new · 🏪 Gmail"
    assert "NhapKho!L3:L3" not in vals  # UID không khớp -> không ghi


def test_push_cat_marks_empty():
    assert asyncio.run(_si.push_cat_marks("", [])) == (0, 0)
    assert asyncio.run(_si.push_cat_marks("sheet123", [])) == (0, 0)


class _FakeMsg:
    def __init__(self):
        self.texts = []

    async def answer(self, text, **kw):
        self.texts.append(text)


class _FakeWait:
    def __init__(self):
        self.texts = []

    async def edit_text(self, text, **kw):
        self.texts.append(text)


def test_import_writeback_ghi_cot_L(tdb, monkeypatch):
    """Nhập kho từ sheet: write-back ghi nhãn loại/gian hàng vào cột L."""
    cat_id = tdb.acc_category_add("Acc test L", 10000, 24, "",
                                  stall="Acc Facebook", live_check=0)
    c = tdb.acc_category_get(cat_id)
    rows = [{"uid": "100001", "password": "", "created_date": "",
             "backup_mail": "", "note": "", "totp": "", "cookie": "",
             "token": "", "_sheet_row": 2}]
    monkeypatch.setattr(asyncio, "create_task",
                        lambda coro, **k: asyncio.Future())
    written = []

    async def _fake_write(sid, tab, updates):
        written.append((sid, tab, updates))

    monkeypatch.setattr(_si, "write_updates", _fake_write)
    asyncio.run(_bot._import_stock_rows(
        rows, cat_id, 0, 0, c, _FakeMsg(), _FakeWait(),
        sheet_ctx={"sheet_id": "sid_test", "tab": "NhapKho"}))
    assert written
    _sid, _tab, updates = written[0]
    by_col = {}
    for _rnum, col, val in updates:
        by_col.setdefault(col, []).append(val)
    assert by_col.get(9) == ["✅ OK"]
    assert by_col.get(_si.CAT_COL) == \
        [f"#{cat_id} Acc test L · 🏪 Acc Facebook"], updates


def test_push_cat_to_sheet_moi_gian_hang(tdb, monkeypatch):
    """Recheck refresh cột L cho mọi gian hàng, mọi trạng thái (kể cả SOLD)."""
    from app import poller as _poller
    cat_fb = tdb.acc_category_add("FB L", 10000, 24, "",
                                  stall="Acc Facebook", live_check=1)
    cat_gm = tdb.acc_category_add("GM L", 5000, 24, "",
                                  stall="Gmail", live_check=0)
    # acc có sheet_ref ở 2 sạp + 1 acc SOLD
    for cid, uid, tab, st in [(cat_fb, "100001", "NhapKho", "AVAILABLE"),
                              (cat_gm, "200001", "kho gmail", "AVAILABLE"),
                              (cat_fb, "100002", "NhapKho", "SOLD")]:
        _db.get_conn().execute(
            "INSERT INTO acc_stock (cat_id, uid, password, status, sheet_ref, added_at)"
            " VALUES (?,?,?,?,?,strftime('%s','now'))",
            (cid, uid, "pw", st, f"{tab}:2"))
    _db.get_conn().commit()
    # sheet_ref trùng dòng 2 -> dedupe còn 2 item (NhapKho:2, kho gmail:2)
    calls = []
    col_a = {("NhapKho", 2): "100001", ("kho gmail", 2): "200001"}
    monkeypatch.setattr(_si, "_cli", _fake_cli(calls, col_a))
    monkeypatch.setattr(_db, "get_setting", lambda k, d="": "sid_test"
                        if k == "sheet_import_id" else d)
    p = _poller.FollowerPoller.__new__(_poller.FollowerPoller)
    done_skip = asyncio.run(_poller.FollowerPoller._push_cat_to_sheet(p))
    assert done_skip is None  # hàm không trả gì, chỉ log
    updates = [p for a, p in calls if p and "batchUpdate" in a]
    vals = {d["range"]: d["values"][0][0] for p in updates for d in p["data"]}
    assert vals["NhapKho!L2:L2"] == f"#{cat_fb} FB L · 🏪 Acc Facebook"
    assert vals["kho gmail!L2:L2"] == f"#{cat_gm} GM L · 🏪 Gmail"
