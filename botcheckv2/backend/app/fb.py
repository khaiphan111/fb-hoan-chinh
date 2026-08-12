import httpx
import re
from . import db

USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.162 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.91 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 14_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0.3 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 12; SM-S901B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/99.0.4844.58 Mobile Safari/537.36",
]

def extract_uid(link: str) -> str:
    link = (link or "").strip()
    if not link:
        return ""
    if "facebook.com" in link or "fb.com" in link:
        if "profile.php?id=" in link:
            match = re.search(r'id=(\d+)', link)
            if match: return match.group(1)
        else:
            match = re.search(r'(?:facebook\.com|fb\.com)/([^/?]+)', link)
            if match:
                # Tránh lấy nhầm các path mặc định của facebook
                if match.group(1).lower() not in ["home.php", "login.php", "watch", "groups", "marketplace"]:
                    return match.group(1)
    
    # Fallback cho trường hợp chỉ là UID thuần
    for sep in ("@", "?", "/", " ", "|"):
        if sep in link and not ("http" in link):
            link = link.split(sep)[0]
    return link.replace("https://", "").replace("http://", "").split("/")[0]

def avatar_url(uid: str, size: int = 500) -> str:
    uid = extract_uid(uid)
    token = db.get_setting("fb_avatar_token", "")
    base = f"https://graph.facebook.com/{uid}/picture?height={size}&width={size}"
    if token:
        base += f"&access_token={token}"
    return base

async def _is_real_avatar(uid: str, client: httpx.AsyncClient) -> bool:
    """
    Kiểm tra UID có ảnh avatar thật (không phải ảnh mặc định silhouette).
    Facebook Graph luôn redirect về CDN - nếu redirect tới ảnh silhouette => acc DIE.
    Trả về True nếu có ảnh thật, False nếu là ảnh mặc định/acc không tồn tại.
    """
    try:
        r = await client.get(
            f"https://graph.facebook.com/{uid}/picture?width=200&height=200&redirect=true",
            follow_redirects=True
        )
        final_url = str(r.url).lower()
        # Ảnh mặc định của Facebook chứa các keyword này
        dead_patterns = [
            "silhouette",
            "cpas/cp_placeholder",
            "static.xx.fbcdn",        # ảnh static mặc định
            "/static/",
            "default_pic",
            "no_photo",
            "cp_placeholder",
            "84628273_176159830277856_972693363922829312_n",
            "176159830277856"
        ]
        for pat in dead_patterns:
            if pat in final_url:
                return False
        # Ảnh thật thường có dạng: scontent-*.fbcdn.net, *.xx.fbcdn.net, fbcdn.net/v/...
        if "fbcdn.net" in final_url and r.status_code == 200:
            return True
        return False
    except Exception:
        return False

