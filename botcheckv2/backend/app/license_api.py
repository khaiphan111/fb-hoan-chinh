# License server cho tool Muse Studio Pro — cap key ban quyen gan theo thiet bi.
# - POST /api/license/activate, /validate: PUBLIC (app Electron goi, khong can auth)
# - Cac endpoint con lai: yeu cau quyen super_admin.
# Luu y bao mat: moi check ban quyen phai qua server; client chi luu key chu khong
# tu xac thuc (source Electron giai nen duoc trong vai phut).
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import db
from .api import require_role

router = APIRouter(prefix="/api/license", tags=["license"])

# Thoi han mac dinh khi kich hoat (giay). lifetime: expires_at=0 (khong het han).
# trial: tinh theo trial_hours luc tao key.
_PLAN_TTL = {"monthly": 30 * 86400, "yearly": 365 * 86400}
_VALID_PLANS = ("trial", "monthly", "yearly", "lifetime")
_KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # bo ky tu de nham: 0/O, 1/I


# ---------- models ----------
class ActivateIn(BaseModel):
    key: str = ""
    machine_id: str = ""
    machine_name: str = ""
    app_version: str = ""


class ValidateIn(BaseModel):
    key: str = ""
    machine_id: str = ""


class CreateKeysIn(BaseModel):
    plan: str = "monthly"
    count: int = Field(default=1, ge=1, le=200)
    months: int = Field(default=12, ge=1, le=120)
    trial_hours: int = Field(default=48, ge=1, le=24 * 90)
    customer_name: str = ""
    note: str = ""


class PatchKeyIn(BaseModel):
    action: str = ""  # lock | unlock | extend | set_note
    months: int = Field(default=1, ge=1, le=120)
    note: str = ""


# ---------- helpers ----------
def _now() -> int:
    return int(time.time())


def _norm_key(k: str) -> str:
    return (k or "").strip().upper()


def _write(sql: str, params=()):
    with db._lock:
        c = db.get_conn()
        cur = c.execute(sql, params)
        c.commit()
        return cur


def _get_license(key_code: str):
    row = db.get_conn().execute(
        "SELECT * FROM licenses WHERE key_code=?", (_norm_key(key_code),)
    ).fetchone()
    return dict(row) if row else None


def _get_license_by_id(license_id: int):
    row = db.get_conn().execute(
        "SELECT * FROM licenses WHERE id=?", (license_id,)
    ).fetchone()
    return dict(row) if row else None


def _log_event(license_id: int, machine_id: str, machine_name: str,
               app_version: str, event: str, detail: str = ""):
    _write(
        "INSERT INTO license_activations"
        "(license_id, machine_id, machine_name, app_version, event, detail, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (license_id, machine_id or "", (machine_name or "")[:120],
         (app_version or "")[:32], event, (detail or "")[:500], _now()),
    )


def _admin_log(admin: dict, action: str, target: str = "", details: str = ""):
    try:
        admin_id = int((admin or {}).get("id") or 0)
    except Exception:
        admin_id = 0
    _write(
        "INSERT INTO license_admin_log(admin_id, action, target, details, created_at)"
        " VALUES (?,?,?,?,?)",
        (admin_id, action, (target or "")[:500], (details or "")[:1000], _now()),
    )


def _version_info() -> dict:
    return {
        "min_version": db.get_setting("license_min_version", "1.0.0"),
        "latest_version": db.get_setting("license_latest_version", ""),
        "download_url": db.get_setting("license_download_url", ""),
    }


def _check_expired(lic: dict) -> bool:
    """Het han -> danh status=expired + log event, tra True."""
    now = _now()
    if int(lic.get("expires_at") or 0) and now > int(lic["expires_at"]):
        _write("UPDATE licenses SET status='expired' WHERE id=?", (lic["id"],))
        _log_event(lic["id"], lic.get("machine_id") or "", lic.get("machine_name") or "",
                   "", "expired", "Key het han")
        lic["status"] = "expired"
        return True
    return False


def _public_check(key_code: str) -> dict:
    lic = _get_license(key_code)
    if not lic:
        raise HTTPException(status_code=404, detail="Key không tồn tại")
    if lic.get("status") == "locked":
        raise HTTPException(status_code=403, detail="Key đã bị khóa")
    if _check_expired(lic):
        raise HTTPException(status_code=403, detail="Key đã hết hạn")
    return lic


def _ok_payload(lic: dict, msg: str = "OK") -> dict:
    return {
        "ok": True,
        "plan": lic["plan"],
        "expires_at": int(lic.get("expires_at") or 0),
        **_version_info(),
        "msg": msg,
    }


def _gen_key() -> str:
    for _ in range(50):
        code = "HAO-" + "-".join(
            "".join(secrets.choice(_KEY_ALPHABET) for _ in range(4)) for _ in range(3)
        )
        exists = db.get_conn().execute(
            "SELECT 1 FROM licenses WHERE key_code=?", (code,)
        ).fetchone()
        if not exists:
            return code
    raise HTTPException(status_code=500, detail="Không sinh được key, thử lại")


