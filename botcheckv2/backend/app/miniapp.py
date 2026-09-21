# Mini App — giao diện web trong Telegram để check UID nhanh.
# Xác thực qua Telegram WebApp initData (HMAC-SHA256 với bot token).
import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from . import db

router = APIRouter(tags=["miniapp"])

_bot_username_cache = {"v": "", "ts": 0}


def _bot_username() -> str:
    """Lấy username bot chính (cache 1h, lưu vào settings)."""
    import time as _t
    now = int(_t.time())
    if _bot_username_cache["v"] and now - _bot_username_cache["ts"] < 3600:
        return _bot_username_cache["v"]
    saved = db.get_setting("bot_username", "")
    if saved:
        _bot_username_cache.update(v=saved, ts=now)
        return saved
    token = _bot_token()
    if not token:
        return ""
    try:
        import httpx
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        kw = {"timeout": 10}
        if proxy:
            kw["proxy"] = proxy
        with httpx.Client(**kw) as cl:
            r = cl.get(f"https://api.telegram.org/bot{token}/getMe")
            un = (r.json().get("result") or {}).get("username", "")
        if un:
            try:
                db.set_setting("bot_username", un)
            except Exception:
                pass
            _bot_username_cache.update(v=un, ts=now)
            return un
    except Exception:
        pass
    return ""

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PAGE_PATH = os.path.join(THIS_DIR, "static", "miniapp", "index.html")


def _bot_token() -> str:
    return os.environ.get("BOT_TOKEN", "") or db.get_setting("bot_token", "") or ""


def validate_init_data(init_data: str) -> dict | None:
    """Xác thực initData của Telegram WebApp. Trả về dict user hoặc None."""
    token = _bot_token()
    if not init_data or not token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        recv_hash = pairs.pop("hash", None)
        if not recv_hash:
            return None
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, recv_hash):
            return None
        # initData quá 24h thì từ chối
        try:
            if int(time.time()) - int(pairs.get("auth_date", "0")) > 86400:
                return None
        except ValueError:
            return None
        user_json = pairs.get("user", "")
        return json.loads(user_json) if user_json else {}
    except Exception:
        return None


@router.get("/miniapp")
async def miniapp_page():
    """Trang Mini App."""
    if not os.path.exists(PAGE_PATH):
        raise HTTPException(status_code=404, detail="Mini App chưa được build")
    return FileResponse(PAGE_PATH, media_type="text/html")


@router.get("/api/mini/me")
async def mini_me(initData: str = ""):
    user = validate_init_data(initData)
    if not user:
        raise HTTPException(status_code=401, detail="initData không hợp lệ")
    tg_id = int(user.get("id", 0))
    row = db.get_user(tg_id)
    u = dict(row) if row else {}
    vip = int(u.get("vip_level") or 0)
    sub_until = int(u.get("sub_until") or 0)
    vip_txt = "Thường"
    if sub_until > 9999999999:
        vip_txt = f"VIP {vip} (vĩnh viễn)"
    elif sub_until > time.time():
        vip_txt = f"VIP {vip} (đến {time.strftime('%d/%m/%Y', time.localtime(sub_until))})"
    return {
        "name": user.get("first_name", "") or u.get("full_name", ""),
        "balance": int(u.get("balance") or 0),
        "credits": db.get_credits(tg_id),
        "vip": vip_txt,
    }


@router.post("/api/mini/check")
async def mini_check(body: dict):
    init_data = str(body.get("initData") or "")
    target = str(body.get("input") or "").strip()
    user = validate_init_data(initData)
    if not user:
        raise HTTPException(status_code=401, detail="initData không hợp lệ")
    if not target:
        raise HTTPException(status_code=400, detail="Thiếu UID/link")
    tg_id = int(user.get("id", 0))
    from .fb import resolve_fb_uid, check_uid

    # 1. Lấy UID từ link (nếu là link)
    uid, name, method = None, "", ""
    if "facebook.com" in target or "fb.watch" in target or "/" in target:
        try:
            uid, name, method = await resolve_fb_uid(target)
        except Exception:
            uid = None
    if not uid:
        import re
        m = re.search(r"\d{5,}", target)
        uid = m.group(0) if m else None
    if not uid:
        raise HTTPException(status_code=400, detail="Không tách được UID từ nội dung")
    # 2. Check live/die
    res = await check_uid(uid)
    status = res.get("status", "die")
    try:
        db.log_check_stat(tg_id, "fb", uid, status, res.get("via", ""))
        db.add_check_history(tg_id, "fb", uid, status)
    except Exception:
        pass
    return {
        "uid": uid,
        "status": status,
        "name": res.get("name") or name or "",
        "via": res.get("via") or "",
        "has_avatar": bool(res.get("has_real_avatar")),
        "avatar_url": res.get("avatar_url") or "" if res.get("has_real_avatar") else "",
    }


@router.get("/api/mini/history")
async def mini_history(initData: str = ""):
    user = validate_init_data(initData)
    if not user:
        raise HTTPException(status_code=401, detail="initData không hợp lệ")
    tg_id = int(user.get("id", 0))
    rows = db.get_check_history(tg_id, days=7)
    return {
        "history": [
            {
                "target": r["target"],
                "result": r["result"],
                "at": r["checked_at"],
            }
            for r in rows[:20]
        ]
    }


