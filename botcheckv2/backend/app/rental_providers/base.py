"""Interface chung cho các nhà cung cấp thuê số OTP (GĐ1).

Mọi provider (ViOTP, OTPVN, ...) implement interface này. Shop /thueso và
các vòng poll chỉ gọi qua interface — thêm provider mới không đụng code cũ.
"""


class RentalProviderError(Exception):
    """Lỗi chung của mọi provider thuê số (hết số, lỗi API, timeout...)."""
    pass


class RentalProvider:
    """Interface nhà cung cấp thuê số OTP.

    Quy ước status của get_session (thống nhất mọi provider):
      0 = đang chờ mã, 1 = đã có mã (trong `code`), 2 = hết hạn/không mã
      (tiền đã tự hoàn bên provider).
    """
    name = "base"           # slug: 'viotp', 'otpvn', ...
    display_name = "Base"   # tên hiện cho khách/admin

    async def get_services(self, country: str = "vn") -> list:
        """Danh sách dịch vụ: [{id, name, price}] — price là giá vốn (đồng)."""
        raise NotImplementedError

    async def rent_number(self, service_id: int, country: str = "vn") -> dict:
        """Thuê 1 số: trả về {phone_number, request_id, ...}."""
        raise NotImplementedError

    async def get_session(self, request_id: str) -> dict:
        """Poll OTP: trả về {status: 0|1|2, code: str}."""
        raise NotImplementedError

    async def get_balance(self) -> int | None:
        """Số dư tài khoản API (đồng). None = provider chưa hỗ trợ."""
        return None

    def clear_services_cache(self) -> None:
        """Xóa cache danh sách dịch vụ (nếu provider có cache)."""

    def get_cached_services(self) -> list:
        """Dịch vụ đã cache (không gọi API). Trả [] nếu chưa có."""
        return []
