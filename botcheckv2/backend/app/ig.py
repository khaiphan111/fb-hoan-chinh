import json, re, logging, time
from typing import Optional
import httpx
from . import db

log = logging.getLogger(__name__)
from . import util

def parse_ig_username(raw: str) -> Optional[str]:
    raw = raw.strip().rstrip("/")
    m = re.search(r"instagram\.com/([\w.]+)", raw)
    if m: return m.group(1)
    if raw.startswith("@"): return raw[1:]
    if raw.startswith("http"): return None
    return raw if re.match(r"^[\w.]+$", raw) else None

def parse_ig_post_id(raw: str) -> Optional[str]:
    raw = raw.strip().rstrip("/")
    # https://www.instagram.com/p/C1234567890/ or /reel/C1234567890/
    m = re.search(r"instagram\.com/(?:p|reel|tv)/([^/?]+)", raw)
    if m: return m.group(1)
    if re.match(r"^[\w-]+$", raw) and len(raw) > 5: return raw
    return None

def fmt_num(n) -> str:
    try: n = int(n)
    except: return "N/A"
    if n >= 1_000_000_000: return f"{n/1_000_000_000:.1f}B"
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1_000: return f"{n/1_000:.1f}K"
    return f"{n:,}"

# ─── FETCH IG INFO ──────────────────────────────────────────
async def fetch_ig_info(username: str) -> dict:
    method = db.get_setting("ig_method", "public")
    
    if method == "rapidapi":
        return await _fetch_ig_rapidapi(username)
    elif method == "instaloader":
        return await _fetch_ig_instaloader(username)
    else:
        return await _fetch_ig_public(username)

async def fetch_ig_post_info(post_url: str) -> dict:
    method = db.get_setting("ig_method", "public")
    shortcode = parse_ig_post_id(post_url)
    if not shortcode: raise ValueError("Link bài viết không hợp lệ.")
    
    if method == "rapidapi":
        return await _fetch_ig_post_rapidapi(shortcode)
    elif method == "instaloader":
        return await _fetch_ig_post_instaloader(shortcode)
    else:
        return await _fetch_ig_post_public(shortcode)

# ─── RAPIDAPI METHOD ─────────────────────────────────────────
# Giả sử dùng Instagram Scraper API (hoặc tương tự) trên RapidAPI
async def _fetch_ig_rapidapi(username: str) -> dict:
    api_key = db.get_setting("ig_rapidapi_key")
    if not api_key: raise ValueError("Chưa cấu hình RapidAPI Key trong Dashboard.")
    
    headers = {
        "x-rapidapi-key": api_key,
        "x-rapidapi-host": "instagram-scraper-api2.p.rapidapi.com"
    }
    url = f"https://instagram-scraper-api2.p.rapidapi.com/v1/info?username_or_id_or_url={username}"
    proxy = db.get_random_proxy()
    
    try:
        async with httpx.AsyncClient(timeout=20, proxy=proxy) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                raise ValueError(f"Lỗi RapidAPI: {resp.status_code} - {resp.text[:100]}")
            data = resp.json().get("data", {})
            if not data: raise ValueError("Không tìm thấy người dùng.")
    except Exception as e:
        if proxy: db.mark_proxy_failed(proxy)
        raise e
        
        return {
            "uid": data.get("id", ""),
            "username": data.get("username", username),
            "full_name": data.get("full_name", ""),
            "bio": data.get("biography", ""),
            "verified": data.get("is_verified", False),
            "private": data.get("is_private", False),
            "avatar": data.get("profile_pic_url_hd", ""),
            "followers": data.get("edge_followed_by", {}).get("count", 0) if isinstance(data.get("edge_followed_by"), dict) else data.get("follower_count", 0),
            "following": data.get("edge_follow", {}).get("count", 0) if isinstance(data.get("edge_follow"), dict) else data.get("following_count", 0),
            "posts": data.get("edge_owner_to_timeline_media", {}).get("count", 0) if isinstance(data.get("edge_owner_to_timeline_media"), dict) else data.get("media_count", 0),
        }

