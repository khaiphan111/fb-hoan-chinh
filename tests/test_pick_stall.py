"""Test chọn gian hàng khi thêm loại acc — chạy trên SQLite TẠM, không đụng data.db thật.

Chạy:  ~/fbvenv/bin/python -m pytest tests/ -q
(cwd = ~/workspace/fb-hoan-chinh)
"""
import asyncio
import os
import sys

os.environ.pop("SUPABASE_DB_URL", None)

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "botcheckv2", "backend")
sys.path.insert(0, os.path.normpath(_BACKEND))

import app.bot as _bot  # noqa: E402
import app.shop_menu as _sm  # noqa: E402


def _mk_stalls(tdb):
    tdb.acc_category_add("Via Việt", 25000, 24, "via vn", stall="Acc Facebook",
                         live_check=1)
    tdb.acc_category_add("Gmail Edu", 15000, 24, "", stall="Gmail", live_check=0)


def test_stall_live_check_inherit(tdb):
    _mk_stalls(tdb)
    assert tdb.acc_stall_live_check("Acc Facebook") == 1
    assert tdb.acc_stall_live_check("Gmail") == 0
    assert tdb.acc_stall_live_check("Sạp Chưa Có") == 0


def test_parse_step_pick_stall(tdb):
    _mk_stalls(tdb)
    ok, val, _ = _sm._parse_step("pick_stall", "Gmail")
    assert ok and val == "Gmail"
    ok, val, _ = _sm._parse_step("pick_stall", "gmail")  # không phân biệt hoa/thường
    assert ok and val == "Gmail"
    ok, _, _ = _sm._parse_step("pick_stall", "Sạp Không Tồn Tại")
    assert not ok
    ok, _, _ = _sm._parse_step("pick_stall", "   ")
    assert not ok


def test_add_cat_flow_build():
    build = _sm.FLOWS["add_cat"]["build"]
    assert len(_sm.FLOWS["add_cat"]["steps"]) == 5
    assert _sm.FLOWS["add_cat"]["steps"][0][1] == "pick_stall"
    cmd = build(["Gmail", "Gmail Edu", 15000, "24h", "-"])
    assert cmd == "/themloai Gmail Edu | 15000 | 24h |  | Gmail"
    cmd2 = build(["Acc Facebook", "Via Việt", 25000, "24h", "Via VN xịn"])
    assert cmd2 == "/themloai Via Việt | 25000 | 24h | Via VN xịn | Acc Facebook"


def test_stall_pick_kb(tdb):
    _mk_stalls(tdb)
    kb = _sm._stall_pick_kb("add_cat", 0, "kho")
    assert kb is not None
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "shopm:pick:add_cat:0:Gmail" in datas
    assert "shopm:pick:add_cat:0:Acc Facebook" in datas


class _FakeUser:
    id = 1
    full_name = "Test Admin"


class _FakeMsg:
    def __init__(self, text):
        self.text = text
        self.from_user = _FakeUser()
        self.answers = []

    async def answer(self, text, **kw):
        self.answers.append(text)


def _cat_by_name(tdb, name):
    r = tdb.get_conn().execute(
        "SELECT * FROM acc_categories WHERE name=?", (name,)).fetchone()
    return dict(r) if r else None


def test_on_themloai_with_stall(tdb, monkeypatch):
    _mk_stalls(tdb)
    monkeypatch.setattr("app.handlers.stock._is_admin", lambda uid: True)

    m = _FakeMsg("/themloai Gmail Clone | 12000 | 24h | - | Gmail")
    asyncio.run(_bot.on_themloai(m))
    assert any("Đã thêm loại acc" in a for a in m.answers)
    c = _cat_by_name(tdb, "Gmail Clone")
    assert c and c["stall"] == "Gmail" and int(c["live_check"]) == 0

    # không chỉ định sạp -> về sạp FB mặc định như cũ
    m2 = _FakeMsg("/themloai Via US | 30000 | 24h | via us")
    asyncio.run(_bot.on_themloai(m2))
    c2 = _cat_by_name(tdb, "Via US")
    assert c2 and c2["stall"] == "Acc Facebook" and int(c2["live_check"]) == 1

    # sạp không tồn tại -> báo lỗi, không tạo loại
    m3 = _FakeMsg("/themloai Loại Lạ | 1000 | 24h | - | Sạp Ảo")
    asyncio.run(_bot.on_themloai(m3))
    assert any("Chưa có gian hàng" in a for a in m3.answers)
    assert _cat_by_name(tdb, "Loại Lạ") is None
