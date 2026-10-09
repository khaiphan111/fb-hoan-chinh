# FB Live/Die Checker & Tiktok Checker
import logging
import re
import secrets
import time
from collections import defaultdict

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile, File, Request
from pydantic import BaseModel, Field
import os

from . import config, db, fb
# from .bot import manager, zalo_manager -> lazy trong tung ham (muc 5)
from .poller import poller
from .util import now

# --- Chong brute-force /login (them 2026-09-30) ---
# Theo doi so lan dang nhap sai theo IP. Qua 5 lan trong 15 phut -> khoa 15 phut.
_LOGIN_FAILS: dict = defaultdict(list)  # ip -> [timestamp, ...]
_LOGIN_MAX_FAILS = 5
_LOGIN_WINDOW = 15 * 60  # 15 phut

def _login_blocked(ip: str) -> tuple[bool, int]:
    """Tra (bi_khoa, so_giay_con_lai)."""
    now = time.time()
    fails = [t for t in _LOGIN_FAILS.get(ip, []) if now - t < _LOGIN_WINDOW]
    _LOGIN_FAILS[ip] = fails
    if len(fails) >= _LOGIN_MAX_FAILS:
        wait = int(_LOGIN_WINDOW - (now - fails[0]))
        return True, max(wait, 1)
    return False, 0

def _login_fail(ip: str):
    _LOGIN_FAILS[ip].append(time.time())

def _login_ok(ip: str):
    _LOGIN_FAILS.pop(ip, None)

def _check_password_strength(pw: str) -> str | None:
    """Tra None neu manh, tra thong bao loi neu yeu."""
    if len(pw) < 8:
        return "Mật khẩu phải từ 8 ký tự trở lên"
    if not any(c.isupper() for c in pw):
        return "Mật khẩu phải có ít nhất 1 chữ HOA"
    if not any(c.islower() for c in pw):
        return "Mật khẩu phải có ít nhất 1 chữ thường"
    if not any(c.isdigit() for c in pw):
        return "Mật khẩu phải có ít nhất 1 chữ số"
    return None
from .event_bus import event_bus
from fastapi import WebSocket, WebSocketDisconnect

router = APIRouter(prefix="/api")
_tokens = set()

log = logging.getLogger(__name__)

DAY = 86400


# Old auth removed, now below _user_tokens


class LoginIn(BaseModel):
    username: str = "admin"
    password: str


class SettingsIn(BaseModel):
    model_config = {"extra": "allow"}
    bot_token: str | None = None
    zalo_bot_token: str | None = None
    price_1d: str | None = None
    price_7d: str | None = None
    price_1m: str | None = None
    poll_interval: str | None = None
    fb_avatar_token: str | None = None
    admin_password: str | None = None
    ig_method: str | None = None
    ig_rapidapi_key: str | None = None
    ig_session_cookie: str | None = None
    ig_username: str | None = None
    ig_password: str | None = None
    enable_free_trial: str | None = None
    free_trial_days: str | None = None
    bank_name: str | None = None
    bank_account: str | None = None
    bank_owner: str | None = None
    banks_list: str | None = None
    admin_zalo_id: str | None = None
    admin_bot_token: str | None = None
    admin_tg_id: str | None = None
    admin_tg_group_id: str | None = None
    main_tg_group_id: str | None = None
    vip0_limit: str | None = None
    vip1_limit: str | None = None
    vip2_limit: str | None = None
    vip3_limit: str | None = None
    vip1_price: str | None = None
    vip2_price: str | None = None
    vip3_price: str | None = None
    vip_lifetime_price: str | None = None
    proxy_api_url: str | None = None
    proxy_api_key: str | None = None
    min_active_proxies: str | None = None
    vip0_daily_check: str | None = None
    vip1_daily_check: str | None = None
    vip2_daily_check: str | None = None
    vip3_daily_check: str | None = None
    fb_cookie: str | None = None
    zalo_cookie: str | None = None
    zalo_imei: str | None = None
    web_domain: str | None = None
    ref_f1_pct: str | None = None
    ref_f1_silver_min: str | None = None
    ref_f1_silver_pct: str | None = None
    ref_f1_gold_min: str | None = None
    ref_f1_gold_pct: str | None = None
    ref_f2_pct: str | None = None


class TokenIn(BaseModel):
    token: str


class AmountIn(BaseModel):
    # FIX (M11): trước đây nhận cả số âm -> POST /users/{id}/topup {"amount": -100000}
    # là TRỪ tiền của user mà vẫn trả ok:True (và bỏ qua giá trị trả về của db).
    amount: int = Field(gt=0)


class WalletAdjustIn(BaseModel):
    wallet: str  # main | shop | buff | rent
    amount: int  # dương = cộng, âm = trừ


class MonthsIn(BaseModel):
    days: int = Field(ge=1)


class UidIn(BaseModel):
    uid: str


def _row(r):
    return dict(r) if r else None


def get_secret():
    """Khoá ký token admin/user.

    FIX 2026 (bảo mật): TRƯỚC ĐÂY hàm này lấy `bot_token` làm khoá ký, và fallback
    về chuỗi hard-code "default_secret_key_12345". Hai hệ quả:
      1. Ai đọc được bot_token (vd moderator qua GET /api/settings) là tự ký được
         token super-admin -> leo thang đặc quyền.
      2. DB mới/chưa cấu hình bot -> khoá ký là hằng số nằm trong source, ai biết
         source cũng tạo được token super-admin.
    Nay: ưu tiên biến môi trường APP_SECRET; nếu chưa có thì sinh ngẫu nhiên 1 lần
    và lưu vào settings (app_secret) để token không đổi sau mỗi lần restart.
    LƯU Ý KHI NÂNG CẤP: token cũ (ký bằng bot_token) sẽ hết hiệu lực -> admin và
    user phải đăng nhập lại 1 lần."""
    env_secret = (os.environ.get("APP_SECRET") or "").strip()
    if env_secret:
        return env_secret.encode()

    cur = (db.get_setting("app_secret", "") or "").strip()
    if not cur:
        cur = secrets.token_hex(32)
        try:
            db.set_setting("app_secret", cur)
        except Exception:
            log.warning("Không lưu được app_secret vào DB; token sẽ hết hiệu lực "
                        "sau khi restart backend", exc_info=True)
    return cur.encode()

def create_admin_token(admin_id: int):
    import hmac
    import hashlib
    import time
    expiry = int(time.time()) + 86400 * 36500  # Never expires (100 years)
    data = f"admin-{admin_id}-{expiry}"
    signature = hmac.new(get_secret(), data.encode(), hashlib.sha256).hexdigest()
    return f"{data}-{signature}"