async def _fetch_ig_post_rapidapi(shortcode: str) -> dict:
    api_key = db.get_setting("ig_rapidapi_key")
    if not api_key: raise ValueError("Chưa cấu hình RapidAPI Key.")
    
    headers = {
        "x-rapidapi-key": api_key,
        "x-rapidapi-host": "instagram-scraper-api2.p.rapidapi.com"
    }
    url = f"https://instagram-scraper-api2.p.rapidapi.com/v1/post_info?code_or_id_or_url={shortcode}"
    proxy = db.get_random_proxy()
    
    try:
        async with httpx.AsyncClient(timeout=20, proxy=proxy) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200: raise ValueError(f"Lỗi RapidAPI: {resp.status_code}")
            data = resp.json().get("data", {})
            if not data: raise ValueError("Không tìm thấy bài viết.")
    except Exception as e:
        if proxy: db.mark_proxy_failed(proxy)
        raise e
        
        author = data.get("owner", {}).get("username", "")
        desc = ""
        try: desc = data.get("edge_media_to_caption", {}).get("edges", [])[0].get("node", {}).get("text", "")
        except: pass
        
        return {
            "id": shortcode,
            "username": author,
            "desc": desc,
            "cover": data.get("display_url", ""),
            "url": f"https://www.instagram.com/p/{shortcode}/",
            "likes": data.get("edge_media_preview_like", {}).get("count", 0) if isinstance(data.get("edge_media_preview_like"), dict) else data.get("like_count", 0),
            "comments": data.get("edge_media_to_comment", {}).get("count", 0) if isinstance(data.get("edge_media_to_comment"), dict) else data.get("comment_count", 0),
            "views": data.get("video_view_count", 0),
        }

# ─── INSTALOADER METHOD ──────────────────────────────────────
def _get_instaloader_instance():
    try: import instaloader
    except ImportError: raise ValueError("Thư viện instaloader chưa được cài đặt.")
    L = instaloader.Instaloader(quiet=True)
    session_str = db.get_setting("ig_session_cookie")
    if session_str:
        if "=" in session_str:
            from http.cookies import SimpleCookie
            cookie = SimpleCookie()
            cookie.load(session_str)
            for key, morsel in cookie.items():
                L.context._session.cookies.set(key, morsel.value, domain=".instagram.com")
        else:
            L.context._session.cookies.set("sessionid", session_str, domain=".instagram.com")
    return L

def _attempt_ig_login(L):
    ig_username = db.get_setting("ig_username")
    ig_password = db.get_setting("ig_password")
    if not ig_username or not ig_password:
        raise ValueError("Cookie hết hạn/bị chặn và không có cấu hình tài khoản/mật khẩu để tự động đăng nhập.")
    try:
        L.login(ig_username, ig_password)
        new_sessionid = L.context._session.cookies.get("sessionid", domain=".instagram.com")
        if new_sessionid:
            db.set_setting("ig_session_cookie", new_sessionid)
        else:
            cookies_dict = L.context._session.cookies.get_dict(domain=".instagram.com")
            db.set_setting("ig_session_cookie", "; ".join([f"{k}={v}" for k, v in cookies_dict.items()]))
    except Exception as e:
        raise ValueError(f"Lỗi tự động đăng nhập Instagram: {e}")

async def _fetch_ig_instaloader(username: str) -> dict:
    try: import instaloader
    except ImportError: raise ValueError("Thư viện instaloader chưa được cài đặt.")
    L = _get_instaloader_instance()
    
    try:
        profile = instaloader.Profile.from_username(L.context, username)
    except (instaloader.exceptions.LoginRequiredException, instaloader.exceptions.BadResponseException, instaloader.exceptions.ProfileNotExistsException):
        _attempt_ig_login(L)
        try:
            profile = instaloader.Profile.from_username(L.context, username)
        except Exception as e:
            raise ValueError(f"Lỗi Instaloader (sau khi relogin): {e}")
    except Exception as e:
        raise ValueError(f"Lỗi Instaloader: {e}")

    return {
        "uid": str(profile.userid),
        "username": profile.username,
        "full_name": profile.full_name,
        "bio": profile.biography,
        "verified": profile.is_verified,
        "private": profile.is_private,
        "avatar": profile.profile_pic_url,
        "followers": profile.followers,
        "following": profile.followees,
        "posts": profile.mediacount,
    }