@router.get("/api/mini/ref")
async def mini_ref(initData: str = ""):
    """Thông tin giới thiệu/hoa hồng cho Mini App."""
    user = validate_init_data(initData)
    if not user:
        raise HTTPException(status_code=401, detail="initData không hợp lệ")
    tg_id = int(user.get("id", 0))
    u = db.get_user(tg_id)
    ud = dict(u) if u else {}
    earnings = int(ud.get("ref_earnings") or 0)
    withdrawn = int(ud.get("ref_withdrawn") or 0)
    ref_code = ud.get("ref_code") or f"REF{tg_id}"
    c = db.get_conn()
    try:
        f1 = c.execute("SELECT COUNT(*) FROM tg_users WHERE referrer_id=?", (tg_id,)).fetchone()[0]
    except Exception:
        f1 = 0
    try:
        f2 = c.execute(
            "SELECT COUNT(*) FROM tg_users WHERE referrer_id IN (SELECT tg_id FROM tg_users WHERE referrer_id=?)",
            (tg_id,),
        ).fetchone()[0]
    except Exception:
        f2 = 0
    username = _bot_username()
    base = f"https://t.me/{username}" if username else "https://t.me"
    return {
        "code": ref_code,
        "link": f"{base}?start={tg_id}",
        "link_code": f"{base}?start={ref_code}",
        "f1": f1,
        "f2": f2,
        "earnings": earnings,
        "withdrawn": withdrawn,
        "available": earnings - withdrawn,
        "rates": db.get_ref_rates(),
    }


def _extract_uids(filename: str, content: bytes) -> list:
    """Tách UID từ file txt/csv/xlsx. Trả về list UID duy nhất."""
    import re
    uids: list = []
    seen = set()

    def _add(text: str):
        for m in re.finditer(r"\d{6,}", text or ""):
            u = m.group(0)
            if u not in seen:
                seen.add(u)
                uids.append(u)

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "xlsx":
        from io import BytesIO
        import openpyxl
        wb = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=True)
        try:
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    for v in row:
                        if v is not None:
                            _add(str(v))
                    if len(uids) >= 1200:
                        break
                if len(uids) >= 1200:
                    break
        finally:
            wb.close()
    else:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("latin-1", errors="ignore")
        _add(text)
    return uids


async def _bulk_check_uids(uids: list, tg_id: int) -> list:
    """Check hàng loạt UID, giữ nguyên thứ tự. Mỗi phần tử: {uid, status, name}."""
    import asyncio
    from .fb import check_uid
    sem = asyncio.Semaphore(15)

    async def _one(uid: str) -> dict:
        async with sem:
            try:
                res = await check_uid(uid)
                st = res.get("status", "error")
                try:
                    db.log_check_stat(tg_id, "fb", uid, st, res.get("via", ""))
                except Exception:
                    pass
                return {"uid": uid, "status": st, "name": res.get("name") or ""}
            except Exception:
                return {"uid": uid, "status": "error", "name": ""}

    return list(await asyncio.gather(*[_one(u) for u in uids]))


@router.post("/api/mini/bulk")
async def mini_bulk(initData: str = Form(...), file: UploadFile = File(...)):
    """Check hàng loạt UID từ file upload (.txt/.csv/.xlsx) trong Mini App."""
    user = validate_init_data(initData)
    if not user:
        raise HTTPException(status_code=401, detail="initData không hợp lệ")
    tg_id = int(user.get("id", 0))
    filename = file.filename or "upload.txt"
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File quá lớn (tối đa 5MB)")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ("txt", "csv", "xlsx"):
        raise HTTPException(status_code=400, detail="Chỉ hỗ trợ file .txt, .csv, .xlsx")
    try:
        if ext == "xlsx":
            # xlsx: mọi cấu trúc cột, link FB tự giải thành UID
            from .fb import extract_uids_from_xlsx
            parsed = await extract_uids_from_xlsx(content)
            uids = parsed["uids"]
        else:
            uids = _extract_uids(filename, content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Không đọc được file: {e}")
    if not uids:
        raise HTTPException(status_code=400, detail="Không tìm thấy UID hợp lệ nào trong file")
    uids = uids[:1000]

    # Trừ credits (VIP còn hạn được miễn) — giống luồng check file trên bot
    bulk_cost = int(db.get_setting("bulk_credit_cost", "0") or 0)
    need = 0
    is_vip = False
    try:
        row = db.get_user(tg_id)
        if row and row["sub_until"] and row["sub_until"] > time.time():
            is_vip = True
    except Exception:
        pass
    if bulk_cost > 0 and not is_vip:
        need = len(uids) * bulk_cost
        have = db.get_credits(tg_id)
        if have < need:
            raise HTTPException(
                status_code=402,
                detail=f"Không đủ credits (cần {need}, bạn có {have}). Mua thêm bằng /muacredit",
            )
        if not db.consume_credits(tg_id, need):
            raise HTTPException(status_code=402, detail="Không trừ được credits, thử lại sau")

    results = await _bulk_check_uids(uids, tg_id)
    live_n = sum(1 for r in results if r["status"] == "live")
    die_n = sum(1 for r in results if r["status"] == "die")
    err_n = len(results) - live_n - die_n
    try:
        db.add_check_history(tg_id, "fb", f"Check file miniapp: {live_n} Live, {die_n} Die", "live" if live_n else "die")
    except Exception:
        pass
    credits_left = db.get_credits(tg_id)
    low_warn_at = int(db.get_setting("low_credit_warn", "50") or 50)
    return {
        "total": len(results),
        "live": live_n,
        "die": die_n,
        "error": err_n,
        "credits_used": need,
        "credits_left": credits_left,
        "low_credit_warn": credits_left < low_warn_at and need > 0,
        "results": results,
    }