def verify_admin_token(token: str):
    import hmac
    import hashlib
    import time
    try:
        parts = token.split("-")
        if len(parts) != 4 or parts[0] != "admin":
            return None
        admin_id = int(parts[1])
        expiry = int(parts[2])
        signature = parts[3]
        if time.time() > expiry:
            return None
        data = f"admin-{admin_id}-{expiry}"
        expected = hmac.new(get_secret(), data.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(signature, expected):
            return admin_id
    except:
        pass
    return None

def create_user_token(tg_id: int):
    import hmac
    import hashlib
    import time
    expiry = int(time.time()) + 86400 * 30
    data = f"user-{tg_id}-{expiry}"
    signature = hmac.new(get_secret(), data.encode(), hashlib.sha256).hexdigest()
    return f"{data}-{signature}"

def verify_user_token(token: str):
    import hmac
    import hashlib
    import time
    try:
        parts = token.split("-")
        if len(parts) != 4 or parts[0] != "user":
            return None
        tg_id = int(parts[1])
        expiry = int(parts[2])
        signature = parts[3]
        if time.time() > expiry:
            return None
        data = f"user-{tg_id}-{expiry}"
        expected = hmac.new(get_secret(), data.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(signature, expected):
            return tg_id
    except:
        pass
    return None

@router.post("/login")
def login(body: LoginIn, request: Request):
    ip = request.client.host if request.client else "unknown"
    # Chong brute-force: khoa IP 15 phut sau 5 lan sai
    blocked, wait = _login_blocked(ip)
    if blocked:
        raise HTTPException(status_code=429, detail=f"Quá nhiều lần thử sai. Vui lòng đợi {wait//60} phút {wait%60} giây.")
    # FIX: chặn mật khẩu rỗng. Trước đây super_admin được seed với sha256("") nên
    # POST /api/login {"username":"khaiphan111","password":""} là vào được super-admin.
    if not body.password or not body.password.strip():
        _login_fail(ip)
        raise HTTPException(status_code=401, detail="Sai tên đăng nhập hoặc mật khẩu")

    admin = db.get_admin_by_username(body.username)
    if not admin:
        _login_fail(ip)
        raise HTTPException(status_code=401, detail="Sai tên đăng nhập hoặc mật khẩu")

    import hashlib
    hash_pw = hashlib.sha256(body.password.encode()).hexdigest()
    if admin["password_hash"] != hash_pw:
        _login_fail(ip)
        raise HTTPException(status_code=401, detail="Sai tên đăng nhập hoặc mật khẩu")

    if not admin["is_active"]:
        raise HTTPException(status_code=403, detail="Tài khoản đã bị khóa")

    _login_ok(ip)  # dang nhap dung -> xoa lich su sai
    db.update_admin_last_login(admin["id"])
    ip = request.client.host if request.client else ""
    db.log_admin_action(admin["id"], "login", admin["username"], "Admin logged in", ip)

    tok = create_admin_token(admin["id"])
    return {"ok": True, "token": tok, "role": admin["role"]}

def user_auth(authorization: str = Header(default="")):
    token = authorization.replace("Bearer ", "").strip()
    tg_id = verify_user_token(token)
    if not tg_id:
        raise HTTPException(status_code=401, detail="User unauthorized")
    return tg_id

def auth(authorization: str = Header(default="")):
    token = authorization.replace("Bearer ", "").strip()
    admin_id = verify_admin_token(token)
    if admin_id:
        return admin_id
    raise HTTPException(status_code=401, detail="Chưa đăng nhập")

def require_role(min_role: str):
    def role_checker(admin_id: int = Depends(auth)):
        admin = db.get_admin_by_id(admin_id)
        if not admin or not admin["is_active"]:
            raise HTTPException(status_code=403, detail="Tài khoản không hợp lệ hoặc bị khóa")
        roles = {"super_admin": 3, "admin": 2, "moderator": 1}
        admin_level = roles.get(admin["role"], 0)
        min_level = roles.get(min_role, 0)
        if admin_level < min_level:
            raise HTTPException(status_code=403, detail="Không đủ quyền")
        return admin
    return role_checker

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = None):
    await websocket.accept()
    if not token:
        await websocket.close(code=1008)
        return
        
    is_authenticated = False
    if verify_admin_token(token):
        is_authenticated = True
    elif verify_user_token(token):
        is_authenticated = True
    else:
        tg_id = db.verify_magic_link(token)
        if tg_id:
            is_authenticated = True
            
    if not is_authenticated:
        await websocket.close(code=1008)
        return
        
    sub_id, queue = event_bus.subscribe()
    try:
        while True:
            msg = await queue.get()
            await websocket.send_text(msg)
    except WebSocketDisconnect:
        event_bus.unsubscribe(sub_id)
    except Exception:
        event_bus.unsubscribe(sub_id)

@router.get("/me")
def api_me(admin: dict = Depends(require_role("moderator"))):
    return _row(admin)

class AdminUserIn(BaseModel):
    username: str
    password: str | None = None
    display_name: str | None = None
    role: str = "moderator"
    tg_id: int = 0
    is_active: int = 1

@router.get("/admins")
def api_get_admins(admin: dict = Depends(require_role("super_admin"))):
    return [_row(a) for a in db.list_admins()]

@router.post("/admins")
def api_create_admin(body: AdminUserIn, admin: dict = Depends(require_role("super_admin")), request: Request = None):
    if not body.password:
        raise HTTPException(status_code=400, detail="Mật khẩu là bắt buộc khi tạo")
    pw_err = _check_password_strength(body.password)
    if pw_err:
        raise HTTPException(status_code=400, detail=pw_err)
    existing = db.get_admin_by_username(body.username)
    if existing:
        raise HTTPException(status_code=400, detail="Tên đăng nhập đã tồn tại")
        
    import hashlib
    hash_pw = hashlib.sha256(body.password.encode()).hexdigest()
    new_id = db.create_admin(
        body.username, hash_pw, body.display_name or body.username,
        body.role, body.tg_id, created_by=admin["id"]
    )
    ip = request.client.host if request and request.client else ""
    db.log_admin_action(admin["id"], "create_admin", body.username, f"Created admin {body.username}", ip)
    return {"ok": True, "id": new_id}

@router.put("/admins/{id}")
def api_update_admin(id: int, body: AdminUserIn, admin: dict = Depends(require_role("super_admin")), request: Request = None):
    target_admin = db.get_admin_by_id(id)
    if not target_admin:
        raise HTTPException(status_code=404, detail="Không tìm thấy")

    if body.password:
        pw_err = _check_password_strength(body.password)
        if pw_err:
            raise HTTPException(status_code=400, detail=pw_err)

    import hashlib
    hash_pw = hashlib.sha256(body.password.encode()).hexdigest() if body.password else None
    
    db.update_admin(
        id, 
        password_hash=hash_pw,
        display_name=body.display_name,
        role=body.role,
        tg_id=body.tg_id,
        is_active=body.is_active
    )
    ip = request.client.host if request and request.client else ""
    db.log_admin_action(admin["id"], "update_admin", target_admin["username"], f"Updated admin {target_admin['username']}", ip)
    return {"ok": True}

@router.delete("/admins/{id}")
def api_delete_admin(id: int, admin: dict = Depends(require_role("super_admin")), request: Request = None):
    target_admin = db.get_admin_by_id(id)
    if not target_admin:
        raise HTTPException(status_code=404, detail="Không tìm thấy")
    if target_admin["id"] == admin["id"]:
        raise HTTPException(status_code=400, detail="Không thể tự xóa")
        
    db.delete_admin(id)
    ip = request.client.host if request and request.client else ""
    db.log_admin_action(admin["id"], "delete_admin", target_admin["username"], f"Deleted admin {target_admin['username']}", ip)
    return {"ok": True}

@router.get("/admins/audit-log")
def api_get_audit_log(admin: dict = Depends(require_role("super_admin"))):
    return [_row(l) for l in db.get_admin_audit_log(200)]

@router.post("/user/login")
def user_login(body: TokenIn):
    tg_id = db.verify_magic_link(body.token)
    if not tg_id:
        raise HTTPException(status_code=401, detail="Token hết hạn hoặc không hợp lệ")
    
    # Check if user exists
    user = db.get_user(tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")
        
    tok = create_user_token(tg_id)
    return {"ok": True, "token": tok, "user": dict(user)}

@router.get("/user/me")
def user_me(tg_id: int = Depends(user_auth)):
    user = db.get_user(tg_id)
    return dict(user) if user else {}

@router.get("/user/analytics")
def user_analytics(tg_id: int = Depends(user_auth)):
    watches = db.all_watches()
    user_watches = [w for w in watches if w["tg_id"] == tg_id]
    
    # Just basic counts for now
    live = sum(1 for w in user_watches if w["last_status"] == "live")
    die = sum(1 for w in user_watches if w["last_status"] == "die")
    
    # Return user tracks
    c = db.get_conn()
    fb_tracks = [dict(r) for r in c.execute("SELECT * FROM fb_post_tracks WHERE tg_user_id=?", (tg_id,)).fetchall()]
    ig_tracks = [dict(r) for r in c.execute("SELECT * FROM ig_tracks WHERE tg_user_id=?", (tg_id,)).fetchall()]
    ig_videos = [dict(r) for r in c.execute("SELECT * FROM ig_video_tracks WHERE tg_user_id=?", (tg_id,)).fetchall()]
    tk_tracks = [dict(r) for r in c.execute("SELECT * FROM tracks WHERE tg_user_id=?", (tg_id,)).fetchall()]
    tk_videos = [dict(r) for r in c.execute("SELECT * FROM video_tracks WHERE tg_user_id=?", (tg_id,)).fetchall()]
    zalo_tracks = [dict(r) for r in c.execute("SELECT * FROM zalo_tracks WHERE tg_user_id=?", (tg_id,)).fetchall()]
    
    return {
        "ok": True,
        "live": live,
        "die": die,
        "fb_watches": user_watches,
        "fb_tracks": fb_tracks,
        "ig_tracks": ig_tracks,
        "ig_videos": ig_videos,
        "tk_tracks": tk_tracks,
        "tk_videos": tk_videos,
        "zalo_tracks": zalo_tracks,
    }


@router.get("/status")
def status(_=Depends(auth)):
    # Web admin chạy độc lập (Render) có thể không load được bot managers;
    # bọc từng phần để 1 lỗi không làm sập cả endpoint (500) -> frontend
    # mất menu phân quyền.
    try:
        from .bot import manager, zalo_manager  # lazy (muc 5: tranh import aiogram nang luc boot)
        bot_running = bool(manager.running)
        zalo_running = bool(zalo_manager.running)
    except Exception as e:
        print(f"[!] /api/status: bot managers unavailable: {e}")
        bot_running = False
        zalo_running = False
    # Web chạy trên Render (WEB_ONLY) không thấy bot trên VM nên
    # manager.running luôn False ở đó -> dùng heartbeat DB do backend VM
    # ghi mỗi 30s. Tươi (< 3 phút) nghĩa là bot đang sống thật.
    try:
        hb = int(db.get_setting("bot_last_heartbeat", "0") or 0)
        if hb and time.time() - hb < 180:
            bot_running = True
    except Exception:
        pass
    try:
        watches = db.all_watches()
    except Exception as e:
        print(f"[!] /api/status: all_watches failed: {e}")
        watches = []
    live = sum(1 for w in watches if w["last_status"] == "live")
    die = sum(1 for w in watches if w["last_status"] == "die")

    try:
        logs_today = [l for l in db.recent_logs(500)
                      if dict(l).get("kind") in ("follower_change","video_new","video_stats")
                      and dict(l).get("ts", 0) > int(time.time()) - 86400]
    except Exception as e:
        print(f"[!] /api/status: recent_logs failed: {e}")
        logs_today = []

    try:
        n_users = len(db.list_users())
    except Exception as e:
        print(f"[!] /api/status: list_users failed: {e}")
        n_users = 0
    try:
        tracks_total = len(db.all_active_tracks())
    except Exception as e:
        print(f"[!] /api/status: all_active_tracks failed: {e}")
        tracks_total = 0
    try:
        video_tracks_total = len(db.all_active_video_tracks())
    except Exception as e:
        print(f"[!] /api/status: all_active_video_tracks failed: {e}")
        video_tracks_total = 0

    return {
        "app": config.APP_NAME,
        "version": config.APP_VERSION,
        "author": config.AUTHOR,
        "setup_done": db.get_setting("setup_done") == "1",
        "bot_running": bot_running,
        "zalo_running": zalo_running,
        "poller_running": poller.running,
        "poller_last_run": poller.last_run,
        "users": n_users,
        "watches_total": len(watches),
        "watches_live": live,
        "watches_die": die,
        "tracks_total": tracks_total,
        "video_tracks_total": video_tracks_total,
        "notifs_today": len(logs_today),
    }


# Key nhạy cảm KHÔNG trả về cho admin phụ (chỉ super_admin xem được).
_SECRET_SETTING_KEYS = {
    "admin_password", "bot_token", "admin_bot_token", "zalo_bot_token",
    "fb_avatar_token", "fb_cookie", "zalo_cookie", "ig_password",
    "ig_rapidapi_key", "ig_session_cookie", "proxy_api_key",
    "payos_api_key", "payos_checksum_key", "payos_client_id", "viotp_token",
}


@router.get("/settings")
def get_settings(admin=Depends(require_role("moderator"))):
    # require_role trả sqlite3.Row khi chạy SQLite và dict khi chạy Postgres -> chuẩn hoá
    # về dict để dùng .get() an toàn cho cả 2 backend.
    admin = dict(admin) if not isinstance(admin, dict) else admin
    s = db.all_settings()
    # FIX bảo mật: bot_token từng được dùng làm khoá ký token admin (xem get_secret),
    # nên lộ nó cho admin phụ = leo thang đặc quyền. Moderator/admin chỉ thấy cấu
    # hình thường; super_admin vẫn xem được token để vận hành.
    if (admin.get("role") or "") != "super_admin":
        for _k in _SECRET_SETTING_KEYS:
            s.pop(_k, None)
    s.pop("app_secret", None)  # khoá ký nội bộ: không bao giờ trả ra API
    
    import os
    img_dir = os.path.join(os.path.dirname(__file__), "..", "data", "images")
    qr_images = []
    if os.path.exists(img_dir):
        qr_images = [f for f in os.listdir(img_dir) if f.startswith("qr_")]
    s["qr_images"] = qr_images
    
    return s


@router.post("/settings")
async def save_settings(body: SettingsIn, admin=Depends(require_role("super_admin"))):
    from .bot import manager, zalo_manager  # lazy (muc 5: tranh import aiogram nang luc boot)
    restart_bot = False
    restart_zalo = False
    restart_admin_bot = False
    data = body.model_dump(exclude_none=True)
    # 2026-10-05: các field secret/token: BỎ QUA giá trị rỗng, giữ nguyên giá trị cũ.
    # Lý do: trang web Settings gửi toàn bộ form khi lưu; nếu ô token đang trống
    # (chưa load xong hoặc user không động tới) mà vẫn ghi đè "" thì mất token
    # mà không hay biết — đã từng làm mất fb_avatar_token đúng kiểu này.
    _SECRET_KEYS = {"bot_token", "zalo_bot_token", "admin_bot_token",
                    "fb_avatar_token", "ig_password", "ig_rapidapi_key",
                    "proxy_api_key"}
    # FIX: chỉ ghi các key nằm trong model (whitelist). Trước đây ghi MỌI key mà
    # client gửi lên -> ghi đè được cả key nội bộ (setup_done, bot_last_heartbeat,
    # viotp_disabled_services...) và mỗi lần lưu lại chèn rác "qr_images" vào settings.
    _allowed = set(SettingsIn.model_fields.keys())
    _ignored = [k for k in data if k not in _allowed]
    if _ignored:
        log.warning("save_settings: bỏ qua %d key ngoài whitelist: %s",
                    len(_ignored), ",".join(sorted(_ignored)[:10]))
    data = {k: v for k, v in data.items() if k in _allowed}

    # admin_password: trước đây là field CHẾT (không code nào đọc để xác thực) và còn
    # bị lưu plaintext xuống bảng settings. Nay đổi thật mật khẩu super_admin (bên dưới).
    _new_admin_pw = (data.get("admin_password") or "").strip()
    if _new_admin_pw:
        _pw_err = _check_password_strength(_new_admin_pw)
        if _pw_err:
            raise HTTPException(status_code=400,
                                detail=f"Mật khẩu admin chưa đạt yêu cầu: {_pw_err}")

    for k, v in data.items():
        if k == "admin_password":
            continue  # xử lý riêng ở dưới, KHÔNG lưu plaintext
        if k in _SECRET_KEYS and not (v or "").strip():
            continue
        if k == "bot_token" and v != db.get_setting("bot_token"):
            restart_bot = True
        if k == "zalo_bot_token" and v != db.get_setting("zalo_bot_token"):
            restart_zalo = True
        if k == "admin_bot_token" and v != db.get_setting("admin_bot_token"):
            restart_admin_bot = True
        if k == "poll_interval":
            try:
                v = str(max(60, int(v)))
            except:
                v = "60"
        db.set_setting(k, v)

    _pw_updated = False
    if _new_admin_pw:
        import hashlib as _hashlib
        _ph = _hashlib.sha256(_new_admin_pw.encode()).hexdigest()
        for _a in db.list_admins():
            if (_a.get("role") or "") == "super_admin":
                db.update_admin(_a["id"], password_hash=_ph)
                _pw_updated = True
        try:
            db.set_setting("admin_password", "")  # xoá plaintext cũ nếu có
        except Exception:
            pass
        try:
            db.admin_audit_add(admin["id"], "", "doi_mat_khau_admin",
                               "Đổi mật khẩu super_admin qua web")
        except Exception:
            pass

    started = False
    zalo_started = False
    
    token = db.get_setting("bot_token") or ""
    zalo_token = db.get_setting("zalo_bot_token") or ""
    
    if restart_bot and token.strip():
        started = await manager.start(token.strip())
    elif not restart_bot:
        started = manager.running
        
    if restart_zalo and zalo_token.strip():
        zalo_started = await zalo_manager.start(zalo_token.strip())
    elif not restart_zalo:
        zalo_started = zalo_manager.running
        
    admin_bot_token = db.get_setting("admin_bot_token") or ""
    if restart_admin_bot:
        from .admin_bot import manager as admin_manager
        if admin_bot_token.strip():
            await admin_manager.start()
        else:
            await admin_manager.stop()
        
    if restart_bot or restart_zalo or restart_admin_bot:
        if manager.running or zalo_manager.running:
            db.set_setting("setup_done", "1")
            poller.start()
            
    return {"ok": True, "bot_running": manager.running, "bot_started": started,
            "zalo_started": zalo_started, "admin_password_updated": _pw_updated}

class CodeGenerateIn(BaseModel):
    amount: int = Field(gt=0)
    max_uses: int = Field(ge=1)
    expire_days: int = Field(ge=0)
    expire_hours: int = Field(ge=0)
    wallet: str = "main"

@router.post("/codes/generate")
def generate_code_api(body: CodeGenerateIn, _=Depends(auth)):
    expire_at = 0
    total_seconds = body.expire_days * 86400 + body.expire_hours * 3600
    if total_seconds > 0:
        expire_at = int(time.time()) + total_seconds
        
    code = db.generate_code(
        amount=body.amount,
        prefix="GLOBAL" if body.max_uses > 1 else "CODE",
        max_uses=body.max_uses,
        expire_at=expire_at,
        wallet=body.wallet,
    )
    return {"ok": True, "code": code}

@router.get("/codes/{code}")
def code_detailed(code: str, _=Depends(auth)):
    data = db.get_code_detailed(code)
    if not data:
        raise HTTPException(status_code=404, detail="Mã không tồn tại")
    return data


class ProxyIn(BaseModel):
    url: str

@router.get("/proxies")
def list_proxies(_=Depends(auth)):
    return db.get_proxies()

@router.post("/proxies")
def add_proxy(body: ProxyIn, _=Depends(auth)):
    if not body.url.strip():
        raise HTTPException(status_code=400, detail="Proxy URL trống")
    if db.add_proxy(body.url.strip()):
        return {"ok": True}
    raise HTTPException(status_code=400, detail="Thêm proxy thất bại (có thể bị trùng)")

@router.delete("/proxies/{proxy_id}")
def delete_proxy(proxy_id: int, _=Depends(auth)):
    db.delete_proxy(proxy_id)
    return {"ok": True}

@router.post("/proxies/{proxy_id}/toggle")
def toggle_proxy(proxy_id: int, _=Depends(auth)):
    db.toggle_proxy(proxy_id)
    return {"ok": True}

@router.get("/analytics")
def analytics(_=Depends(auth)):
    return db.get_analytics()

from fastapi import Form
import asyncio

@router.post("/broadcast")
async def broadcast(text: str = Form(...), photo: UploadFile = File(None), _=Depends(auth)):
    users = db.list_users()
    
    photo_path = None
    if photo and photo.filename:
        photo_path = os.path.join(os.path.dirname(__file__), "..", "data", f"tmp_bc_{photo.filename}")
        content = await photo.read()
        with open(photo_path, "wb") as f:
            f.write(content)

    async def _send():
        from aiogram.types import FSInputFile
        from .bot import manager
        if not manager.running: return
        formatted_text = (
            f"📢 <b>THÔNG BÁO TỪ HỆ THỐNG</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{text}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Cảm ơn bạn đã đồng hành cùng chúng tôi!</i>"
        )
        for u in users:
            try:
                if photo_path:
                    await manager.bot.send_photo(u["tg_id"], photo=FSInputFile(photo_path), caption=formatted_text, parse_mode="HTML")
                else:
                    await manager.bot.send_message(u["tg_id"], formatted_text, parse_mode="HTML")
                await asyncio.sleep(0.05)
            except: pass
        if photo_path and os.path.exists(photo_path):
            try: os.remove(photo_path)
            except: pass
            
    asyncio.create_task(_send())
    return {"ok": True, "total_queued": len(users)}

@router.post("/upload-qr")
async def upload_qr(file: UploadFile = File(...), _=Depends(require_role("super_admin"))):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Không có file")
    
    img_dir = os.path.join(os.path.dirname(__file__), "..", "data", "images")
    os.makedirs(img_dir, exist_ok=True)
    
    # Define max 2 files (qr_1.png/jpg, qr_2.png/jpg)
    # Just save it as qr_1 or qr_2 depending on what exists, or overwrite
    ext = os.path.splitext(file.filename)[1]
    
    # List current qr_ files
    existing = [f for f in os.listdir(img_dir) if f.startswith("qr_")]
    if len(existing) == 0:
        target = f"qr_1{ext}"
    elif len(existing) == 1:
        # Check if qr_1 exists
        if existing[0].startswith("qr_1"):
            target = f"qr_2{ext}"
        else:
            target = f"qr_1{ext}"
    else:
        # Overwrite the first one
        target = existing[0]
        
    filepath = os.path.join(img_dir, target)
    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)
        
    return {"ok": True, "filename": target}

@router.delete("/upload-qr/{filename}")
async def delete_qr(filename: str, _=Depends(require_role("super_admin"))):
    img_dir = os.path.join(os.path.dirname(__file__), "..", "data", "images")
    filepath = os.path.join(img_dir, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
    return {"ok": True}


@router.get("/qr/{filename}")
def get_qr_image(filename: str, t: str = ""):
    """Trả ảnh QR ngân hàng cho trang Cấu hình (admin) — có xác thực.

    FIX bảo mật: trước đây cả thư mục `data/images` được mount tĩnh công khai ở
    `/images` nên ai biết URL đều tải được ảnh QR ngân hàng cá nhân. Khách vẫn nhận
    ảnh QR qua bot (bot gửi trực tiếp từ file trên đĩa ở handlers/wallet.py) nên
    luồng nạp tiền KHÔNG bị ảnh hưởng. Thẻ <img> không gửi được header Authorization
    nên token admin truyền qua query `?t=`.
    """
    from fastapi.responses import FileResponse
    if not re.fullmatch(r"qr_[A-Za-z0-9._-]{1,80}", filename or ""):
        raise HTTPException(status_code=404, detail="Not found")
    if not verify_admin_token((t or "").strip()):
        raise HTTPException(status_code=401, detail="Chưa đăng nhập")
    img_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "images"))
    path = os.path.abspath(os.path.join(img_dir, filename))
    if not path.startswith(img_dir + os.sep) or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path)

