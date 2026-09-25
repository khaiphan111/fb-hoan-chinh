"""Test /doiqua: menu 2 lựa chọn + chống bấm callback lặp — SQLite TẠM.

- Bấm lặp doiqua_stall 2 lần cùng lúc -> chỉ đổi quà 1 lần, lần 2 bị chặn.
- doiqua_any: chọn ngẫu nhiên 1 loại còn hàng trong sạp FB.
- Đổi xong -> gỡ nút bấm khỏi tin nhắn (không bấm tiếp được).
- Đổi scope giữa chừng -> báo thay đổi, không đổi quà.

Chạy:  ~/fbvenv/bin/python -m pytest tests/test_doiqua_flow.py -q
(cwd = ~/workspace/fb-hoan-chinh)
"""
import asyncio
from types import SimpleNamespace

from app import bot as botmod


class _FakeMsg:
    def __init__(self):
        self.answers = []
        self.kb_removed = False

    async def answer(self, *a, **k):
        self.answers.append((a, k))

    async def edit_reply_markup(self, **k):
        self.kb_removed = True


class _FakeCb:
    def __init__(self, tg, data):
        self.from_user = SimpleNamespace(id=tg)
        self.data = data
        self.bot = None
        self.message = _FakeMsg()
        self.alerts = []

    async def answer(self, text="", show_alert=False):
        self.alerts.append(text)


def _setup_db(db, monkeypatch):
    monkeypatch.setattr(botmod.db, "get_setting", db.get_setting)
    monkeypatch.setattr(botmod.db, "set_setting", db.set_setting)
    monkeypatch.setattr(botmod.db, "acc_category_get", db.acc_category_get)
    monkeypatch.setattr(botmod.db, "loyalty_get", db.loyalty_get)
    db.set_setting("loyalty_redeem_scope", "stall")
    db.set_setting("loyalty_redeem_mode", "acc")
    db.set_setting("loyalty_redeem_points", "40")


def _fake_redeem_factory(db, calls, delay=0.2):
    async def fake(bot, tg_id, cat_id, need):
        calls.append(cat_id)
        await asyncio.sleep(delay)  # giả lập lúc check LIVE
        if not db.loyalty_consume(tg_id, need):
            return False, None, "points_changed"
        return True, {"id": 1, "uid": "fakeuid", "cat_id": cat_id}, ""
    return fake


async def _noop_success(*a, **k):
    pass


# ── 1. Lock chống bấm lặp ──────────────────────────────────────────
def test_doiqua_lock_unit():
    botmod._doiqua_processing.clear()
    assert botmod._doiqua_try_lock(111) is True
    assert botmod._doiqua_try_lock(111) is False, "đang xử lý thì bấm nữa phải chặn"
    botmod._doiqua_unlock(111)
    assert botmod._doiqua_try_lock(111) is True, "xong thì được đổi tiếp"
    botmod._doiqua_unlock(111)


# ── 2. Bấm lặp 2 lần cùng lúc -> chỉ 1 lần đổi quà ──────────────────
def test_double_tap_single_redeem(tdb, monkeypatch):
    db = tdb
    _setup_db(db, monkeypatch)
    calls = []
    monkeypatch.setattr(botmod, "_doiqua_redeem_acc", _fake_redeem_factory(db, calls))
    monkeypatch.setattr(botmod, "_doiqua_success", _noop_success)
    cid = db.acc_category_add("Via DQ tap", 10000, 24, "mô tả", stall="Acc Facebook")
    tg = 930001
    db.upsert_user(tg, "dq", "DQ")
    db.loyalty_add(tg, 40, "test")

    async def run():
        cb1 = _FakeCb(tg, f"doiqua_stall:{cid}")
        cb2 = _FakeCb(tg, f"doiqua_stall:{cid}")
        await asyncio.gather(botmod.on_doiqua_stall(cb1), botmod.on_doiqua_stall(cb2))
        return cb1, cb2

    cb1, cb2 = asyncio.run(run())
    assert len(calls) == 1, f"bấm lặp phải chỉ đổi 1 lần (gọi {len(calls)} lần)"
    assert db.loyalty_get(tg) == 0, "chỉ trừ điểm 1 lần"
    assert any("Đang xử lý" in a for a in cb2.alerts), "lần bấm 2 phải bị chặn"
    assert cb1.message.kb_removed, "đổi xong phải gỡ nút bấm"


# ── 3. doiqua_any: nhận acc bất kỳ còn hàng ─────────────────────────
def test_doiqua_any_picks_stocked_cat(tdb, monkeypatch):
    db = tdb
    _setup_db(db, monkeypatch)
    monkeypatch.setattr(botmod.db, "acc_category_list", db.acc_category_list)
    monkeypatch.setattr(botmod.db, "acc_stock_count", db.acc_stock_count)
    calls = []
    monkeypatch.setattr(botmod, "_doiqua_redeem_acc",
                        _fake_redeem_factory(db, calls, delay=0))
    monkeypatch.setattr(botmod, "_doiqua_success", _noop_success)
    c1 = db.acc_category_add("Via DQ any 1", 10000, 24, "m", stall="Acc Facebook")
    c2 = db.acc_category_add("Via DQ any 2", 10000, 24, "m", stall="Acc Facebook")
    db.acc_stock_add_batch(c1, [{"uid": "any1", "password": "p"}], batch="t")
    db.acc_stock_add_batch(c2, [{"uid": "any2", "password": "p"}], batch="t")
    tg = 930002
    db.upsert_user(tg, "dq2", "DQ2")
    db.loyalty_add(tg, 40, "test")

    cb = _FakeCb(tg, "doiqua_any")
    asyncio.run(botmod.on_doiqua_any(cb))
    assert len(calls) == 1 and calls[0] in (c1, c2), \
        "phải đổi đúng 1 acc ở 1 loại còn hàng"
    assert db.loyalty_get(tg) == 0


# ── 4. Đổi scope giữa chừng -> không đổi quà ────────────────────────
def test_doiqua_scope_changed_no_redeem(tdb, monkeypatch):
    db = tdb
    _setup_db(db, monkeypatch)
    calls = []
    monkeypatch.setattr(botmod, "_doiqua_redeem_acc", _fake_redeem_factory(db, calls))
    monkeypatch.setattr(botmod, "_doiqua_success", _noop_success)
    db.set_setting("loyalty_redeem_scope", "cat")  # admin đổi giữa chừng
    cid = db.acc_category_add("Via DQ scope", 10000, 24, "m", stall="Acc Facebook")
    tg = 930003
    db.upsert_user(tg, "dq3", "DQ3")
    db.loyalty_add(tg, 40, "test")

    cb = _FakeCb(tg, f"doiqua_stall:{cid}")
    asyncio.run(botmod.on_doiqua_stall(cb))
    assert calls == [], "scope đổi thì không được đổi quà"
    assert db.loyalty_get(tg) == 40, "không được trừ điểm"
    assert any("thay đổi" in a for a in cb.alerts)
