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
    assert "viotp" in [x.name for x in list_providers()]


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


# ---------------- GĐ2: OTPVN ----------------
from app import otpvn as _otpvn
from app.rental_providers.otpvn_adapter import OtpvnProvider


def test_registry_co_otpvn():
    p = get_provider("otpvn")
    assert isinstance(p, OtpvnProvider)
    assert p.name == "otpvn"
    assert {x.name for x in list_providers()} == {"viotp", "otpvn"}
    # default vẫn là viotp (hành vi không đổi)
    assert default_provider().name == "viotp"


def test_otpvn_error_tuong_thich():
    assert issubclass(_otpvn.OtpvnError, RentalProviderError)


def test_otpvn_adapter_uy_thac(monkeypatch):
    calls = {}

    async def fake_services(country="vn"):
        calls["services"] = country
        return [{"id": 7, "name": "Facebook", "price": 2700}]

    async def fake_rent(service_id, country="vn"):
        calls["rent"] = (service_id, country)
        return {"phone_number": "0909", "request_id": "abc123"}

    async def fake_session(request_id):
        calls["session"] = request_id
        return {"status": 1, "code": "999999"}

    async def fake_balance():
        return 50000

    monkeypatch.setattr(_otpvn, "get_services", fake_services)
    monkeypatch.setattr(_otpvn, "rent_number", fake_rent)
    monkeypatch.setattr(_otpvn, "get_session", fake_session)
    monkeypatch.setattr(_otpvn, "get_balance", fake_balance)
    monkeypatch.setattr(_otpvn, "_services_cache",
                        {"at": 0, "country": "", "items": [{"id": 2}]})

    p = OtpvnProvider()
    assert asyncio.run(p.get_services())[0]["price"] == 2700
    assert calls["services"] == "vn"
    r = asyncio.run(p.rent_number(7))
    assert r == {"phone_number": "0909", "request_id": "abc123"}
    assert calls["rent"] == (7, "vn")
    assert asyncio.run(p.get_session("abc123")) == {"status": 1, "code": "999999"}
    assert calls["session"] == "abc123"
    assert asyncio.run(p.get_balance()) == 50000
    assert p.get_cached_services() == [{"id": 2}]


def test_gia_rieng_tung_nha(tdb):
    # Mặc định 50% cả hai nhà
    assert tdb.rent_sell_price(3000, "viotp") == 4500
    assert tdb.rent_sell_price(3000, "otpvn") == 4500
    # Đổi riêng OTPVN -> ViOTP không đổi
    tdb.set_setting("otpvn_markup_pct", "100")
    assert tdb.rent_sell_price(3000, "otpvn") == 6000
    assert tdb.rent_sell_price(3000, "viotp") == 4500
    # Wrapper cũ giữ nguyên
    assert tdb.viotp_sell_price(3000) == 4500


def test_gia_co_dinh_tung_dich_vu(tdb):
    # Chưa đặt -> dùng % lãi
    assert tdb.rent_price_get("otpvn", 7) is None
    assert tdb.rent_sell_price(2700, "otpvn", 7) == tdb.rent_sell_price(2700, "otpvn")
    # Đặt giá 5000 cho Facebook OTPVN
    tdb.rent_price_set("otpvn", 7, 5000)
    assert tdb.rent_price_get("otpvn", 7) == 5000
    assert tdb.rent_sell_price(2700, "otpvn", 7) == 5000
    # Không ảnh hưởng ViOTP và dịch vụ khác
    assert tdb.rent_price_get("viotp", 7) is None
    assert tdb.rent_sell_price(2700, "otpvn", 99) != 5000
    # List
    lst = tdb.rent_price_list("otpvn")
    assert {"service_id": 7, "sell_price": 5000} in lst
    # Xóa (0) -> về % lãi
    tdb.rent_price_set("otpvn", 7, 0)
    assert tdb.rent_price_get("otpvn", 7) is None
    assert tdb.rent_price_list("otpvn") == []


# ---------------------------------------------------------------- GĐ3: gộp dịch vụ
from app.rental_providers import merge as _merge


class _FakeProv:
    def __init__(self, name, items):
        self.name = name
        self._items = items

    async def get_services(self, country="vn"):
        return self._items


def _fake_two(monkeypatch):
    viotp = _FakeProv("viotp", [
        {"id": 101, "name": "Facebook", "price": 2500},
        {"id": 102, "name": "Google", "price": 3000},
    ])
    otpvn_p = _FakeProv("otpvn", [
        {"id": 7, "name": "Facebook ", "price": 2700},   # tên lệch khoảng trắng
        {"id": 9, "name": "Telegram", "price": 11700},
    ])
    monkeypatch.setattr(_merge, "list_providers", lambda: [viotp, otpvn_p])


