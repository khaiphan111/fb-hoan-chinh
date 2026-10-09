"""Test hardening phần TIỀN (P1) — chạy trên SQLite tạm, không đụng DB thật.

Bối cảnh (xem BAO-CAO-REVIEW):
  M1 rút hoa hồng vượt số dư (không giữ chỗ) -> nay giữ chỗ nguyên tử.
  M2 duyệt ứng tiền cộng ví 2 lần (bỏ qua rowcount) -> nay lần 2 trả None.
  M5/M6 số dư/credits âm khi 2 tiến trình -> nay điều kiện nằm trong SQL + rowcount.
"""
import pytest


def _set_ref(db, tg_id: int, earnings: int, withdrawn: int = 0):
    c = db.get_conn()
    c.execute("UPDATE tg_users SET ref_earnings=?, ref_withdrawn=? WHERE tg_id=?",
              (earnings, withdrawn, tg_id))
    c.commit()


def _val(db, tg_id: int, col: str) -> int:
    return int(db.get_user(tg_id)[col] or 0)


# ── M1: giữ chỗ tiền khi rút hoa hồng ─────────────────────────────────
def test_withdrawal_reserve_khong_cho_rut_vuot(tdb):
    db = tdb
    tg = 880001
    db.upsert_user(tg, "wd1", "WD 1")
    _set_ref(db, tg, 1_000_000)

    r1, _ = db.withdrawal_reserve(tg, 500_000, "VCB - 111", 0)
    r2, _ = db.withdrawal_reserve(tg, 500_000, "VCB - 111", 0)
    assert r1 and r2
    assert _val(db, tg, "ref_withdrawn") == 1_000_000   # đã giữ chỗ đủ

    r3, err3 = db.withdrawal_reserve(tg, 500_000, "VCB - 111", 0)
    assert r3 is None and err3 == "insufficient"        # KHÔNG rút vượt được
    assert _val(db, tg, "ref_withdrawn") == 1_000_000


def test_withdrawal_release_hoan_giu_cho(tdb):
    db = tdb
    tg = 880002
    db.upsert_user(tg, "wd2", "WD 2")
    _set_ref(db, tg, 300_000)
    rid, _ = db.withdrawal_reserve(tg, 200_000, "VCB - 222", 0)
    assert _val(db, tg, "ref_withdrawn") == 200_000

    assert db.withdrawal_release(rid, "rejected") is True
    assert _val(db, tg, "ref_withdrawn") == 0
    assert db.withdrawal_release(rid, "rejected") is False   # idempotent
    rid2, _ = db.withdrawal_reserve(tg, 300_000, "VCB - 222", 0)
    assert rid2                                              # hoàn chỗ rồi rút lại được


def test_withdrawal_mark_approved_idempotent(tdb):
    db = tdb
    tg = 880003
    db.upsert_user(tg, "wd3", "WD 3")
    _set_ref(db, tg, 500_000)
    rid, _ = db.withdrawal_reserve(tg, 500_000, "VCB - 333", 0)

    assert db.withdrawal_mark_approved(rid) is True
    assert _val(db, tg, "ref_withdrawn") == 500_000      # đã giữ chỗ, KHÔNG cộng lại
    assert db.withdrawal_mark_approved(rid) is False     # duyệt lần 2 không ăn
    assert _val(db, tg, "ref_withdrawn") == 500_000


def test_withdrawal_don_cu_reserved_0_van_dung_so(tdb):
    """Đơn tạo TRƯỚC bản vá (reserved=0) -> khi duyệt phải cộng ref_withdrawn như cũ."""
    db = tdb
    tg = 880004
    db.upsert_user(tg, "wd4", "WD 4")
    _set_ref(db, tg, 400_000)
    rid = db.create_withdrawal_request(tg, 150_000, "VCB - 444", 0)
    assert _val(db, tg, "ref_withdrawn") == 0
    assert db.withdrawal_mark_approved(rid) is True
    assert _val(db, tg, "ref_withdrawn") == 150_000


# ── M5/M6: không cho âm số dư / credits ───────────────────────────────
def test_adjust_balance_khong_am(tdb):
    db = tdb
    tg = 880005
    db.upsert_user(tg, "b1", "B1")
    assert db.adjust_balance(tg, 10_000, "test") is True
    assert db.adjust_balance(tg, -15_000, "test") is False
    assert _val(db, tg, "balance") == 10_000
    assert db.adjust_balance(tg, -10_000, "test") is True
    assert _val(db, tg, "balance") == 0


def test_adjust_wallet_khong_am(tdb):
    db = tdb
    tg = 880006
    db.upsert_user(tg, "b2", "B2")
    assert db.adjust_wallet(tg, "shop", 5_000, "test") is True
    assert db.adjust_wallet(tg, "shop", -9_000, "test") is False
    assert _val(db, tg, "shop_balance") == 5_000


def test_consume_credits_khong_am(tdb):
    db = tdb
    tg = 880007
    db.upsert_user(tg, "c1", "C1")
    c = db.get_conn()
    c.execute("UPDATE tg_users SET credits=100 WHERE tg_id=?", (tg,))
    c.commit()
    assert db.consume_credits(tg, 100) is True
    assert db.consume_credits(tg, 1) is False
    assert _val(db, tg, "credits") == 0


def test_transfer_balance_khong_vuot_so_du(tdb):
    db = tdb
    a, b = 880008, 880009
    db.upsert_user(a, "t1", "T1")
    db.upsert_user(b, "t2", "T2")
    db.adjust_balance(a, 20_000, "test")
    ok, _msg = db.transfer_balance(a, b, 50_000)
    assert ok is False
    assert _val(db, a, "balance") == 20_000 and _val(db, b, "balance") == 0
    ok2, _ = db.transfer_balance(a, b, 20_000)
    assert ok2 is True
    assert _val(db, a, "balance") == 0 and _val(db, b, "balance") == 20_000


# ── M2: duyệt ứng tiền không cộng 2 lần ──────────────────────────────
def test_loan_approve_lan_hai_tra_none(tdb):
    db = tdb
    tg = 880010
    db.upsert_user(tg, "l1", "L1")
    lid = db.loan_create(tg, 100_000, "test")
    assert db.loan_approve(lid, 1) is not None
    # Đây là điều kiện để caller KHÔNG cộng ví lần 2.
    assert db.loan_approve(lid, 1) is None