@router.post("/verify-bot")
async def verify_bot(body: TokenIn, _=Depends(auth)):
    from .bot import manager  # FIX: trước đây thiếu import -> NameError -> HTTP 500
    username = await manager.verify_token(body.token)
    if not username:
        raise HTTPException(status_code=400, detail="Token không hợp lệ")
    return {"ok": True, "username": username}

@router.delete("/user/tracks/{type}/{target}")
def user_delete_track(type: str, target: str, token: str = Header(default="")):
    username = db.verify_magic_link(token)
    if not username:
        raise HTTPException(status_code=401, detail="Chưa đăng nhập")
    tg_id = int(username)
    
    if type == "fb_watch":
        db.remove_watch(tg_id, target)
    elif type == "fb_track":
        db.remove_fb_post_track(tg_id, target)
    elif type == "tk_track":
        db.remove_track(tg_id, target)
    elif type == "tk_video":
        db.remove_video_track(tg_id, target)
    elif type == "ig_track":
        db.remove_ig_track(tg_id, target)
    elif type == "ig_video":
        db.remove_ig_video_track(tg_id, target)
    else:
        raise HTTPException(400, "Invalid type")
    return {"ok": True}



@router.get("/prereq")
async def prereq(_=Depends(auth)):
    from .bot import manager  # FIX: trước đây thiếu import -> NameError -> HTTP 500
    out = {"telegram": False, "facebook": False, "bot_token": False}
    async with httpx.AsyncClient(timeout=10) as c:
        try:
            r = await c.get("https://api.telegram.org")
            out["telegram"] = r.status_code < 500
        except Exception:
            pass
        try:
            r = await c.get(f"{config.FB_GRAPH}/4/picture", params={"redirect": "false"})
            out["facebook"] = r.status_code < 500
        except Exception:
            pass
    token = db.get_setting("bot_token")
    if token:
        out["bot_token"] = bool(await manager.verify_token(token))
    return out


