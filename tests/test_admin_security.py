"""Test cho các bản vá BẢO MẬT web admin (P0).

Bối cảnh (xem BAO-CAO-REVIEW):
  S1: GET /api/settings trả cả bot_token; mà bot_token từng là khoá ký token admin
      -> moderator đọc được là tự ký token super-admin.
  S2: get_secret() fallback về chuỗi hard-code "default_secret_key_12345".
  S3: super-admin seed với mật khẩu rỗng (sha256("")).
  S4: endpoint tiền/user chỉ cần Depends(auth), không kiểm tra role.
  S7: POST /api/settings ghi mọi key client gửi -> ghi đè key nội bộ.

Chạy: python -m pytest tests/test_admin_security.py -q
"""
import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _hash(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


@pytest.fixture()
def api_client(tdb):
    from app.api import router as api_router
    app = FastAPI()
    app.include_router(api_router)
    return TestClient(app)


def _make_admin(db, username: str, password: str, role: str) -> int:
    return db.create_admin(username, _hash(password), username, role)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_get_secret_khong_dung_bot_token(tdb, monkeypatch):
    """S2: khoá ký KHÔNG được lấy từ bot_token hay hằng số hard-code."""
    from app import api

    monkeypatch.delenv("APP_SECRET", raising=False)
    db = tdb
    db.set_setting("bot_token", "123456:FAKE-BOT-TOKEN")
    db.set_setting("app_secret", "")

    s1 = api.get_secret()
    s2 = api.get_secret()

    assert s1 != b"123456:FAKE-BOT-TOKEN", "khoa ky van lay tu bot_token!"
    assert s1 != b"default_secret_key_12345", "van dung secret hard-code!"
    assert len(s1) >= 32, "khoa ky qua ngan"
    assert s1 == s2, "khoa ky phai on dinh giua cac lan goi (da luu vao settings)"

    import hashlib as _h
    import hmac as _hmac
    expiry = 9999999999
    forged = _hmac.new(b"123456:FAKE-BOT-TOKEN", f"admin-1-{expiry}".encode(),
                       _h.sha256).hexdigest()
    assert api.verify_admin_token(f"admin-1-{expiry}-{forged}") is None, \
        "token gia ky bang bot_token van duoc chap nhan!"


def test_login_tu_choi_mat_khau_rong(api_client, tdb):
    """S3: không được đăng nhập bằng mật khẩu rỗng dù hash trong DB là sha256("")."""
    db = tdb
    adm = db.get_admin_by_username("khaiphan111")
    assert adm, "chua seed super-admin?"
    db.update_admin(adm["id"], password_hash=_hash(""))

    r = api_client.post("/api/login", json={"username": "khaiphan111", "password": ""})
    assert r.status_code == 401, f"van dang nhap duoc bang mat khau rong! {r.text}"

    r = api_client.post("/api/login", json={"username": "khaiphan111", "password": "   "})
    assert r.status_code == 401, "mat khau toan khoang trang van vao duoc"

    db.update_admin(adm["id"], password_hash=_hash("MatKhauThat123"))
    r = api_client.post("/api/login", json={"username": "khaiphan111",
                                            "password": "MatKhauThat123"})
    assert r.status_code == 200 and r.json().get("token"), r.text


def test_settings_an_secret_voi_moderator(api_client, tdb):
    """S1: moderator KHÔNG được thấy token; super_admin thì được."""
    from app.api import create_admin_token

    db = tdb
    db.set_setting("bot_token", "999:SUPER-SECRET-TOKEN")
    mod_id = _make_admin(db, "mod1", "ModPass123", "moderator")
    super_id = _make_admin(db, "boss1", "BossPass123", "super_admin")

    r_mod = api_client.get("/api/settings", headers=_bearer(create_admin_token(mod_id)))
    assert r_mod.status_code == 200, r_mod.text
    body = r_mod.json()
    for k in ("bot_token", "admin_bot_token", "zalo_bot_token", "fb_cookie", "app_secret"):
        assert k not in body, f"moderator van doc duoc {k}!"

    r_sup = api_client.get("/api/settings", headers=_bearer(create_admin_token(super_id)))
    assert r_sup.status_code == 200, r_sup.text
    sup_body = r_sup.json()
    assert sup_body.get("bot_token") == "999:SUPER-SECRET-TOKEN"
    assert "app_secret" not in sup_body, "khoa ky noi bo bi tra ra API!"


def test_settings_chi_ghi_key_trong_whitelist(api_client, tdb):
    """S7: key lạ không được ghi vào bảng settings."""
    from app.api import create_admin_token

    db = tdb
    sup_id = _make_admin(db, "boss2", "BossPass123", "super_admin")
    before = db.get_setting("setup_done", "0")

    r = api_client.post("/api/settings", headers=_bearer(create_admin_token(sup_id)),
                        json={"web_domain": "https://example.com",
                              "setup_done": "0",
                              "qr_images": ["qr_1.jpg"],
                              "key_la_khong_hop_le": "hacked"})
    assert r.status_code == 200, r.text
    assert db.get_setting("setup_done", "0") == before, "key noi bo bi ghi de!"
    assert not db.get_setting("key_la_khong_hop_le", ""), "key la bi ghi xuong DB!"
    assert not db.get_setting("qr_images", ""), "key dan xuat qr_images bi ghi xuong DB!"
    assert db.get_setting("web_domain") == "https://example.com"


def test_endpoint_tien_yeu_cau_role_admin(api_client, tdb):
    """S4: moderator không được nạp/trừ ví, xoá user, sửa cấu hình."""
    from app.api import create_admin_token

    db = tdb
    mod_id = _make_admin(db, "mod2", "ModPass123", "moderator")
    tok = _bearer(create_admin_token(mod_id))

    cases = (
        ("post", "/api/users/123/topup", {"amount": 10000}),
        ("post", "/api/users/123/wallet", {"wallet": "main", "amount": -1000}),
        ("post", "/api/users/123/trial", {"days": 3}),
        ("post", "/api/users/123/reset", None),
        ("delete", "/api/users/123", None),
        ("post", "/api/admin/withdrawals/1/approve", None),
        ("post", "/api/settings", {"web_domain": "https://x.y"}),
    )
    for method, path, payload in cases:
        call = getattr(api_client, method)
        if payload is not None:
            r = call(path, headers=tok, json=payload)
        else:
            r = call(path, headers=tok)
        assert r.status_code == 403, f"{method.upper()} {path} khong chan moderator: {r.status_code}"


def test_qr_route_chan_truy_cap_an_danh_va_path_traversal(api_client, tdb):
    """Ảnh QR ngân hàng không còn public; chặn cả path traversal."""
    r = api_client.get("/api/qr/qr_1.jpg")
    assert r.status_code == 401, "anh QR van tai duoc khong can token!"

    for bad in ("..%2F..%2Fdata.db", "...%2Fapp%2Fdb.py", "khong_phai_qr.png", "data.db"):
        r = api_client.get(f"/api/qr/{bad}")
        assert r.status_code in (401, 404), f"{bad} tra {r.status_code} - phai chan"

    from app.api import create_admin_token
    db = tdb
    sup_id = _make_admin(db, "boss3", "BossPass123", "super_admin")
    tok = create_admin_token(sup_id)
    r = api_client.get(f"/api/qr/khong_ton_tai.jpg?t={tok}")
    assert r.status_code == 404