def test_merge_gop_theo_ten_chuan_hoa(monkeypatch, tdb):
    _fake_two(monkeypatch)
    items = asyncio.run(_merge.get_merged_services())
    by_key = {s["key"]: s for s in items}
    # Facebook gộp từ 2 nhà (chuẩn hóa bỏ khoảng trắng)
    fb = by_key["facebook"]
    assert set(fb["providers"]) == {"viotp", "otpvn"}
    assert fb["providers"]["viotp"]["service_id"] == 101
    assert fb["providers"]["otpvn"]["service_id"] == 7
    # Google chỉ ViOTP, Telegram chỉ OTPVN
    assert set(by_key["google"]["providers"]) == {"viotp"}
    assert set(by_key["telegram"]["providers"]) == {"otpvn"}
    assert len(items) == 3


def test_merge_nha_loi_khong_hong_danh_sach(monkeypatch, tdb):
    class _BadProv:
        name = "bad"

        async def get_services(self, country="vn"):
            raise RuntimeError("sập")

    good = _FakeProv("viotp", [{"id": 1, "name": "Zalo", "price": 1000}])
    monkeypatch.setattr(_merge, "list_providers", lambda: [_BadProv(), good])
    items = asyncio.run(_merge.get_merged_services())
    assert [s["key"] for s in items] == ["zalo"]


def test_offers_sorted_re_truoc_theo_gia_ban(monkeypatch, tdb):
    _fake_two(monkeypatch)
    # DB tạm (fixture tdb): markup 2 nhà = 0% để so đúng giá vốn
    tdb.set_setting("viotp_markup_pct", "0")
    tdb.set_setting("otpvn_markup_pct", "0")
    items = asyncio.run(_merge.get_merged_services())
    fb = {s["key"]: s for s in items}["facebook"]
    offers = _merge.offers_sorted(fb)
    # ViOTP 2500 rẻ hơn OTPVN 2700 -> đứng trước
    assert offers[0][0] == "viotp" and offers[0][3] == 2500
    assert offers[1][0] == "otpvn" and offers[1][3] == 2700
    # Giá cố định ghi đè: đặt OTPVN Facebook = 1000 -> OTPVN lên trước
    tdb.rent_price_set("otpvn", 7, 1000)
    offers = _merge.offers_sorted(fb)
    assert offers[0][0] == "otpvn" and offers[0][3] == 1000
    assert _merge.best_sell(fb) == 1000


# ------------------------------------------------- GĐ3b: bật/tắt từng nhà
def test_provider_enabled_mac_dinh_bat(monkeypatch, tdb):
    assert _merge.provider_enabled("viotp") is True
    assert _merge.provider_enabled("otpvn") is True
    assert _merge.any_provider_enabled() is True


def test_tat_nha_loai_khoi_danh_sach_gop(monkeypatch, tdb):
    _fake_two(monkeypatch)
    tdb.set_setting("otpvn_enabled", "0")
    assert _merge.provider_enabled("otpvn") is False
    assert _merge.any_provider_enabled() is True  # còn ViOTP
    items = asyncio.run(_merge.get_merged_services())
    by_key = {s["key"]: s for s in items}
    # Facebook chỉ còn ViOTP
    assert set(by_key["facebook"]["providers"]) == {"viotp"}
    # Telegram (chỉ OTPVN có) biến mất
    assert "telegram" not in by_key
    # Tắt nốt ViOTP -> không còn nhà nào
    tdb.set_setting("viotp_enabled", "0")
    assert _merge.any_provider_enabled() is False
    items = asyncio.run(_merge.get_merged_services())
    assert items == []


# --------------------------------------- GĐ3c: đối soát lãi riêng từng nhà
def test_profit_report_tach_lai_theo_nha(tdb):
    from app import profit_report as _pr
    # 2 đơn done: 1 ViOTP (vốn 2500, bán 3750), 1 OTPVN (vốn 2700, bán 4050)
    r1 = tdb.viotp_rental_create(1, "req1", "0901", 101, "Facebook", "vn", 2500, 3750,
                                 provider="viotp")
    r2 = tdb.viotp_rental_create(1, "req2", "0902", 7, "Facebook", "vn", 2700, 4050,
                                 provider="otpvn")
    tdb.viotp_rental_set_status(r1, "done", "111111")
    tdb.viotp_rental_set_status(r2, "done", "222222")
    data = _pr.daily_profit()
    ts = data["thueso"]
    assert ts["don"] == 2
    assert ts["lai"] == (3750 - 2500) + (4050 - 2700)
    theo = ts["theo_nha"]
    assert theo["viotp"]["lai"] == 1250
    assert theo["otpvn"]["lai"] == 1350
    txt = _pr.format_report(data)
    assert "viotp" in txt and "otpvn" in txt
