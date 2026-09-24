"""Test đổi quà acc bất kỳ trong gian hàng FB — SQLite TẠM, không đụng data.db thật.

Chạy:  ~/fbvenv/bin/python -m pytest tests/test_loyalty_stall.py -q
(cwd = ~/workspace/fb-hoan-chinh)
"""
import asyncio

import pytest

from app import bot as botmod
from app import db as dbmod


def _mkcat(db, name, stall="Acc Facebook"):
    return db.acc_category_add(name, 10000, 24, "mô tả", stall=stall)


def _add(db, cid, uids):
    rows = [{"uid": u, "password": "p"} for u in uids]
    added, _ = db.acc_stock_add_batch(cid, rows, batch="test")
    assert added == len(uids)


# ── 1. _fb_stall_gift_cats: chỉ loại còn hàng của sạp FB ──────────
def test_fb_stall_gift_cats(tdb, monkeypatch):
    db = tdb
    monkeypatch.setattr(botmod.db, "acc_category_list", db.acc_category_list)
    monkeypatch.setattr(botmod.db, "acc_stock_count", db.acc_stock_count)
    c1 = _mkcat(db, "Via FB 1")          # sạp FB, có hàng
    c2 = _mkcat(db, "Via FB 2")          # sạp FB, hết hàng
    c3 = _mkcat(db, "Gmail xịn", stall="kho gmail")  # sạp khác, có hàng
    _add(db, c1, ["u1", "u2"])
    _add(db, c3, ["g1"])
    cats = botmod._fb_stall_gift_cats()
    ids = [c["id"] for c, _n in cats]
    assert c1 in ids, "loại FB còn hàng phải hiện"
    assert c2 not in ids, "loại FB hết hàng phải ẩn"
    assert c3 not in ids, "loại sạp khác không được hiện"


# ── 2. _loyalty_gift_desc hiện phạm vi stall ───────────────────────
def test_gift_desc_stall_scope(tdb, monkeypatch):
    db = tdb
    monkeypatch.setattr(botmod.db, "get_setting", db.get_setting)
    monkeypatch.setattr(botmod.db, "set_setting", db.set_setting)
    db.set_setting("loyalty_redeem_mode", "acc")
    db.set_setting("loyalty_redeem_scope", "stall")
    db.set_setting("loyalty_redeem_points", "40")
    desc = botmod._loyalty_gift_desc()
    assert "bất kỳ" in desc and "40" in desc


# ── 3. _doiqua_redeem_acc: trừ điểm + giao acc (mock live check) ──
def _fake_sell_factory():
    async def fake_sell(bot, tg_id, price_total, qty, pick_fn, check_live=True):
        cands = pick_fn(set())
        assert cands, "phải có ứng viên để bán"
        row = cands[0]
        oid, _ = dbmod.acc_sell_one(row["cat_id"], tg_id, 0)
        return [{"id": oid, "uid": row["uid"], "cat_id": row["cat_id"]}], None
    return fake_sell


def test_doiqua_redeem_acc_ok(tdb, monkeypatch):
    db = tdb
    monkeypatch.setattr(botmod, "_sell_live_stock", _fake_sell_factory())
    cid = _mkcat(db, "Via doi qua")
    _add(db, cid, ["dq1"])
    tg = 920001
    db.upsert_user(tg, "dq", "Doi Qua")
    db.loyalty_add(tg, 40, "test")
    ok, order, reason = asyncio.run(botmod._doiqua_redeem_acc(None, tg, cid, 40))
    assert ok and order and reason == ""
    assert db.loyalty_get(tg) == 0, "phải trừ 40 điểm"
    assert db.acc_stock_count(cid) == 0, "acc phải được giao đi"


def test_doiqua_redeem_acc_out_of_stock_no_consume(tdb, monkeypatch):
    db = tdb
    monkeypatch.setattr(botmod, "_sell_live_stock", _fake_sell_factory())
    cid = _mkcat(db, "Via het hang")
    tg = 920002
    db.upsert_user(tg, "dq2", "Doi Qua 2")
    db.loyalty_add(tg, 40, "test")
    ok, order, reason = asyncio.run(botmod._doiqua_redeem_acc(None, tg, cid, 40))
    assert not ok and reason == "out_of_stock"
    assert db.loyalty_get(tg) == 40, "hết hàng thì KHÔNG được trừ điểm"


def test_doiqua_redeem_acc_no_live_refunds_points(tdb, monkeypatch):
    db = tdb

    async def fake_sell_no_live(bot, tg_id, price_total, qty, pick_fn, check_live=True):
        return None, "short"

    monkeypatch.setattr(botmod, "_sell_live_stock", fake_sell_no_live)
    cid = _mkcat(db, "Via die")
    _add(db, cid, ["dq3"])
    tg = 920003
    db.upsert_user(tg, "dq3", "Doi Qua 3")
    db.loyalty_add(tg, 40, "test")
    ok, order, reason = asyncio.run(botmod._doiqua_redeem_acc(None, tg, cid, 40))
    assert not ok and reason == "no_live"
    assert db.loyalty_get(tg) == 40, "không có acc LIVE thì phải hoàn điểm"
