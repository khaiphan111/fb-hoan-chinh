"""Adapter ViOTP cho RentalProvider (GĐ1).

Bọc nguyên module app/viotp.py — hành vi giữ nguyên 100%, chỉ đổi cách gọi:
call site gọi qua provider thay vì import viotp trực tiếp.
"""
from .base import RentalProvider
from .. import viotp as _viotp


class ViotpProvider(RentalProvider):
    name = "viotp"
    display_name = "ViOTP"

    async def get_services(self, country: str = "vn") -> list:
        return await _viotp.get_services(country=country)

    async def rent_number(self, service_id: int, country: str = "vn") -> dict:
        # token "" -> viotp tự resolve từ Secure Vault (đúng hành vi cũ)
        return await _viotp.rent_number("", service_id, country=country)

    async def get_session(self, request_id: str) -> dict:
        return await _viotp.get_session("", request_id=request_id)

    def clear_services_cache(self) -> None:
        _viotp.clear_services_cache()

    def get_cached_services(self) -> list:
        return list(_viotp._services_cache.get("items", []))
