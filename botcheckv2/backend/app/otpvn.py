"""OTPVN API client — thuê số điện thoại nhận OTP (https://otpvn.com).

API: https://api.otpvn.com — xác thực Authorization: Bearer <token>.
Tài liệu: POST /v1/rentals {"serviceId": 1} -> thuê số (giữ id);
GET /v1/rentals/{id} poll 5s/lần tới khi status != 0:
  status 0 = đang chờ mã, 1 = có mã trong `code`, 2 = hết hạn (tiền tự hoàn).
Lưu ý: Zalo hiện chưa thuê qua API được.

Credential qua Secure Vault (custom.otpvn) giống viotp.py: import mềm
dynamic_credentials, chỉ báo lỗi khi thực sự cần gọi API.
"""
import logging
import sys
import time

_dc = None
try:
    sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
    import dynamic_credentials as _dc  # type: ignore
except Exception:
    _dc = None

from .fb import _make_http_client
from .rental_providers.base import RentalProviderError

log = logging.getLogger("otpvn")

BASE = "https://api.otpvn.com"
TIMEOUT = 25
_VAULT_CRED = "custom.otpvn"

_services_cache: dict = {"at": 0, "country": "", "items": []}
SERVICES_TTL = 3600  # 1 giờ


class OtpvnError(RentalProviderError):
    """Lỗi OTPVN — con của RentalProviderError nên code bắt lỗi chung vẫn được."""
    pass


def _vault_token() -> str:
    """Lấy surrogate token từ Secure Vault (authd thay bằng token thật khi gọi ra ngoài)."""
    if _dc is None:
        raise OtpvnError(
            "Không có module dynamic_credentials (chỉ tồn tại trên máy Hatch) — "
            "hãy chạy trên máy Hatch")
    entry = _dc.dynamic_credential_entry(_VAULT_CRED)
    tok = str(entry.get("surrogate") or "").strip()
    if not tok:
        raise OtpvnError("Không lấy được credential OTPVN từ vault")
    return tok


def _headers() -> dict:
    return {"Authorization": f"Bearer {_vault_token()}"}


def _raise_for_body(body, ctx: str):
    """OTPVN trả JSON; lỗi thường là HTTP error đã raise ở dưới."""
    if isinstance(body, dict) and body.get("error"):
        raise OtpvnError(f"OTPVN báo lỗi ({ctx}): {body.get('error')}")
    return body


async def _get(path: str) -> object:
    try:
        async with _make_http_client(timeout=TIMEOUT) as client:
            r = await client.get(BASE + path, headers=_headers())
            r.raise_for_status()
            body = r.json()
    except OtpvnError:
        raise
    except Exception as e:
        raise OtpvnError(f"Lỗi kết nối OTPVN ({path}): {e}") from e
    return _raise_for_body(body, path)


async def _post(path: str, payload: dict) -> object:
    try:
        async with _make_http_client(timeout=TIMEOUT) as client:
            r = await client.post(BASE + path, json=payload, headers=_headers())
            r.raise_for_status()
            body = r.json()
    except OtpvnError:
        raise
    except Exception as e:
        raise OtpvnError(f"Lỗi kết nối OTPVN ({path}): {e}") from e
    return _raise_for_body(body, path)


async def get_services(country: str = "vn", force: bool = False) -> list:
    """Danh sách dịch vụ: [{id, name, price}] — price là giá vốn (đồng).

    OTPVN không phân biệt country như ViOTP; tham số country giữ cho khớp
    interface provider (hiện bỏ qua).
    """
    global _services_cache
    now = time.time()
    if (not force and _services_cache["items"]
            and _services_cache["country"] == country
            and now - _services_cache["at"] < SERVICES_TTL):
        return _services_cache["items"]
    body = await _get("/v1/services")
    items = body if isinstance(body, list) else (
        (body.get("data") if isinstance(body, dict) else None) or
        (body.get("services") if isinstance(body, dict) else None) or [])
    if isinstance(items, dict):
        items = items.get("services") or items.get("list") or []
    out = []
    for s in items:
        if not isinstance(s, dict):
            continue
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


async def rent_number(service_id: int, country: str = "vn") -> dict:
    """Thuê 1 số: trả về {phone_number, request_id} (request_id = id của OTPVN)."""
    body = await _post("/v1/rentals", {"serviceId": int(service_id)})
    d = body.get("data") if isinstance(body, dict) else None
    d = d if isinstance(d, dict) else (body if isinstance(body, dict) else {})
    rid = str(d.get("id") or d.get("rentalId") or d.get("request_id") or "")
    phone = str(d.get("phone") or d.get("phoneNumber") or d.get("phone_number") or "")
    if not rid:
        raise OtpvnError(f"OTPVN không trả id rental: {str(body)[:200]}")
    return {"phone_number": phone, "request_id": rid}


async def get_session(request_id: str) -> dict:
    """Poll OTP: trả về {status: 0|1|2, code: str}."""
    body = await _get(f"/v1/rentals/{request_id}")
    d = body.get("data") if isinstance(body, dict) else None
    d = d if isinstance(d, dict) else (body if isinstance(body, dict) else {})
    try:
        status = int(d.get("status", 0))
    except (TypeError, ValueError):
        status = 0
    return {"status": status, "code": str(d.get("code") or "")}


async def get_balance() -> int:
    """Số dư tài khoản OTPVN (đồng)."""
    body = await _get("/v1/balance")
    d = body if isinstance(body, dict) else {}
    try:
        return int(float(d.get("balance") or 0))
    except (TypeError, ValueError):
        return 0