@router.get("/users")
def users(_=Depends(auth)):
    return [_row(u) for u in db.list_users()]

@router.get("/codes")
def codes_history(_=Depends(auth)):
    return [_row(c) for c in db.get_code_history()]


@router.post("/users/{tg_id}/topup")
async def topup(tg_id: int, body: AmountIn, _=Depends(require_role("admin"))):
    if not db.get_user(tg_id):
        raise HTTPException(status_code=404, detail="Không có user này")
    db.adjust_balance(tg_id, body.amount, "Admin nạp")
    db.add_log("topup", f"Admin nạp {body.amount}", tg_id)
    
    # Kiem tra VIP
    upgraded, new_vip, is_lifetime = db.check_vip_upgrade(tg_id)
    
    try:
        from .bot import manager
        from .util import vnd
        if manager.running:
            import asyncio
            asyncio.create_task(manager.bot.send_message(tg_id, f"💵 Admin vừa nạp cho bạn <b>{vnd(body.amount)}</b> vào tài khoản!", parse_mode="HTML"))
            if upgraded or is_lifetime:
                limit = db.get_setting(f"vip{new_vip}_limit", "10")
                msg = (
                    f"🎉 <b>CHÚC MỪNG BẠN ĐÃ LÊN VIP {new_vip}!</b> 🎉\n\n"
                    f"💎 <b>Quyền lợi mới:</b>\n"
                    f"- Theo dõi tối đa: <b>{limit} UID/Kênh</b>\n"
                )
                if is_lifetime:
                    msg += "- Hạn sử dụng: <b>VĨNH VIỄN</b>\n\n"
                else:
                    msg += "\n"
                msg += "Cảm ơn bạn đã tin tưởng và sử dụng dịch vụ của chúng tôi! ❤️"
                asyncio.create_task(manager.bot.send_message(tg_id, msg, parse_mode="HTML"))
    except: pass
    return {"ok": True, "user": _row(db.get_user(tg_id))}


