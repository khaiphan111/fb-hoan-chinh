"""FB check worker - process RIENG biet khoi bot chinh.

Ly do tach: khi Facebook chan IP egress hoac request treo, check_uid co the
ngam het timeout (15s) hang loat -> lam nghen event loop cua bot, bot ban hang
khong phan hoi. Worker chay tren 127.0.0.1:8001, bot goi qua HTTP voi timeout;
worker chet/treo thi bot fail-closed ve status "error" (bo qua, cho lan sau).

Chay bang: bash ~/workspace/fb-hoan-chinh/fb_check_worker.sh
Giam sat: cron fb-check-worker-keepalive (moi phut)
"""
import asyncio
import logging
import re

from fastapi import FastAPI
from pydantic import BaseModel

from .fb import check_uid_direct

log = logging.getLogger("fb_worker")

app = FastAPI(title="fb-check-worker")


class CheckReq(BaseModel):
    uid: str


class BatchReq(BaseModel):
    uids: list


@app.get("/health")
async def health():
    return {"ok": True, "service": "fb-check-worker"}


@app.post("/check")
async def check_one(req: CheckReq):
    """Check 1 UID, tra ve dict ket qua nhu fb.check_uid."""
    return await check_uid_direct(req.uid)


@app.post("/check_batch")
async def check_batch(req: BatchReq):
    """Check nhieu UID (toi da 50/lan), dong thoi toi da 10."""
    uids = [str(u) for u in (req.uids or [])][:50]
    sem = asyncio.Semaphore(10)

    async def _one(uid):
        async with sem:
            try:
                return await check_uid_direct(uid)
            except Exception as e:
                return {"uid": uid, "alive": False, "status": "error",
                        "name": "", "ok": False, "via": "worker_exception",
                        "error": str(e)[:200]}

    results = await asyncio.gather(*[_one(u) for u in uids])
    return {"results": results}


class PostStatsReq(BaseModel):
    url: str


def _parse_int(text: str) -> int:
    """'1,234' / '1.2K' / '1,4K' (dấu phẩy = thập phân kiểu Việt) -> int.
    Hậu tố K/M/B phải đứng riêng (không phải ký tự đầu của từ tiếp theo,
    vd 'b' trong '842 bình luận' không phải là B = tỷ).
    Không parse được -> 0 (không bịa)."""
    if not text:
        return 0
    t = str(text).strip().replace("\u00a0", " ")
    # Ưu tiên match số ở đầu chuỗi, hậu tố K/M/B có word boundary
    m = re.match(r"^([\d.,]+)(?:\s*([KMB])(?![a-zà-ỹ]))?", t, re.IGNORECASE)
    if not m or not m.group(1).strip(".,"):
        return 0
    num_s = m.group(1)
    suffix = (m.group(2) or "").upper()
    if suffix:
        # Có K/M/B: dấu phẩy là thập phân kiểu Việt ("1,4K" = 1400),
        # dấu chấm là thập phân kiểu Anh ("1.2K" = 1200)
        num_s = num_s.replace(",", ".")
    else:
        # Không hậu tố: cả "," và "." đều là phân tách nghìn
        # ("1,234" = "8.990" kiểu Việt = 8990)
        num_s = num_s.replace(",", "").replace(".", "")
    try:
        val = float(num_s)
    except ValueError:
        return 0
    mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    return int(val * mult.get(suffix, 1))


def _post_id_from_url(url: str) -> str:
    """Trích ID bài viết/reel từ URL đã resolve. Không có -> ''."""
    url = url or ""
    for pat in [r"/reel/(\d+)", r"/reels/(\d+)", r"/videos/(\d+)",
                r"/posts/(\d+)", r"[?&]v=(\d+)", r"story_fbid=(\d+)",
                r"fbid=(\d+)", r"pfbid=([A-Za-z0-9]+)"]:
        m = re.search(pat, url, re.IGNORECASE)
        if m:
            return m.group(1)
    return ""