# ---------- PUBLIC: kich hoat / kiem tra ----------
@router.post("/activate")
def activate(body: ActivateIn):
    lic = _public_check(body.key)
    machine_id = (body.machine_id or "").strip()
    if not machine_id:
        raise HTTPException(status_code=400, detail="Thiếu machine_id")
    machine_name = (body.machine_name or "")[:120]
    app_version = (body.app_version or "")[:32]
    now = _now()

    if not lic.get("machine_id"):
        # Bind lan dau
        plan = lic["plan"]
        expires_at = int(lic.get("expires_at") or 0)
        if plan == "trial":
            expires_at = now + int(lic.get("trial_hours") or 0) * 3600
        elif plan in _PLAN_TTL and expires_at == 0:
            expires_at = now + _PLAN_TTL[plan]
        # lifetime: expires_at giu 0 (khong het han)
        _write(
            "UPDATE licenses SET machine_id=?, machine_name=?, activated_at=?,"
            " expires_at=?, last_seen=?, app_version=? WHERE id=?",
            (machine_id, machine_name, now, expires_at, now, app_version, lic["id"]),
        )
        _log_event(lic["id"], machine_id, machine_name, app_version,
                   "activate", f"plan={plan}")
        lic = _get_license(body.key)
        return _ok_payload(lic, "Kích hoạt thành công")

    if lic["machine_id"] != machine_id:
        _log_event(lic["id"], machine_id, machine_name, app_version, "validate_fail",
                   f"Key da bind may khac: {(lic.get('machine_name') or '')[:60]}")
        raise HTTPException(status_code=403,
                            detail="Key đã được kích hoạt trên thiết bị khác")

    _write(
        "UPDATE licenses SET last_seen=?, app_version=?, machine_name=? WHERE id=?",
        (now, app_version, machine_name or lic.get("machine_name") or "", lic["id"]),
    )
    return _ok_payload(lic)


@router.post("/validate")
def validate(body: ValidateIn):
    lic = _public_check(body.key)
    machine_id = (body.machine_id or "").strip()
    if not lic.get("machine_id"):
        raise HTTPException(status_code=403, detail="Key chưa được kích hoạt")
    if lic["machine_id"] != machine_id:
        _log_event(lic["id"], machine_id, "", "", "validate_fail",
                   "Sai thiet bi khi validate")
        raise HTTPException(status_code=403,
                            detail="Key đã được kích hoạt trên thiết bị khác")
    _write("UPDATE licenses SET last_seen=? WHERE id=?", (_now(), lic["id"]))
    return _ok_payload(lic)


# ---------- ADMIN ----------
@router.get("/keys")
def list_keys(status: str = "", q: str = "", page: int = 1, page_size: int = 50,
              admin: dict = Depends(require_role("super_admin"))):
    where, params = [], []
    if status:
        where.append("status=?")
        params.append(status)
    if q:
        like = f"%{q.strip().lower()}%"
        where.append("(LOWER(key_code) LIKE ? OR LOWER(customer_name) LIKE ?"
                     " OR LOWER(note) LIKE ?)")
        params += [like, like, like]
    w = ("WHERE " + " AND ".join(where)) if where else ""
    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    total = db.get_conn().execute(
        f"SELECT COUNT(*) FROM licenses {w}", tuple(params)).fetchone()[0]
    rows = db.get_conn().execute(
        f"SELECT * FROM licenses {w} ORDER BY id DESC LIMIT ? OFFSET ?",
        tuple(params) + (page_size, (page - 1) * page_size)).fetchall()
    return {"ok": True, "total": total, "page": page, "page_size": page_size,
            "items": [dict(r) for r in rows]}


@router.post("/keys")
def create_keys(body: CreateKeysIn,
                admin: dict = Depends(require_role("super_admin"))):
    plan = (body.plan or "monthly").lower()
    if plan not in _VALID_PLANS:
        raise HTTPException(status_code=400,
                            detail="plan phải là trial|monthly|yearly|lifetime")
    now = _now()
    try:
        admin_id = int((admin or {}).get("id") or 0)
    except Exception:
        admin_id = 0
    created = []
    for _ in range(body.count):
        code = _gen_key()
        _write(
            "INSERT INTO licenses(key_code, plan, status, customer_name, note,"
            " trial_hours, created_at, created_by) VALUES (?,?,?,?,?,?,?,?)",
            (code, plan, "active", (body.customer_name or "")[:120],
             (body.note or "")[:500], int(body.trial_hours or 0), now, admin_id),
        )
        created.append(code)
    _admin_log(admin, "create_keys",
               ",".join(created[:5]) + ("..." if len(created) > 5 else ""),
               f"plan={plan} count={len(created)}")
    return {"ok": True, "plan": plan, "keys": created}


