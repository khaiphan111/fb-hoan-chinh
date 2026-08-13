# FB Live/Die Checker & Tiktok Checker
import secrets
import time

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile, File, Request
from pydantic import BaseModel
import os

from . import config, db, fb
from .bot import manager, zalo_manager
from .poller import poller
from .util import now
from .event_bus import event_bus
from fastapi import WebSocket, WebSocketDisconnect

router = APIRouter(prefix="/api")
_tokens = set()

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
    yt_api_key: str | None = None
    fb_cookie: str | None = None
    zalo_cookie: str | None = None
    zalo_imei: str | None = None
    web_domain: str | None = None


class TokenIn(BaseModel):
    token: str


class AmountIn(BaseModel):
    amount: int


class MonthsIn(BaseModel):
    days: int


class UidIn(BaseModel):
    uid: str


def _row(r):
    return dict(r) if r else None


def get_secret():
    import hmac
    import hashlib
    # Dùng bot_token làm secret key cho việc mã hóa JWT-like token (hoặc một string cố định nếu chưa có)
    return db.get_setting("bot_token", "default_secret_key_12345").encode()

def create_admin_token(admin_id: int):
    import hmac
    import hashlib
    import time
    expiry = int(time.time()) + 86400 * 7
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
    admin = db.get_admin_by_username(body.username)
    if not admin:
        raise HTTPException(status_code=401, detail="Sai tên đăng nhập hoặc mật khẩu")
        
    import hashlib
    hash_pw = hashlib.sha256(body.password.encode()).hexdigest()
    if admin["password_hash"] != hash_pw:
        raise HTTPException(status_code=401, detail="Sai tên đăng nhập hoặc mật khẩu")
        
    if not admin["is_active"]:
        raise HTTPException(status_code=403, detail="Tài khoản đã bị khóa")
        
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
    yt_tracks = [dict(r) for r in c.execute("SELECT * FROM yt_tracks WHERE tg_user_id=?", (tg_id,)).fetchall()]
    yt_videos = [dict(r) for r in c.execute("SELECT * FROM yt_video_tracks WHERE tg_user_id=?", (tg_id,)).fetchall()]
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
        "yt_tracks": yt_tracks,
        "yt_videos": yt_videos,
        "zalo_tracks": zalo_tracks,
    }


@router.get("/status")
def status(_=Depends(auth)):
    watches = db.all_watches()
    live = sum(1 for w in watches if w["last_status"] == "live")
    die = sum(1 for w in watches if w["last_status"] == "die")
    
    logs_today = [l for l in db.recent_logs(500)
                  if dict(l).get("kind") in ("follower_change","video_new","video_stats")
                  and dict(l).get("ts", 0) > int(time.time()) - 86400]

    return {
        "app": config.APP_NAME,
        "version": config.APP_VERSION,
        "author": config.AUTHOR,
        "setup_done": db.get_setting("setup_done") == "1",
        "bot_running": manager.running,
        "zalo_running": zalo_manager.running,
        "poller_running": poller.running,
        "poller_last_run": poller.last_run,
        "users": len(db.list_users()),
        "watches_total": len(watches),
        "watches_live": live,
        "watches_die": die,
        "tracks_total": len(db.all_active_tracks()),
        "video_tracks_total": len(db.all_active_video_tracks()),
        "yt_tracks_total": len(db.all_active_yt_tracks()),
        "yt_video_tracks_total": len(db.all_active_yt_video_tracks()),
        "notifs_today": len(logs_today),
    }


@router.get("/settings")
def get_settings(_=Depends(auth)):
    s = db.all_settings()
    s.pop("admin_password", None)
    
    import os
    img_dir = os.path.join(os.path.dirname(__file__), "..", "data", "images")
    qr_images = []
    if os.path.exists(img_dir):
        qr_images = [f for f in os.listdir(img_dir) if f.startswith("qr_")]
    s["qr_images"] = qr_images
    
    return s


@router.post("/settings")
async def save_settings(body: SettingsIn, _=Depends(auth)):
    restart_bot = False
    restart_zalo = False
    restart_admin_bot = False
    data = body.model_dump(exclude_none=True)
    for k, v in data.items():
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
            
    return {"ok": True, "bot_running": manager.running, "bot_started": started, "zalo_started": zalo_started}