async def fetch_post_stats_playwright(url: str) -> dict:
    """Đọc số liệu công khai của 1 bài viết/reel/video FB bằng Playwright.

    Không đăng nhập, chỉ đọc những gì trang hiển thị công khai.
    KHÔNG raise — luôn trả dict đủ key:
      ok, status ("ok"/"error"/"unavailable"), post_id, post_url (đã resolve),
      author, desc, likes (tổng cảm xúc), comments, shares, views (0 nếu không thấy),
      via ("playwright_public").
    Không đọc được số liệu -> ok=False, số liệu = 0 (không đoán, không bịa).
    """
    from playwright.async_api import async_playwright

    url = (url or "").strip()
    result = {
        "ok": False, "status": "error", "post_id": "", "post_url": url,
        "author": "", "desc": "", "likes": None, "comments": None,
        "shares": None, "views": None, "via": "playwright_public",
    }
    if not url or "facebook.com" not in url.lower():
        result["status"] = "unavailable"
        return result

    apw = browser = ctx = None
    try:
        apw = await async_playwright().start()
        launch_kw = {}
        try:
            from . import egress_proxy
            if egress_proxy.ensure_running():
                launch_kw["proxy"] = {"server": egress_proxy.proxy_server()}
        except Exception:
            pass
        # KHÔNG dùng cookie FB của shop: chỉ đọc dữ liệu công khai (đúng phạm vi
        # user đã duyệt). Cookie cũ đã checkpoint, set vào chỉ gây redirect
        # vòng lặp làm hỏng cả lần đọc công khai.
        fb_cookies = []
        browser = await apw.chromium.launch(headless=True, **launch_kw)
        ctx = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            ignore_https_errors=True,
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"),
            locale="vi-VN",
        )
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            # Timeout nhưng trang có thể đã redirect (vd về checkpoint) ->
            # không return vội, để logic bên dưới xử lý
            log.warning("post_stats goto (lan 1) cham/timeout %s: %s", url, str(e)[:120])
        await page.wait_for_timeout(5000)

        # Cookie chết -> FB đá về trang checkpoint/login, hoặc trang lỗi hẳn:
        # bỏ hẳn context cũ, tạo context mới không cookie rồi load lại
        # (đọc og:title công khai).
        cur = (page.url or "").lower()
        if "checkpoint" in cur or "/login" in cur or "chrome-error" in cur or cur in ("about:blank", ""):
            log.warning("post_stats: context dau tien hong (url=%s), tao context moi khong cookie",
                        page.url[:80])
            try:
                await ctx.close()
            except Exception:
                pass
            ctx = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                ignore_https_errors=True,
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/126.0.0.0 Safari/537.36"),
                locale="vi-VN",
            )
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                # Timeout vẫn có thể đã load được một phần trang -> tiếp tục đọc
                log.warning("post_stats goto (no-cookie) cham/timeout %s: %s", url, e)
            await page.wait_for_timeout(5000)

        # Đóng hộp thoại yêu cầu đăng nhập (nếu có) để thấy nội dung công khai
        for _ in range(2):
            try:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(600)
            except Exception:
                break
        try:
            close_btn = page.locator('[aria-label="Đóng"], [aria-label="Close"]')
            if await close_btn.count() > 0:
                await close_btn.first.click(timeout=2000)
                await page.wait_for_timeout(800)
        except Exception:
            pass

        resolved = page.url or url
        result["post_url"] = resolved
        result["post_id"] = _post_id_from_url(resolved)

        html = await page.content()

        # 1) Đọc số liệu từ các nút Thích / Bình luận / Chia sẻ (cần cookie
        #    còn hiệu lực thì trang mới render đầy đủ). Không đọc được -> None.
        async def _btn_num(name: str):
            try:
                loc = page.get_by_role("button", name=name)
                n = await loc.count()
                for i in range(min(n, 4)):
                    try:
                        t = (await loc.nth(i).inner_text() or "").strip()
                        val = _parse_int(t)
                        if val:
                            return val
                    except Exception:
                        continue
            except Exception:
                pass
            return None

        likes = await _btn_num("Thích")
        comments = await _btn_num("Bình luận")
        shares = await _btn_num("Chia sẻ")

        # 2) Fallback: meta og:title / og:description (đọc từ HTML, không phụ
        #    thuộc timing của locator)
        #  - Reel: og:title = "110K lượt xem · 869 cảm xúc | <mô tả> | <tác giả>"
        #  - Bài viết thường: og:title = "<tác giả>", og:description = "<mô tả>"
        views = None
        def _og(prop: str) -> str:
            m = re.search(r'<meta[^>]+property="%s"[^>]+content="([^"]{1,400})"' % prop,
                          html, re.IGNORECASE)
            if m:
                return m.group(1).replace("&quot;", '"').replace("&#039;", "'").replace("&amp;", "&").strip()
            m = re.search(r'<meta[^>]+content="([^"]{1,400})"[^>]+property="%s"' % prop,
                          html, re.IGNORECASE)
            if m:
                return m.group(1).replace("&quot;", '"').replace("&#039;", "'").replace("&amp;", "&").strip()
            return ""
        try:
            og_title = _og("og:title")
            og_desc = _og("og:description")
            if og_title:
                if re.search(r"cảm xúc|lượt xem", og_title, re.IGNORECASE):
                    # Dạng reel
                    result["desc"] = og_title[:300]
                    if not result["author"] and "|" in og_title:
                        tail = og_title.rsplit("|", 1)[-1].strip()
                        if tail and len(tail) < 60:
                            result["author"] = tail
                    m = re.search(r"([\d.,]+(?:\s*[KMB](?![a-zà-ỹ]))?)\s*lượt xem", og_title, re.IGNORECASE)
                    if m:
                        views = _parse_int(m.group(1))
                    m = re.search(r"([\d.,]+(?:\s*[KMB](?![a-zà-ỹ]))?)\s*cảm xúc", og_title, re.IGNORECASE)
                    if m and not likes:
                        likes = _parse_int(m.group(1))
                else:
                    # Bài viết thường: og:title là tên tác giả
                    if not result["author"] and len(og_title) < 80:
                        result["author"] = og_title
                    if og_desc:
                        result["desc"] = og_desc[:300]
            elif og_desc:
                result["desc"] = og_desc[:300]
        except Exception:
            pass

        # 2b) Bài viết thường: số liệu nằm trong text HTML
        # ("842 bình luận", "1,4K lượt chia sẻ")
        if not comments:
            m = re.search(r">([\d.,]+(?:\s*[KMB](?![a-zà-ỹ]))?)\s*bình luận<", html, re.IGNORECASE)
            if m:
                comments = _parse_int(m.group(1)) or None
        if not shares:
            m = re.search(r">([\d.,]+(?:\s*[KMB](?![a-zà-ỹ]))?)\s*lượt chia sẻ<", html, re.IGNORECASE)
            if m:
                shares = _parse_int(m.group(1)) or None
        if not likes:
            # reaction_count đầu tiên trong HTML thường là của bài chính
            m = re.search(r'"reaction_count"\s*:\s*(\d+)', html)
            if m:
                likes = int(m.group(1)) or None

        # 3) Fallback cuối: JSON nhúng trong HTML
        if not (likes or comments or shares):
            def _json_int(*keys):
                for k in keys:
                    m = re.search(r'"' + k + r'"\s*:\s*(\d+)', html)
                    if m:
                        return int(m.group(1))
                return None
            likes = likes or _json_int("reaction_count", "like_count")
            comments = comments or _json_int("comment_count")
            shares = shares or _json_int("share_count")
            views = views or _json_int("video_view_count", "play_count")

        result["likes"] = likes
        result["comments"] = comments
        result["shares"] = shares
        result["views"] = views

        # Tác giả dự phòng: link profile đầu tiên (khi og:title không dùng được)
        try:
            if not result["author"]:
                author_loc = page.locator('a[href^="https://www.facebook.com/"]:not([href*="/reel"])')
                n = await author_loc.count()
                for i in range(min(n, 15)):
                    try:
                        t = (await author_loc.nth(i).inner_text()).strip()
                        href = await author_loc.nth(i).get_attribute("href") or ""
                        if t and len(t) < 60 and "/share" not in href and "?" not in href:
                            result["author"] = t
                            break
                    except Exception:
                        continue
        except Exception:
            pass

        if result["post_id"] and (likes or comments or shares or result["author"]):
            result["ok"] = True
            result["status"] = "ok"
        elif result["post_id"]:
            result["status"] = "error"
        else:
            result["status"] = "unavailable"
    except Exception as e:
        log.warning("fetch_post_stats_playwright loi %s: %s", url, e, exc_info=True)
        result["status"] = "error"
    finally:
        # Đóng sạch để không rò Chromium (bài học D1)
        try:
            if ctx is not None:
                await ctx.close()
        except Exception:
            pass
        try:
            if browser is not None:
                await browser.close()
        except Exception:
            pass
        try:
            if apw is not None:
                await apw.stop()
        except Exception:
            pass
    return result


@app.post("/post_stats")
async def post_stats(req: PostStatsReq):
    """Đọc số liệu công khai 1 bài viết/reel FB. Không cần đăng nhập."""
    return await fetch_post_stats_playwright(req.url)
