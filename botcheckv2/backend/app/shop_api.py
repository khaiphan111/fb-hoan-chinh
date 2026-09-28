"""API quan tri shop acc FB + buff tuong tac cho web admin.

Chi la lop boc HTTP quanh cac ham db.py san co — KHONG doi logic nghiep vu.
Tat ca endpoint yeu cau dang nhap admin web (Depends(auth)).
"""
import time
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from . import db
from . import pause as pause_mod
from .api import auth, require_role

router = APIRouter(prefix="/api", tags=["shop"])


def _d(r):
    return dict(r) if r else None


def _mask(s: str) -> str:
    s = str(s or "")
    if len(s) <= 4:
        return "•" * len(s) if s else ""
    return s[:2] + "•" * (len(s) - 4) + s[-2:]


def _mask_email(s: str) -> str:
    s = str(s or "")
    if "@" not in s:
        return _mask(s)
    local, domain = s.split("@", 1)
    return (local[:1] + "•••" if local else "") + "@" + domain


def _mask_stock_row(r: dict) -> dict:
    r = dict(r)
    r["password"] = _mask(r.get("password", ""))
    r["backup_mail"] = _mask_email(r.get("backup_mail", ""))
    r["totp"] = _mask(r.get("totp", ""))
    # cookie/token dai, chi goi y do dai
    for k in ("cookie", "token"):
        v = str(r.get(k) or "")
        r[k] = f"<{len(v)} ky tu>" if v else ""
    return r


# ---------------- Shop: tong quan ----------------

@router.get("/shop/overview")
def shop_overview(_=Depends(auth)):
    """Tong quan kho theo gian hang: so loai, AVAILABLE/DIE/EXISTS/SOLD."""
    c = db.get_conn()
    stalls = db.acc_stall_list() or ["Acc Facebook"]
    out = []
    for st in stalls:
        st = st if isinstance(st, str) else st.get("stall", "")
        cats = c.execute(
            "SELECT id FROM acc_categories WHERE stall=? AND active=1", (st,)
        ).fetchall()
        cids = [r["id"] for r in cats]
        avail = die = exists = sold = 0
        if cids:
            ph = ",".join("?" * len(cids))
            for status, var in (("AVAILABLE", "avail"), ("DIE", "die"), ("EXISTS", "exists")):
                n = c.execute(
                    f"SELECT COUNT(*) n FROM acc_stock WHERE cat_id IN ({ph}) AND status=?",
                    (*cids, status),
                ).fetchone()["n"]
                if var == "avail":
                    avail = n
                elif var == "die":
                    die = n
                else:
                    exists = n
            sold = c.execute(
                f"SELECT COUNT(*) n FROM acc_stock WHERE cat_id IN ({ph}) AND status='SOLD'",
                (*cids,),
            ).fetchone()["n"]
        out.append({
            "stall": st,
            "live_check": db.acc_stall_live_check(st),
            "categories": len(cids),
            "available": avail, "die": die, "exists": exists, "sold": sold,
        })
    return {"ok": True, "data": out}


@router.get("/shop/categories")
def shop_categories(include_inactive: int = 1, _=Depends(auth)):
    c = db.get_conn()
    cats = db.acc_category_list(active_only=not bool(include_inactive), include_hidden=True)
    out = []
    for cat in cats:
        cat = _d(cat)
        cid = cat["id"]
        avail = c.execute(
            "SELECT COUNT(*) n FROM acc_stock WHERE cat_id=? AND status='AVAILABLE'", (cid,)
        ).fetchone()["n"]
        die = c.execute(
            "SELECT COUNT(*) n FROM acc_stock WHERE cat_id=? AND status='DIE'", (cid,)
        ).fetchone()["n"]
        exists = c.execute(
            "SELECT COUNT(*) n FROM acc_stock WHERE cat_id=? AND status='EXISTS'", (cid,)
        ).fetchone()["n"]
        sold = c.execute(
            "SELECT COUNT(*) n FROM acc_stock WHERE cat_id=? AND status='SOLD'", (cid,)
        ).fetchone()["n"]
        cat["available"] = avail
        cat["die"] = die
        cat["exists_count"] = exists
        cat["sold"] = sold
        out.append(cat)
    return {"ok": True, "data": out}