@router.post("/users/{tg_id}/wallet")
async def wallet_adjust(tg_id: int, body: WalletAdjustIn, _=Depends(require_role("admin"))):
    """Cộng/trừ tiền 1 ví của user (main/shop/buff/rent)."""
    wallet = (body.wallet or "").strip().lower()
    if wallet not in ("main", "shop", "buff", "rent"):
        raise HTTPException(status_code=400, detail="Ví không hợp lệ (main/shop/buff/rent)")
    if not body.amount:
        raise HTTPException(status_code=400, detail="Số tiền phải khác 0")
    if not db.get_user(tg_id):
        raise HTTPException(status_code=404, detail="Không có user này")
    ok = db.adjust_wallet(tg_id, wallet, body.amount, "Admin điều chỉnh ví")
    if not ok:
        raise HTTPException(status_code=400, detail="Số dư ví không đủ để trừ")
    db.add_log("wallet_adjust", f"Admin {'cộng' if body.amount > 0 else 'trừ'} {abs(body.amount)} ví {wallet}", tg_id)

    # Báo tin cho user qua Telegram
    try:
        from .bot import manager
        from .util import vnd
        wallet_names = {"main": "Ví chính", "shop": "Ví shop", "buff": "Ví buff", "rent": "Ví thuê số"}
        wname = wallet_names.get(wallet, wallet)
        if manager.running:
            import asyncio
            action = "cộng" if body.amount > 0 else "trừ"
            icon = "💵" if body.amount > 0 else "💸"
            user = db.get_user(tg_id)
            new_bal = 0
            if user:
                col = {"main": "balance", "shop": "shop_balance", "buff": "buff_balance", "rent": "rent_balance"}[wallet]
                new_bal = int(user[col] or 0)
            asyncio.create_task(manager.bot.send_message(
                tg_id,
                f"{icon} Admin vừa {action} <b>{vnd(abs(body.amount))}</b> vào {wname} của bạn!\n"
                f"Số dư {wname} hiện tại: <b>{vnd(new_bal)}</b>",
                parse_mode="HTML"))
    except: pass
    return {"ok": True, "user": _row(db.get_user(tg_id))}


@router.post("/users/{tg_id}/trial")
async def grant_trial(tg_id: int, body: dict = None, _=Depends(require_role("admin"))):
    user = db.get_user(tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="Không có user này")
    try:
        days = body.get("days") if body and "days" in body else int(db.get_setting("free_trial_days", "3"))
    except:
        days = 3
    if db.activate_trial(tg_id, days):
        db.add_log("trial", f"Admin tặng trial {days} ngày", tg_id)
        try:
            from .bot import manager, _sub_text
            if manager.running:
                import asyncio
                u2 = db.get_user(tg_id)
                asyncio.create_task(manager.bot.send_message(
                    tg_id, 
                    f"🎉 <b>Chúc mừng!</b>\n\nAdmin vừa tặng bạn <b>{days} ngày</b> dùng thử miễn phí full tính năng!\n"
                    f"Hạn sử dụng mới: <b>{_sub_text(u2)}</b>\n\n"
                    "Hãy trải nghiệm các lệnh theo dõi nhé!"
                ))
        except: pass
        return {"ok": True, "user": _row(db.get_user(tg_id))}
    else:
        raise HTTPException(status_code=400, detail="Tài khoản này đã nhận dùng thử rồi")

@router.post("/users/{tg_id}/reset")
async def reset_user_api(tg_id: int, _=Depends(require_role("admin"))):
    user = db.get_user(tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="Không có user này")
    db.reset_user(tg_id)
    db.add_log("reset", "Admin reset dữ liệu (số dư, gói, trial)", tg_id)
    try:
        from .bot import manager
        if manager.running:
            import asyncio
            asyncio.create_task(manager.bot.send_message(tg_id, "🔄 Dữ liệu tài khoản của bạn (số dư, gói, trạng thái Trial) vừa được Admin khôi phục về mặc định."))
    except: pass
    return {"ok": True, "user": _row(db.get_user(tg_id))}

@router.delete("/users/{tg_id}")
async def delete_user_api(tg_id: int, _=Depends(require_role("admin"))):
    user = db.get_user(tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="Không có user này")
    db.delete_user(tg_id)
    db.add_log("delete", "Admin xóa người dùng khỏi hệ thống", tg_id)
    return {"ok": True}


@router.post("/users/{tg_id}/sub")
async def grant_sub(tg_id: int, body: MonthsIn, _=Depends(require_role("admin"))):
    user = db.get_user(tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="Không có user này")
    base = max(now(), user["sub_until"] or 0)
    db.set_sub_until(tg_id, base + body.days * DAY)
    db.add_log("sub", f"Admin cấp {body.days} ngày", tg_id)
    try:
        from .bot import manager, _sub_text
        if manager.running:
            import asyncio
            u2 = db.get_user(tg_id)
            asyncio.create_task(manager.bot.send_message(tg_id, f"💎 Admin vừa cấp thêm <b>{body.days} ngày</b> sử dụng VIP cho bạn.\nHạn sử dụng mới: {_sub_text(u2)}"))
    except: pass
    return {"ok": True, "user": _row(db.get_user(tg_id))}


@router.get("/watches")
def watches(_=Depends(auth)):
    return [_row(w) for w in db.all_watches()]


@router.delete("/watches/{watch_id}")
def del_watch(watch_id: int, _=Depends(auth)):
    db.deactivate_watch(watch_id)
    return {"ok": True}


@router.get("/fb-post-tracks")
def fb_post_tracks(_=Depends(auth)):
    return [_row(w) for w in db.all_active_fb_post_tracks()]

@router.delete("/fb-post-tracks/{track_id}")
def del_fb_post_track(track_id: int, _=Depends(auth)):
    db.deactivate_fb_post_track(track_id)
    return {"ok": True}


@router.get("/logs")
def logs(_=Depends(auth)):
    return [_row(l) for l in db.recent_logs(150)]


@router.post("/check")
async def manual_check(body: UidIn, _=Depends(auth)):
    res = await fb.check_uid(body.uid)
    return {
        "uid": res["uid"],
        "status": "live" if res["alive"] else ("die" if res["ok"] else "error"),
        "avatar_url": fb.display_avatar(res),
    }


# ─── ACCOUNT TRACKS (TIKTOK) ─────────────────────────────────
class TrackIn(BaseModel):
    tiktok_username: str

