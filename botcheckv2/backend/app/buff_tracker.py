"""📊 Theo dõi tiến độ đơn buff tự động.

- Quét đơn đang chạy (status='running') định kỳ
- Đọc tiến độ từ panel qua Playwright
- Cập nhật done_quantity vào DB
- Báo user khi có tiến triển / khi hoàn thành
- Dừng theo dõi khi đơn xong/thất bại/hủy

Chạy qua poller job nền.
"""

import asyncio
import logging
import time

log = logging.getLogger("buff_tracker")

# Trạng thái panel -> trạng thái bot
PANEL_STATUS_MAP = {
    "completed": "completed",
    "hoàn thành": "completed",
    "done": "completed",
    "in progress": "running",
    "đang chạy": "running",
    "running": "running",
    "pending": "pending",
    "chờ": "pending",
    "canceled": "cancelled",
    "cancelled": "cancelled",
    "đã hủy": "cancelled",
    "failed": "failed",
    "thất bại": "failed",
    "partial": "partial",
    "một phần": "partial",
}


async def get_panel_order_progress(panel_order_id: str) -> dict:
    """Đọc tiến độ 1 đơn từ panel. Trả về:
    {"ok": True, "done": int, "total": int, "status": str}
    {"ok": False, "error": str}
    """
    from . import buff_worker
    from . import db

    if not panel_order_id:
        return {"ok": False, "error": "thiếu mã panel"}

    apw = browser = None
    try:
        apw, browser, ctx = await buff_worker._new_context(None)
        page = await ctx.new_page()
        ok, info = await buff_worker._ensure_login(page)
        if not ok:
            await ctx.close()
            return {"ok": False, "error": f"login panel thất bại: {info}"}

        # Trang lịch sử đơn — thử các URL phổ biến
        for orders_url in [
            buff_worker.PANEL_BASE + "/orders",
            buff_worker.PANEL_BASE + "/history",
            buff_worker.PANEL_BASE + "/order-history",
        ]:
            try:
                await page.goto(orders_url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(1500)
                # Kiểm tra có bảng đơn không
                if await page.locator("table").count() > 0:
                    break
            except Exception:
                continue

        # Tìm dòng chứa mã đơn
        # Panel thường hiện: ID | Link | Số lượng | Đã chạy | Trạng thái
        rows = page.locator("table tbody tr")
        n = await rows.count()
        for i in range(min(n, 50)):
            try:
                txt = await rows.nth(i).inner_text()
                if panel_order_id in txt:
                    # Parse số liệu từ dòng
                    # Tìm các số trong dòng: thường có format "done/total" hoặc riêng lẻ
                    import re
                    nums = re.findall(r"[\d,]+", txt.replace(".", ""))
                    # Heuristic: tìm cặp done/total
                    # Thử tìm pattern "X/Y" trước
                    m = re.search(r"(\d[\d,]*)\s*/\s*(\d[\d,]*)", txt)
                    if m:
                        done = int(m.group(1).replace(",", ""))
                        total = int(m.group(2).replace(",", ""))
                    elif len(nums) >= 2:
                        # Giả định: số đầu là done, số sau là total
                        # (cần tinh chỉnh theo HTML thực tế)
                        done = int(nums[0].replace(",", ""))
                        total = int(nums[1].replace(",", ""))
                    else:
                        done, total = 0, 0

                    # Xác định trạng thái từ text
                    txt_low = txt.lower()
                    status = "running"
                    for k, v in PANEL_STATUS_MAP.items():
                        if k in txt_low:
                            status = v
                            break

                    await ctx.close()
                    return {"ok": True, "done": done, "total": total, "status": status}
            except Exception:
                continue

        await ctx.close()
        return {"ok": False, "error": f"không tìm thấy đơn {panel_order_id} trên panel"}
    except Exception as e:
        log.warning("get_panel_order_progress lỗi: %s", e)
        try:
            if ctx:
                await ctx.close()
        except Exception:
            pass
        return {"ok": False, "error": str(e)[:200]}


async def scan_running_orders(bot=None):
    """Quét tất cả đơn đang chạy, cập nhật tiến độ, báo user.
    Gọi từ poller job nền.
    """
    from . import db

    try:
        orders = db.get_conn().execute(
            "SELECT id, code, tg_id, quantity, done_quantity, panel_order_id, status "
            "FROM buff_orders WHERE status IN ('running','pending') "
            "ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
    except Exception as e:
        log.warning("scan_running_orders: không đọc được DB: %s", e)
        return

    if not orders:
        return

    log.info("buff_tracker: quét %d đơn đang chạy", len(orders))
    for o in orders:
        o = dict(o)
        try:
            await _check_one_order(o, bot)
        except Exception as e:
            log.warning("buff_tracker: lỗi đơn %s: %s", o["code"], e)
        # Nghỉ giữa các đơn để không quá tải panel
        await asyncio.sleep(3)


async def _check_one_order(o: dict, bot=None):
    from . import db

    code = o["code"]
    tg_id = o["tg_id"]
    qty = int(o["quantity"] or 0)
    old_done = int(o["done_quantity"] or 0)
    panel_oid = o["panel_order_id"] or ""

    res = await get_panel_order_progress(panel_oid)
    now = int(time.time())

    if not res.get("ok"):
        # Không đọc được, chỉ cập nhật last_checked để tránh quét liên tục
        try:
            db.get_conn().execute(
                "UPDATE buff_orders SET last_checked_at=? WHERE id=?",
                (now, o["id"]),
            )
            db.get_conn().commit()
        except Exception:
            pass
        return

    new_done = int(res.get("done") or 0)
    panel_status = res.get("status") or "running"

    # Cập nhật DB
    new_status = o["status"]
    if panel_status == "completed" or (qty > 0 and new_done >= qty):
        new_status = "completed"
        new_done = qty  # chốt đủ
    elif panel_status in ("cancelled", "failed"):
        new_status = panel_status

    try:
        db.get_conn().execute(
            "UPDATE buff_orders SET done_quantity=?, status=?, last_checked_at=?, updated_at=? WHERE id=?",
            (new_done, new_status, now, now, o["id"]),
        )
        db.get_conn().commit()
    except Exception as e:
        log.warning("buff_tracker: update DB lỗi %s: %s", code, e)
        return

    if not bot:
        log.warning("buff_tracker: đơn %s đổi trạng thái %s->%s nhưng bot=None, không gửi được tin",
                    code, o["status"], new_status)
        return

    # Báo user
    try:
        if new_status == "completed" and o["status"] != "completed":
            await bot.send_message(
                tg_id,
                f"✅ <b>ĐƠN BUFF {code} ĐÃ HOÀN THÀNH!</b>\n\n"
                f"🔢 Số lượng: <b>{qty:,}</b> đã chạy đủ\n"
                f"Cảm ơn bạn đã sử dụng dịch vụ! ❤️",
                parse_mode="HTML",
            )
        elif new_status in ("cancelled", "failed") and o["status"] not in ("cancelled", "failed"):
            await bot.send_message(
                tg_id,
                f"❌ <b>ĐƠN BUFF {code} GẶP SỰ CỐ</b>\n\n"
                f"Trạng thái panel: {panel_status}\n"
                f"Đã chạy: {new_done:,}/{qty:,}\n"
                f"Liên hệ admin để được hỗ trợ nhé.",
                parse_mode="HTML",
            )
        elif new_done > old_done and new_status == "running":
            # Chỉ báo khi tiến triển đáng kể (>=10% hoặc >=100 đơn)
            pct_old = (old_done / qty * 100) if qty else 0
            pct_new = (new_done / qty * 100) if qty else 0
            if pct_new - pct_old >= 10 or new_done - old_done >= 100:
                await bot.send_message(
                    tg_id,
                    f"📊 <b>Đơn buff {code}</b> đang chạy:\n"
                    f"🔢 Tiến độ: <b>{new_done:,}/{qty:,}</b> ({pct_new:.0f}%)",
                    parse_mode="HTML",
                )
    except Exception as e:
        log.warning("buff_tracker: gửi tin lỗi %s: %s", code, e)
