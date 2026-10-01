"""Test money paths: payout + don mua acc.
CHI DUNG DU LIEU GIA. Khong dong vao du lieu that.
- tg_id test: 9210001 (buyer), 9210002 (consignor)  [dai cho phep 9210001-9210099]
- uid test: testmp*   |  loai acc: 'TestMP Cat'
Chay xong don sach va verify COUNT=0.

Ho tro chay tu dong moi deploy: dat TEST_BACKEND_PATH (duong dan botcheckv2/backend
cua worktree) va TEST_ENV_PATH (duong dan .env) qua bien moi truong.
"""
import sys, os, time

# Nap .env TRUOC khi import db (dam bao noi Supabase qua tunnel, khong rot ve SQLite)
# TEST_ENV_PATH cho phep chay tu worktree (deploy test tu dong)
_env_path = os.environ.get("TEST_ENV_PATH",
    "/home/hatch/workspace/fb-hoan-chinh/botcheckv2/backend/.env")
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

# TEST_BACKEND_PATH cho phep chay tu worktree (deploy test tu dong)
_backend_path = os.environ.get("TEST_BACKEND_PATH",
    "/home/hatch/workspace/fb-hoan-chinh/botcheckv2/backend")
sys.path.insert(0, _backend_path)
from app import db

BUYER_TG = 9210001
CONSIGNOR_TG = 9210002
CAT_NAME = "TestMP Cat"
UIDS = ["testmp001", "testmp002", "testmp003"]
SUPER_ID = 5964340237  # chi dung lam decided_by trong test, khong tao du lieu that

passed, failed = [], []
def check(name, cond):
    if cond:
        passed.append(name); print(f"  \u2713 {name}")
    else:
        failed.append(name); print(f"  \u2717 FAIL: {name}")

def cleanup_test_data(c):
    """Xoa MOI du lieu test theo id test. Khong dung pattern rong."""
    # lay id consignor test truoc
    rows = c.execute(
        "SELECT id FROM consignors WHERE tg_id BETWEEN 9210001 AND 9210099").fetchall()
    cids = [r["id"] for r in rows]
    for t in ("consignment_ledger", "consignment_payouts"):
        if cids:
            c.execute(f"DELETE FROM {t} WHERE consignor_id IN ({','.join('?'*len(cids))})",
                      tuple(cids))
    # don mua acc
    c.execute("DELETE FROM acc_orders WHERE tg_id BETWEEN 9210001 AND 9210099")
    c.execute("DELETE FROM acc_stock WHERE uid LIKE 'testmp%%'")
    c.execute("DELETE FROM acc_categories WHERE name=?", (CAT_NAME,))
    c.execute("DELETE FROM txns WHERE tg_id BETWEEN 9210001 AND 9210099")
    c.execute("DELETE FROM tg_users WHERE tg_id BETWEEN 9210001 AND 9210099")
    c.execute("DELETE FROM consignors WHERE tg_id BETWEEN 9210001 AND 9210099")
    c.commit()