async def _fetch_ig_post_instaloader(shortcode: str) -> dict:
    try: import instaloader
    except ImportError: raise ValueError("Thư viện instaloader chưa được cài đặt.")
    L = _get_instaloader_instance()
        
    try:
        post = instaloader.Post.from_shortcode(L.context, shortcode)
    except (instaloader.exceptions.LoginRequiredException, instaloader.exceptions.BadResponseException):
        _attempt_ig_login(L)
        try:
            post = instaloader.Post.from_shortcode(L.context, shortcode)
        except Exception as e:
            raise ValueError(f"Lỗi Instaloader (sau khi relogin): {e}")
    except Exception as e:
        raise ValueError(f"Lỗi Instaloader: {e}")

    return {
        "id": shortcode,
        "username": post.owner_username,
        "desc": post.caption or "",
        "cover": post.url,
        "url": f"https://www.instagram.com/p/{shortcode}/",
        "likes": post.likes,
        "comments": post.comments,
        "views": post.video_view_count if post.is_video else 0,
    }

# ─── PUBLIC METHOD (FALLBACK) ────────────────────────────────
async def _fetch_ig_public(username: str) -> dict:
    import asyncio
    # Public web endpoint thuong bi chan rat nhanh
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "X-IG-App-ID": "936619743392459",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
    proxy = db.get_random_proxy()
    
    try:
        async with httpx.AsyncClient(timeout=20, headers=headers, proxy=proxy, follow_redirects=True) as client:
            # Bước quan trọng: lấy cookie session từ trang chủ trước để giảm thiểu block
            await client.get("https://www.instagram.com/")
            await asyncio.sleep(2) # Tránh rate limit
            
            resp = await client.get(url)
            if resp.status_code == 429:
                raise ValueError("IG Public Web đang bị chặn (429). Hãy đổi sang dùng Instaloader hoặc RapidAPI trong Cấu Hình.")
            if resp.status_code != 200:
                raise ValueError(f"Lỗi Public IG: {resp.status_code}")
            try:
                data = resp.json().get("data", {}).get("user", {})
                if not data: raise ValueError("Không có data")
                return {
                    "uid": data.get("id", ""),
                    "username": data.get("username", username),
                    "full_name": data.get("full_name", ""),
                    "bio": data.get("biography", ""),
                    "verified": data.get("is_verified", False),
                    "private": data.get("is_private", False),
                    "avatar": data.get("profile_pic_url_hd", ""),
                    "followers": data.get("edge_followed_by", {}).get("count", 0),
                    "following": data.get("edge_follow", {}).get("count", 0),
                    "posts": data.get("edge_owner_to_timeline_media", {}).get("count", 0),
                }
            except:
                raise ValueError("Không thể phân tích dữ liệu Instagram lúc này.")
    except Exception as e:
        if proxy: db.mark_proxy_failed(proxy)
        raise e

async def _fetch_ig_post_public(shortcode: str) -> dict:
    import asyncio
    url = f"https://www.instagram.com/graphql/query/?query_hash=b3055c01b4b222b8a47dc12b090e4e64&variables=%7B%22shortcode%22%3A%22{shortcode}%22%7D"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "X-IG-App-ID": "936619743392459",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    proxy = db.get_random_proxy()
    try:
        async with httpx.AsyncClient(timeout=20, headers=headers, proxy=proxy, follow_redirects=True) as client:
            await client.get(f"https://www.instagram.com/p/{shortcode}/")
            await asyncio.sleep(2)
            
            resp = await client.get(url)
            if resp.status_code == 429:
                raise ValueError("IG Public Web bị chặn (429).")
            try:
                data = resp.json().get("data", {}).get("shortcode_media", {})
                if not data: raise ValueError("Không có data")
                author = data.get("owner", {}).get("username", "")
                desc = ""
                try: desc = data.get("edge_media_to_caption", {}).get("edges", [])[0].get("node", {}).get("text", "")
                except: pass
                
                return {
                    "id": shortcode,
                    "username": author,
                    "desc": desc,
                    "cover": data.get("display_url", ""),
                    "url": f"https://www.instagram.com/p/{shortcode}/",
                    "likes": data.get("edge_media_preview_like", {}).get("count", 0),
                    "comments": data.get("edge_media_to_parent_comment", {}).get("count", 0),
                    "views": data.get("video_view_count", 0),
                }
            except:
                raise ValueError("Không thể lấy dữ liệu bài viết IG. Hãy dùng Instaloader/RapidAPI.")
    except Exception as e:
        if proxy: db.mark_proxy_failed(proxy)
        raise e

