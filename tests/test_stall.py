"""Test gian hàng (stall) + tab Sheet — chạy trên SQLite TẠM, không đụng data.db thật.

Chạy:  ~/fbvenv/bin/python -m pytest tests/test_stall.py -q
(cwd = ~/workspace/fb-hoan-chinh)
"""
from app import sheet_import as si


def test_sanitize_tab_name():
    assert si.sanitize_tab_name("Gmail Edu") == "Gmail Edu"
    assert si.sanitize_tab_name("a/b\\c?d*e[f]g") == "a b c d e f g"
    assert si.sanitize_tab_name("  nhiều   space  ") == "nhiều space"
    assert si.sanitize_tab_name("") == "Sheet"
    assert len(si.sanitize_tab_name("x" * 200)) == 90


def test_tab_for_stall():
    # Sạp FB mặc định dùng tab chung cũ
    assert si.tab_for_stall("Acc Facebook", "NhapKho") == "NhapKho"
    assert si.tab_for_stall("", "NhapKho") == "NhapKho"
    assert si.tab_for_stall(None) == "NhapKho"
    # Sạp mới -> tab theo tên
    assert si.tab_for_stall("Gmail", "NhapKho") == "Gmail"
    assert si.tab_for_stall("Tik Tok VN", "NhapKho") == "Tik Tok VN"


def test_category_stall_default(tdb):
    db = tdb
    cid = db.acc_category_add("Via Test", 10000, 24, "mô tả")
    assert cid > 0
    c = db.acc_category_get(cid)
    keys = c.keys()
    assert ("stall" in keys and (c["stall"] or "Acc Facebook")) == "Acc Facebook"
    assert ("live_check" in keys and int(c["live_check"] or 0)) == 1


def test_themstall_category(tdb):
    db = tdb
    cid = db.acc_category_add("Gmail Edu", 15000, 24, "gmail", stall="Gmail", live_check=0)
    assert cid > 0
    c = db.acc_category_get(cid)
    assert c["stall"] == "Gmail"
    assert int(c["live_check"]) == 0


def test_stall_list(tdb):
    db = tdb
    db.acc_category_add("Via A", 10000, 24, "")
    db.acc_category_add("Gmail Edu", 15000, 24, "", stall="Gmail", live_check=0)
    stalls = {s["stall"]: s["n"] for s in db.acc_stall_list()}
    assert stalls.get("Acc Facebook") == 1
    assert stalls.get("Gmail") == 1
