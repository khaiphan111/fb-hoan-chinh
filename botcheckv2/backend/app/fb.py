import asyncio
import httpx
import re
import os
import html
import logging
from datetime import datetime
from . import db
from . import config

log = logging.getLogger(__name__)

def _make_http_client(**kwargs):
    """Tạo httpx.AsyncClient, workaround lỗi httpx parse no_proxy chứa IPv6 [::1].
    
    httpx parse no_proxy=[::1],... bị lỗi 'Invalid port: :1]' khi khởi tạo client.
    Tạm loại bỏ các entry IPv6 có ngoặc vuông khỏi no_proxy trước khi tạo client,
    rồi restore lại ngay sau đó.
    """
    saved = {}
    for key in ("no_proxy", "NO_PROXY"):
        val = os.environ.get(key)
        if val and "[" in val:
            saved[key] = val
            entries = [e.strip() for e in val.split(",")]
            clean = [e for e in entries if "[" not in e and "]" not in e]
            os.environ[key] = ",".join(clean)
    try:
        return httpx.AsyncClient(**kwargs)
    finally:
        for key, val in saved.items():
            os.environ[key] = val

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
    result = link.replace("https://", "").replace("http://", "").split("/")[0]
    # UID bị Excel convert thành float ("61593959792972.0") -> cắt đuôi .0
    # để không check/giao nhầm UID lỗi. Chỉ áp dụng cho chuỗi toàn số.
    if re.fullmatch(r"\d+\.0+", result):
        result = result.split(".")[0]
    return result


# Cache kết quả traodoisub để giảm tải API bên thứ 3 và tránh DIE oan khi API sập.
# key: link -> (timestamp, uid, name, ok). Kết quả thành công cache 1h, thất bại cache 5 phút.
# LƯU Ý: lỗi thoáng qua (API bận/giới hạn tốc độ/lỗi mạng) KHÔNG được cache — phải raise
# TraodoisubBusy để caller retry, nếu không lần thử lại sẽ đọc cache fail mà bỏ cuộc oan.
_traodoisub_cache: dict = {}
_TRAODOISUB_TTL_OK = 3600
_TRAODOISUB_TTL_FAIL = 300


class TraodoisubBusy(Exception):
    """API traodoisub đang bận / bị giới hạn tốc độ / lỗi mạng thoáng qua.

    Caller bắt exception này để thử lại sau vài giây, TUYỆT ĐỐI không coi như
    "link chết" (tránh bỏ sót link còn sống).
    """


def _traodoisub_is_busy(data) -> bool:
    """Nhận diện thông báo bận/giới hạn tốc độ từ API (VD: "Vui lòng thao tác chậm lại")."""
    try:
        import json as _json
        txt = _json.dumps(data, ensure_ascii=False).lower() if isinstance(data, dict) else ""
    except Exception:
        txt = ""
    return any(k in txt for k in (
        "chậm lại", "thao tác", "slow", "rate", "too many", "quá tải", "busy",
        "overload", "try again later",
    ))


async def _resolve_via_traodoisub(link: str, client: httpx.AsyncClient) -> tuple:
    """
    Dùng API công khai của id.traodoisub.com để lấy UID từ link Facebook.
    Hỗ trợ mọi dạng link: profile, group, fanpage, post, video, ảnh, share link.
    Trả về (uid, name). Trả ("", "") nếu link chết/không công khai (dứt khoát).
    Raise TraodoisubBusy nếu API bận/giới hạn tốc độ/lỗi mạng -> caller phải retry.
    """
    import time as _time

    key = (link or "").strip()
    now = _time.time()
    hit = _traodoisub_cache.get(key)
    if hit:
        ts, uid, name, ok = hit
        ttl = _TRAODOISUB_TTL_OK if ok else _TRAODOISUB_TTL_FAIL
        if now - ts < ttl:
            return uid, name
    uid, name, ok = "", "", False
    try:
        r = await client.post(
            "https://id.traodoisub.com/api.php",
            data={"link": key},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0",
            },
            timeout=20,
        )
        data = r.json() if r.text else {}
        if data.get("success") and data.get("id"):
            uid, name, ok = str(data["id"]), data.get("name", "") or "", True
        elif _traodoisub_is_busy(data):
            # API đang bận -> không cache, raise để retry (không được đánh "link chết")
            raise TraodoisubBusy(str(data.get("error") or "busy"))
    except TraodoisubBusy:
        raise
    except Exception:
        # Lỗi mạng/timeout: không cache để lần thử lại được gọi API thật
        raise TraodoisubBusy("network")
    _traodoisub_cache[key] = (now, uid, name, ok)
    if len(_traodoisub_cache) > 2000:  # chống phình bộ nhớ
        _traodoisub_cache.clear()
    return uid, name


async def _traodoisub_alive(uid: str, client: "httpx.AsyncClient") -> tuple:
    """Hỏi trọng tài traodoisub xem UID có tồn tại không.

    Trả về (True, name) nếu resolve được -> acc sống.
    Trả về (False, "") nếu API trả lời đàng hoàng nhưng không resolve được -> acc die.
    Trả về (None, "") nếu lỗi hạ tầng (timeout/mạng/API sập) -> KHÔNG kết luận được,
    caller phải để status "error" chứ không được đánh "dead" (tránh loại nhầm acc sống).
    Có retry 1 lần cho lỗi thoáng qua.
    """
    import asyncio as _aio
    link = f"https://www.facebook.com/profile.php?id={uid}"
    last_exc = None
    for attempt in range(2):
        try:
            r = await client.post(
                "https://id.traodoisub.com/api.php",
                data={"link": link},
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "Mozilla/5.0",
                },
                timeout=20,
            )
            data = r.json() if r.text else {}
            if data.get("success") and data.get("id"):
                return True, data.get("name", "") or ""
            # API trả lời nhưng không resolve được -> coi như die
            return False, ""
        except Exception as e:
            last_exc = e
            if attempt == 0:
                try:
                    await _aio.sleep(2)
                except Exception:
                    pass
    return None, ""