@router.get("/tracks")
def get_tracks(_=Depends(auth)): 
    c = db.get_conn()
    rows = c.execute("SELECT tiktok_username, MAX(last_followers) as last_followers, MAX(last_following) as last_following, MAX(last_videos) as last_videos, MAX(avatar_url) as avatar_url FROM tracks WHERE active=1 GROUP BY tiktok_username ORDER BY tiktok_username").fetchall()
    return [dict(r) for r in rows]

@router.post("/tracks")
async def add_track(body: TrackIn, _=Depends(auth)):
    from .tiktok import fetch_tiktok_info, parse_username
    u = parse_username(body.tiktok_username)
    if not u: raise HTTPException(400, detail="Username khong hop le")
    try: info = await fetch_tiktok_info(u)
    except Exception as e: raise HTTPException(400, detail=str(e))
    r = db.add_track(0, "admin", info["username"], info["followers"], info["following"], info["videos"], avatar_url=info.get("avatar", ""))
    if r == -1: raise HTTPException(409, detail="Da theo doi tai khoan nay roi")
    db.add_log("track_add", f"Admin them @{info['username']}", 0, info["username"])
    return {"ok": True, "track_id": r, "info": info}

@router.delete("/tracks/{username}")
def del_track(username: str, _=Depends(auth)):
    c = db.get_conn()
    with db._lock:
        c.execute("UPDATE tracks SET active=0 WHERE tiktok_username=?", (username,))
        c.commit()
    return {"ok": True}

# ─── VIDEO TRACKS (TIKTOK) ──────────────────────────────────
class VideoTrackIn(BaseModel):
    video_url: str
    check_interval: int = 3600

@router.get("/video-tracks")
def get_video_tracks(_=Depends(auth)):
    c = db.get_conn()
    rows = c.execute("SELECT video_id, MAX(video_url) as video_url, MAX(tiktok_username) as tiktok_username, MAX(video_desc) as video_desc, MAX(cover_url) as cover_url, MAX(last_plays) as last_plays, MAX(last_likes) as last_likes, MAX(last_comments) as last_comments, MIN(check_interval) as check_interval FROM video_tracks WHERE active=1 GROUP BY video_id ORDER BY video_id").fetchall()
    return [dict(r) for r in rows]

@router.post("/video-tracks")
async def add_video_track(body: VideoTrackIn, _=Depends(auth)):
    from .tiktok import fetch_video_info, parse_video_id
    vid_id = parse_video_id(body.video_url)
    if not vid_id: raise HTTPException(400, detail="Khong the trich xuat Video ID tu URL nay")
    try: info = await fetch_video_info(body.video_url)
    except Exception as e: raise HTTPException(400, detail=str(e))
    r = db.add_video_track(
        0, "admin", body.video_url, info["id"] or vid_id,
        info.get("username",""), info.get("desc",""), info.get("cover",""),
        body.check_interval, info["plays"], info["likes"], info["comments"], info["shares"])
    if r == -1: raise HTTPException(409, detail="Da theo doi video nay roi")
    db.add_log("video_track_add", f"Admin theo doi video @{info.get('username','')}: {info.get('desc','')[:50]}", 0, info.get("username",""))
    return {"ok": True, "track_id": r, "info": info}

@router.delete("/video-tracks/{video_id}")
def del_video_track(video_id: str, _=Depends(auth)):
    c = db.get_conn()
    with db._lock:
        c.execute("UPDATE video_tracks SET active=0 WHERE video_id=?", (video_id,))
        c.commit()
    return {"ok": True}

class VideoTrackUpdateIn(BaseModel):
    check_interval: int

@router.put("/video-tracks/{video_id}")
def update_video_track(video_id: str, body: VideoTrackUpdateIn, _=Depends(auth)):
    c = db.get_conn()
    with db._lock:
        c.execute("UPDATE video_tracks SET check_interval=? WHERE video_id=?", (body.check_interval, video_id))
        c.commit()
    return {"ok": True}

# ─── BOT CONTROL ─────────────────────────────────────────────
@router.post("/bot/start")
async def bot_start(_=Depends(require_role("super_admin"))):
    from .bot import manager, zalo_manager  # FIX: trước đây thiếu import -> NameError 500
    token = db.get_setting("bot_token")
    zalo_token = db.get_setting("zalo_bot_token")
    if not token and not zalo_token: raise HTTPException(400, detail="Chua co Bot Token")
    
    ok = False
    zalo_ok = False
    if token: ok = await manager.start(token)
    if zalo_token: zalo_ok = await zalo_manager.start(zalo_token)
    
    if ok or zalo_ok:
        db.set_setting("setup_done", "1")
        poller.start()
    return {"ok": ok or zalo_ok, "bot_running": manager.running, "zalo_running": zalo_manager.running}

@router.post("/bot/stop")
async def bot_stop(_=Depends(require_role("super_admin"))):
    from .bot import manager, zalo_manager  # FIX: trước đây thiếu import -> NameError 500
    await poller.stop()
    await manager.stop()
    await zalo_manager.stop()
    return {"ok": True, "bot_running": False}

# ─── ACCOUNT TRACKS (IG) ─────────────────────────────────
class IGTrackIn(BaseModel):
    ig_username: str

@router.get("/ig-tracks")
def get_ig_tracks(_=Depends(auth)):
    c = db.get_conn()
    rows = c.execute("SELECT ig_username, MAX(last_followers) as last_followers, MAX(last_following) as last_following, MAX(last_posts) as last_posts, MAX(avatar_url) as avatar_url FROM ig_tracks WHERE active=1 GROUP BY ig_username ORDER BY ig_username").fetchall()
    return [dict(r) for r in rows]

@router.post("/ig-tracks")
async def add_ig_track(body: IGTrackIn, _=Depends(auth)):
    from .ig import fetch_ig_info, parse_ig_username
    u = parse_ig_username(body.ig_username)
    if not u: raise HTTPException(400, detail="Username không hợp lệ")
    try: info = await fetch_ig_info(u)
    except Exception as e: raise HTTPException(400, detail=str(e))
    r = db.add_ig_track(0, "admin", info["username"], info["followers"], info["following"], info["posts"], avatar_url=info.get("avatar", ""))
    if r == -1: raise HTTPException(409, detail="Đã theo dõi tài khoản này rồi")
    db.add_log("track_add", f"Admin thêm IG @{info['username']}", 0, info["username"])
    return {"ok": True, "track_id": r, "info": info}

@router.delete("/ig-tracks/{username}")
def del_ig_track(username: str, _=Depends(auth)):
    c = db.get_conn()
    with db._lock:
        c.execute("DELETE FROM ig_tracks WHERE ig_username=?", (username,))
        c.commit()
    return {"ok": True}

# ─── VIDEO TRACKS (IG) ──────────────────────────────────
class IGVideoTrackIn(BaseModel):
    post_url: str
    check_interval: int = 3600

@router.get("/ig-video-tracks")
def get_ig_video_tracks(_=Depends(auth)):
    c = db.get_conn()
    rows = c.execute("SELECT post_id, MAX(post_url) as post_url, MAX(ig_username) as ig_username, MAX(post_desc) as post_desc, MAX(cover_url) as cover_url, MAX(last_views) as last_views, MAX(last_likes) as last_likes, MAX(last_comments) as last_comments, MIN(check_interval) as check_interval FROM ig_video_tracks WHERE active=1 GROUP BY post_id ORDER BY post_id").fetchall()
    return [dict(r) for r in rows]

@router.post("/ig-video-tracks")
async def add_ig_video_track(body: IGVideoTrackIn, _=Depends(auth)):
    from .ig import fetch_ig_post_info, parse_ig_post_id
    post_id = parse_ig_post_id(body.post_url)
    if not post_id: raise HTTPException(400, detail="Không thể trích xuất Post ID")
    try: info = await fetch_ig_post_info(body.post_url)
    except Exception as e: raise HTTPException(400, detail=str(e))
    r = db.add_ig_video_track(
        0, "admin", body.post_url, info["id"] or post_id,
        info.get("username",""), info.get("desc",""), info.get("cover",""),
        body.check_interval, info["likes"], info["comments"], info.get("views",0))
    if r == -1: raise HTTPException(409, detail="Đã theo dõi bài viết này rồi")
    db.add_log("video_track_add", f"Admin theo dõi IG bài @{info.get('username','')}: {info.get('desc','')[:50]}", 0, info.get("username",""))
    return {"ok": True, "track_id": r, "info": info}