@router.patch("/keys/{license_id}")
def patch_key(license_id: int, body: PatchKeyIn,
              admin: dict = Depends(require_role("super_admin"))):
    lic = _get_license_by_id(license_id)
    if not lic:
        raise HTTPException(status_code=404, detail="Key không tồn tại")
    action = (body.action or "").lower()
    now = _now()
    detail = ""

    if action == "lock":
        _write("UPDATE licenses SET status='locked' WHERE id=?", (license_id,))
        _log_event(license_id, lic.get("machine_id") or "",
                   lic.get("machine_name") or "", "", "locked", "Admin khoa key")
        detail = "status=locked"
    elif action == "unlock":
        new_status = ("expired" if int(lic.get("expires_at") or 0)
                      and now > int(lic["expires_at"]) else "active")
        _write("UPDATE licenses SET status=? WHERE id=?", (new_status, license_id))
        _log_event(license_id, lic.get("machine_id") or "",
                   lic.get("machine_name") or "", "", "unlocked", "Admin mo khoa key")
        detail = f"status={new_status}"
    elif action == "extend":
        if lic["plan"] == "lifetime":
            raise HTTPException(status_code=400,
                                detail="Key lifetime không cần gia hạn")
        months = max(1, int(body.months or 1))
        base = int(lic.get("expires_at") or 0)
        if base == 0 or base < now:
            base = now
        new_exp = base + months * 30 * 86400
        _write("UPDATE licenses SET expires_at=?, status='active' WHERE id=?",
               (new_exp, license_id))
        _log_event(license_id, lic.get("machine_id") or "",
                   lic.get("machine_name") or "", "", "extended",
                   f"Gia han {months} thang -> {new_exp}")
        detail = f"expires_at={new_exp}"
    elif action == "set_note":
        _write("UPDATE licenses SET note=? WHERE id=?",
               ((body.note or "")[:500], license_id))
        detail = "note updated"
    else:
        raise HTTPException(status_code=400,
                            detail="action phải là lock|unlock|extend|set_note")

    _admin_log(admin, action, lic["key_code"], detail)
    return {"ok": True, "id": license_id, "action": action, "detail": detail}


@router.delete("/keys/{license_id}")
def delete_key(license_id: int,
               admin: dict = Depends(require_role("super_admin"))):
    lic = _get_license_by_id(license_id)
    if not lic:
        raise HTTPException(status_code=404, detail="Key không tồn tại")
    _write("DELETE FROM license_activations WHERE license_id=?", (license_id,))
    _write("DELETE FROM licenses WHERE id=?", (license_id,))
    _admin_log(admin, "delete", lic["key_code"],
               f"plan={lic['plan']} activated={'yes' if lic.get('machine_id') else 'no'}")
    return {"ok": True}


@router.post("/keys/{license_id}/reset-device")
def reset_device(license_id: int,
                 admin: dict = Depends(require_role("super_admin"))):
    lic = _get_license_by_id(license_id)
    if not lic:
        raise HTTPException(status_code=404, detail="Key không tồn tại")
    old_machine = lic.get("machine_id") or ""
    new_count = int(lic.get("reset_count") or 0) + 1
    _write("UPDATE licenses SET machine_id='', machine_name='',"
           " reset_count=? WHERE id=?", (new_count, license_id))
    _log_event(license_id, old_machine, lic.get("machine_name") or "", "",
               "reset", f"Admin reset thiet bi (lan {new_count})")
    _admin_log(admin, "reset_device", lic["key_code"],
               f"old_machine={old_machine[:40]} reset_count={new_count}")
    return {"ok": True, "reset_count": new_count}


@router.get("/keys/{license_id}/activations")
def key_activations(license_id: int,
                    admin: dict = Depends(require_role("super_admin"))):
    lic = _get_license_by_id(license_id)
    if not lic:
        raise HTTPException(status_code=404, detail="Key không tồn tại")
    rows = db.get_conn().execute(
        "SELECT * FROM license_activations WHERE license_id=?"
        " ORDER BY id DESC LIMIT 200", (license_id,)).fetchall()
    return {"ok": True, "key_code": lic["key_code"],
            "items": [dict(r) for r in rows]}


@router.get("/stats")
def stats(admin: dict = Depends(require_role("super_admin"))):
    c = db.get_conn()
    total = c.execute("SELECT COUNT(*) FROM licenses").fetchone()[0]
    by_status = {r["status"]: r["c"] for r in
                 (dict(x) for x in c.execute(
                     "SELECT status, COUNT(*) AS c FROM licenses GROUP BY status").fetchall())}
    by_plan = {r["plan"]: r["c"] for r in
               (dict(x) for x in c.execute(
                   "SELECT plan, COUNT(*) AS c FROM licenses GROUP BY plan").fetchall())}
    return {
        "ok": True,
        "total": total,
        "active": by_status.get("active", 0),
        "expired": by_status.get("expired", 0),
        "locked": by_status.get("locked", 0),
        "by_plan": by_plan,
    }


@router.get("/admin-log")
def admin_log(limit: int = 100,
              admin: dict = Depends(require_role("super_admin"))):
    limit = max(1, min(limit, 500))
    rows = db.get_conn().execute(
        "SELECT * FROM license_admin_log ORDER BY id DESC LIMIT ?",
        (limit,)).fetchall()
    return {"ok": True, "items": [dict(r) for r in rows]}