async def resolve_fb_uid(link: str) -> tuple:
    """
    Lấy UID số từ link Facebook.
    Trả về (uid, name, method): method = "link" | "traodoisub.com" | "Graph API" | "HTML".
    Trả về ("", "", "") nếu không lấy được.
    """
    link = (link or "").strip()
    if not link:
        return "", "", ""

    # B0: link rút gọn dạng /share/xxx hoặc fb.watch -> follow redirect để lấy URL thật
    # (validate chống SSRF: chỉ follow trong facebook.com/fb.watch, chặn IP nội bộ)
    if "/share/" in link or "fb.watch" in link:
        try:
            from .safeurl import fetch_with_safe_redirects, FB_HOSTS, is_safe_url

            if await is_safe_url(link, FB_HOSTS):
                async with _make_http_client(timeout=15) as client:
                    r = await fetch_with_safe_redirects(
                        client, link, FB_HOSTS,
                        headers={"User-Agent": "Mozilla/5.0"},
                    )
                    if r.status_code == 200 and "facebook.com" in str(r.url):
                        link = str(r.url)
        except Exception:
            pass

    # B1: link dạng profile.php?id=123456
    m = re.search(r"[?&#]id=(\d{4,})", link)
    if m:
        return m.group(1), "", "link"

    # B2: path là số (facebook.com/1000xxxx)
    m = re.search(r"(?:facebook\.com|fb\.com|fb\.watch)/+(\d{4,})(?:[/?#]|$)", link)
    if m:
        return m.group(1), "", "link"

    # B2.5: thử API id.traodoisub.com (hỗ trợ mọi dạng link, trả cả tên)
    # API hay giới hạn tốc độ khi gọi dồn dập -> retry với backoff; chỉ dừng khi
    # API trả lời dứt khoát "không resolve được" (link chết/không công khai).
    tds_answered = False  # API đã trả lời dứt khoát (dù được hay không)
    for _att in range(3):
        try:
            async with _make_http_client(timeout=25) as client:
                uid2, name2 = await _resolve_via_traodoisub(link, client)
                tds_answered = True
                if uid2:
                    return uid2, name2, "traodoisub.com"
                break
        except TraodoisubBusy:
            if _att < 2:
                await asyncio.sleep(3 * (_att + 1))
                continue
        except Exception:
            break
    if tds_answered:
        # API đã trả lời dứt khoát "link này không resolve được" -> dừng ngay.
        # Bỏ qua Graph API + cào HTML vì từ IP máy chủ cả hai luôn fail
        # (chưa có fb_avatar_token; FB chặn toàn bộ page load từ IP egress),
        # mỗi link fail sẽ bị đốt thêm ~25s vô ích.
        return "", "", ""

    # B3: link dạng username (facebook.com/ten_user) -> cần resolve
    m = re.search(r"(?:facebook\.com|fb\.com)/+([A-Za-z0-9._-]+)", link)
    username = m.group(1) if m else ""
    if not username or username.lower() in (
        "profile.php", "login.php", "home.php", "watch", "groups",
        "marketplace", "pages", "events", "reel", "share", "story.php",
    ):
        return "", "", ""

    # B3a: thử Graph API với app token (nếu đã cấu hình)
    token = db.get_setting("fb_avatar_token", "")
    if token:
        try:
            async with _make_http_client(timeout=15) as client:
                r = await client.get(
                    f"https://graph.facebook.com/v18.0/{username}?access_token={token}"
                )
                data = r.json() if r.text else {}
                if data.get("id"):
                    return str(data["id"]), data.get("name", "") or "", "Graph API"
        except Exception:
            pass

    # B3b: fallback - cào HTML trang cá nhân tìm userID
    try:
        import urllib.request

        def fetch_html():
            req = urllib.request.Request(
                f"https://www.facebook.com/{username}",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            return urllib.request.urlopen(req, timeout=10).read().decode("utf-8", errors="ignore")

        html_text = await asyncio.to_thread(fetch_html)
        for pat in (r'"userID":"(\d+)"', r'"entity_id":"(\d+)"', r'"pageID":"(\d+)"'):
            mm = re.search(pat, html_text)
            if mm:
                return mm.group(1), "", "HTML"
    except Exception:
        pass

    return "", "", ""

def avatar_url(uid: str, size: int = 500) -> str:
    uid = extract_uid(uid)
    token = db.get_setting("fb_avatar_token", "")
    base = f"https://graph.facebook.com/{uid}/picture?height={size}&width={size}"
    if token:
        base += f"&access_token={token}"
    return base

def display_avatar(res: dict) -> str:
    """URL avatar CHỈ khi chắc chắn là ảnh thật, ngược lại trả ''.

    Đã kiểm chứng 2026-10-05: khi chưa cấu hình fb_avatar_token,
    graph.facebook.com/{uid}/picture luôn trả ảnh silhouette mặc định
    cho MỌI UID (kể cả acc có avatar thật) -> nếu cứ gửi URL đó, khách
    luôn thấy "ảnh mặc định" và tưởng bot lỗi. Trả '' để phía hiển thị
    (_send_card) gửi text-only cho sạch.
    """
    try:
        token = db.get_setting("fb_avatar_token", "")
    except Exception:
        token = ""
    if token and (res or {}).get("has_real_avatar"):
        uid = (res or {}).get("uid") or ""
        return (res or {}).get("avatar_url") or (avatar_url(uid) if uid else "")
    return ""

async def _is_real_avatar(uid: str, client: httpx.AsyncClient) -> bool:
    """
    Kiểm tra UID có ảnh avatar thật hay không.
    Dùng Graph API ?redirect=false -> field is_silhouette do chính Facebook trả về
    (chính xác nhất - vì ảnh silhouette mặc định cũng được serve từ fbcdn với URL
    trông như ảnh thật, check pattern URL sẽ bị lừa).
    Trả về True nếu có ảnh thật, False nếu là ảnh mặc định/acc không tồn tại.
    """
    try:
        token = db.get_setting("fb_avatar_token", "")
        url = f"https://graph.facebook.com/{uid}/picture?redirect=false&width=500&height=500"
        if token:
            url += f"&access_token={token}"
        r = await client.get(url)
        data = r.json() if r.text else {}
        info = data.get("data") or {}
        if "is_silhouette" in info:
            return not info["is_silhouette"]
    except Exception:
        pass
    # Fallback: kiểm tra redirect URL như cách cũ
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

def _classify_checkpoint(url: str, html: str) -> str:
    """Phân loại checkpoint 282 (xác minh danh tính) vs 956 (vi phạm/khóa).

    Dựa trên URL checkpoint và nội dung trang. Trả về:
    "checkpoint_282" | "checkpoint_956" | "checkpoint" (không xác định rõ).
    """
    u = (url or "").lower()
    t = (html or "").lower()

    # 282: xác minh danh tính — URL thường chứa /checkpoint/282
    if "/checkpoint/282" in u or "checkpoint/282" in t:
        return "checkpoint_282"
    for marker in (
        "xác minh danh tính", "xác thực danh tính", "verify your identity",
        "confirm your identity", "xác nhận danh tính",
    ):
        if marker in t:
            return "checkpoint_282"

    # 956: vi phạm chính sách / bị khóa — URL chứa 956/828 hoặc nội dung vi phạm
    if "/checkpoint/956" in u or "/checkpoint/828" in u:
        return "checkpoint_956"
    for marker in (
        "vi phạm tiêu chuẩn cộng đồng", "violated our community standards",
        "tài khoản của bạn đã bị khóa", "your account has been locked",
        "kháng nghị", "request review", "chúng tôi đã đình chỉ",
    ):
        if marker in t:
            return "checkpoint_956"

    return "checkpoint"


async def _check_with_cookie(uid: str, cookie: str) -> dict:
    """
    Check Facebook account status using a user cookie.
    Returns: {"alive": bool, "status": "live"|"checkpoint_282"|"checkpoint_956"|"checkpoint"|"disabled"|"dead"|"cookie_invalid"|"error", "name": str}
    - live: Acc bình thường
    - checkpoint_282: Bị checkpoint xác minh danh tính (282) — thường mở được
    - checkpoint_956: Bị checkpoint vi phạm/khóa (956) — khó mở
    - checkpoint: Bị checkpoint nhưng chưa xác định được loại
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
        async with _make_http_client(timeout=15, follow_redirects=True) as client:
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
                
            # Kiểm tra bị checkpoint (acc tồn tại nhưng chủ bị khoá) — phân loại 282/956
            if "/checkpoint/" in url_path or "<title>checkpoint</title>" in text.lower():
                return {"alive": False, "status": _classify_checkpoint(final_url, text), "name": ""}
            
            # Kiểm tra acc bị vô hiệu hoá (disabled)
            if "/disabled/" in url_path or "has been disabled" in text.lower() or "account has been disabled" in text.lower():
                return {"alive": False, "status": "disabled", "name": ""}
            
            # Kiểm tra trang lỗi "Content Not Found"
            if "content_not_found" in text.lower() or "page not available" in text.lower():
                return {"alive": False, "status": "dead", "name": ""}
            
            # Lấy tên người dùng từ title
            # LƯU Ý: loại trừ các title generic như "Facebook", "Lỗi", "Error"
            # vì đó là trang lỗi/chặn, không phải profile thật.
            title_m = re.search(r'<title>(.*?)</title>', text, re.IGNORECASE)
            name = ""
            if title_m:
                raw_title = title_m.group(1)
                if raw_title and raw_title.lower().strip() not in [
                    "facebook", "log in or sign up", "lỗi", "error",
                    "facebook - log in or sign up",
                ]:
                    name = raw_title.strip()
            
            # Nếu đang ở trang profile không bị chặn => LIVE
            # LƯU Ý: FB chặn IP server thường trả về trang generic vẫn chứa
            # UID trong HTML (script/meta) nhưng không có tên profile thật.
            # Chỉ báo LIVE khi đọc được TÊN NGƯỜI DÙNG THẬT từ title.
            # Nếu không có tên -> để fallback Graph API (chỉ kết luận "tồn tại").
            if name:
                # Cookie đọc được tên thật => LIVE; kèm check avatar để bot gửi ảnh
                has_avatar = await _is_real_avatar(uid, client)
                return {"alive": True, "status": "live", "name": name,
                        "has_real_avatar": has_avatar,
                        "avatar_url": avatar_url(uid) if has_avatar else None}
            
            # Fallback: Kiểm tra graph API picture (để phân biệt acc tồn tại hay không)
            # LUẬT MỚI (user 2026-10-08): avatar thật + tìm được UID => 🟢 LIVE
            r2 = await client.get(f"https://graph.facebook.com/{uid}/picture?redirect=false")
            if r2.status_code == 200:
                has_avatar = await _is_real_avatar(uid, client)
                st = "live" if has_avatar else "exists"
                return {"alive": True, "status": st, "name": name,
                        "has_real_avatar": has_avatar}
            else:
                return {"alive": False, "status": "dead", "name": ""}
                
    except Exception:
        return {"alive": None, "status": "error", "name": ""}


def get_fb_cookie_pool() -> list:
    """Danh sách cookie Facebook dự phòng (setting fb_cookie_pool, JSON array).
    Dùng để xoay vòng khi check — cookie nào hỏng sẽ tự bỏ qua."""
    import json
    try:
        from . import db
        raw = db.get_setting("fb_cookie_pool", "")
        if raw:
            data = json.loads(raw)
            if isinstance(data, list):
                return [c.strip() for c in data if isinstance(c, str) and c.strip()]
    except Exception:
        pass
    return []


def set_fb_cookie_pool(cookies: list) -> None:
    from . import db
    import json
    db.set_setting("fb_cookie_pool", json.dumps(cookies))


async def _check_with_cookie_pool(uid: str):
    """Thử lần lượt từng cookie trong pool.
    Trả về kết quả chắc chắn đầu tiên (live/checkpoint_*/disabled/dead),
    bỏ qua cookie hỏng (cookie_invalid/error). Trả về None nếu không có
    cookie nào cho kết quả chắc chắn."""
    for ck in get_fb_cookie_pool():
        try:
            r = await _check_with_cookie(uid, ck)
        except Exception:
            continue
        if r.get("status") in ("cookie_invalid", "error"):
            continue
        r["via"] = "cookie_pool"
        return r
    return None


async def check_uid_direct(uid: str) -> dict:
    """Logic check FB truc tiep (HTTP toi Facebook).
    Duoc goi boi fb_worker (process rieng) hoac fallback khi worker chet.
    KHONG goi truc tiep tu handler - dung check_uid() de co timeout cach ly."""
    uid = extract_uid(uid)
    result = {"uid": uid, "alive": False, "avatar_url": None, "ok": False, "status": "unknown", "name": ""}
    if not uid:
        return result

    cookie = db.get_setting("fb_cookie", "")
    token = db.get_setting("fb_avatar_token", "")
    _completed = False   # FIX (D2): chỉ set ok=True khi đã chạy hết nhánh xử lý

    try:
        async with _make_http_client(timeout=15) as client:
            # FIX (D2): TRƯỚC ĐÂY set result["ok"] = True ngay tại đây (trước khi làm gì)
            # nên mọi lỗi httpx/proxy/DNS bị nuốt ở except bên dưới vẫn trả ok=True với
            # status "unknown" -> tầng poller hiểu là đã check xong và quy thành DIE.
            result["avatar_url"] = avatar_url(uid)
            
            if cookie:
                # 🔑 CÓ COOKIE => CHECK CHUẨN XÁC 100%: phân biệt LIVE vs CHECKPOINT vs DIE
                ck_result = await _check_with_cookie(uid, cookie)
                if ck_result["status"] == "cookie_invalid":
                    # Cookie đơn hết hạn => thử xoay sang cookie dự phòng trong pool
                    pool_res = await _check_with_cookie_pool(uid)
                    if pool_res:
                        result["alive"] = pool_res["alive"]
                        result["status"] = pool_res["status"]
                        result["name"] = pool_res.get("name", "")
                        result["via"] = "cookie_pool"
                        if pool_res.get("has_real_avatar"):
                            result["has_real_avatar"] = True
                    else:
                        # Không còn cookie nào dùng được => fallback sang check avatar:
                        # LUẬT MỚI (user 2026-10-08): avatar thật + tìm được UID => 🟢 LIVE
                        try:
                            if await _is_real_avatar(uid, client):
                                result["alive"] = True
                                result["status"] = "live"
                                result["via"] = "avatar_fallback"
                                result["has_real_avatar"] = True
                            else:
                                result["alive"] = False
                                result["status"] = "cookie_invalid"
                        except Exception:
                            result["alive"] = False
                            result["status"] = "cookie_invalid"
                elif ck_result["alive"] is None:
                    # Cookie check thất bại (lỗi mạng / IP bị FB chặn):
                    # Fallback sang check avatar trước khi báo error.
                    # LUẬT MỚI (user 2026-10-08): avatar thật + tìm được UID => 🟢 LIVE
                    try:
                        if await _is_real_avatar(uid, client):
                            result["alive"] = True
                            result["status"] = "live"
                            result["via"] = "avatar_fallback"
                            result["has_real_avatar"] = True
                            result["name"] = ck_result.get("name", "")
                        else:
                            result["alive"] = False
                            result["status"] = "error"
                            result["name"] = ck_result.get("name", "")
                    except Exception:
                        result["alive"] = False
                        result["status"] = "error"
                        result["name"] = ck_result.get("name", "")
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
                    result["name"] = data.get("name", "")
                    result["has_real_avatar"] = await _is_real_avatar(uid, client)
                    # LUẬT MỚI (user 2026-10-08): token tìm được UID + avatar thật => 🟢 LIVE
                    result["status"] = "live" if result["has_real_avatar"] else "exists"
                else:
                    err = data.get("error", {})
                    err_type = err.get("type", "")
                    if err_type == "GraphMethodException":
                        result["alive"] = False
                        result["status"] = "dead"
                    else:
                        # Graph API lỗi không xác định: không đoán từ avatar
                        # (avatar thật không chứng minh được acc còn sống).
                        result["alive"] = False
                        result["status"] = "error"
            else:
                # Không có cookie/token => thử cookie pool trước (chính xác nhất)
                pool_res = await _check_with_cookie_pool(uid)
                if pool_res:
                    result["alive"] = pool_res["alive"]
                    result["status"] = pool_res["status"]
                    result["name"] = pool_res.get("name", "")
                    result["via"] = "cookie_pool"
                    return result
                # Không có cookie nào => check qua Graph API (is_silhouette) + tên profile
                has_real_avatar = await _is_real_avatar(uid, client)
                result["has_real_avatar"] = has_real_avatar

                # Cố gắng lấy tên profile từ page title bằng urllib (vì httpx dễ bị FB block)
                # (lấy tên TRƯỚC để đánh giá acc có tồn tại hay không)
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

                if has_real_avatar:
                    result["alive"] = True
                    result["status"] = "live"
                elif result.get("name"):
                    # Acc tồn tại (đọc được tên thật) nhưng đang dùng ảnh đại diện mặc định
                    result["alive"] = True
                    result["status"] = "live"
                else:
                    # FB chặn đọc tên (login wall) -> nhờ traodoisub.com làm "trọng tài":
                    # nếu nó resolve được UID + tên => acc tồn tại.
                    # LƯU Ý: lỗi hạ tầng (timeout/mạng) trả về status "error",
                    # TUYỆT ĐỐI không đánh "dead" để tránh loại nhầm acc còn sống.
                    try:
                        t_alive, t_name = await _traodoisub_alive(uid, client)
                    except Exception:
                        t_alive, t_name = None, ""
                    if t_alive is True:
                        result["alive"] = True
                        result["status"] = "live"
                        result["via"] = "traodoisub"
                        if t_name:
                            result["name"] = t_name
                    elif t_alive is False:
                        # traodoisub không resolve được: KHÔNG đánh "dead".
                        # Theo quyết định user 2026-09-26: không báo DIE khi không
                        # đọc được tên (tránh đánh DIE oan acc còn sống).
                        # Báo "exists" để hiển thị 🟡 TỒN TẠI (Chưa rõ trạng thái).
                        result["alive"] = False
                        result["status"] = "exists"
                    else:
                        result["alive"] = False
                        result["status"] = "error"
            _completed = True

    except Exception as e:
        # FIX (D2): trước đây `pass` nuốt lỗi, không log và không hạ ok -> lỗi hạ tầng
        # biến thành "acc die". Nay log rõ và trả status "error" (chưa kết luận).
        result["ok"] = False
        result["alive"] = False
        result["status"] = "error"
        log.warning("check_uid_direct loi %s: %s", uid, e, exc_info=True)

    if _completed:
        result["ok"] = True
    return result


# ================= WORKER CHECK-LIVE RIENG (muc 7) =================
# Bot goi worker qua HTTP thay vi check truc tiep, de khi Facebook treo/chan IP
# thi chi worker bi ket, bot ban hang van phan hoi binh thuong.
FB_WORKER_URL = "http://127.0.0.1:8001"
FB_WORKER_TIMEOUT = 30.0  # giay cho 1 UID


def _worker_available() -> bool:
    """Kiem tra nhanh worker co song khong (khong nem exception)."""
    import socket
    try:
        s = socket.create_connection(("127.0.0.1", 8001), timeout=2)
        s.close()
        return True
    except Exception:
        return False


async def check_uid_via_worker(uid: str) -> dict | None:
    """Gui check sang worker. Tra None neu worker chet/timeout (de fallback)."""
    uid = extract_uid(uid)
    if not uid:
        return {"uid": uid, "alive": False, "avatar_url": None,
                "ok": False, "status": "unknown", "name": ""}
    try:
        async with _make_http_client(timeout=FB_WORKER_TIMEOUT) as client:
            r = await client.post(f"{FB_WORKER_URL}/check", json={"uid": uid})
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and data.get("uid"):
                return data
    except Exception:
        pass
    return None


async def check_uid(uid: str) -> dict:
    """Diem vao duy nhat cho moi handler: uu tien worker rieng,
    fallback check truc tiep (timeout ngan) khi worker chet,
    fail-closed ve "error" khi ca hai deu hong."""
    uid = extract_uid(uid)
    if not uid:
        return {"uid": uid, "alive": False, "avatar_url": None,
                "ok": False, "status": "unknown", "name": ""}
    # 1. Thu worker rieng (cach ly hoan toan khoi event loop bot)
    res = await check_uid_via_worker(uid)
    if res is not None:
        return res
    # 2. Worker chet -> fallback truc tiep de bot khong liet tinh nang check.
    #    Neu ca truc tiep cung hong -> check_uid_direct tu tra "error".
    try:
        return await asyncio.wait_for(check_uid_direct(uid), timeout=20.0)
    except Exception:
        return {"uid": uid, "alive": False, "avatar_url": None,
                "ok": False, "status": "error", "name": "",
                "via": "direct_fallback_failed"}


FB_POST_STATS_TIMEOUT = 180.0  # giay cho 1 link (worker mo trinh duyet, co the thu lai khi cookie chet)


def _blank_post_stats(url: str, status: str = "error") -> dict:
    return {
        "ok": False, "status": status, "post_id": "", "post_url": (url or "").strip(),
        "author": "", "desc": "", "cover": None,
        "likes": 0, "comments": 0, "shares": 0, "views": 0, "via": "",
    }


async def fetch_post_stats_via_worker(url: str) -> dict | None:
    """Goi worker /post_stats. Tra None neu worker chet/timeout (de fallback)."""
    url = (url or "").strip()
    if not url or "facebook.com" not in url.lower():
        return _blank_post_stats(url, "unavailable")
    try:
        async with _make_http_client(timeout=FB_POST_STATS_TIMEOUT) as client:
            r = await client.post(f"{FB_WORKER_URL}/post_stats", json={"url": url})
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and data.get("status"):
                return data
    except Exception:
        pass
    return None


async def fetch_post_stats(url: str) -> dict:
    """Diem vao duy nhat de check so lieu 1 bai viet/reel FB.

    1. Thu Graph API (fetch_fb_post_info): nhanh, chinh xac khi co token
       va link trich duoc post_id.
    2. That bai -> nho worker mo trinh duyet doc so lieu cong khai
       (khong can dang nhap, ho tro ca link share).
    3. Worker chet -> tra ok=False (khong doan, khong bia so lieu).
    """
    url = (url or "").strip()
    if not url or "facebook.com" not in url.lower():
        return _blank_post_stats(url, "unavailable")
    # 1. Graph API truoc
    try:
        info = await fetch_fb_post_info(url)
        if info and info.get("ok"):
            info["views"] = 0
            info["via"] = "graph_api"
            return info
    except Exception:
        pass
    # 2. Worker Playwright (doc cong khai, ho tro link share)
    res = await fetch_post_stats_via_worker(url)
    if res is not None:
        return res
    # 3. Ca hai deu hong
    out = _blank_post_stats(url, "error")
    out["via"] = "worker_unavailable"
    return out


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
        elif status == "error":
            status_icon = "⚠️"
            status_text = "Không thể xác định (Lỗi mạng)"
            note = "🌐 <i>Không kết nối được Facebook lúc này, vui lòng thử lại sau. Không kết luận acc live/die từ lỗi mạng.</i>"
        else:
            status_icon = "🟢" if res["alive"] else "🔴"
            status_text = "TỒN TẠI" if res["alive"] else "DIE (Không tồn tại)"
            note = ""
    else:
        # Không có cookie => dùng ảnh avatar + tên profile để xác định
        # LƯU Ý: khi chỉ biết acc TỒN TẠI (status="exists") mà không xác định
        # được trạng thái hoạt động (do FB chặn IP server), phải báo "TỒN TẠI"
        # chứ không được báo "LIVE" để tránh hiểu nhầm.
        if status == "exists":
            status_icon = "🟡"
            status_text = "TỒN TẠI (Chưa rõ trạng thái)"
            note = "⚠️ <i>Chỉ xác định được acc tồn tại, chưa kiểm tra được có bị khoá/checkpoint hay không (FB chặn IP server)</i>"
        elif status == "error":
            status_icon = "⚠️"
            status_text = "Không thể xác định (Lỗi mạng)"
            note = "🌐 <i>Không kết nối được Facebook lúc này, vui lòng thử lại sau. Không kết luận acc live/die từ lỗi mạng.</i>"
        elif res["alive"] or status == "live":
            status_icon = "🟢"
            if res.get("has_real_avatar"):
                status_text = "LIVE (Tài khoản đang hoạt động)"
                note = "📸 <i>Xác định qua Avatar - Có ảnh thật trên Facebook</i>"
            elif res.get("via") == "traodoisub":
                status_text = "LIVE (Tài khoản tồn tại)"
                note = "🔎 <i>Xác định qua traodoisub.com - Acc tồn tại</i>"
            else:
                status_text = "LIVE (Tài khoản tồn tại)"
                note = "ℹ️ <i>Acc đang dùng ảnh đại diện mặc định</i>"
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


# ─── Theo dõi bài viết Facebook (post tracker) ────────────────────────────────
# Được gọi bởi poller._check_fb_posts() và handlers/check.py:process_fb_post_check().
# fetch_fb_post_info KHÔNG bao giờ raise và LUÔN trả đủ key (ok/status/post_id/
# post_url/author/desc/cover/likes/comments/shares) để poller đọc trực tiếp
# (info["likes"], info["comments"], info["shares"]) mà không bị KeyError.

_FB_POST_ID_PATTERNS = (
    r"/posts/([A-Za-z0-9._-]+)",
    r"/reel/(\d+)",
    r"/reels/(\d+)",
    r"/videos/(\d+)",
    r"/watch/\?v=(\d+)",
    r"[?&]story_fbid=([A-Za-z0-9._-]+)",
    r"[?&]fbid=([A-Za-z0-9._-]+)",
    r"(pfbid[A-Za-z0-9]+)",
)


def parse_fb_post_id(post_url: str) -> str:
    """Trích ID bài viết Facebook từ link. Trả "" nếu không nhận diện được.

    Hỗ trợ: /posts/<id>, /permalink.php?story_fbid=<id>, /photo.php?fbid=<id>,
    /videos/<id>, story_fbid=..., pfbid...
    """
    url = (post_url or "").strip()
    if not url:
        return ""
    for pat in _FB_POST_ID_PATTERNS:
        m = re.search(pat, url, re.IGNORECASE)
        if m:
            return m.group(1).strip("?&#/")
    return ""


def _fb_count(value) -> int:
    """Ép số liệu Graph API về int; thiếu/None/kiểu lạ -> 0 (KHÔNG bịa số)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _fb_stat(value) -> str:
    """Hiển thị số liệu: None (không đọc được) -> '—', số -> định dạng ngàn."""
    if value is None:
        return "—"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "—"


async def fetch_fb_post_info(post_url: str) -> dict:
    """Lấy thông tin bài viết Facebook qua Graph API (token fb_avatar_token).

    Luôn trả dict đủ key, KHÔNG raise:
      ok (bool), status ("ok"/"error"/"unavailable"), post_id, post_url,
      author, desc, cover, likes, comments, shares.
    Không có token hoặc Graph trả lỗi -> ok=False, status="error", số liệu = 0
    (không đoán, không bịa số liệu).
    """
    post_url = (post_url or "").strip()
    post_id = parse_fb_post_id(post_url)
    result = {
        "ok": False,
        "status": "error",
        "post_id": post_id,
        "post_url": post_url,
        "author": "",
        "desc": "",
        "cover": None,
        "likes": 0,
        "comments": 0,
        "shares": 0,
    }
    if not post_id:
        # Link không nhận diện được bài viết -> không phải lỗi hạ tầng
        result["status"] = "unavailable"
        return result
    if not result["post_url"]:
        result["post_url"] = f"https://www.facebook.com/{post_id}"

    token = db.get_setting("fb_avatar_token", "")
    if not token:
        log.warning("fetch_fb_post_info: chua cau hinh fb_avatar_token, bo qua %s", post_url)
        return result

    try:
        async with _make_http_client(timeout=20) as client:
            r = await client.get(
                f"{config.FB_GRAPH}/{post_id}",
                params={
                    "fields": "id,message,shares,likes.summary(true),"
                              "comments.summary(true),full_picture,from",
                    "access_token": token,
                },
            )
            data = r.json() if r.text else {}
            if not isinstance(data, dict) or data.get("error") or not data.get("id"):
                log.warning("fetch_fb_post_info: Graph tra loi loi cho %s: %s",
                            post_url, str(data)[:300])
                return result

            likes = ((data.get("likes") or {}).get("summary") or {})
            comments = ((data.get("comments") or {}).get("summary") or {})
            shares = (data.get("shares") or {})
            result["ok"] = True
            result["status"] = "ok"
            # Giữ post_id trích từ link (không thay bằng id page-scoped của Graph)
            # để log/caption luôn khớp với bài viết khách đã gửi.
            result["desc"] = data.get("message") or ""
            result["cover"] = data.get("full_picture") or None
            result["author"] = ((data.get("from") or {}).get("name") or "")
            result["likes"] = _fb_count(likes.get("total_count"))
            result["comments"] = _fb_count(comments.get("total_count"))
            result["shares"] = _fb_count(shares.get("count"))
    except Exception:
        # Mẫu xử lý lỗi giống check_uid_direct: không raise ra ngoài, chỉ log + trả dict
        log.warning("fetch_fb_post_info loi %s", post_url, exc_info=True)
        result["ok"] = False
        result["status"] = "error"
        result["likes"] = 0
        result["comments"] = 0
        result["shares"] = 0
    return result


def build_fb_post_caption(info: dict) -> str:
    """Dựng caption HTML cho thông báo bài viết FB (poller + handler dùng chung)."""
    info = info or {}
    post_id = str(info.get("post_id") or "")
    link = (info.get("post_url") or "").strip()
    if not link and post_id:
        link = f"https://www.facebook.com/{post_id}"

    if not info.get("ok"):
        # Không hiển thị "DIE" hay số liệu giả khi không lấy được số liệu
        lines = [
            "📊 <b>CẬP NHẬT BÀI VIẾT FACEBOOK</b>",
            "",
            "⚠️ <b>Không lấy được số liệu bài viết (kiểm tra token FB)</b>",
        ]
        if post_id:
            lines.append(f"🆔 Bài viết: <code>{html.escape(post_id)}</code>")
        if link:
            lines.append(f"🔗 <a href=\"{html.escape(link, quote=True)}\">▶ Xem bài viết</a>")
        lines += ["", "🤖 <i>FB Checker V2 by @khaikhai998</i>"]
        return "\n".join(lines)

    desc = " ".join(str(info.get("desc") or "").split())
    if len(desc) > 200:
        desc = desc[:200] + "..."
    author = str(info.get("author") or "").strip() or "Không xác định"

    lines = [
        "📊 <b>CẬP NHẬT BÀI VIẾT FACEBOOK</b>",
        "",
        f"👤 Tác giả: <b>{html.escape(author)}</b>",
    ]
    if post_id:
        lines.append(f"🆔 Bài viết: <code>{html.escape(post_id)}</code>")
    if desc:
        lines.append(f"📝 {html.escape(desc)}")

    lines += [
        "",
        "━━━━ 📈 THỐNG KÊ ━━━━",
        f"❤️ Lượt thích: <b>{_fb_stat(info.get('likes'))}</b>",
        f"💬 Bình luận: <b>{_fb_stat(info.get('comments'))}</b>",
        f"🔁 Chia sẻ: <b>{_fb_stat(info.get('shares'))}</b>",
    ]
    if info.get("views") is not None:
        lines.append(f"👁️ Lượt xem: <b>{_fb_stat(info.get('views'))}</b>")
    if link:
        lines += ["", f"🔗 <a href=\"{html.escape(link, quote=True)}\">▶ Xem bài viết ngay</a>"]
    lines += ["", "🤖 <i>FB Checker V2 by @khaikhai998</i>"]
    return "\n".join(lines)


# ─── Check UID từ file .xlsx với mọi cấu trúc cột ─────────────────────────────
# Ô chứa link Facebook (kể cả link chưa có ID) -> tự resolve thành UID số,
# ô chứa UID số -> giữ nguyên. File kết quả giữ nguyên cấu trúc cột, chỉ thay
# ô link bằng UID và thêm cột "Trạng thái" ở cuối.

_FB_LINK_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.|web\.|mbasic\.)?(?:facebook\.com|fb\.com|fb\.watch)[^\s,;\"'<>()\[\]]*",
    re.IGNORECASE,
)
_UID_NUM_RE = re.compile(r"\d{6,}")


