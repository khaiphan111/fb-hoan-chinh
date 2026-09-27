"""🛍️ Worker đặt đơn buff hộ trên panel tuongtactg.pro (playwright).

- place_buff_order(order) -> {"ok": True, "panel_order_id": ...}
                           | {"ok": False, "error": ...}
- test_panel_login() -> (ok: bool, info: str)
- clear_session(): xóa session đã lưu (dùng khi đổi mật khẩu panel).

KHÔNG đặt đơn thật khi dev/test — chỉ login + đọc form.
Session lưu ở ~/workspace/fb-hoan-chinh/buff_session.json (đã gitignore).
"""

import asyncio
import logging
import os

log = logging.getLogger("buff_worker")

PANEL_BASE = "https://tuongtactg.pro"
PANEL_NEW_ORDER = PANEL_BASE + "/new"
SESSION_PATH = os.path.expanduser("~/workspace/fb-hoan-chinh/buff_session.json")

# platform_key (bot) -> tên hiển thị trên dropdown "Nền tảng" của panel
PLATFORM_LABEL = {
    "tiktok": "TIKTOK",
    "facebook": "Facebook",
    "instagram": "Instagram",
    "youtube": "Youtube",
    "telegram": "Telegram",
    "shopee": "Shoppe",      # panel ghi "Shoppe"
    "threads": "Threads",
}


def _creds():
    from . import db
    user = (db.get_setting("buff_panel_user", "") or "").strip()
    pw = db.get_setting("buff_panel_pass", "") or ""
    return user, pw


def clear_session() -> None:
    try:
        if os.path.exists(SESSION_PATH):
            os.remove(SESSION_PATH)
    except Exception:
        pass


async def _new_context(pw, **kw):
    """Tạo browser context: dùng session cũ nếu còn hiệu lực.

    Dùng proxy local (app.egress_proxy) để vượt proxy egress có auth —
    Chromium không tự gắn Proxy-Authorization được. Bỏ qua lỗi cert vì
    proxy egress MITM TLS (CA đã tin cậy ở tầng hệ thống, bot chạy server).
    """
    from playwright.async_api import async_playwright
    from . import egress_proxy
    apw = await async_playwright().start()
    launch_kw = dict(kw)
    if egress_proxy.ensure_running():
        launch_kw.setdefault("proxy", {"server": egress_proxy.proxy_server()})
    browser = await apw.chromium.launch(headless=True, **launch_kw)
    ctx_kw = {"viewport": {"width": 1280, "height": 900},
              "ignore_https_errors": True}
    if os.path.exists(SESSION_PATH):
        ctx_kw["storage_state"] = SESSION_PATH
    ctx = await browser.new_context(**ctx_kw)
    return apw, browser, ctx


async def _is_logged_in(page) -> bool:
    """Đang ở trang đặt đơn và thấy dropdown Nền tảng -> coi như đã login."""
    try:
        await page.goto(PANEL_NEW_ORDER, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1500)
        # Tìm select/combobox nền tảng theo label
        for sel in ["Nền tảng", "Platform"]:
            try:
                loc = page.get_by_label(sel, exact=False)
                if await loc.count() > 0:
                    return True
            except Exception:
                pass
        html = (await page.content()).lower()
        # Trang login thường có ô password
        if 'type="password"' in html and "nền tảng" not in html:
            return False
        return "nền tảng" in html
    except Exception as e:
        log.warning("buff _is_logged_in lỗi: %s", e)
        return False


async def _do_login(page, user: str, pw: str) -> bool:
    """Điền form đăng nhập hiện tại rồi submit."""
    try:
        # Tìm ô username: input text đầu tiên không phải password/search
        user_filled = False
        for loc in [page.locator('input[name*="user" i]'),
                    page.locator('input[name*="login" i]'),
                    page.locator('input[name*="email" i]'),
                    page.locator('input[type="text"]')]:
            try:
                if await loc.count() > 0:
                    el = loc.first
                    if await el.is_visible():
                        await el.fill(user)
                        user_filled = True
                        break
            except Exception:
                continue
        pw_loc = page.locator('input[type="password"]')
        if await pw_loc.count() == 0 or not user_filled:
            log.error("buff login: không tìm thấy form đăng nhập")
            return False
        await pw_loc.first.fill(pw)
        # Bấm nút submit: ưu tiên nút có chữ đăng nhập/login
        clicked = False
        for btn in [page.get_by_role("button", name="Đăng nhập"),
                    page.get_by_role("button", name="Login"),
                    page.locator('button[type="submit"]')]:
            try:
                if await btn.count() > 0 and await btn.first.is_visible():
                    await btn.first.click()
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            await pw_loc.first.press("Enter")
        await page.wait_for_timeout(2500)
        ok = await _is_logged_in(page)
        if ok:
            try:
                await page.context.storage_state(path=SESSION_PATH)
            except Exception as e:
                log.warning("buff: không lưu được session: %s", e)
        return ok
    except Exception as e:
        log.exception("buff _do_login lỗi: %s", e)
        return False