def verify_clean(c):
    res = {}
    res["consignors"] = c.execute(
        "SELECT COUNT(*) v FROM consignors WHERE tg_id BETWEEN 9210001 AND 9210099").fetchone()["v"]
    res["tg_users"] = c.execute(
        "SELECT COUNT(*) v FROM tg_users WHERE tg_id BETWEEN 9210001 AND 9210099").fetchone()["v"]
    res["acc_orders"] = c.execute(
        "SELECT COUNT(*) v FROM acc_orders WHERE tg_id BETWEEN 9210001 AND 9210099").fetchone()["v"]
    res["acc_stock"] = c.execute(
        "SELECT COUNT(*) v FROM acc_stock WHERE uid LIKE 'testmp%%'").fetchone()["v"]
    res["acc_categories"] = c.execute(
        "SELECT COUNT(*) v FROM acc_categories WHERE name=?", (CAT_NAME,)).fetchone()["v"]
    res["txns"] = c.execute(
        "SELECT COUNT(*) v FROM txns WHERE tg_id BETWEEN 9210001 AND 9210099").fetchone()["v"]
    # ledger/payouts: khong con consignor test thi khong con gi lien quan
    res["ledger"] = c.execute(
        "SELECT COUNT(*) v FROM consignment_ledger l WHERE EXISTS "
        "(SELECT 1 FROM consignors c WHERE c.id=l.consignor_id "
        "AND c.tg_id BETWEEN 9210001 AND 9210099)").fetchone()["v"]
    res["payouts"] = c.execute(
        "SELECT COUNT(*) v FROM consignment_payouts p WHERE EXISTS "
        "(SELECT 1 FROM consignors c WHERE c.id=p.consignor_id "
        "AND c.tg_id BETWEEN 9210001 AND 9210099)").fetchone()["v"]
    return res

