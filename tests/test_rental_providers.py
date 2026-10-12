"""Test GĐ1: lớp provider thuê số — ViOTP thành adapter đầu tiên, hành vi không đổi."""
import asyncio

from app import db as _db
from app import viotp as _viotp
from app.rental_providers import (
    RentalProviderError,
    default_provider,
    get_provider,
    list_providers,
)
from app.rental_providers.viotp_adapter import ViotpProvider


def test_registry_default_la_viotp():
    p = default_provider()
    assert isinstance(p, ViotpProvider)
    assert p.name == "viotp"
    assert get_provider("viotp") is p
    assert [x.name for x in list_providers()] == ["viotp"]


def test_registry_provider_la_bao_loi_chung():
    try:
        get_provider("khong_ton_tai")
        assert False, "phải raise"
    except RentalProviderError:
        pass


def test_viotp_error_tuong_thich_nguoc():
    # Code cũ `except viotp.ViotpError` vẫn bắt được (GĐ1 giữ nguyên hành vi)
    assert issubclass(_viotp.ViotpError, RentalProviderError)
    try:
        raise _viotp.ViotpError("x")
    except RentalProviderError:
        pass
    else:
        assert False, "except RentalProviderError phải bắt được ViotpError"


def test_adapter_uy_thac_nguyen_viotp(monkeypatch):
    calls = {}

    async def fake_services(country="vn"):
        calls["services"] = country
        return [{"id": 7, "name": "Facebook", "price": 2700}]

    async def fake_rent(token, service_id, country="vn"):
        calls["rent"] = (token, service_id, country)
        return {"phone_number": "0901", "request_id": "r1"}

    async def fake_session(token="", request_id=""):
        calls["session"] = (token, request_id)
        return {"status": 1, "code": "123456"}

    monkeypatch.setattr(_viotp, "get_services", fake_services)
    monkeypatch.setattr(_viotp, "rent_number", fake_rent)
    monkeypatch.setattr(_viotp, "get_session", fake_session)
    monkeypatch.setattr(_viotp, "_services_cache",
                        {"at": 0, "country": "", "items": [{"id": 1}]})

    p = ViotpProvider()
    assert asyncio.run(p.get_services(country="vn"))[0]["name"] == "Facebook"
    assert calls["services"] == "vn"
    r = asyncio.run(p.rent_number(7, country="vn"))
    assert r["request_id"] == "r1"
    # token "" -> viotp tự resolve từ Secure Vault (đúng hành vi cũ)
    assert calls["rent"] == ("", 7, "vn")
    s = asyncio.run(p.get_session("r1"))
    assert s == {"status": 1, "code": "123456"}
    assert calls["session"] == ("", "r1")
    assert p.get_cached_services() == [{"id": 1}]
    p.clear_services_cache()  # không nổ


def test_don_thue_luu_provider(tdb):
    rid = tdb.viotp_rental_create(999, "req-p1", "0901", 7, "Facebook",
                                  "vn", 2700, 4050)
    row = tdb.get_conn().execute(
        "SELECT provider FROM viotp_rentals WHERE id=?", (rid,)).fetchone()
    assert row[0] == "viotp"  # mặc định đơn cũ
    rid2 = tdb.viotp_rental_create(999, "req-p2", "0902", 7, "Facebook",
                                   "vn", 2700, 4050, provider="otpvn")
    row2 = tdb.get_conn().execute(
        "SELECT provider FROM viotp_rentals WHERE id=?", (rid2,)).fetchone()
    assert row2[0] == "otpvn"
