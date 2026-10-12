"""Adapter OTPVN cho RentalProvider (GĐ2).

Bọc module app/otpvn.py. Quy ước status 0/1/2 của OTPVN khớp đúng interface.
"""
from .base import RentalProvider
from .. import otpvn as _otpvn


class OtpvnProvider(RentalProvider):
    name = "otpvn"
    display_name = "OTPVN"

    async def get_services(self, country: str = "vn") -> list:
        return await _otpvn.get_services(country=country)

    async def rent_number(self, service_id: int, country: str = "vn") -> dict:
        return await _otpvn.rent_number(service_id, country=country)

    async def get_session(self, request_id: str) -> dict:
        return await _otpvn.get_session(request_id)

    async def get_balance(self) -> int | None:
        return await _otpvn.get_balance()

    def clear_services_cache(self) -> None:
        _otpvn.clear_services_cache()

    def get_cached_services(self) -> list:
        return list(_otpvn._services_cache.get("items", []))