# ─── CAPTIONS ────────────────────────────────────────────────
def build_ig_info_caption(info: dict) -> str:
    verified = "Đã xác minh ✅" if info["verified"] else "Chưa xác minh ❌"
    privacy  = "🔒 Riêng tư"   if info["private"]  else "🌐 Công khai"
    lines = [
        "╔══════════════════════════╗",
        "  📸  <b>CHECK THÔNG TIN INSTAGRAM</b>",
        "╚══════════════════════════╝",
        "",
        f"👤 Username  : <b>@{info['username']}</b>",
        f"📛 Tên hiển thị: <b>{info['full_name']}</b>",
        f"🔑 Xác minh  : {verified}",
        f"🔐 Trạng thái: {privacy}",
        "",
        "━━━━━━ 📊 THỐNG KÊ ━━━━━━",
        f"👥 Người theo dõi: <b>{fmt_num(info['followers'])}</b>",
        f"➡️ Đang theo dõi : <b>{fmt_num(info['following'])}</b>",
        f"🖼️ Bài viết (Posts): <b>{fmt_num(info['posts'])}</b>",
    ]
    if info.get("bio"):
        lines += ["", f"📝 Tiểu sử:\n<i>{info['bio']}</i>"]
    
    lines += [
        "",
        f"🔗 <a href=\"https://www.instagram.com/{info['username']}/\">➜ Xem trang Instagram</a>",
        "", "──────────────────────────",
        "🤖 <i>Instagram Checker V2 by @khaikhai998</i>",
    ]
    return "\n".join(lines)

def build_ig_video_caption(v: dict, old: dict = None) -> str:
    desc = (v["desc"][:100] + "...") if len(v.get("desc","")) > 100 else v.get("desc","Không có mô tả")
    lines = [
        "📊 <b>CẬP NHẬT BÀI VIẾT INSTAGRAM</b>",
        "",
        f"📸 <b>@{v['username']}</b>",
        f"📝 {desc}",
        "",
        "━━━━ 📈 THỐNG KÊ ━━━━",
        f"❤️ Lượt thích: <b>{fmt_num(v['likes'])}</b>",
        f"💬 Bình luận: <b>{fmt_num(v['comments'])}</b>",
    ]
    if v.get("views"):
        lines.append(f"▶️ Lượt xem: <b>{fmt_num(v['views'])}</b>")
        
    if old:
        dl = v["likes"]    - old.get("likes", 0)
        dc = v["comments"] - old.get("comments", 0)
        dv = v.get("views", 0) - old.get("views", 0)
        def d2s(x): return (f"+{x:,}" if x > 0 else f"{x:,}") if x != 0 else "—"
        lines += [
            "", "━━━━ 📊 THAY ĐỔI ━━━━",
            f"❤️ Thích  : <b>{d2s(dl)}</b>",
            f"💬 Cmt    : <b>{d2s(dc)}</b>",
        ]
        if v.get("views"): lines.append(f"▶️ Views  : <b>{d2s(dv)}</b>")
        
    now_str = util.vn_time_str("%d/%m/%Y %H:%M:%S")
    lines += [
        "", f"⏰ Thời gian: <b>{now_str}</b>",
        "", f"🔗 <a href=\"{v['url']}\">▶ Xem bài viết ngay</a>",
        "", "🤖 <i>Instagram Checker V2</i>",
    ]
    return "\n".join(lines)
