"""Regression test: _import_stock_rows không được NameError vì link_idx.

Bug 2026-09-25 (commit 94aa7c1): tách _resolve_stock_links ra hàm riêng nhưng sót
`if link_idx:` trong _import_stock_rows -> NameError mỗi lần nhập kho có link FB:
acc đã INSERT vào kho nhưng user không thấy "Nhập kho xong", sheet không được
đánh dấu, quét chất lượng không chạy.

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
import app.sheet_import as _si  # noqa: E402


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


def _mk_rows():
    links = [
        "https://www.facebook.com/profile.php?id=100004561894813",
        "https://www.facebook.com/profile.php?id=61586556314994",
        "https://www.facebook.com/profile.php?id=100000470945598",
    ]
    return [
        {"uid": lk, "password": "", "created_date": "", "backup_mail": "",
         "note": "", "totp": "", "cookie": "", "token": "", "_sheet_row": i + 2}
        for i, lk in enumerate(links)
    ]


def test_import_stock_rows_with_links_no_nameerror(tdb, monkeypatch):
    cat_id = tdb.acc_category_add("Via Test", 10000, 24, "", stall="Acc Facebook",
                                  live_check=0)
    c = tdb.acc_category_get(cat_id)
    rows = _mk_rows()
    msg, wait = _FakeMsg(), _FakeWait()

    # chặn quét nền (tránh gọi mạng) và ghi sheet (tránh gọi hatch_gws_cli)
    spawned = []
    monkeypatch.setattr(asyncio, "create_task",
                        lambda coro, **k: spawned.append(coro) or asyncio.Future())
    written = []

    async def _fake_write(sid, tab, updates):
        written.append((sid, tab, updates))

    monkeypatch.setattr(_si, "write_updates", _fake_write)

    asyncio.run(_bot._import_stock_rows(
        rows, cat_id, 0, 0, c, msg, wait,
        sheet_ctx={"sheet_id": "sid_test", "tab": "NhapKho"}))

    # 1. không NameError -> chạy tới cuối, báo "Nhập kho xong"
    assert any("Nhập kho xong" in t for t in wait.texts), wait.texts
    # 2. 3 acc đã vào kho, UID giải từ link profile.php?id=
    got = {r["uid"] for r in
           tdb.get_conn().execute("SELECT uid FROM acc_stock WHERE cat_id=?",
                                  (cat_id,)).fetchall()}
    assert got == {"100004561894813", "61586556314994", "100000470945598"}, got
    # 3. ghi trạng thái ngược vào sheet (cột I = ✅ OK, cột A = UID số)
    assert written, "không ghi write-back vào sheet"
    _sid, _tab, updates = written[0]
    by_col = {}
    for _rnum, col, val in updates:
        by_col.setdefault(col, []).append(val)
    assert by_col.get(9) == ["✅ OK"] * 3, updates
    assert sorted(by_col.get(1, [])) == sorted(got), updates
    # 4. quét nền được lên lịch
    assert spawned, "quét chất lượng nền không được tạo task"


def test_resolve_stock_links_returns_link_idx(tdb):
    """_resolve_stock_links trả đủ 5 phần tử, kèm link_idx."""
    rows = _mk_rows()
    wait = _FakeWait()
    out = asyncio.run(_bot._resolve_stock_links(rows, wait))
    assert len(out) == 5
    resolved, failed, failed_lines, uid_to_link, link_idx = out
    assert resolved == 3 and failed == 0
    assert link_idx == [0, 1, 2]
    assert uid_to_link == {
        "100004561894813": "https://www.facebook.com/profile.php?id=100004561894813",
        "61586556314994": "https://www.facebook.com/profile.php?id=61586556314994",
        "100000470945598": "https://www.facebook.com/profile.php?id=100000470945598",
    }