def _cell_text(value) -> str:
    """Chuyển giá trị ô Excel thành text sạch để quét link/UID."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, datetime):
        return ""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    if isinstance(value, int):
        return str(value)
    return str(value).strip()


def _cell_link(cell) -> str:
    """Lấy link FB trong ô (kể cả hyperlink ẩn sau text hiển thị)."""
    m = _FB_LINK_RE.search(_cell_text(cell.value))
    if m:
        return m.group(0)
    try:
        hl = cell.hyperlink.target if cell.hyperlink else ""
    except Exception:
        hl = ""
    if hl:
        m2 = _FB_LINK_RE.search(str(hl))
        if m2:
            return m2.group(0)
    return ""


async def extract_uids_from_xlsx(content: bytes, max_uids: int = 1000,
                                 resolve_concurrency: int = 8) -> dict:
    """
    Đọc file .xlsx với mọi cấu trúc cột:
      - ô chứa link FB -> tự resolve thành UID (qua resolve_fb_uid)
      - ô chứa UID số -> lấy trực tiếp
    Trả về dict:
      "sheets":           [{"name": str, "rows": [[giá trị ô...]], "ncols": int}]
      "uids":             [uid...]  # UID duy nhất, theo thứ tự xuất hiện
      "cell_uid":         {(sheet_idx, row_idx, col_idx): uid}
      "link_cells":       {(sheet_idx, row_idx, col_idx)}  # ô link đã resolve được
      "unresolved_cells": {(sheet_idx, row_idx, col_idx)}  # ô link KHÔNG resolve được
      "unresolved":       [link...]  # link FB không resolve ra UID được
    """
    from io import BytesIO
    import openpyxl

    wb = openpyxl.load_workbook(BytesIO(content), data_only=True)
    sheets = []
    # Đọc trước tất cả các dòng để nhận diện header: các cột đã map sang
    # field (mk/2fa/cookie/token/...) thì KHÔNG quét UID số trong đó
    # (tránh nhầm dãy số trong mã 2fa, cookie... thành UID).
    raw_sheets = []
    for ws in wb.worksheets:
        all_rows = [list(row) for row in ws.iter_rows()]
        raw_sheets.append((ws, all_rows))
    link_jobs = []       # [((si, r, c), link)]
    raw_candidates = []  # [((si, r, c), uid_text)]
    try:
        for si, (ws, ws_rows) in enumerate(raw_sheets):
            rows, ncols = [], 0
            skip_cols = (set(_detect_field_columns(
                [c.value for c in ws_rows[0]]).values())
                if ws_rows else set())
            for r, row in enumerate(ws_rows):
                vals = [cell.value for cell in row]
                rows.append(vals)
                ncols = max(ncols, len(vals))
                for c, cell in enumerate(row):
                    link = _cell_link(cell)
                    if link:
                        link_jobs.append(((si, r, c), link))
                        continue
                    if c in skip_cols:
                        continue  # cột mk/2fa/cookie...: bỏ qua dãy số
                    txt = _cell_text(cell.value)
                    if not txt:
                        continue
                    m = _UID_NUM_RE.search(txt)
                    if m:
                        raw_candidates.append(((si, r, c), m.group(0)))
            sheets.append({"name": (ws.title or f"Sheet{si+1}")[:31],
                           "rows": rows, "ncols": ncols})
    finally:
        wb.close()

    # Resolve các link khác nhau (mỗi link chỉ resolve 1 lần)
    uniq_links = list(dict.fromkeys(link for _, link in link_jobs))
    sem = asyncio.Semaphore(resolve_concurrency)

    async def _one(link: str):
        async with sem:
            try:
                uid, _name, _method = await resolve_fb_uid(link)
                return link, (uid or "")
            except Exception:
                return link, ""

    link_uid = dict(await asyncio.gather(*[_one(l) for l in uniq_links]))

    # key -> ("link", uid|None) hoặc ("raw", uid); link ưu tiên hơn số thuần
    cell_kind: dict = {}
    for key, link in link_jobs:
        cell_kind[key] = ("link", link_uid.get(link) or "")
    for key, num in raw_candidates:
        if key not in cell_kind:
            cell_kind[key] = ("raw", num)

    uids, seen = [], set()
    cell_uid, link_cells, unresolved_cells = {}, set(), set()
    for key in sorted(cell_kind.keys()):
        kind, val = cell_kind[key]
        if kind == "link":
            if val:
                link_cells.add(key)
                cell_uid[key] = val
            else:
                unresolved_cells.add(key)
                continue
        else:
            cell_uid[key] = val
        uid = cell_uid[key]
        if uid not in seen and len(uids) < max_uids:
            seen.add(uid)
            uids.append(uid)

    return {
        "sheets": sheets,
        "uids": uids,
        "cell_uid": cell_uid,
        "link_cells": link_cells,
        "unresolved_cells": unresolved_cells,
        "unresolved": [link for link in uniq_links if not link_uid.get(link)],
    }


def _xlsx_status_text(status: str, name: str = "") -> str:
    st = (status or "").lower()
    if st == "live":
        s = "🟢 LIVE"
    elif st in ("dead", "die"):
        s = "🔴 DIE"
    elif st.startswith("checkpoint"):
        s = "🟡 CHECKPOINT"
    elif st == "disabled":
        s = "⛔ Vô hiệu hoá"
    elif st == "cookie_invalid":
        s = "⚠️ Cookie hết hạn"
    elif st == "exists":
        s = "🟢 Tồn tại"
    elif st in ("error", "unknown", ""):
        s = "⚠️ Lỗi check"
    else:
        s = f"⚠️ {st}"
    return f"{s} - {name}" if name else s


XLSX_FIXED_HEADERS = ["uid", "mk", "tình trạng", "gmail", "mail thay",
                      "2fa", "Ghi chú", "Cookie", "Token"]


def _detect_field_columns(header_row) -> dict:
    """Nhận diện cột theo tên header -> {field: col_index}.

    Không phân biệt hoa/thường; chịu được header rút gọn
    (vd: 'mk', 'pass', 'mật khẩu' đều -> mk).
    """
    mapping = {}
    for c, v in enumerate(header_row):
        h = str(v or "").strip().lower()
        if not h:
            continue
        if "2fa" in h:
            mapping.setdefault("2fa", c)
        elif "cookie" in h:
            mapping.setdefault("Cookie", c)
        elif "token" in h:
            mapping.setdefault("Token", c)
        elif ("mail" in h or "email" in h) and "thay" in h:
            mapping.setdefault("mail thay", c)
        elif "ghi ch" in h or h == "note" or "chú thích" in h or "chu thich" in h:
            mapping.setdefault("Ghi chú", c)
        elif h in ("mk", "pass") or "mật khẩu" in h or "mat khau" in h \
                or "password" in h or "passwd" in h:
            mapping.setdefault("mk", c)
        elif "gmail" in h or h in ("mail", "email"):
            mapping.setdefault("gmail", c)
    return mapping


def build_xlsx_result(parsed: dict, check_results: dict) -> bytes:
    """Dựng file .xlsx kết quả với cấu trúc cột cố định:
      uid | mk | tình trạng | gmail | mail thay | 2fa | Ghi chú | Cookie | Token
    - uid: link FB được resolve ra UID số; ô UID số thuần giữ nguyên
    - tình trạng: kết quả check (LIVE / DIE / CHECKPOINT...)
    - các cột còn lại: lấy từ file gốc theo tên header (nhận diện linh hoạt),
      cột nào file gốc không có thì để trống.
    parsed: dict từ extract_uids_from_xlsx
    check_results: {uid: {"status": str, "name": str}}
    """
    from io import BytesIO
    from openpyxl import Workbook

    wb = Workbook()
    first = True
    cell_uid = parsed.get("cell_uid", {})
    unresolved_cells = parsed.get("unresolved_cells", set())

    for si, sh in enumerate(parsed.get("sheets", [])):
        rows = sh.get("rows") or []
        if not rows:
            continue
        ws = wb.active if first else wb.create_sheet(title=sh["name"])
        if first:
            ws.title = sh["name"]
            first = False
        field_cols = _detect_field_columns(rows[0])
        # Dòng 0 là header nếu: nhận diện được tên cột, hoặc không chứa UID/link
        # nào (file không header thì dòng 0 đã có UID/link như các dòng data).
        row0_has_uid = any(cell_uid.get((si, 0, c))
                           for c in range(len(rows[0])))
        has_header = bool(field_cols) or not row0_has_uid
        data_start = 1 if has_header else 0
        if not has_header:
            field_cols = {}
        ws.append(XLSX_FIXED_HEADERS)
        for r in range(data_start, len(rows)):
            vals = rows[r] or []
            if not any(str(v or "").strip() for v in vals):
                continue  # bỏ dòng trống
            uid = ""
            for c in range(len(vals)):
                u = cell_uid.get((si, r, c))
                if u:
                    uid = u
                    break
            row_out = {}
            for field, c in field_cols.items():
                row_out[field] = (str(vals[c]).strip()
                                  if c < len(vals) and vals[c] is not None
                                  else "")
            if uid:
                rr = check_results.get(uid) or {}
                row_out["tình trạng"] = _xlsx_status_text(rr.get("status", ""),
                                                          rr.get("name", ""))
            elif any((si, r, c) in unresolved_cells
                     for c in range(len(vals))):
                row_out["tình trạng"] = "⚠️ Không lấy được UID"
            else:
                row_out["tình trạng"] = ""
            row_out["uid"] = uid
            ws.append([row_out.get(h, "") for h in XLSX_FIXED_HEADERS])

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