@router.delete("/ig-video-tracks/{post_id}")
def del_ig_video_track(post_id: str, _=Depends(auth)):
    c = db.get_conn()
    with db._lock:
        c.execute("DELETE FROM ig_video_tracks WHERE post_id=?", (post_id,))
        c.commit()
    return {"ok": True}

class IGVideoTrackUpdateIn(BaseModel):
    check_interval: int

@router.put("/ig-video-tracks/{post_id}")
def update_ig_video_track(post_id: str, body: IGVideoTrackUpdateIn, _=Depends(auth)):
    c = db.get_conn()
    with db._lock:
        c.execute("UPDATE ig_video_tracks SET check_interval=? WHERE post_id=?", (body.check_interval, post_id))
        c.commit()
    return {"ok": True}


# --- ZALO ENDPOINTS ---
@router.get("/zalo-tracks")
def api_get_zalo_tracks(tg_id: int = Depends(user_auth)):
    # FIX (C4): auth() trả admin_id (int) nên `user["tg_id"]` trước đây luôn TypeError ->
    # endpoint 500. Đây là endpoint cho USER nên dùng user_auth (trả về tg_id).
    return db.user_zalo_tracks(tg_id)

@router.post("/zalo-tracks")
async def api_add_zalo_track(body: dict, tg_id: int = Depends(user_auth)):
    phone = body.get("phone", "").strip()
    if not phone: raise HTTPException(400, "Thiếu SĐT")
    
    # FIX (C4): user_auth trả tg_id (int) -> phải tự lấy bản ghi user
    user = db.get_user(tg_id)
    if not user:
        raise HTTPException(404, "Không tìm thấy người dùng")
    vip_level = int(user["vip_level"] or 0)
    try: max_limit = int(db.get_setting(f"vip{vip_level}_limit", [5, 50, 200, 1000][vip_level if vip_level <= 3 else 3]))
    except: max_limit = [5, 50, 200, 1000][vip_level if vip_level <= 3 else 3]
    
    with db._lock: 
        count = db.get_conn().execute("SELECT COUNT(*) FROM tracks WHERE tg_user_id=?", (tg_id,)).fetchone()[0]
        z_count = db.get_conn().execute("SELECT COUNT(*) FROM zalo_tracks WHERE tg_user_id=?", (tg_id,)).fetchone()[0]
    
    if count + z_count >= max_limit:
        raise HTTPException(400, f"Giới hạn hạng VIP của bạn là {max_limit} mục.")
        
    ok, err = db.check_daily_limit(tg_id)
    if not ok: raise HTTPException(400, err)
    
    cookie = db.get_setting("zalo_cookie", "")
    imei = db.get_setting("zalo_imei", "")
    from app.zalo_checker import check_zalo_phone
    res = await check_zalo_phone(phone, cookie, imei)
    
    if res.get("live"):
        status = "LIVE"
        name = res.get("name", "")
        avatar = res.get("avatar", "")
    else:
        status = "DIE"
        name = ""
        avatar = ""
        # Still add it to track its state, unless user only wants to track existing?
        # Let's add it anyway with DIE status.
        
    db.add_zalo_track(tg_id, user["username"], phone, name, avatar, status)
    db.add_log("track_add", f"Thêm Zalo {phone}", tg_id, phone)
    return {"ok": True, "res": res}

@router.delete("/zalo-tracks/{phone}")
def api_del_zalo_track(phone: str, tg_id: int = Depends(user_auth)):
    db.remove_zalo_track(tg_id, phone)
    db.add_log("track_remove", f"Xóa Zalo {phone}", tg_id, phone)
    return {"ok": True}

@router.get("/admin/zalo-tracks")
def api_admin_zalo_tracks(_=Depends(auth)):
    with db._lock:
        return db.get_conn().execute("SELECT * FROM zalo_tracks ORDER BY id DESC LIMIT 500").fetchall()

@router.delete("/admin/zalo-tracks/{track_id}")
def api_admin_delete_zalo_track(track_id: int, _=Depends(auth)):
    with db._lock:
        c = db.get_conn()
        c.execute("DELETE FROM zalo_tracks WHERE id=?", (track_id,))
        c.commit()
    return {"ok": True}

@router.get("/user/referral")
def get_user_referral(tg_id: int = Depends(user_auth)):
    c = db.get_conn()
    user = c.execute("SELECT ref_code, ref_earnings, ref_withdrawn FROM tg_users WHERE tg_id=?", (tg_id,)).fetchone()
    
    if user and not user["ref_code"]:
        ref_code = f"REF{tg_id}"
        with db._lock:
            c.execute("UPDATE tg_users SET ref_code=? WHERE tg_id=?", (ref_code, tg_id))
            c.commit()
        user = c.execute("SELECT ref_code, ref_earnings, ref_withdrawn FROM tg_users WHERE tg_id=?", (tg_id,)).fetchone()
        
    f1_count = c.execute("SELECT COUNT(*) FROM tg_users WHERE referrer_id=?", (tg_id,)).fetchone()[0]
    
    f2_count = c.execute("""
        SELECT COUNT(*) FROM tg_users 
        WHERE referrer_id IN (SELECT tg_id FROM tg_users WHERE referrer_id=?)
    """, (tg_id,)).fetchone()[0]
    
    history = [dict(r) for r in c.execute("SELECT * FROM ref_commissions WHERE referrer_id=? ORDER BY created_at DESC LIMIT 50", (tg_id,)).fetchall()]
    
    return {
        "ok": True,
        "ref_code": user["ref_code"] if user else None,
        "ref_earnings": user["ref_earnings"] if user else 0,
        "ref_withdrawn": user["ref_withdrawn"] if user else 0,
        "f1_count": f1_count,
        "f2_count": f2_count,
        "history": history
    }

class WithdrawIn(BaseModel):
    amount: int
    bank_info: str = ""

