"""Test ví cho giftcode / mã giảm giá % / đổi quà — chạy trên SQLite TẠM, không đụng data.db thật.

Chạy:  ~/fbvenv/bin/python -m pytest tests/test_wallet_promo.py -q
(cwd = ~/workspace/fb-hoan-chinh)
"""


# ── Helpers ví ────────────────────────────────────────────────────
def test_parse_wallet(tdb):
    db = tdb
    assert db.parse_wallet("shop") == "shop"
    assert db.parse_wallet("SHOP") == "shop"
    assert db.parse_wallet("chinh") == "main"
    assert db.parse_wallet("") == "main"
    assert db.parse_wallet("main") == "main"
    assert db.parse_wallet(None) == "main"
    assert db.parse_wallet("xyz") == "main"


def test_wallet_label(tdb):
    db = tdb
    assert db.wallet_label("shop") == "ví shop"
    assert db.wallet_label("main") == "ví chính"
    assert db.wallet_label(None) == "ví chính"


def test_credit_wallet_routes(tdb):
    db = tdb
    tg = 910001
    db.upsert_user(tg, "w1", "Wallet 1")
    assert db.credit_wallet(tg, 50000, "test", "main") is True
    assert db.credit_wallet(tg, 30000, "test", "shop") is True
    u = db.get_user(tg)
    assert u["balance"] == 50000
    assert u["shop_balance"] == 30000


# ── Giftcode theo ví ───────────────────────────────────────────────
def test_giftcode_shop_wallet(tdb):
    db = tdb
    tg = 910002
    db.upsert_user(tg, "w2", "Wallet 2")
    code = db.generate_code(amount=20000, prefix="SHP", wallet="shop")
    ok, amount, _msg, wallet = db.use_code(code, tg)
    assert ok is True and amount == 20000 and wallet == "shop"
    db.credit_wallet(tg, amount, "giftcode", wallet)
    u = db.get_user(tg)
    assert u["shop_balance"] == 20000
    assert u["balance"] == 0, "tiền giftcode ví shop lọt sang ví chính!"


def test_giftcode_default_main(tdb):
    db = tdb
    tg = 910003
    db.upsert_user(tg, "w3", "Wallet 3")
    code = db.generate_code(amount=15000, prefix="NOR")
    ok, amount, _msg, wallet = db.use_code(code, tg)
    assert ok is True and wallet == "main"
    db.credit_wallet(tg, amount, "giftcode", wallet)
    u = db.get_user(tg)
    assert u["balance"] == 15000
    assert u["shop_balance"] == 0


def test_giftcode_old_row_defaults_main(tdb):
    """Dữ liệu cũ (insert trực tiếp, mô phỏng row trước migration) mặc định về ví chính."""
    db = tdb
    tg = 910004
    db.upsert_user(tg, "w4", "Wallet 4")
    import time as _t
    c = db.get_conn()
    c.execute(
        "INSERT INTO giftcodes(code, amount, created_at, max_uses) VALUES(?,?,?,?)",
        ("OLD-ROW-1", 10000, int(_t.time()), 1),
    )
    c.commit()
    ok, amount, _msg, wallet = db.use_code("OLD-ROW-1", tg)
    assert ok is True and wallet == "main", f"row cũ phải mặc định ví chính, got {wallet!r}"
    db.credit_wallet(tg, amount, "giftcode", wallet)
    assert db.get_user(tg)["balance"] == 10000


# ── Mã giảm giá % theo ví ─────────────────────────────────────────
def test_promo_shop_not_applied_to_credit(tdb):
    db = tdb
    tg = 910005
    db.upsert_user(tg, "w5", "Wallet 5")
    ok, _txt = db.create_promo("SHOP10", 10, 0, 0, wallet="shop")
    assert ok
    db.set_user_promo(tg, "SHOP10")
    # Mua credit (ví chính) → mã ví shop KHÔNG được áp, KHÔNG bị tiêu thụ
    new_price, used = db.apply_user_promo(tg, 100000, wallet="main")
    assert new_price == 100000 and used == "", "mã ví shop áp nhầm vào mua credit!"
    ok2, _why, _row = db.promo_valid("SHOP10")
    assert ok2, "mã bị tiêu thụ dù giao dịch sai ví!"


def test_promo_main_applied_to_credit(tdb):
    db = tdb
    tg = 910006
    db.upsert_user(tg, "w6", "Wallet 6")
    ok, _txt = db.create_promo("MAIN20", 20, 0, 0, wallet="main")
    assert ok
    db.set_user_promo(tg, "MAIN20")
    new_price, used = db.apply_user_promo(tg, 100000, wallet="main")
    assert new_price == 80000 and used == "MAIN20"
    assert db.get_user_promo(tg) is None, "mã phải được xóa sau khi dùng"


def test_promo_shop_applied_to_shop(tdb):
    db = tdb
    tg = 910007
    db.upsert_user(tg, "w7", "Wallet 7")
    ok, _txt = db.create_promo("SHOP15", 15, 0, 0, wallet="shop")
    assert ok
    db.set_user_promo(tg, "SHOP15")
    new_price, used = db.apply_user_promo(tg, 200000, wallet="shop")
    assert new_price == 170000 and used == "SHOP15"


