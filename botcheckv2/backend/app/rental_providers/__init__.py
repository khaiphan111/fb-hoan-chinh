"""Registry các nhà cung cấp thuê số OTP.

GĐ1: chỉ có ViOTP (hành vi không đổi). GĐ2 thêm OTPVN adapter vào đây.

Đăng ký lazy (trong _ensure_registered) để tránh import vòng tròn:
app/viotp.py import RentalProviderError từ .base, mà import .base sẽ chạy
__init__ này trước — nên __init__ không được import viotp_adapter ở cấp module.
"""
from .base import RentalProvider, RentalProviderError

_PROVIDERS: dict[str, RentalProvider] = {}
_registered = False


def register(provider: RentalProvider) -> None:
    _PROVIDERS[provider.name] = provider


def _ensure_registered() -> None:
    global _registered
    if not _registered:
        from .viotp_adapter import ViotpProvider
        register(ViotpProvider())
        _registered = True


def get_provider(name: str = "viotp") -> RentalProvider:
    _ensure_registered()
    try:
        return _PROVIDERS[name]
    except KeyError:
        raise RentalProviderError(f"Không có nhà cung cấp thuê số '{name}'")


def default_provider() -> RentalProvider:
    """Provider mặc định của shop /thueso (hiện tại: ViOTP)."""
    return get_provider("viotp")


def list_providers() -> list[RentalProvider]:
    _ensure_registered()
    return list(_PROVIDERS.values())


__all__ = [
    "RentalProvider", "RentalProviderError",
    "register", "get_provider", "default_provider", "list_providers",
]