c = db.get_conn()
now = int(time.time())
try:
    print("== Test money paths ==")
    cleanup_test_data(c)  # phong truong hop chay truoc roi rot giua chung

    # ---------- A. PAYOUT ----------
    print("-- A. payout --")
    c.execute("INSERT INTO consignors (tg_id, name, status, level, created_at) "
              "VALUES (?,?, 'active','new',?)", (CONSIGNOR_TG, "TestMP Payout", now))
    c.commit()
    cid = c.execute("SELECT id FROM consignors WHERE tg_id=?",
                    (CONSIGNOR_TG,)).fetchone()["id"]
    c.execute("INSERT INTO consignment_ledger (consignor_id, kind, amount, ref_type, ref_id, note, created_at)"
              " VALUES (?,?,?,?,?,?,?)", (cid, "avail_in", 500000, "test", 0, "nap test", now))
    c.commit()
    w = db.consign_wallets(cid)
    check("setup: avail=500000", w["avail"] == 500000)

    fee_setting = int(db.get_setting("consign_withdraw_fee", "0") or 0)

    # A1. tao payout hop le 100k
    w0 = db.consign_wallets(cid)
    ok, msg, pid = db.consign_payout_create(cid, 100000, "bank", "test acc 123")
    check("A1 tao payout ok", ok is True and pid > 0)
    w1 = db.consign_wallets(cid)
    check("A1 avail giam 100k", w1["avail"] == w0["avail"] - 100000)
    check("A1 withdrawing tang 100k", w1["withdrawing"] == w0["withdrawing"] + 100000)
    kinds = {(r["kind"], r["amount"]) for r in db.consign_ledger_list(cid, 10)
             if r["ref_type"] == "payout" and r["ref_id"] == pid}
    check("A1 ledger co avail_out 100k", ("avail_out", 100000) in kinds)
    check("A1 ledger co withdraw_in 100k", ("withdraw_in", 100000) in kinds)
    prow = c.execute("SELECT fee, net FROM consignment_payouts WHERE id=?",
                     (pid,)).fetchone()
    check("A1 fee dung setting", int(prow["fee"]) == fee_setting)
    check("A1 net = amount - fee", int(prow["net"]) == 100000 - fee_setting)

    # A2. duyet -> withdrawing ve 0, khong tru them
    r1 = db.consign_payout_decide(pid, True, SUPER_ID, "ref-test-1")
    check("A2 duyet lan 1 ok", r1 is True)
    w2 = db.consign_wallets(cid)
    check("A2 withdrawing ve 0", w2["withdrawing"] == 0)
    check("A2 avail khong doi sau duyet", w2["avail"] == w1["avail"])
    st = c.execute("SELECT status FROM consignment_payouts WHERE id=?",
                   (pid,)).fetchone()["status"]
    check("A2 status=paid", st == "paid")
    wo = [r for r in db.consign_ledger_list(cid, 10)
          if r["kind"] == "withdraw_out" and r["ref_id"] == pid]
    check("A2 ledger co withdraw_out 100k", len(wo) == 1 and wo[0]["amount"] == 100000)

    # A3. duyet lan 2 -> idempotent, khong tru trung
    r2 = db.consign_payout_decide(pid, True, SUPER_ID, "ref-test-1")
    check("A3 duyet lan 2 tra False", r2 is False)
    w3 = db.consign_wallets(cid)
    check("A3 vi khong doi sau duyet lan 2", w3 == w2)

    # A4. tu choi -> hoan avail
    ok, msg, pid2 = db.consign_payout_create(cid, 50000, "bank", "y")
    check("A4 tao payout 2 ok", ok is True)
    wb = db.consign_wallets(cid)
    check("A4 withdrawing=50000 truoc reject", wb["withdrawing"] == 50000)
    db.consign_payout_decide(pid2, False, SUPER_ID, "ly do test")
    wa = db.consign_wallets(cid)
    check("A4 reject: avail tang lai 50k", wa["avail"] == wb["avail"] + 50000)
    check("A4 reject: withdrawing ve 0", wa["withdrawing"] == 0)
    st2 = c.execute("SELECT status, reject_reason FROM consignment_payouts WHERE id=?",
                    (pid2,)).fetchone()
    check("A4 status=rejected", st2["status"] == "rejected")
    check("A4 luu ly do tu choi", (st2["reject_reason"] or "") == "ly do test")

    # A5. doi tac bi khoa -> chan
    c.execute("UPDATE consignors SET status='locked' WHERE id=?", (cid,)); c.commit()
    ok, msg, pidX = db.consign_payout_create(cid, 10000, "bank", "x")
    check("A5 locked -> tu choi", ok is False and pidX == 0)
    check("A5 msg co chu 'khoa'", "khóa" in msg)
    c.execute("UPDATE consignors SET status='active' WHERE id=?", (cid,)); c.commit()

    # A6. duoi min / vuot avail -> chan
    min_w = int(db.get_setting("consign_min_withdraw", "0") or 0)
    ok, msg, _ = db.consign_payout_create(cid, min_w - 1, "bank", "x")
    check(f"A6 duoi min ({min_w}) -> tu choi", ok is False)
    wa_now = db.consign_wallets(cid)["avail"]
    ok, msg, _ = db.consign_payout_create(cid, wa_now + 1, "bank", "x")
    check("A6 vuot avail -> tu choi", ok is False)
    check("A6 msg vuot avail dung", "không đủ" in msg)

    # ---------- B. DON MUA ACC ----------
    print("-- B. don mua acc --")
    c.execute("INSERT INTO tg_users (tg_id, name, shop_balance, created_at) VALUES (?,?,0,?)",
              (BUYER_TG, "TestMP Buyer", now))
    c.commit()
    db.add_shop_balance_only(BUYER_TG, 1000000, "test nap")
    bal = c.execute("SELECT shop_balance FROM tg_users WHERE tg_id=?",
                    (BUYER_TG,)).fetchone()["shop_balance"]
    check("B1 nap vi shop 1,000,000", bal == 1000000)
    txn_in = c.execute("SELECT amount FROM txns WHERE tg_id=? ORDER BY ts DESC LIMIT 1",
                       (BUYER_TG,)).fetchone()
    check("B1 txns ghi +1000000", txn_in and txn_in["amount"] == 1000000)

    cat_id = db.acc_category_add(CAT_NAME, 200000, 24, live_check=0)
    check("B2 tao loai acc test", cat_id > 0)
    sids = []
    for u in UIDS:
        c.execute("INSERT INTO acc_stock (cat_id, uid, password, status, source, added_at)"
                  " VALUES (?,?,?,'AVAILABLE','shop',?)", (cat_id, u, "pw-" + u, now))
        sids.append(c.lastrowid if hasattr(c, "lastrowid") else None)
    c.commit()
    sids = [r["id"] for r in c.execute(
        "SELECT id FROM acc_stock WHERE uid LIKE 'testmp%%' ORDER BY id").fetchall()]
    check("B2 tao 3 acc testmp*", len(sids) == 3)

    # B3. tru vi + tao don (mo phong handler: tru tien truoc, tao don sau)
    ok_d = db.adjust_shop_balance(BUYER_TG, -200000, "test mua acc")
    check("B3 tru vi shop ok", ok_d is True)
    bal2 = c.execute("SELECT shop_balance FROM tg_users WHERE tg_id=?",
                     (BUYER_TG,)).fetchone()["shop_balance"]
    check("B3 vi con 800,000", bal2 == 800000)
    txn_out = c.execute("SELECT amount, reason FROM txns WHERE tg_id=? AND amount<0 "
                        "ORDER BY ts DESC LIMIT 1", (BUYER_TG,)).fetchone()
    check("B3 txns ghi -200000", txn_out and txn_out["amount"] == -200000)

    res = db.acc_sell_stock_ids(cat_id, BUYER_TG, 200000, [sids[0]])
    check("B4 acc_sell_stock_ids tra ket qua", res is not None and len(res) == 1)
    order_id = res[0][0] if res else None
    check("B4 order_id hop le (khong None)", order_id is not None and order_id > 0)
    orow = c.execute("SELECT * FROM acc_orders WHERE id=?", (order_id,)).fetchone()
    check("B4 don ton tai, gia 200000", orow and orow["price"] == 200000
          and orow["tg_id"] == BUYER_TG)
    srow = c.execute("SELECT status, sold_to, price_sold FROM acc_stock WHERE id=?",
                     (sids[0],)).fetchone()
    check("B4 acc SOLD, sold_to dung", srow["status"] == "SOLD"
          and srow["sold_to"] == BUYER_TG and srow["price_sold"] == 200000)

    # B5. khong du tien -> chan tru (nguyen tu)
    ok_d2 = db.adjust_shop_balance(BUYER_TG, -999999999, "x")
    check("B5 vuot so du -> False", ok_d2 is False)
    bal3 = c.execute("SELECT shop_balance FROM tg_users WHERE tg_id=?",
                     (BUYER_TG,)).fetchone()["shop_balance"]
    check("B5 vi khong doi", bal3 == 800000)

    # B6. ban acc da SOLD lan nua -> phai rollback nguyen tu, tra None
    n_before = c.execute("SELECT COUNT(*) v FROM acc_orders WHERE tg_id=?",
                         (BUYER_TG,)).fetchone()["v"]
    try:
        res2 = db.acc_sell_stock_ids(cat_id, BUYER_TG, 200000, [sids[1], sids[0]])
        check("B6 ban kem acc SOLD -> None (rollback)", res2 is None)
    except AttributeError as e:
        # BUG production: PgConnection khong co rollback() -> crash thay vi tra None
        check(f"B6 khong crash (BUG tien: AttributeError {e})", False)
        res2 = "crashed"
    s1 = c.execute("SELECT status FROM acc_stock WHERE id=?", (sids[1],)).fetchone()["status"]
    check("B6 acc chua ban van AVAILABLE", s1 == "AVAILABLE")
    n_after = c.execute("SELECT COUNT(*) v FROM acc_orders WHERE tg_id=?",
                        (BUYER_TG,)).fetchone()["v"]
    check("B6 khong tao don rac", n_after == n_before)

finally:
    print("-- don sach --")
    cleanup_test_data(c)
    res = verify_clean(c)
    for k, v in res.items():
        check(f"cleanup: {k}=0", v == 0)

print()
if failed:
    print(f"\u274c FAIL {len(failed)}/{len(passed)+len(failed)}: {failed}")
    sys.exit(1)
print(f"\u2705 PASS {len(passed)} kiem tra, khong co FAIL")
