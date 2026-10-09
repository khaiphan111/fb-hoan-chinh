"""ViOTP API client — thuê số điện thoại nhận OTP (https://viotp.com).

Endpoints (đối chiếu từ tài liệu cộng đồng, cần verify lại khi có token thật):
  - GET /service/getv2?token=TOKEN&country=vn      → danh sách dịch vụ
  - GET /request/getv2?token=TOKEN&serviceId=ID     → thuê 1 số
  - GET /session/getv2?token=TOKEN&requestId=ID     → poll OTP

Response chung: {"status_code":..., "message":..., "success":bool, "data":{...}}
Session Status: 0 = đang chờ OTP, 1 = đã nhận OTP, 2 = hết hạn/hủy.
"""
import logging
import time
import sys

# FIX (liên kết module): `dynamic_credentials` CHỈ tồn tại trên máy Hatch (xem SETUP.md).
# Trước đây import ở cấp module nên trên máy khác cả app không import nổi:
#   main.py -> handlers/__init__ -> thueso -> viotp -> ModuleNotFoundError
# Lỗi lại xảy ra bên trong asyncio.create_task nên bị nuốt ("Task exception was never
# retrieved"): web admin vẫn lên nhưng bot Telegram KHÔNG bao giờ chạy — rất khó phát
# hiện. Nó cũng làm bộ test không chạy được ngoài máy Hatch. Nay import mềm: chỉ báo lỗi
# rõ ràng khi thực sự cần credential ViOTP.
_dc = None
try:
    sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
    import dynamic_credentials as _dc  # type: ignore
except Exception:
    _dc = None

from .fb import _make_http_client

log = logging.getLogger("viotp")

BASE = "https://api.viotp.com"
TIMEOUT = 25
_VAULT_CRED = "custom.viotp"

# Cache danh sách dịch vụ trong RAM (tránh spam API mỗi lần mở menu).
_services_cache: dict = {"at": 0, "country": "", "items": []}
SERVICES_TTL = 3600  # 1 giờ


class ViotpError(Exception):
    pass


def _vault_token() -> str:
    """Lấy surrogate token từ Secure Vault (authd thay bằng token thật khi gọi ra ngoài)."""
    if _dc is None:
        raise ViotpError(
            "Không có module dynamic_credentials (chỉ tồn tại trên máy Hatch) — "
            "hãy chạy trên máy Hatch hoặc truyền token ViOTP trực tiếp")
    entry = _dc.dynamic_credential_entry(_VAULT_CRED)
    tok = str(entry.get("surrogate") or "").strip()
    if not tok:
        raise ViotpError("Không lấy được credential ViOTP từ vault")
    return tok


def _resolve_token(token: str = "") -> str:
    if token:
        return token
    try:
        return _vault_token()
    except ViotpError:
        raise
    except Exception as e:
        raise ViotpError(f"Lỗi đọc credential ViOTP: {e}") from e


async def _get(token: str, path: str, params: dict) -> dict:
    token = _resolve_token(token)
    q = {"token": token}
    q.update(params or {})
    try:
        async with _make_http_client(timeout=TIMEOUT) as client:
            r = await client.get(BASE + path, params=q)
            r.raise_for_status()
            body = r.json()
    except ViotpError:
        raise
    except Exception as e:
        raise ViotpError(f"Lỗi kết nối ViOTP: {e}") from e
    if not isinstance(body, dict) or not body.get("success"):
        raise ViotpError(str(body.get("message") or body.get("status_code") or "ViOTP báo lỗi"))
    return body.get("data") or {}


async def get_services(token: str = "", country: str = "vn", force: bool = False) -> list:
    """Danh sách dịch vụ: [{id, name, price}] — price là giá vốn (đồng)."""
    global _services_cache
    now = time.time()
    if (not force and _services_cache["items"]
            and _services_cache["country"] == country
            and now - _services_cache["at"] < SERVICES_TTL):
        return _services_cache["items"]
    data = await _get(token, "/service/getv2", {"country": country})
    items = data if isinstance(data, list) else data.get("services") or data.get("list") or []
    out = []
    for s in items:
        try:
            out.append({
                "id": int(s.get("id") or s.get("serviceId") or s.get("service_id")),
                "name": str(s.get("name") or s.get("serviceName") or ""),
                "price": int(float(s.get("price") or s.get("cost") or 0)),
            })
        except (TypeError, ValueError):
            continue
    _services_cache = {"at": now, "country": country, "items": out}
    return out


def clear_services_cache():
    _services_cache.update({"at": 0, "items": []})


async def rent_number(token: str, service_id: int, country: str = "vn") -> dict:
    """Thuê 1 số: trả về {phone_number, request_id, countryISO, countryCode}."""
    data = await _get(token, "/request/getv2", {"serviceId": service_id, "country": country})
    phone = str(data.get("phone_number") or data.get("phoneNumber") or data.get("phone") or "")
    req_id = str(data.get("request_id") or data.get("requestId") or data.get("id") or "")
    if not phone or not req_id:
        raise ViotpError(f"ViOTP không trả số/request_id: {data}")
    return {
        "phone_number": phone,
        "request_id": req_id,
        "countryISO": str(data.get("countryISO") or ""),
        "countryCode": str(data.get("countryCode") or ""),
    }


async def get_session(token: str = "", request_id: str = "") -> dict:
    """Poll OTP: trả về {status: 0|1|2, code: str}."""
    data = await _get(token, "/session/getv2", {"requestId": request_id})
    try:
        status = int(data.get("Status", data.get("status", 0)))
    except (TypeError, ValueError):
        status = 0
    return {"status": status, "code": str(data.get("Code") or data.get("code") or "")}
