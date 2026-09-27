"""Test shop buff tương tác: seed dịch vụ, ví buff riêng, đơn hàng, validation.

Chạy trên SQLite TẠM (fixture tdb) — không đụng data.db thật.
Chạy:  ~/fbvenv/bin/python -m pytest tests/test_buff.py -q
"""
import math

import pytest

from app.handlers import buff as buffmod


# ── 1. Seed đúng 25 dịch vụ, chỉ seed khi trống ──
def test_buff_seed_count_25(tdb):
    db = tdb
    n = db.get_conn().execute(
        "SELECT COUNT(*) n FROM buff_services").fetchone()["n"]
    assert int(n) == 25


def test_buff_seed_idempotent(tdb):
    db = tdb
    db._seed_buff_services()
    db._seed_buff_services()
    n = db.get_conn().execute(
        "SELECT COUNT(*) n FROM buff_services").fetchone()["n"]
    assert int(n) == 25


def test_buff_seed_platforms(tdb):
    db = tdb
    plats = db.buff_platforms()
    keys = {p["key"] for p in plats}
    assert keys == {"tiktok", "facebook", "instagram", "youtube",
                    "telegram", "shopee", "threads"}
    # TikTok có 4 loại, 7 gói
    assert len(db.buff_categories("tiktok")) == 4
    n_tiktok = sum(len(db.buff_services_list("tiktok", c["category_key"]))
                   for c in db.buff_categories("tiktok"))
    assert n_tiktok == 7


# ── 2. Tính tổng tiền: ceil(qty/1000 * sell_price) ──
def test_buff_total_calc():
    assert buffmod._calc_total(1000, 5000) == 5000
    assert buffmod._calc_total(500, 5000) == 2500
    assert buffmod._calc_total(1500, 5000) == 7500
    # ceil: 1 đơn vị * 5000/1000 = 5
    assert buffmod._calc_total(1, 5000) == 5
    assert buffmod._calc_total(1001, 5000) == math.ceil(1001 / 1000 * 5000)


# ── 3. Ví buff riêng: cộng/trừ/không đủ ──
def test_buff_wallet_add_deduct(tdb):
    db = tdb
    tg = 910001
    db.upsert_user(tg, "buff_u", "Buff User")
    assert db.buff_get_balance(tg) == 0
    assert db.buff_adjust_balance(tg, 50000, "test nap") is True
    assert db.buff_get_balance(tg) == 50000
    assert db.buff_adjust_balance(tg, -20000, "test tru") is True
    assert db.buff_get_balance(tg) == 30000
    # Trừ quá số dư -> False, số dư giữ nguyên
    assert db.buff_adjust_balance(tg, -30001, "test over") is False
    assert db.buff_get_balance(tg) == 30000
    # Trừ đúng hết -> OK
    assert db.buff_adjust_balance(tg, -30000, "test all") is True
    assert db.buff_get_balance(tg) == 0


def test_buff_wallet_separate_from_main_and_shop(tdb):
    """Ví buff độc lập: nạp ví buff không ảnh hưởng ví chính/ví shop."""
    db = tdb
    tg = 910002
    db.upsert_user(tg, "buff_u2", "Buff User 2")
    db.adjust_balance(tg, 100000, "test main")
    db.adjust_shop_balance(tg, 70000, "test shop")
    db.buff_adjust_balance(tg, 50000, "test buff")
    u = db.get_user(tg)
    assert int(u["balance"]) == 100000
    assert int(u["shop_balance"]) == 70000
    assert db.buff_get_balance(tg) == 50000
    # Trừ ví buff không động 2 ví kia
    db.buff_adjust_balance(tg, -10000, "test")
    u = db.get_user(tg)
    assert int(u["balance"]) == 100000
    assert int(u["shop_balance"]) == 70000


# ── 4. Đơn hàng: tạo -> pending -> update ──
def test_buff_order_lifecycle(tdb):
    db = tdb
    tg = 910003
    db.upsert_user(tg, "buff_u3", "Buff User 3")
    svc = db.buff_services_list("tiktok", "views")[0]
    total = buffmod._calc_total(2000, int(svc["sell_price"]))
    cost = buffmod._calc_total(2000, int(svc["cost_price"]))
    o = db.buff_order_create(tg, int(svc["id"]), "https://tiktok.com/x",
                             2000, total, cost)
    assert o["status"] == "pending"
    assert o["total_price"] == total
    assert o["code"].startswith("B")
    assert len(db.buff_orders_pending()) == 1
    assert db.buff_order_update(o["id"], status="running",
                                panel_order_id="P999") is True
    assert db.buff_order_get(o["id"])["status"] == "running"
    assert len(db.buff_orders_pending()) == 0
    mine = db.buff_orders_by_user(tg)
    assert len(mine) == 1 and mine[0]["code"] == o["code"]


# ── 5. Min/max validation trên dữ liệu seed ──
def test_buff_min_max_seed(tdb):
    db = tdb
    # Follow Việt TikTok: min 50, max 100000
    svcs = db.buff_services_list("tiktok", "followers")
    by_name = {s["name"]: s for s in svcs}
    assert int(by_name["Follow Việt"]["min_qty"]) == 50
    assert int(by_name["Follow Việt"]["max_qty"]) == 100000
    # Like Threads: min 1, max 20000
    likes = db.buff_services_list("threads", "likes")
    assert int(likes[0]["min_qty"]) == 1
    assert int(likes[0]["max_qty"]) == 20000


# ── 6. Bật/tắt + đổi giá bán ──
def test_buff_toggle(tdb):
    db = tdb
    svc = db.buff_services_list("tiktok", "views")[0]
    sid = int(svc["id"])
    assert int(svc["enabled"]) == 1
    assert db.buff_service_toggle(sid) is False   # tắt
    hidden = [s["id"] for s in db.buff_services_list("tiktok", "views")]
    assert sid not in hidden                      # list enable không còn gói
    all_rows = db.buff_services_list("tiktok", "views", only_enabled=False)
    assert sid in [s["id"] for s in all_rows]     # nhưng DB vẫn còn
    assert db.buff_service_toggle(sid) is True    # bật lại
    assert sid in [s["id"] for s in db.buff_services_list("tiktok", "views")]


def test_buff_set_price(tdb):
    db = tdb
    svc = db.buff_services_list("instagram", "views")[0]
    sid = int(svc["id"])
    assert db.buff_service_set_price(sid, 999) is True
    assert int(db.buff_service_get(sid)["sell_price"]) == 999
    # Giá vốn không đổi
    assert int(db.buff_service_get(sid)["cost_price"]) == int(svc["cost_price"])


# ── 7. PayOS target buff: tạo đơn + quyết toán cộng vào ví buff ──
def test_payos_buff_target_credits_buff_wallet(tdb):
    db = tdb
    tg = 910004
    db.upsert_user(tg, "buff_u4", "Buff User 4")
    db.create_payos_order(777001, tg, 60000, target="buff")
    res = db.settle_payos_order(777001)
    assert res["ok"] is True
    assert res["target"] == "buff"
    assert db.buff_get_balance(tg) == 60000
    u = db.get_user(tg)
    assert int(u["balance"]) == 0  # ví chính không bị cộng