@router.post("/user/referral/withdraw")
def request_withdrawal(body: WithdrawIn, tg_id: int = Depends(user_auth)):
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="Số tiền không hợp lệ")
    
    c = db.get_conn()
    user = c.execute("SELECT ref_earnings, ref_withdrawn FROM tg_users WHERE tg_id=?", (tg_id,)).fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")
        
    user_dict = dict(user) if user else {}
    earnings = user_dict.get("ref_earnings") or 0
    withdrawn = user_dict.get("ref_withdrawn") or 0
    available = earnings - withdrawn
    
    import datetime
    current_month_start = int(datetime.datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
    count = c.execute("SELECT COUNT(*) FROM withdrawal_requests WHERE tg_id=? AND created_at >= ?", (tg_id, current_month_start)).fetchone()[0]
    fee = 10000 if count >= 2 else 0
    
    if available < body.amount + fee:
        raise HTTPException(status_code=400, detail=f"Không đủ số dư. Lưu ý từ lần thứ 3 trong tháng phí rút là {fee} VNĐ.")
        
    # FIX (M1): giữ chỗ tiền NGUYÊN TỬ ngay khi tạo yêu cầu (trước đây chỉ kiểm tra rồi
    # tạo, nên gửi nhiều yêu cầu liên tiếp trong lúc chờ duyệt là rút vượt số dư).
    req_id, _err = db.withdrawal_reserve(tg_id, body.amount, body.bank_info, fee)
    if not req_id:
        raise HTTPException(status_code=400,
                            detail="Không đủ số dư khả dụng để rút (có thể đang có yêu cầu chờ duyệt).")
    try:
        from .bot import notify_admin_withdrawal_request
        asyncio.create_task(notify_admin_withdrawal_request(req_id, tg_id, body.amount, body.bank_info, fee))
    except Exception as e:
        log.error("Could not trigger admin notification for web withdrawal: %s", e)
        
    return {"ok": True, "id": req_id}

@router.get("/admin/referral")
def get_referral_admin(_=Depends(auth)):
    c = db.get_conn()
    leaderboard_rows = c.execute("""
        SELECT tg_id, username, name, ref_earnings,
        (SELECT COUNT(*) FROM tg_users u2 WHERE u2.referrer_id = u.tg_id) as f1_count
        FROM tg_users u
        WHERE ref_earnings > 0
        ORDER BY ref_earnings DESC LIMIT 100
    """).fetchall()
    
    withdraw_rows = c.execute("SELECT * FROM withdrawal_requests ORDER BY created_at DESC LIMIT 100").fetchall()
    
    return {
        "ok": True,
        "leaderboard": [dict(r) for r in leaderboard_rows],
        "history": [dict(r) for r in withdraw_rows]
    }

@router.post("/admin/withdrawals/{id}/approve")
def approve_withdrawal(id: int, _=Depends(require_role("admin"))):
    c = db.get_conn()
    req = _row(c.execute("SELECT * FROM withdrawal_requests WHERE id=?", (id,)).fetchone())
    if not req or req["status"] != "pending":
        raise HTTPException(status_code=400, detail="Không tìm thấy yêu cầu hoặc đã xử lý")

    # FIX (M1): chuyển trạng thái + ghi sổ bằng hàm NGUYÊN TỬ/IDEMPOTENT của db, và số
    # tiền luôn lấy từ BẢN GHI (req["amount"]) chứ không từ tham số ngoài. Tiền đã được
    # GIỮ CHỖ lúc tạo yêu cầu nên ở đây không cộng lại ref_withdrawn (hàm db tự xử lý
    # trường hợp yêu cầu cũ tạo trước bản vá, chưa giữ chỗ).
    if not db.withdrawal_mark_approved(id):
        raise HTTPException(status_code=400, detail="Yêu cầu đã được xử lý ở nơi khác")
        
    # Notify customer
    try:
        from .bot import manager as main_bot_manager
        cust_msg = (
            "🎉 <b>RÚT TIỀN HOA HỒNG THÀNH CÔNG!</b>\n\n"
            f"Yêu cầu rút tiền <b>#{id}</b> của bạn đã được Admin duyệt và chuyển tiền.\n"
            f"💰 Số tiền: <b>{req['amount']:,.0f} VNĐ</b>\n"
            f"🏦 Ngân hàng / STK: <b>{req.get('bank_info', '')}</b>\n\n"
            "Cảm ơn bạn đã đồng hành và phát triển cùng hệ thống! ❤️"
        )
        if main_bot_manager.bot:
            asyncio.create_task(main_bot_manager.bot.send_message(req["tg_id"], cust_msg, parse_mode="HTML"))
    except Exception as notify_err:
        log.error("Could not notify user of approved withdrawal: %s", notify_err)
        
    return {"ok": True}

@router.post("/admin/withdrawals/{id}/reject")
def reject_withdrawal(id: int, _=Depends(require_role("admin"))):
    c = db.get_conn()
    req = _row(c.execute("SELECT * FROM withdrawal_requests WHERE id=?", (id,)).fetchone())
    if not req or req["status"] != "pending":
        raise HTTPException(status_code=400, detail="Không tìm thấy yêu cầu hoặc đã xử lý")

    # FIX (M1): từ chối phải HOÀN phần tiền đã giữ chỗ — trước đây chỉ đổi trạng thái nên
    # tiền bị treo vĩnh viễn trong ref_withdrawn (khách không rút lại được).
    if not db.withdrawal_release(id, "rejected"):
        raise HTTPException(status_code=400, detail="Yêu cầu đã được xử lý ở nơi khác")
        
    # Notify customer
    try:
        from .bot import manager as main_bot_manager
        cust_msg = (
            "❌ <b>YÊU CẦU RÚT TIỀN BỊ TỪ CHỐI</b>\n\n"
            f"Yêu cầu rút tiền hoa hồng <b>#{id}</b> ({req['amount']:,.0f} VNĐ) của bạn đã bị Admin từ chối.\n"
            "Số dư hoa hồng của bạn vẫn được giữ nguyên.\n"
            "Vui lòng kiểm tra lại thông tin Ngân hàng / STK hoặc liên hệ Admin để được hỗ trợ."
        )
        if main_bot_manager.bot:
            asyncio.create_task(main_bot_manager.bot.send_message(req["tg_id"], cust_msg, parse_mode="HTML"))
    except Exception as notify_err:
        log.error("Could not notify user of rejected withdrawal: %s", notify_err)
        
    return {"ok": True}

# --- ALERTS ---
class AlertRuleIn(BaseModel):
    platform: str
    target: str
    condition: str = "status_change"

@router.get("/user/alerts")
def api_get_alerts(token: str = Header(default="")):
    username = db.verify_magic_link(token)
    if not username:
        raise HTTPException(status_code=401, detail="Chưa đăng nhập")
    tg_id = str(username)
    return [_row(a) for a in db.get_alert_rules(tg_id=tg_id)]

@router.post("/user/alerts")
def api_create_alert(body: AlertRuleIn, token: str = Header(default="")):
    username = db.verify_magic_link(token)
    if not username:
        raise HTTPException(status_code=401, detail="Chưa đăng nhập")
    tg_id = str(username)
    rule_id = db.create_alert_rule(tg_id, body.platform, body.target, body.condition)
    return {"ok": True, "id": rule_id}

@router.delete("/user/alerts/{id}")
def api_delete_alert(id: int, token: str = Header(default="")):
    username = db.verify_magic_link(token)
    if not username:
        raise HTTPException(status_code=401, detail="Chưa đăng nhập")
    db.delete_alert_rule(id)
    return {"ok": True}

# --- Admin xem/quản lý cảnh báo (web admin dùng, tránh gọi nhầm endpoint user gây 401 -> văng phiên) ---
@router.get("/admin/alerts")
def api_admin_get_alerts(_=Depends(auth)):
    return [_row(a) for a in db.get_alert_rules()]

@router.post("/admin/alerts")
def api_admin_create_alert(body: AlertRuleIn, _=Depends(auth)):
    rule_id = db.create_alert_rule("admin", body.platform, body.target, body.condition)
    return {"ok": True, "id": rule_id}

@router.delete("/admin/alerts/{id}")
def api_admin_delete_alert(id: int, _=Depends(auth)):
    db.delete_alert_rule(id)
    return {"ok": True}

# --- V2 PRO API ---

@router.get("/admin/audit-logs")
def api_get_audit_logs(admin_id: int = Depends(auth)):
    admin = db.get_admin_by_id(admin_id)
    if not admin or admin["role"] not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    return db.get_audit_logs(limit=100)

class CampaignIn(BaseModel):
    name: str

@router.get("/user/campaigns")
def api_get_campaigns(token: str = Header(default="")):
    username = db.verify_magic_link(token)
    if not username:
        raise HTTPException(status_code=401, detail="Chưa đăng nhập")
    tg_id = int(username)
    return db.get_campaigns(tg_id)

@router.post("/user/campaigns")
def api_create_campaign(body: CampaignIn, token: str = Header(default="")):
    username = db.verify_magic_link(token)
    if not username:
        raise HTTPException(status_code=401, detail="Chưa đăng nhập")
    tg_id = int(username)
    cid = db.add_campaign(tg_id, body.name)
    return {"ok": True, "id": cid}

@router.delete("/user/campaigns/{id}")
def api_delete_campaign(id: int, token: str = Header(default="")):
    username = db.verify_magic_link(token)
    if not username:
        raise HTTPException(status_code=401, detail="Chưa đăng nhập")
    tg_id = int(username)
    success = db.delete_campaign(id, tg_id)
    return {"ok": success}

class BulkDeleteIn(BaseModel):
    ids: list[int]
    platform: str

@router.post("/user/bulk-delete")
def api_bulk_delete(body: BulkDeleteIn, token: str = Header(default="")):
    username = db.verify_magic_link(token)
    if not username:
        raise HTTPException(status_code=401, detail="Chưa đăng nhập")
    tg_id = int(username)
    
    count = 0
    with db._lock:
        c = db.get_conn()
        for id in body.ids:
            if body.platform == 'watches':
                c.execute("UPDATE watches SET active=0 WHERE id=? AND tg_id=?", (id, tg_id))
            elif body.platform == 'tiktok':
                c.execute("UPDATE tracks SET active=0 WHERE id=? AND tg_user_id=?", (id, tg_id))
            elif body.platform == 'tiktok_video':
                c.execute("UPDATE video_tracks SET active=0 WHERE id=? AND tg_user_id=?", (id, tg_id))
            elif body.platform == 'ig':
                c.execute("UPDATE ig_tracks SET active=0 WHERE id=? AND tg_user_id=?", (id, tg_id))
            elif body.platform == 'fb':
                c.execute("DELETE FROM fb_tracks WHERE id=? AND tg_user_id=?", (id, tg_id))
            count += 1
        c.commit()
    return {"ok": True, "deleted": count}