class CatIn(BaseModel):
    name: str
    price: int = 0
    warranty_hours: int = 0
    description: str = ""
    stall: str = "Acc Facebook"
    live_check: int = 1


@router.post("/shop/categories")
def shop_cat_add(body: CatIn, _=Depends(auth)):
    cid = db.acc_category_add(
        body.name, int(body.price), int(body.warranty_hours),
        description=body.description, stall=body.stall, live_check=int(body.live_check),
    )
    if cid < 0:
        return {"ok": False, "detail": "Ten loai da ton tai"}
    return {"ok": True, "id": cid}


class CatUp(BaseModel):
    name: Optional[str] = None
    price: Optional[int] = None
    warranty_hours: Optional[int] = None
    description: Optional[str] = None
    active: Optional[int] = None
    hidden: Optional[int] = None
    mystery_weight: Optional[int] = None
    mystery_eligible: Optional[int] = None
    cover_photo: Optional[str] = None


@router.put("/shop/categories/{cid}")
def shop_cat_update(cid: int, body: CatUp, _=Depends(auth)):
    kw = {k: v for k, v in body.dict().items() if v is not None}
    ok = db.acc_category_update(cid, **kw)
    return {"ok": bool(ok)}


# ---------------- Shop: kho ----------------

@router.get("/shop/stock")
def shop_stock(
    cat_id: int = Query(...),
    status: str = Query("", description="AVAILABLE|DIE|EXISTS|SOLD"),
    q: str = Query("", description="tim theo uid"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    _=Depends(auth),
):
    c = db.get_conn()
    conds = ["cat_id=?"]
    params: list = [cat_id]
    if status:
        conds.append("status=?")
        params.append(status)
    if q:
        conds.append("uid LIKE ?")
        params.append(f"%{q.strip()}%")
    where = " AND ".join(conds)
    total = c.execute(f"SELECT COUNT(*) n FROM acc_stock WHERE {where}", tuple(params)).fetchone()["n"]
    off = (page - 1) * per_page
    rows = c.execute(
        f"SELECT id, cat_id, uid, password, created_date, backup_mail, note, totp,"
        f" cookie, token, status, batch, added_at, sold_at"
        f" FROM acc_stock WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
        (*params, per_page, off),
    ).fetchall()
    return {"ok": True, "total": total, "page": page,
            "items": [_mask_stock_row(_d(r)) for r in rows]}


class ImportIn(BaseModel):
    cat_id: int
    text: str
    batch: str = ""
    cost_per_acc: int = 0
    supplier_id: int = 0


@router.post("/shop/import")
def shop_import(body: ImportIn, _=Depends(auth)):
    """Nhap kho thu cong: moi dong `uid|mk|ngay tao|mail thay|ghi chu|2fa|cookie|token`.
    Chong trung theo dung luat bot: chi bo qua UID dang AVAILABLE/DIE."""
    cat = db.acc_category_get(body.cat_id)
    if not cat:
        return {"ok": False, "detail": "Loai khong ton tai"}
    batch = body.batch.strip() or time.strftime("Web %d/%m %H:%M")
    rows, seen, dup = [], set(), 0
    for line in (body.text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        uid = parts[0] if parts else ""
        if not uid or uid in seen:
            dup += 1
            continue
        seen.add(uid)
        # chong trung kho: chi chan AVAILABLE/DIE (giong bot)
        ex = db.acc_stock_find(body.cat_id, uid)
        if ex and str(ex.get("status")) in ("AVAILABLE", "DIE"):
            dup += 1
            continue
        while len(parts) < 8:
            parts.append("")
        rows.append({
            "uid": uid, "password": parts[1], "created_date": parts[2],
            "backup_mail": parts[3], "note": parts[4], "totp": parts[5],
            "cookie": parts[6], "token": parts[7],
        })
    added, skipped = db.acc_stock_add_batch(
        body.cat_id, rows, batch=batch,
        supplier_id=int(body.supplier_id or 0), cost_per_acc=int(body.cost_per_acc or 0),
    )
    return {"ok": True, "added": added, "duplicate": dup, "skipped": skipped, "batch": batch}


class DieIn(BaseModel):
    cat_id: Optional[int] = None


@router.post("/shop/stock/delete-die")
def shop_delete_die(body: DieIn, _=Depends(auth)):
    n = db.acc_stock_delete_die(body.cat_id)
    return {"ok": True, "deleted": n}


# ---------------- Shop: hop mu ----------------

@router.get("/shop/mystery")
def shop_mystery(_=Depends(auth)):
    return {"ok": True, "data": [dict(r) for r in db.acc_mystery_setup_list()]}


class MysteryIn(BaseModel):
    weights: dict  # {cat_id: pct 1-99}


@router.post("/shop/mystery")
def shop_mystery_set(body: MysteryIn, _=Depends(auth)):
    pct_map = {int(k): int(v) for k, v in (body.weights or {}).items()}
    ok = db.acc_mystery_set_multi(pct_map)
    return {"ok": bool(ok)}


class EligibleIn(BaseModel):
    cat_id: int
    on: int


@router.post("/shop/mystery/eligible")
def shop_mystery_eligible(body: EligibleIn, _=Depends(auth)):
    ok = db.acc_mystery_set_eligible(body.cat_id, int(body.on))
    return {"ok": bool(ok)}


# ---------------- Shop: nhap kho tu dong ----------------

@router.get("/shop/autoimport")
def shop_autoimport_get(stall: str = Query(...), _=Depends(auth)):
    return {"ok": True, "data": db.stall_import_cfg_get(stall)}


class AutoImpIn(BaseModel):
    stall: str
    enabled: int = 0
    interval_min: int = 60
    cat_id: int = 0
    supplier_id: int = 0
    cost: int = 0
    run_now: int = 0


@router.put("/shop/autoimport")
def shop_autoimport_set(body: AutoImpIn, _=Depends(auth)):
    kw = {
        "enabled": int(body.enabled), "interval_min": int(body.interval_min),
        "cat_id": int(body.cat_id), "supplier_id": int(body.supplier_id),
        "cost": int(body.cost),
    }
    if body.run_now:
        kw["last_run"] = 0  # poller se thay den han va chay ngay
    db.stall_import_cfg_set(body.stall, **kw)
    return {"ok": True}


# ---------------- Shop: lai theo lo, don hang ----------------

@router.get("/shop/batches")
def shop_batches(cat_id: int = Query(...), _=Depends(auth)):
    return {"ok": True, "data": [_d(r) for r in db.acc_batches_by_cat(cat_id)]}


@router.get("/shop/profit")
def shop_profit(batch: str = Query(...), _=Depends(auth)):
    return {"ok": True, "data": db.acc_profit_by_batch(batch)}


@router.get("/shop/orders")
def shop_orders(limit: int = Query(20, ge=1, le=100), _=Depends(auth)):
    return {"ok": True, "data": [_d(r) for r in db.acc_recent_orders(limit)]}


@router.get("/shop/revenue")
def shop_revenue(_=Depends(auth)):
    return {"ok": True, "data": db.acc_revenue()}


# ---------------- Buff tuong tac ----------------

@router.get("/buff/platforms")
def buff_platforms(_=Depends(auth)):
    return {"ok": True, "data": [_d(r) for r in db.buff_platforms()]}


@router.get("/buff/services")
def buff_services(
    platform: str = Query(""), category: str = Query(""),
    include_disabled: int = Query(1), _=Depends(auth),
):
    c = db.get_conn()
    conds, params = [], []
    if platform:
        conds.append("platform_key=?")
        params.append(platform)
    if category:
        conds.append("category_key=?")
        params.append(category)
    if not include_disabled:
        conds.append("enabled=1")
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    rows = c.execute(
        f"SELECT * FROM buff_services {where} ORDER BY platform_key, category_key, sell_price",
        params,
    ).fetchall()
    return {"ok": True, "data": [dict(r) for r in rows]}


@router.get("/buff/categories")
def buff_categories(platform: str = Query(""), _=Depends(auth)):
    return {"ok": True, "data": [_d(r) for r in db.buff_categories(platform)]}


class BuffSvcIn(BaseModel):
    sell_price: Optional[int] = None
    enabled: Optional[int] = None


@router.put("/buff/services/{sid}")
def buff_service_update(sid: int, body: BuffSvcIn, _=Depends(auth)):
    if body.sell_price is not None:
        db.buff_service_set_price(sid, int(body.sell_price))
    if body.enabled is not None:
        cur = db.buff_service_get(sid)
        if cur and int(cur.get("enabled", 1)) != int(body.enabled):
            db.buff_service_toggle(sid)
    return {"ok": True}


@router.get("/buff/links")
def buff_links(
    platform: str = Query(""), q: str = Query(""),
    page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=100),
    _=Depends(auth),
):
    total, rows = db.buff_link_warehouse_list(platform=platform, search=q,
                                                 page=page - 1, per_page=per_page)
    return {"ok": True, "total": total, "page": page, "items": rows}


@router.get("/buff/orders/pending")
def buff_orders_pending(limit: int = Query(20, ge=1, le=100), _=Depends(auth)):
    return {"ok": True, "data": [_d(r) for r in db.buff_orders_pending(limit)]}


# ---------------- Tam dung / mo lai bot ----------------

class PauseBody(BaseModel):
    reason: str = ""
    minutes: Optional[int] = None   # mo lai sau N phut
    until: Optional[int] = None     # hoac unix timestamp mo lai
    stop_jobs: bool = False


@router.get("/pause/status")
def pause_status(admin=Depends(require_role("super_admin"))):
    """Trang thai tam dung hien tai."""
    info = pause_mod.get_info()
    return {"ok": True, "data": info}


@router.post("/pause")
def pause_activate(body: PauseBody,
                   admin=Depends(require_role("super_admin"))):
    """Bat che do tam dung bot."""
    import time as _t
    reason = (body.reason or "").strip() or "Bảo trì hệ thống"
    now = int(_t.time())
    until = 0
    if body.until:
        until = int(body.until)
    elif body.minutes:
        until = now + int(body.minutes) * 60
    if until <= now:
        return {"ok": False, "error": "Thời gian mở lại không hợp lệ"}
    if until - now > pause_mod.MAX_MINUTES * 60:
        return {"ok": False, "error": "Tối đa 7 ngày"}
    pause_mod.activate(reason, until,
                       by_id=int(admin["id"] or 0),
                       by_name=str(admin["username"] or "web"),
                       stop_jobs=bool(body.stop_jobs))
    return {"ok": True, "data": pause_mod.get_info()}


@router.post("/pause/resume")
def pause_resume(admin=Depends(require_role("super_admin"))):
    """Mo lai bot ngay. Tin 'da mo lai' do poller tren VM gui (web khong co bot)."""
    if not pause_mod.is_paused():
        return {"ok": True, "data": {"resumed": False,
                                     "msg": "Bot đang hoạt động bình thường"}}
    flipped = pause_mod.try_deactivate(
        by_id=int(admin["id"] or 0),
        by_name=str(admin["username"] or "web"))
    return {"ok": True, "data": {"resumed": flipped,
                                 "msg": "Đã mở lại bot. Tin báo sẽ gửi trong vài phút."}}


# ---------------- Ký gửi ----------------

class ConsignFeeIn(BaseModel):
    category_id: int
    fee_fixed: int = 0
    fee_pct: float = 0


class ConsignBatchDecideIn(BaseModel):
    approve: bool
    sell_price: int = 0
    note: str = ""


class ConsignPayoutDecideIn(BaseModel):
    approve: bool
    paid_ref: str = ""


class ConsignDisputeDecideIn(BaseModel):
    decision: str = "refund_buyer"
    refund_amount: int = 0


class ConsignSettingIn(BaseModel):
    key: str
    value: str


@router.get("/consign/stats")
def consign_stats(admin=Depends(require_role("super_admin"))):
    return {"ok": True, "data": db.consign_stats()}


@router.get("/consign/consignors")
def consign_consignors(status: str = Query(""), limit: int = Query(50, le=200),
                       admin=Depends(require_role("super_admin"))):
    rows = db.consignors_list(status, limit)
    for r in rows:
        r["wallets"] = db.consign_wallets(r["id"])
    return {"ok": True, "data": rows}


@router.post("/consign/consignors/{cid}/status")
def consign_consignor_status(cid: int, body: dict, admin=Depends(require_role("super_admin"))):
    st = str(body.get("status", ""))
    if st not in ("active", "suspended", "banned", "pending"):
        return {"ok": False, "error": "status không hợp lệ"}
    ok = db.consignor_set_status(cid, st, int(admin["id"] or 0))
    return {"ok": ok}


@router.get("/consign/batches")
def consign_batches(status: str = Query(""), consignor_id: int = Query(0),
                    limit: int = Query(30, le=100), admin=Depends(require_role("super_admin"))):
    return {"ok": True, "data": db.consign_batches_list(consignor_id, status, limit)}


@router.get("/consign/batches/{bid}")
def consign_batch_detail(bid: int, admin=Depends(require_role("super_admin"))):
    b = db.consign_batch_get(bid)
    if not b:
        return {"ok": False, "error": "Không tìm thấy lô"}
    items = db.consign_items_of_batch(bid)
    for it in items:
        it["password"] = _mask(it.get("password", ""))
        it["backup_mail"] = _mask_email(it.get("backup_mail", ""))
        it["totp"] = _mask(it.get("totp", ""))
    return {"ok": True, "data": {"batch": b, "items": items,
                                 "fee": db.consign_fee_get(b["category_id"])}}


@router.post("/consign/batches/{bid}/decide")
def consign_batch_decide_ep(bid: int, body: ConsignBatchDecideIn,
                            admin=Depends(require_role("super_admin"))):
    """Duyệt lô trên web: quyết định + nhập acc vào kho (source=consign)."""
    import time as _t
    b = db.consign_batch_get(bid)
    if not b or b["status"] not in ("submitted", "draft"):
        return {"ok": False, "error": "Lô không ở trạng thái chờ duyệt"}
    if body.approve and body.sell_price <= 0:
        return {"ok": False, "error": "Cần nhập giá bán"}
    if not db.consign_batch_decide(bid, body.approve, body.sell_price,
                                   int(admin["id"] or 0), body.note):
        return {"ok": False, "error": "Lô đã được xử lý"}
    n = 0
    if body.approve:
        now = int(_t.time())
        for it in db.consign_items_of_batch(bid):
            dup = db.get_conn().execute(
                "SELECT 1 FROM acc_stock WHERE uid=? AND status IN ('AVAILABLE','DIE') LIMIT 1",
                (it["uid"],)).fetchone()
            if dup:
                db.consign_item_set(bid, it["id"], "rejected", "trùng kho")
                continue
            cur = db.get_conn().execute(
                "INSERT INTO acc_stock (cat_id, uid, password, backup_mail, totp, cookie, token,"
                " note, status, source, consign_item_id, price_override, added_at)"
                " VALUES (?,?,?,?,?,?,?,?, 'AVAILABLE','consign',?,?,?) RETURNING id",
                (b["category_id"], it["uid"], it["password"], it["backup_mail"], it["totp"],
                 it["cookie"], it["token"], f"[KG-{b['code']}] {it['note']}", it["id"],
                 body.sell_price, now))
            if cur.lastrowid:
                db.consign_item_link_stock(it["id"], cur.lastrowid)
                n += 1
        db.get_conn().execute("UPDATE consignment_batches SET status='listed', ok_items=? WHERE id=?", (n, bid))
    return {"ok": True, "data": {"listed": n}}


@router.get("/consign/orders")
def consign_orders(consignor_id: int = Query(0), limit: int = Query(30, le=100),
                   admin=Depends(require_role("super_admin"))):
    q = ("SELECT o.*, i.uid FROM consignment_orders o LEFT JOIN consignment_items i ON i.id=o.item_id")
    p = []
    if consignor_id:
        q += " WHERE o.consignor_id=?"
        p.append(consignor_id)
    q += " ORDER BY o.id DESC LIMIT ?"
    p.append(limit)
    rows = [dict(r) for r in db.get_conn().execute(q, p).fetchall()]
    for r in rows:
        u = str(r.get("uid") or "")
        r["uid"] = (u[:4] + "••••") if len(u) > 4 else "••••"
    return {"ok": True, "data": rows}


@router.get("/consign/payouts")
def consign_payouts(status: str = Query(""), limit: int = Query(30, le=100),
                    admin=Depends(require_role("super_admin"))):
    return {"ok": True, "data": db.consign_payouts_list(status, limit)}


@router.post("/consign/payouts/{pid}/decide")
def consign_payout_decide_ep(pid: int, body: ConsignPayoutDecideIn,
                             admin=Depends(require_role("super_admin"))):
    ok = db.consign_payout_decide(pid, body.approve, int(admin["id"] or 0), body.paid_ref)
    return {"ok": ok}


@router.get("/consign/disputes")
def consign_disputes(status: str = Query("open"), limit: int = Query(30, le=100),
                     admin=Depends(require_role("super_admin"))):
    return {"ok": True, "data": db.consign_disputes_list(status, limit)}


@router.post("/consign/disputes/{did}/decide")
def consign_dispute_decide_ep(did: int, body: ConsignDisputeDecideIn,
                              admin=Depends(require_role("super_admin"))):
    if body.decision not in ("refund_buyer", "replace", "reject"):
        return {"ok": False, "error": "decision không hợp lệ"}
    ok = db.consign_dispute_decide(did, body.decision, body.refund_amount, int(admin["id"] or 0))
    return {"ok": ok}


@router.get("/consign/fees")
def consign_fees(admin=Depends(require_role("super_admin"))):
    rows = [dict(r) for r in db.get_conn().execute("SELECT * FROM consignment_fees").fetchall()]
    return {"ok": True, "data": rows}


@router.post("/consign/fees")
def consign_fee_set_ep(body: ConsignFeeIn, admin=Depends(require_role("super_admin"))):
    ok = db.consign_fee_set(body.category_id, body.fee_fixed, body.fee_pct, int(admin["id"] or 0))
    return {"ok": ok}


@router.get("/consign/settings")
def consign_settings_get(admin=Depends(require_role("super_admin"))):
    keys = ["consign_enabled", "consign_default_max_items", "consign_default_max_value",
            "consign_min_withdraw", "consign_withdraw_fee", "consign_withdraw_schedule"]
    return {"ok": True, "data": {k: db.get_setting(k, "") for k in keys}}


@router.post("/consign/settings")
def consign_setting_set_ep(body: ConsignSettingIn, admin=Depends(require_role("super_admin"))):
    allowed = {"consign_enabled", "consign_default_max_items", "consign_default_max_value",
               "consign_min_withdraw", "consign_withdraw_fee", "consign_withdraw_schedule"}
    if body.key not in allowed:
        return {"ok": False, "error": "key không hợp lệ"}
    db.set_setting(body.key, body.value)
    return {"ok": True}