class CodeGenerateIn(BaseModel):
    amount: int
    max_uses: int
    expire_days: int
    expire_hours: int

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
        expire_at=expire_at
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
async def upload_qr(file: UploadFile = File(...), _=Depends(auth)):
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
async def delete_qr(filename: str, _=Depends(auth)):
    img_dir = os.path.join(os.path.dirname(__file__), "..", "data", "images")
    filepath = os.path.join(img_dir, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
    return {"ok": True}

@router.post("/verify-bot")
async def verify_bot(body: TokenIn, _=Depends(auth)):
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
async def topup(tg_id: int, body: AmountIn, _=Depends(auth)):
    if not db.get_user(tg_id):
        raise HTTPException(status_code=404, detail="Không có user này")
    db.adjust_balance(tg_id, body.amount, "Admin nạp")
    db.add_log("topup", f"Admin nạp {body.amount}", tg_id)
    
    # Kiem tra VIP
    upgraded, new_vip, is_lifetime = db.check_vip_upgrade(tg_id)
    
    try:
        from .bot import manager, vnd
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


@router.post("/users/{tg_id}/trial")
async def grant_trial(tg_id: int, body: dict = None, _=Depends(auth)):
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
async def reset_user_api(tg_id: int, _=Depends(auth)):
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
async def delete_user_api(tg_id: int, _=Depends(auth)):
    user = db.get_user(tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="Không có user này")
    db.delete_user(tg_id)
    db.add_log("delete", "Admin xóa người dùng khỏi hệ thống", tg_id)
    return {"ok": True}


@router.post("/users/{tg_id}/sub")
async def grant_sub(tg_id: int, body: MonthsIn, _=Depends(auth)):
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
        "avatar_url": res["avatar_url"] or fb.avatar_url(res["uid"]),
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
async def bot_start(_=Depends(auth)):
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
async def bot_stop(_=Depends(auth)):
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
    rows = c.execute("SELECT ig_username, MAX(last_followers) as last_followers, MAX(last_following) as last_following, MAX(last_posts) as last_posts FROM ig_tracks WHERE active=1 GROUP BY ig_username ORDER BY ig_username").fetchall()
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


# --- YOUTUBE ENDPOINTS ---
@router.get("/yt-tracks")
def api_get_yt_tracks(tg_id: int):
    return db.get_yt_tracks(tg_id)

@router.delete("/yt-tracks/{track_id}")
def api_delete_yt_track(track_id: int, tg_id: int):
    db.remove_yt_track(track_id, tg_id)
    return {"ok": True}

@router.get("/yt-video-tracks")
def api_get_yt_video_tracks(tg_id: int):
    return db.get_yt_video_tracks(tg_id)

@router.delete("/yt-video-tracks/{track_id}")
def api_delete_yt_video_track(track_id: int, tg_id: int):
    db.remove_yt_video_track(track_id, tg_id)
    return {"ok": True}

@router.get("/admin/yt-tracks")
def api_admin_yt_tracks(_=Depends(auth)):
    with db._lock:
        return db.get_conn().execute("SELECT * FROM yt_tracks ORDER BY id DESC LIMIT 500").fetchall()

@router.delete("/admin/yt-tracks/{track_id}")
def api_admin_delete_yt_track(track_id: int, _=Depends(auth)):
    with db._lock:
        c = db.get_conn()
        c.execute("DELETE FROM yt_tracks WHERE id=?", (track_id,))
        c.commit()
    return {"ok": True}

@router.get("/admin/yt-video-tracks")
def api_admin_yt_video_tracks(_=Depends(auth)):
    with db._lock:
        return db.get_conn().execute("SELECT * FROM yt_video_tracks ORDER BY id DESC LIMIT 500").fetchall()

@router.delete("/admin/yt-video-tracks/{track_id}")
def api_admin_delete_yt_video_track(track_id: int, _=Depends(auth)):
    with db._lock:
        c = db.get_conn()
        c.execute("DELETE FROM yt_video_tracks WHERE id=?", (track_id,))
        c.commit()
    return {"ok": True}

# --- ZALO ENDPOINTS ---
@router.get("/zalo-tracks")
def api_get_zalo_tracks(user=Depends(auth)):
    return db.user_zalo_tracks(user["tg_id"])

@router.post("/zalo-tracks")
async def api_add_zalo_track(body: dict, user=Depends(auth)):
    phone = body.get("phone", "").strip()
    if not phone: raise HTTPException(400, "Thiếu SĐT")
    
    vip_level = user.get("vip_level", 0)
    try: max_limit = int(db.get_setting(f"vip{vip_level}_limit", [5, 50, 200, 1000][vip_level if vip_level <= 3 else 3]))
    except: max_limit = [5, 50, 200, 1000][vip_level if vip_level <= 3 else 3]
    
    with db._lock: 
        count = db.get_conn().execute("SELECT COUNT(*) FROM tracks WHERE tg_user_id=?", (user["tg_id"],)).fetchone()[0]
        z_count = db.get_conn().execute("SELECT COUNT(*) FROM zalo_tracks WHERE tg_user_id=?", (user["tg_id"],)).fetchone()[0]
    
    if count + z_count >= max_limit:
        raise HTTPException(400, f"Giới hạn hạng VIP của bạn là {max_limit} mục.")
        
    ok, err = db.check_daily_limit(user["tg_id"])
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
        
    db.add_zalo_track(user["tg_id"], user["username"], phone, name, avatar, status)
    db.add_log("track_add", f"Thêm Zalo {phone}", user["tg_id"], phone)
    return {"ok": True, "res": res}

@router.delete("/zalo-tracks/{phone}")
def api_del_zalo_track(phone: str, user=Depends(auth)):
    db.remove_zalo_track(user["tg_id"], phone)
    db.add_log("track_remove", f"Xóa Zalo {phone}", user["tg_id"], phone)
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

@router.post("/user/referral/withdraw")
def request_withdrawal(body: WithdrawIn, tg_id: int = Depends(user_auth)):
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="Số tiền không hợp lệ")
    
    c = db.get_conn()
    user = c.execute("SELECT ref_earnings, ref_withdrawn FROM tg_users WHERE tg_id=?", (tg_id,)).fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")
        
    available = user["ref_earnings"] - user["ref_withdrawn"]
    if body.amount > available:
        raise HTTPException(status_code=400, detail="Không đủ hoa hồng")
        
    with db._lock:
        c.execute("INSERT INTO withdrawal_requests(tg_id, amount, status, created_at, updated_at) VALUES(?,?,?,?,?)",
                  (tg_id, body.amount, 'pending', int(time.time()), int(time.time())))
        c.commit()
        
    return {"ok": True}

