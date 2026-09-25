"""Test chống UID lỗi ".0" do Excel convert số float — chạy trên SQLite TẠM.

- extract_uid("61593959792972.0") -> "61593959792972" (không check/giao nhầm UID lỗi).
- _parse_stock_file đọc cell Excel dạng số -> ép về số nguyên, không lưu "xxx.0" vào kho.
  (Bug thật 25/09: 3 acc nhập từ .xlsx bị lưu UID "xxx.0" -> check_uid báo die oan
  -> re-check cách ly nhầm acc đang LIVE.)

Chạy:  ~/fbvenv/bin/python -m pytest tests/ -q
(cwd = ~/workspace/fb-hoan-chinh)
"""
import io
import os
import sys

os.environ.pop("SUPABASE_DB_URL", None)

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "botcheckv2", "backend")
sys.path.insert(0, os.path.normpath(_BACKEND))

import app.bot as _bot  # noqa: E402
from app.fb import extract_uid  # noqa: E402


def test_extract_uid_strips_float_tail():
    assert extract_uid("61593959792972.0") == "61593959792972"
    assert extract_uid("100004561894813.00") == "100004561894813"


def test_extract_uid_keeps_normal_cases():
    # UID thuần giữ nguyên
    assert extract_uid("61593959792972") == "61593959792972"
    # Link profile.php?id=
    assert extract_uid("https://www.facebook.com/profile.php?id=100004561894813") \
        == "100004561894813"
    # Username (không phải số) không bị đụng
    assert extract_uid("zuck") == "zuck"
    assert extract_uid("john.0doe") == "john.0doe"
    # Rỗng
    assert extract_uid("") == ""
    assert extract_uid(None) == ""


def _make_xlsx(rows):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_xlsx_float_uid_becomes_int():
    raw = _make_xlsx([
        ["UID", "MK"],                      # dòng tiêu đề -> bỏ qua
        [61593959792972.0, "pass1"],        # Excel convert thành float
        [61594488264377, "pass2"],          # số nguyên thuần
    ])
    rows, err = _bot._parse_stock_file(raw, "acc.xlsx")
    assert err is None
    assert [r["uid"] for r in rows] == ["61593959792972", "61594488264377"]
    assert all("." not in r["uid"] for r in rows)


def test_parse_xlsx_scientific_notation_uid():
    # Số rất dài Excel có thể hiển thị dạng 6.1594E+13 — openpyxl vẫn đọc ra float gốc
    raw = _make_xlsx([[6.1593959792972e13, "p"]])
    rows, err = _bot._parse_stock_file(raw, "acc.xlsx")
    assert err is None
    assert rows[0]["uid"] == "61593959792972"


def test_parse_txt_dot_zero_uid_normalized():
    # .txt: _smart_stock_fields nhận diện "61593959792972.0" là UID và chuẩn hoá ngay
    # (trước đây isdigit() False -> bị đẩy thành mật khẩu, dòng mất UID)
    raw = b"61593959792972.0|pass1\n"
    rows, err = _bot._parse_stock_file(raw, "acc.txt")
    assert err is None
    assert rows[0]["uid"] == "61593959792972"
    assert rows[0]["password"] == "pass1"
