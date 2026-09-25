"""Test credits trong parser, giftcode, loyalty — SQLite TẠM, không đụng data.db thật.

- parse_wallet("credits") -> "credits" (trước đây bị ép lặng lẽ về "main").
- credit_wallet(..., "credits") cộng vào credits, không đụng ví chính/shop.
- Giftcode credits: tạo mã -> đổi mã -> +credits, hiển thị "N credits".
- Mã credits KHÔNG kích hoạt nâng VIP (chỉ ví chính mới tính).
- create_promo(wallet="credits") bị từ chối (mã giảm % vô nghĩa với credits).
- Validator menu "wallet" chấp nhận "credits".

Chạy:  ~/fbvenv/bin/python -m pytest tests/test_credits_wallet.py -q
(cwd = ~/workspace/fb-hoan-chinh)
"""
from app import db as dbmod
from app import shop_menu as smod


# ── 1. parse_wallet / wallet_label / wallet_amount_text ─────────────
def test_parse_wallet_credits():
    assert dbmod.parse_wallet("credits") == "credits"
    assert dbmod.parse_wallet("credit") == "credits"
    assert dbmod.parse_wallet("CREDITS") == "credits"
    assert dbmod.parse_wallet("shop") == "shop"
    assert dbmod.parse_wallet("vishop") == "shop"
    assert dbmod.parse_wallet("main") == "main"
    assert dbmod.parse_wallet("xyz") == "main", "giá trị lạ vẫn về main như cũ"
    assert dbmod.parse_wallet("") == "main"


def test_wallet_label_and_amount_text():
    assert dbmod.wallet_label("credits") == "credits"
    assert dbmod.wallet_label("main") == "ví chính"
    assert dbmod.wallet_label("shop") == "ví shop"
    assert dbmod.wallet_amount_text("credits", 100) == "100 credits"
    assert dbmod.wallet_amount_text("main", 50000) == "50.000đ"
    assert dbmod.wallet_amount_text("shop", 200000) == "200.000đ"


# ── 2. credit_wallet route đúng ví ──────────────────────────────────
def _bals(db, tg):
    u = db.get_user(tg)
    return int(u["balance"] or 0), int(u["shop_balance"] or 0), db.get_credits(tg)


def test_credit_wallet_routes(tdb):
    db = tdb
    tg = 940001
    db.upsert_user(tg, "cr", "Credits")
    assert db.credit_wallet(tg, 100, "test credits", "credits") is True
    assert _bals(db, tg) == (0, 0, 100), "chỉ credits được cộng"
    # main/shop vẫn như cũ
    db.credit_wallet(tg, 50000, "test main", "main")
    db.credit_wallet(tg, 20000, "test shop", "shop")
    assert _bals(db, tg) == (50000, 20000, 100)


# ── 3. Giftcode credits end-to-end ──────────────────────────────────
def test_giftcode_credits_end_to_end(tdb):
    db = tdb
    tg = 940002
    db.upsert_user(tg, "gc", "Giftcode")
    code = db.generate_code(amount=100, prefix="CRT", wallet="credits")
    ok, amount, _msg, wallet = db.use_code(code, tg)
    assert ok and wallet == "credits" and amount == 100, "mã credits phải giữ ví credits"
    db.credit_wallet(tg, amount, f"Sử dụng Giftcode: {code}", wallet)
    assert _bals(db, tg) == (0, 0, 100), "đổi mã credits: chỉ +credits"
    # mã credits không kích hoạt nâng VIP (chỉ ví chính mới tính)
    assert db.parse_wallet(wallet) != "main"


def test_giftcode_main_still_vnd(tdb):
    db = tdb
    tg = 940003
    db.upsert_user(tg, "gm", "GiftcodeMain")
    code = db.generate_code(amount=50000, prefix="VND", wallet="main")
    ok, amount, _msg, wallet = db.use_code(code, tg)
    assert ok and wallet == "main"
    db.credit_wallet(tg, amount, "test", wallet)
    assert _bals(db, tg) == (50000, 0, 0)


# ── 4. Promo % từ chối credits ──────────────────────────────────────
def test_create_promo_rejects_credits(tdb):
    db = tdb
    ok, txt = db.create_promo("CRTPCT", 20, 0, 0, "credits")
    assert ok is False, "mã giảm % với ví credits phải bị từ chối"
    ok2, _ = db.create_promo("MAINPCT", 20, 0, 0, "main")
    assert ok2 is True, "mã giảm % ví chính vẫn tạo được"


# ── 5. Validator menu chấp nhận credits ─────────────────────────────
def test_shop_menu_wallet_validator_accepts_credits():
    ok, val, _err = smod._parse_step("wallet", "credits")
    assert ok and val == "credits"
    ok, val, _err = smod._parse_step("wallet", "chinh")
    assert ok and val == "main"
    ok, val, _err = smod._parse_step("wallet", "shop")
    assert ok and val == "shop"
    ok, _val, err = smod._parse_step("wallet", "bitcoin")
    assert not ok and err, "ví lạ vẫn bị từ chối"