async def _ensure_login(page) -> tuple:
    """Trả (ok, info)."""
    user, pw = _creds()
    if not user or not pw:
        return False, "chưa cài tài khoản panel (vào /buffadm → ⚙️ Tài khoản panel)"
    if await _is_logged_in(page):
        return True, "session còn hiệu lực"
    ok = await _do_login(page, user, pw)
    if ok:
        return True, f"đăng nhập thành công ({user})"
    return False, "đăng nhập thất bại — kiểm tra lại username/mật khẩu trong /buffadm"


async def test_panel_login() -> tuple:
    apw = browser = None
    try:
        apw, browser, ctx = await _new_context(None)
        page = await ctx.new_page()
        ok, info = await _ensure_login(page)
        await ctx.close()
        return ok, info
    except Exception as e:
        log.exception("buff test_panel_login lỗi")
        return False, f"lỗi kỹ thuật: {e}"
    finally:
        try:
            if browser:
                await browser.close()
        except Exception:
            pass
        try:
            if apw:
                await apw.stop()
        except Exception:
            pass


async def _select_option_by_text(select_loc, text: str) -> bool:
    """Chọn option trong <select> theo text chứa (không phân biệt hoa thường)."""
    try:
        options = await select_loc.locator("option").all()
        for opt in options:
            try:
                t = (await opt.inner_text() or "").strip()
                if text.lower() in t.lower() and t:
                    val = await opt.get_attribute("value")
                    await select_loc.select_option(value=val)
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


async def _find_select(page, label_hint: str):
    """Tìm <select> theo label hoặc thứ tự xuất hiện."""
    try:
        loc = page.get_by_label(label_hint, exact=False)
        if await loc.count() > 0:
            return loc.first
    except Exception:
        pass
    return None