@router.get("/admin/referral/leaderboard")
def get_referral_leaderboard(_=Depends(auth)):
    c = db.get_conn()
    rows = c.execute("""
        SELECT tg_id, username, name, ref_earnings,
        (SELECT COUNT(*) FROM tg_users u2 WHERE u2.referrer_id = u.tg_id) as f1_count
        FROM tg_users u
        WHERE ref_earnings > 0
        ORDER BY ref_earnings DESC LIMIT 100
    """).fetchall()
    return {"ok": True, "leaderboard": [dict(r) for r in rows]}

@router.get("/admin/withdrawals")
def get_withdrawals(_=Depends(auth)):
    c = db.get_conn()
    rows = c.execute("SELECT * FROM withdrawal_requests ORDER BY created_at DESC LIMIT 100").fetchall()
    return {"ok": True, "withdrawals": [dict(r) for r in rows]}

@router.post("/admin/withdrawals/{id}/approve")
def approve_withdrawal(id: int, _=Depends(auth)):
    c = db.get_conn()
    req = c.execute("SELECT * FROM withdrawal_requests WHERE id=?", (id,)).fetchone()
    if not req or req["status"] != "pending":
        raise HTTPException(status_code=400, detail="Không tìm thấy yêu cầu hoặc đã xử lý")
        
    with db._lock:
        c.execute("UPDATE withdrawal_requests SET status='approved', updated_at=? WHERE id=?", (int(time.time()), id))
        c.execute("UPDATE tg_users SET ref_withdrawn = ref_withdrawn + ? WHERE tg_id=?", (req["amount"], req["tg_id"]))
        c.commit()
        
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
