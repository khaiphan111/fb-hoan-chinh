"""Test cột K 'Tình trạng' trên Google Sheet — chạy trên SQLite TẠM, không đụng data.db thật.

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

from app import sheet_import as _si  # noqa: E402


def test_headers_co_tinh_trang():
    assert len(_si.SHEET_HEADERS) == 12
    assert _si.SHEET_HEADERS[8] == "Trạng thái"   # cột I giữ nguyên
    assert _si.SHEET_HEADERS[9] == "Đã bán"       # cột J giữ nguyên
    assert _si.SHEET_HEADERS[10] == "Tình trạng"  # cột K giữ nguyên
    assert _si.SHEET_HEADERS[11] == "Loại / Gian hàng"  # cột L mới
    assert _si.HEALTH_COL == 11
    assert _si.CAT_COL == 12
    assert set(_si.HEALTH_MARKS) == {"🟢 LIVE", "☠️ DIE"}


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


def test_push_health_marks(monkeypatch):
    calls = []
    col_a = {("NhapKho", 2): "100001", ("NhapKho", 3): "100002",
             ("Gmail", 2): "200001"}
    monkeypatch.setattr(_si, "_cli", _fake_cli(calls, col_a))
    items = [
        {"uid": "100001", "tab": "NhapKho", "row": 2, "mark": "🟢 LIVE"},
        {"uid": "999999", "tab": "NhapKho", "row": 3, "mark": "☠️ DIE"},
        {"uid": "200001", "tab": "Gmail", "row": 2, "mark": "☠️ DIE"},
        {"uid": "100001", "tab": "NhapKho", "row": 2, "mark": "XXX"},
    ]
    done, skip = asyncio.run(_si.push_health_marks("sheet123", items))
    assert done == 2
    assert skip == 1
    updates = [p for a, p in calls if p and "batchUpdate" in a]
    assert len(updates) == 2  # 2 tab
    vals = {d["range"]: d["values"][0][0] for p in updates for d in p["data"]}
    assert vals["NhapKho!K1:K1"] == "Tình trạng"
    assert vals["Gmail!K1:K1"] == "Tình trạng"
    assert vals["NhapKho!K2:K2"] == "🟢 LIVE"
    assert vals["Gmail!K2:K2"] == "☠️ DIE"
    assert "NhapKho!K3:K3" not in vals  # UID không khớp -> không ghi


def test_push_health_marks_empty():
    assert asyncio.run(_si.push_health_marks("", [])) == (0, 0)
    assert asyncio.run(_si.push_health_marks("sheet123", [])) == (0, 0)


def test_build_health_items():
    qrows = [
        {"id": 1, "uid": "100001", "sheet_ref": "NhapKho:2", "status": "AVAILABLE"},
        {"id": 2, "uid": "100002", "sheet_ref": "NhapKho:3", "status": "AVAILABLE"},
        {"id": 3, "uid": "100003", "sheet_ref": "NhapKho:4", "status": "DIE"},
        {"id": 4, "uid": "100004", "sheet_ref": "", "status": "AVAILABLE"},
        {"id": 5, "uid": "100005", "sheet_ref": "NhapKho:6", "status": "AVAILABLE"},
    ]
    items = _si.build_health_items(qrows, live_ids={1}, die_ids={2})
    by_uid = {i["uid"]: i["mark"] for i in items}
    assert by_uid == {"100001": "🟢 LIVE", "100002": "☠️ DIE",
                      "100003": "☠️ DIE"}  # backfill acc DIE cũ
    # acc lỗi check (id 5) và acc không có sheet_ref (id 4) bị bỏ qua
    assert "100004" not in by_uid and "100005" not in by_uid
    # dedupe theo tab:row
    dup = qrows + [{"id": 6, "uid": "100006", "sheet_ref": "NhapKho:2",
                    "status": "DIE"}]
    items2 = _si.build_health_items(dup, live_ids={1}, die_ids={2})
    assert len([i for i in items2 if (i["tab"], i["row"]) == ("NhapKho", 2)]) == 1