async def _check_with_cookie(uid: str, cookie: str) -> dict:
    """
    Check Facebook account status using a user cookie.
    Returns: {"alive": bool, "status": "live"|"checkpoint"|"disabled"|"dead", "name": str}
    - live: Acc bình thường
    - checkpoint: Acc bị khoá tạm thời, cần xác minh danh tính
    - disabled: Acc bị vô hiệu hoá vĩnh viễn bởi Facebook
    - dead: Acc không tồn tại (đã bị xoá)
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.5",
        "Cookie": cookie,
        "X-FB-Friendly-Name": "CometHovercardQueryRendererQuery",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
    }
    
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            # Check mbasic.facebook.com with cookie - phân biệt được LIVE vs CHECKPOINT
            r = await client.get(
                f"https://mbasic.facebook.com/profile.php?id={uid}",
                headers={
                    **headers,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                }
            )
            text = r.text
            final_url = str(r.url)
            
            url_path = r.url.path.lower()
            
            # Kiểm tra redirect sang login => cookie hết hạn hoặc không hợp lệ
            if "/login" in url_path or ("login" in final_url.lower() and "profile" not in final_url.lower() and "mbasic" not in final_url.lower()):
                return {"alive": None, "status": "cookie_invalid", "name": ""}
                
            # Kiểm tra bị checkpoint (acc tồn tại nhưng chủ bị khoá)
            if "/checkpoint/" in url_path or "<title>checkpoint</title>" in text.lower():
                return {"alive": False, "status": "checkpoint", "name": ""}
            
            # Kiểm tra acc bị vô hiệu hoá (disabled)
            if "/disabled/" in url_path or "has been disabled" in text.lower() or "account has been disabled" in text.lower():
                return {"alive": False, "status": "disabled", "name": ""}
            
            # Kiểm tra trang lỗi "Content Not Found"
            if "content_not_found" in text.lower() or "page not available" in text.lower():
                return {"alive": False, "status": "dead", "name": ""}
            
            # Lấy tên người dùng từ title
            title_m = re.search(r'<title>(.*?)</title>', text, re.IGNORECASE)
            name = ""
            if title_m:
                raw_title = title_m.group(1)
                if raw_title and raw_title.lower() not in ["facebook", "log in or sign up"]:
                    name = raw_title.strip()
            
            # Nếu đang ở trang profile không bị chặn => LIVE
            if uid in final_url or (name and name.lower() not in ["facebook"]):
                return {"alive": True, "status": "live", "name": name}
            
            # Fallback: Kiểm tra graph API picture (để phân biệt acc tồn tại hay không)
            r2 = await client.get(f"https://graph.facebook.com/{uid}/picture?redirect=false")
            if r2.status_code == 200:
                return {"alive": True, "status": "live", "name": name}
            else:
                return {"alive": False, "status": "dead", "name": ""}
                
    except Exception:
        return {"alive": None, "status": "error", "name": ""}


async def check_uid(uid: str) -> dict:
    uid = extract_uid(uid)
    result = {"uid": uid, "alive": False, "avatar_url": None, "ok": False, "status": "unknown", "name": ""}
    if not uid:
        return result

    cookie = db.get_setting("fb_cookie", "")
    token = db.get_setting("fb_avatar_token", "")
    
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            result["ok"] = True
            result["avatar_url"] = avatar_url(uid)
            
            if cookie:
                # 🔑 CÓ COOKIE => CHECK CHUẨN XÁC 100%: phân biệt LIVE vs CHECKPOINT vs DIE
                ck_result = await _check_with_cookie(uid, cookie)
                if ck_result["status"] == "cookie_invalid":
                    # Cookie hết hạn => Giữ nguyên trạng thái để cảnh báo người dùng cập nhật cookie
                    result["alive"] = False
                    result["status"] = "cookie_invalid"
                elif ck_result["alive"] is None:
                    # Lỗi kết nối hoặc lỗi khác => fallback qua picture
                    has_real_avatar = await _is_real_avatar(uid, client)
                    result["alive"] = has_real_avatar
                    result["status"] = "live" if has_real_avatar else "dead"
                else:
                    result["alive"] = ck_result["alive"]
                    result["status"] = ck_result["status"]
                    result["name"] = ck_result.get("name", "")
            elif token:
                # Có App Token => check qua graph API (chỉ biết tồn tại hay không)
                url = f"https://graph.facebook.com/v18.0/{uid}?access_token={token}"
                r = await client.get(url)
                data = r.json() if r.text else {}
                
                if r.status_code == 200:
                    result["alive"] = True
                    result["status"] = "exists"
                    result["name"] = data.get("name", "")
                else:
                    err = data.get("error", {})
                    err_type = err.get("type", "")
                    if err_type == "GraphMethodException":
                        result["alive"] = False
                        result["status"] = "dead"
                    else:
                        # App token => fallback picture
                        has_real_avatar = await _is_real_avatar(uid, client)
                        result["alive"] = has_real_avatar
                        result["status"] = "live" if has_real_avatar else "dead"
            else:
                # Không có cookie/token => check qua redirect của picture
                # Nếu ảnh là ảnh thật (CDN fbcdn) => LIVE, nếu là ảnh mặc định => DIE
                has_real_avatar = await _is_real_avatar(uid, client)
                result["alive"] = has_real_avatar
                result["status"] = "live" if has_real_avatar else "dead"
                
                # Cố gắng lấy tên profile từ page title bằng urllib (vì httpx dễ bị FB block)
                try:
                    import urllib.request
                    import asyncio
                    
                    def fetch_fb_html():
                        req = urllib.request.Request(
                            f"https://www.facebook.com/{uid}", 
                            headers={"User-Agent": "Mozilla/5.0"}
                        )
                        return urllib.request.urlopen(req, timeout=5).read().decode('utf-8', errors='ignore')
                        
                    html_text = await asyncio.to_thread(fetch_fb_html)
                    title_m = re.search(r'<title>(.*?)</title>', html_text, re.IGNORECASE)
                    if title_m:
                        raw_title = title_m.group(1).strip()
                        if raw_title and raw_title.lower() not in ["facebook", "log in or sign up", "đăng nhập hoặc đăng ký", "error"]:
                            fetched_name = raw_title
                            if fetched_name.endswith(" | Facebook"):
                                fetched_name = fetched_name[:-11]
                            result["name"] = fetched_name
                except Exception:
                    pass
                
    except Exception:
        pass

    return result


def build_fb_caption(res: dict) -> str:
    has_cookie = bool(db.get_setting("fb_cookie", ""))
    status = res.get("status", "unknown")
    name = res.get("name", "")
    
    if has_cookie:
        # Check với cookie => chính xác 100%
        if status == "live":
            status_icon = "🟢"
            status_text = "LIVE (Tài khoản đang hoạt động)"
            note = "✅ <i>Xác nhận qua Cookie - Độ chính xác 100%</i>"
        elif status == "checkpoint":
            status_icon = "🔴"
            status_text = "DIE (Bị Checkpoint / Khoá tạm thời)"
            note = "⚠️ <i>Acc còn tồn tại nhưng chủ sở hữu không thể đăng nhập</i>"
        elif status == "disabled":
            status_icon = "🔴"
            status_text = "DIE (Bị vô hiệu hoá vĩnh viễn)"
            note = "🚫 <i>Facebook đã vô hiệu hoá acc này vĩnh viễn</i>"
        elif status == "dead":
            status_icon = "🔴"
            status_text = "DIE (Đã bị xoá vĩnh viễn)"
            note = "❌ <i>Acc này không còn tồn tại trên hệ thống Facebook</i>"
        elif status == "cookie_invalid":
            status_icon = "⚠️"
            status_text = "Không thể xác định (Cookie hết hạn)"
            note = "🔑 <i>Vui lòng cập nhật Cookie mới trong Admin Panel</i>"
        else:
            status_icon = "🟢" if res["alive"] else "🔴"
            status_text = "TỒN TẠI" if res["alive"] else "DIE (Không tồn tại)"
            note = ""
    else:
        # Không có cookie => dùng ảnh avatar để xác định
        if res["alive"] or status == "live":
            status_icon = "🟢"
            status_text = "LIVE (Tài khoản đang hoạt động)"
            note = "📸 <i>Xác định qua Avatar - Có ảnh thật trên Facebook</i>"
        else:
            status_icon = "🔴"
            status_text = "DIE (Không có ảnh / Acc đã xoá hoặc bị khoá)"
            note = "❌ <i>Không tìm thấy ảnh avatar thật - Acc có thể đã bị xoá hoặc bị khoá</i>"
    
    lines = [
        "╔══════════════════════════╗",
        "  📘  <b>CHECK THÔNG TIN FACEBOOK</b>",
        "╚══════════════════════════╝",
        "",
        f"🆔 UID: <code>{res['uid']}</code>",
    ]
    
    display_name = name if name else "Không xác định"
    lines.append(f"👤 Tên: <b>{display_name}</b>")
    
    lines += [
        f"Trạng thái: {status_icon} <b>{status_text}</b>",
    ]
    
    if note:
        lines.append(note)
    
    lines += [
        "",
        f"🔗 <a href=\"https://www.facebook.com/{res['uid']}\">➜ Xem trang cá nhân</a>",
        "", "──────────────────────────",
        "🤖 <i>FB Checker V2 by @khaikhai998</i>",
    ]
    return "\n".join(lines)
