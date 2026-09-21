# API cho reseller — check FB live/die tính phí theo lượt (credits).
# Xác thực qua header X-API-Key. Mỗi lượt check trừ 1 credit.
import asyncio
import logging
import socket
import time
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException

from . import db

router = APIRouter(prefix="/api/v1", tags=["reseller"])
log = logging.getLogger(__name__)


async def _webhook_url_ok(url: str) -> bool:
    """URL webhook hợp lệ: http(s) và không resolve ra IP nội bộ (chống SSRF)."""
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False
        host = (p.hostname or "").lower()
        if not host:
            return False
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
        if not infos:
            return False
        import ipaddress
        for info in infos:
            ip = ipaddress.ip_address(str(info[4][0]))
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True
    except Exception:
        return False


def _fire_webhook(key: dict, event: str, payload: dict) -> None:
    """Bắn webhook bất đồng bộ (fire-and-forget) nếu key có webhook_url."""
    url = (key.get("webhook_url") or "").strip()
    if not url:
        return

    async def _post():
        try:
            if not await _webhook_url_ok(url):
                log.warning("Webhook URL không an toàn, bỏ qua: %s", url)
                return
            import httpx
            async with httpx.AsyncClient(timeout=6) as cli:
                await cli.post(url, json={
                    "event": event,
                    "key_name": key.get("name"),
                    "at": int(time.time()),
                    "data": payload,
                })
        except Exception as e:
            log.warning("Webhook thất bại (%s): %s", url, e)

    try:
        asyncio.create_task(_post())
    except RuntimeError:
        pass

# Rate limit đơn giản: tối đa 60 request/phút cho mỗi key
_RATE: dict = {}
_RATE_LIMIT = 60


def _get_key(x_api_key: str = Header(default=None, alias="X-API-Key")) -> dict:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Thiếu header X-API-Key")
    row = db.get_reseller_key(x_api_key)
    if not row:
        raise HTTPException(status_code=401, detail="API key không hợp lệ hoặc đã bị khóa")
    return dict(row)


def _rate_limit(key_id: int) -> None:
    now = time.time()
    hits = [t for t in _RATE.get(key_id, []) if now - t < 60]
    if len(hits) >= _RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Quá nhiều request — thử lại sau 1 phút")
    hits.append(now)
    _RATE[key_id] = hits


@router.get("/balance")
def api_balance(key: dict = Depends(_get_key)):
    """Xem số credits còn lại của key."""
    return {"name": key["name"], "credits": int(key["credits"] or 0)}


@router.post("/fb/check")
async def api_fb_check(body: dict, key: dict = Depends(_get_key)):
    """Check live/die 1 UID Facebook. Body: {"uid": "..."} hoặc {"target": "..."}."""
    from .fb import check_uid

    _rate_limit(key["id"])
    target = str(body.get("uid") or body.get("target") or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="Thiếu uid/target")
    if not db.consume_reseller_credits(key["id"], 1):
        raise HTTPException(status_code=402, detail="Hết credits — liên hệ admin để nạp thêm")
    try:
        res = await check_uid(target)
    except Exception as e:
        db.log_reseller_usage(key["id"], "/api/v1/fb/check", target, 1)
        raise HTTPException(status_code=500, detail=f"Lỗi khi check: {e}")
    db.log_reseller_usage(key["id"], "/api/v1/fb/check", target, 1)
    result = {
        "uid": res.get("uid"),
        "status": "live" if res.get("alive") else "die",
        "name": res.get("name") or "",
        "via": res.get("via") or "",
        "has_avatar": bool(res.get("has_real_avatar")),
    }
    _fire_webhook(key, "fb.check", {"target": target, **result})
    return result


@router.post("/fb/getuid")
async def api_fb_getuid(body: dict, key: dict = Depends(_get_key)):
    """Lấy UID từ link Facebook. Body: {"link": "..."}."""
    from .fb import resolve_fb_uid

    _rate_limit(key["id"])
    link = str(body.get("link") or "").strip()
    if not link:
        raise HTTPException(status_code=400, detail="Thiếu link")
    if not db.consume_reseller_credits(key["id"], 1):
        raise HTTPException(status_code=402, detail="Hết credits — liên hệ admin để nạp thêm")
    try:
        uid, name, method = await resolve_fb_uid(link)
    except Exception as e:
        db.log_reseller_usage(key["id"], "/api/v1/fb/getuid", link, 1)
        raise HTTPException(status_code=500, detail=f"Lỗi khi lấy UID: {e}")
    db.log_reseller_usage(key["id"], "/api/v1/fb/getuid", link, 1)
    result = {"uid": uid, "name": name or "", "method": method}
    _fire_webhook(key, "fb.getuid", {"link": link, **result})
    return result


@router.get("/usage")
def api_usage(limit: int = 50, key: dict = Depends(_get_key)):
    """Lịch sử sử dụng gần đây của key."""
    rows = db.get_reseller_usage(key["id"], limit=min(limit, 200))
    return {
        "usage": [
            {
                "endpoint": r["endpoint"],
                "target": r["target"],
                "credits_used": r["credits_used"],
                "at": r["created_at"],
            }
            for r in rows
        ]
    }