async def place_buff_order(order: dict, dry_run: bool = False) -> dict:
    """Đặt 1 đơn buff trên panel. order: dict từ db.buff_order_get
    (cần service join thêm panel_service_id, platform_key).
    dry_run=True: điền form tới bước cuối nhưng KHÔNG bấm ĐẶT HÀNG
    (dùng để test), trả về tổng tiền panel hiển thị."""
    from . import db
    svc = db.buff_service_get(order["service_id"])
    if not svc:
        return {"ok": False, "error": "không tìm thấy dịch vụ"}
    panel_id = int(svc["panel_service_id"])
    plat_label = PLATFORM_LABEL.get(svc["platform_key"], svc["platform_name"])
    link = order["link"]
    qty = int(order["quantity"])

    apw = browser = None
    try:
        apw, browser, ctx = await _new_context(None)
        page = await ctx.new_page()
        ok, info = await _ensure_login(page)
        if not ok:
            await ctx.close()
            return {"ok": False, "error": info}
        await page.goto(PANEL_NEW_ORDER, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1200)

        # Tìm dropdown theo id (panel dùng sl-platform/sl-category/sl-service;
        # còn có sl-quick-search, sl-loop-* nên không được lấy theo index)
        async def _sel_by_id(sid):
            loc = page.locator(f"select#{sid}")
            return loc.first if await loc.count() > 0 else None

        sel_platform = await _sel_by_id("sl-platform")
        sel_category = await _sel_by_id("sl-category")
        sel_service = await _sel_by_id("sl-service")
        if not (sel_platform and sel_category and sel_service):
            return {"ok": False,
                    "error": "form đặt đơn lạ (không thấy đủ 3 dropdown platform/category/service)"}
        # B2: Nền tảng
        if not await _select_option_by_text(sel_platform, plat_label):
            return {"ok": False, "error": f"không thấy nền tảng '{plat_label}'"}
        await page.wait_for_timeout(1200)
        # Phân loại + Dịch vụ: panel để ID gói ở value của option dịch vụ
        # (text không có ID). Duyệt từng phân loại tới khi thấy value == panel_id.
        svc_ok = False
        cat_opts = []
        for opt in await sel_category.locator("option").all():
            try:
                t = (await opt.inner_text() or "").strip()
                v = await opt.get_attribute("value")
                if t and v and t.lower() not in ("chọn", "select", "--", "phân loại"):
                    cat_opts.append((t, v))
            except Exception:
                continue
        for _t, _v in cat_opts:
            try:
                await sel_category.select_option(value=_v)
            except Exception:
                continue
            await page.wait_for_timeout(1000)
            for sopt in await sel_service.locator("option").all():
                try:
                    sv = await sopt.get_attribute("value")
                    if sv and sv.strip() == str(panel_id):
                        await sel_service.select_option(value=sv)
                        svc_ok = True
                        break
                except Exception:
                    continue
            if svc_ok:
                break
        if not svc_ok:
            return {"ok": False,
                    "error": f"không thấy gói panel ID {panel_id}"}
        await page.wait_for_timeout(800)

        # B6: link — panel dùng input#ipt-link
        link_ok = False
        for loc in [page.locator("input#ipt-link"),
                    page.get_by_label("Link", exact=False),
                    page.locator('input[type="url"]')]:
            try:
                if await loc.count() > 0 and await loc.first.is_visible():
                    await loc.first.fill(link)
                    link_ok = True
                    break
            except Exception:
                continue
        if not link_ok:
            # fallback: input text dài nhất còn trống
            try:
                for inp in await page.locator('input[type="text"]').all():
                    try:
                        if await inp.is_visible() and not (await inp.input_value()):
                            await inp.fill(link)
                            link_ok = True
                            break
                    except Exception:
                        continue
            except Exception:
                pass
        if not link_ok:
            return {"ok": False, "error": "không điền được link"}

        # B7: số lượng — panel dùng input#ipt-quantity (type=text)
        qty_ok = False
        for loc in [page.locator("input#ipt-quantity"),
                    page.locator('input[type="number"]')]:
            try:
                if await loc.count() > 0 and await loc.first.is_visible():
                    await loc.first.fill(str(qty))
                    qty_ok = True
                    break
            except Exception:
                continue
        if not qty_ok:
            return {"ok": False, "error": "không điền được số lượng"}
        await page.wait_for_timeout(500)

        if dry_run:
            # Đọc tổng tiền panel hiển thị, không bấm đặt
            total_txt = ""
            try:
                body = (await page.locator("body").inner_text())[:3000]
                import re
                m = re.search(r"(tổng[^₫\n]{0,40}₫|total[^$\n]{0,40})", body, re.I)
                total_txt = m.group(1).strip() if m else body[-200:]
            except Exception:
                pass
            await ctx.close()
            return {"ok": True, "dry_run": True, "panel_total": total_txt}

        # B9: bấm ĐẶT HÀNG
        btn_ok = False
        btn_selectors = [
            page.get_by_role("button", name="ĐẶT HÀNG"),
            page.get_by_role("button", name="Đặt hàng"),
            page.locator('button[type="submit"]'),
            page.locator('input[type="submit"]'),
            page.locator('button:has-text("ĐẶT HÀNG")'),
            page.locator('.btn:has-text("ĐẶT HÀNG")'),
        ]
        for btn in btn_selectors:
            try:
                if await btn.count() > 0:
                    el = btn.first
                    # Cuộn tới nút trước khi bấm
                    try:
                        await el.scroll_into_view_if_needed(timeout=5000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(500)
                    if await el.is_visible():
                        # Thử click thường, fallback sang JS click
                        try:
                            await el.click(timeout=5000)
                        except Exception:
                            await el.evaluate("el => el.click()")
                        btn_ok = True
                        break
            except Exception:
                continue
        if not btn_ok:
            return {"ok": False, "error": "không bấm được nút ĐẶT HÀNG"}
        # Chờ thông báo kết quả (thành công/lỗi) xuất hiện, tối đa 30s.
        # Không chờ cứng 3s như trước vì panel chậm sẽ bị đánh failed oan
        # trong khi đơn thực tế đã được tạo.
        try:
            await page.wait_for_selector(
                ".alert-success, .alert-danger, .toast-success, .toast-error, "
                "[role='alert'], .swal2-popup",
                timeout=30000,
            )
            await page.wait_for_timeout(1000)  # chờ thông báo render xong
        except Exception:
            # Timeout: panel chưa hiện thông báo, đọc HTML như cũ
            await page.wait_for_timeout(2000)

        # Đọc kết quả: tìm mã đơn hoặc thông báo lỗi trên trang
        html = await page.content()
        low = html.lower()
        if any(k in low for k in ["thành công", "success", "đặt hàng thành công"]):
            # Cố trích mã đơn panel (dãy số gần chữ "mã đơn"/"order")
            import re
            m = re.search(r"(?:mã đơn|order)[^\d]{0,20}(\d{4,})", html, re.I)
            panel_oid = m.group(1) if m else "unknown"
            return {"ok": True, "panel_order_id": panel_oid}
        # Tìm thông báo lỗi hiển thị
        err = "panel báo lỗi (không rõ)"
        try:
            for sel in [".alert-danger", ".toast-error", "[role='alert']"]:
                el = page.locator(sel)
                if await el.count() > 0:
                    t = (await el.first.inner_text() or "").strip()
                    if t:
                        err = t[:200]
                        break
        except Exception:
            pass
        return {"ok": False, "error": err}
    except Exception as e:
        log.exception("buff place_buff_order lỗi")
        return {"ok": False, "error": f"lỗi kỹ thuật: {e}"}
    finally:
        try:
            if browser:
                await browser.close()
        except Exception:
            pass
        try:
            if apw:
                await apw.stop()
        except Exception:
            pass


# Chạy test tay: python -m app.buff_worker (KHÔNG đặt đơn thật)
if __name__ == "__main__":
    async def _main():
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        ok, info = await test_panel_login()
        print(("OK: " if ok else "FAIL: ") + info)
    asyncio.run(_main())