def test_promo_main_not_applied_to_shop(tdb):
    db = tdb
    tg = 910008
    db.upsert_user(tg, "w8", "Wallet 8")
    ok, _txt = db.create_promo("MAIN25", 25)
    assert ok
    db.set_user_promo(tg, "MAIN25")
    new_price, used = db.apply_user_promo(tg, 200000, wallet="shop")
    assert new_price == 200000 and used == ""
    ok2, _why, _row = db.promo_valid("MAIN25")
    assert ok2, "mã ví chính bị tiêu thụ khi mua ở shop!"


def test_preview_promo_no_consume(tdb):
    db = tdb
    tg = 910009
    db.upsert_user(tg, "w9", "Wallet 9")
    db.create_promo("PREV10", 10, 0, 0, wallet="shop")
    db.set_user_promo(tg, "PREV10")
    p1, c1 = db.preview_user_promo(tg, 100000, wallet="shop")
    p2, c2 = db.preview_user_promo(tg, 100000, wallet="shop")
    assert (p1, c1) == (90000, "PREV10") == (p2, c2)
    ok, _why, _row = db.promo_valid("PREV10")
    assert ok, "preview không được trừ lượt dùng"


def test_promo_old_row_defaults_main(tdb):
    """Mã % tạo trước migration (không có cột wallet lúc insert) → ví chính."""
    db = tdb
    tg = 910010
    db.upsert_user(tg, "w10", "Wallet 10")
    import time as _t
    c = db.get_conn()
    c.execute(
        "INSERT INTO promo_codes(code, pct, max_uses, used_count, expires_at, created_at) VALUES(?,?,?,?,?,?)",
        ("OLDPC", 30, 0, 0, 0, int(_t.time())),
    )
    c.commit()
    db.set_user_promo(tg, "OLDPC")
    new_price, used = db.apply_user_promo(tg, 100000, wallet="main")
    assert new_price == 70000 and used == "OLDPC"


# ── Đổi quà tiền về ví (mức DB) ────────────────────────────────────
def test_loyalty_money_settings_roundtrip(tdb):
    db = tdb
    tg = 910011
    db.upsert_user(tg, "w11", "Wallet 11")
    db.set_setting("loyalty_redeem_mode", "money")
    db.set_setting("loyalty_redeem_amount", "50000")
    db.set_setting("loyalty_redeem_wallet", "shop")
    db.set_setting("loyalty_redeem_points", "10")
    db.loyalty_add(tg, 10, "test")
    assert db.loyalty_get(tg) == 10
    assert db.loyalty_consume(tg, 10) is True
    assert db.loyalty_get(tg) == 0
    wallet = db.get_setting("loyalty_redeem_wallet", "main")
    db.credit_wallet(tg, 50000, "Đổi điểm loyalty", wallet)
    u = db.get_user(tg)
    assert u["shop_balance"] == 50000 and u["balance"] == 0


# ── M3: promo chỉ bị trừ lượt SAU khi mua thành công ──────────────
def test_preview_promo_khong_tru_luot(tdb):
    """preview_user_promo: tinh gia giam nhung KHONG tru luot, KHONG xoa ma dang giu."""
    db = tdb
    tg = 920001
    db.upsert_user(tg, "w", "W")
    db.create_promo("PREV10", 10, 0, 0, wallet="shop")
    db.set_user_promo(tg, "PREV10")
    new_price, code = db.preview_user_promo(tg, 100000, wallet="shop")
    assert new_price == 90000 and code == "PREV10"
    row = db.get_promo("PREV10")
    assert int(row["used_count"]) == 0, "preview khong duoc tru luot!"
    assert db.get_user_promo(tg) is not None, "preview khong duoc xoa ma dang giu"


def test_finalize_promo_chi_tru_khi_thanh_cong(tdb):
    """finalize_user_promo: tru luot + xoa ma dang giu; ma het hieu luc thi khong tru."""
    db = tdb
    tg = 920002
    db.upsert_user(tg, "w", "W")
    db.create_promo("FIN10", 10, 0, 0, wallet="shop")
    db.set_user_promo(tg, "FIN10")
    # that bai (khong goi finalize) -> luot giu nguyen
    assert int(db.get_promo("FIN10")["used_count"]) == 0
    # thanh cong -> tru 1 luot + xoa ma giu
    assert db.finalize_user_promo(tg, "FIN10") is True
    assert int(db.get_promo("FIN10")["used_count"]) == 1
    assert db.get_user_promo(tg) is None


def test_finalize_promo_ma_het_han_khong_crash(tdb):
    db = tdb
    tg = 920003
    db.upsert_user(tg, "w", "W")
    db.create_promo("EXP10", 10, 1, 0, wallet="shop")
    db.set_user_promo(tg, "EXP10")
    db.consume_promo("EXP10")  # dung het luot
    assert db.finalize_user_promo(tg, "EXP10") is False  # khong tru them, khong crash
    assert int(db.get_promo("EXP10")["used_count"]) == 1
    assert db.get_user_promo(tg) is None  # van xoa ma giu
    assert db.finalize_user_promo(tg, "") is False
    assert db.finalize_user_promo(tg, None) is False


def test_cac_luong_mua_dung_preview_va_finalize():
    """7 luong mua (credit/shop/uid/nhieu uid/gio hang/coc/hop mu) phai dung
    preview_user_promo luc tinh gia + finalize_user_promo sau khi thanh cong."""
    import re
    src = open("botcheckv2/backend/app/bot.py", encoding="utf-8").read()
    assert "db.apply_user_promo(" not in src, "van con apply_user_promo (tru luot som)!"
    assert src.count("db.finalize_user_promo(") >= 7, \
        f"thieu diem finalize (can >=7, thay {src.count('db.finalize_user_promo(')})"
