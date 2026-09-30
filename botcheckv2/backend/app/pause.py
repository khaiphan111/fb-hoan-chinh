"""Chế độ tạm dừng bot (maintenance mode).

- Admin (chủ shop) bật/tắt từ bot Telegram (/tamdung, /molai, nút trong /adm)
  hoặc từ web admin.
- Khi bật: khách thường nhắn gì cũng chỉ nhận 1 tin auto-reply (mỗi người
  tối đa 1 tin/giờ); giao dịch đang dở được coi như hủy — khách làm lại
  sau khi bot mở.
- Tự động mở lại khi hết giờ (poller gọi sweep() mỗi vòng, middleware cũng
  lazy-resume khi có khách nhắn đến); gửi tin "đã mở lại" cho những khách
  đã nhận tin tạm dừng.
- Tùy chọn tạm dừng cả job nền hướng-khách (đặt đơn buff, hỏi thăm, xin
  đánh giá) — re-check kho, backup, dọn dẹp vẫn chạy.

State lưu trong bảng settings (dùng chung DB nên web và bot đồng bộ).
Danh sách khách cần báo "mở lại" lưu ở bảng pause_notified.
"""
import asyncio
import html
import logging
import time

from . import db

log = logging.getLogger("pause")

K_ACTIVE = "pause_active"
K_REASON = "pause_reason"
K_UNTIL = "pause_until"
K_BY = "pause_by"
K_BY_NAME = "pause_by_name"
K_STARTED = "pause_started_at"
K_STOP_JOBS = "pause_stop_jobs"
K_REOPEN_PENDING = "pause_reopen_pending"

NOTICE_THROTTLE = 3600  # mỗi khách tối đa 1 tin tạm dừng / giờ
MAX_MINUTES = 7 * 24 * 60  # thời gian tạm dừng tối đa: 7 ngày


def _ts(v, default=0) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return default


def is_paused() -> bool:
    """Bot có đang tạm dừng không (kèm lazy-resume nếu đã quá giờ — không gửi tin)."""
    if db.get_setting(K_ACTIVE) != "1":
        return False
    until = _ts(db.get_setting(K_UNTIL))
    if until and until <= time.time():
        # Hết giờ: tắt cờ ngay để khách nhắn đến được phục vụ bình thường.
        # Tin "đã mở lại" do sweep() của poller (hoặc lazy-resume ở middleware) gửi.
        try:
            try_deactivate(by_id=0, by_name="Hệ thống (hết giờ)")
        except Exception:
            pass
        return False
    return True


def get_info() -> dict:
    until = _ts(db.get_setting(K_UNTIL))
    return {
        "active": db.get_setting(K_ACTIVE) == "1",
        "reason": db.get_setting(K_REASON) or "Bảo trì hệ thống",
        "until": until,
        "by": db.get_setting(K_BY),
        "by_name": db.get_setting(K_BY_NAME),
        "started_at": _ts(db.get_setting(K_STARTED)),
        "stop_jobs": db.get_setting(K_STOP_JOBS) == "1",
    }


def jobs_paused() -> bool:
    """Job nền hướng-khách có bị tạm dừng theo không."""
    return is_paused() and db.get_setting(K_STOP_JOBS) == "1"


def fmt_until(until: int) -> str:
    if not until:
        return "chưa hẹn"
    return time.strftime("%H:%M %d/%m/%Y", time.localtime(until))


def pause_notice_text(info: dict | None = None) -> str:
    info = info or get_info()
    return (
        "⏸️ <b>BOT TẠM DỪNG</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"🔧 Lý do: <b>{html.escape(info.get('reason') or '')}</b>\n"
        f"🕐 Dự kiến mở lại: <b>{fmt_until(info.get('until') or 0)}</b>\n\n"
        "Giao dịch đang thực hiện (nếu có) đã bị hủy — "
        "bạn vui lòng thực hiện lại sau khi bot hoạt động trở lại.\n"
        "Cảm ơn bạn đã chờ! 🙏"
    )


def reopen_notice_text() -> str:
    return (
        "✅ <b>BOT ĐÃ HOẠT ĐỘNG TRỞ LẠI</b>\n"
        "━━━━━━━━━━━━━━━\n"
        "Bot đã mở lại bình thường. "
        "Nếu lúc nãy bạn đang giao dịch dở, vui lòng thực hiện lại nhé!"
    )


def activate(reason: str, until_ts: int, by_id: int = 0, by_name: str = "",
             stop_jobs: bool = False) -> None:
    """Bật chế độ tạm dừng. until_ts: unix timestamp lúc mở lại."""
    now = int(time.time())
    reason = (reason or "").strip() or "Bảo trì hệ thống"
    db.set_setting(K_REASON, reason[:200])
    db.set_setting(K_UNTIL, str(int(until_ts)))
    db.set_setting(K_BY, str(by_id or ""))
    db.set_setting(K_BY_NAME, (by_name or "")[:100])
    db.set_setting(K_STARTED, str(now))
    db.set_setting(K_STOP_JOBS, "1" if stop_jobs else "0")
    db.set_setting(K_REOPEN_PENDING, "0")
    # Phiên tạm dừng mới: xóa danh sách chờ báo của phiên cũ (nếu sót)
    try:
        c = db.get_conn()
        c.execute("DELETE FROM pause_notified")
        c.commit()
    except Exception as e:
        log.warning("clear pause_notified: %s", e)
    db.set_setting(K_ACTIVE, "1")
    try:
        db.admin_audit_add(by_id, by_name, "pause_on",
                           f"{reason} | mở lại {fmt_until(int(until_ts))} | "
                           f"job_nen={'dung' if stop_jobs else 'chay'}")
    except Exception:
        pass


def try_deactivate(by_id: int = 0, by_name: str = "") -> bool:
    """Tắt chế độ tạm dừng (nguyên tử: chỉ 1 bên flip được 1→0).

    Trả True nếu chính lần gọi này là bên tắt — bên đó chịu trách nhiệm
    gửi tin "đã mở lại".
    """
    try:
        c = db.get_conn()
        cur = c.execute(
            "UPDATE settings SET value='0' WHERE key=? AND value='1'",
            (K_ACTIVE,))
        c.commit()
        flipped = (cur.rowcount or 0) > 0
    except Exception as e:
        log.warning("try_deactivate: %s", e)
        return False
    if flipped:
        db.settings_cache_invalidate(K_ACTIVE)
        db.set_setting(K_REOPEN_PENDING, "1")
        try:
            db.admin_audit_add(by_id, by_name, "pause_off",
                               "Mở lại bot" + (" (tự động)" if not by_id else ""))
        except Exception:
            pass
    return flipped


def record_notified(tg_id: int, name: str = "") -> bool:
    """Ghi nhận khách đã nhận tin tạm dừng.

    Trả True nếu nên gửi tin (chưa gửi trong NOTICE_THROTTLE giây qua),
    False nếu đang bị throttle — vẫn ghi nhận để báo "mở lại" sau.
    """
    now = int(time.time())
    try:
        c = db.get_conn()
        row = c.execute(
            "SELECT last_notice_at FROM pause_notified WHERE tg_id=?",
            (tg_id,)).fetchone()
        if row:
            last = _ts(row["last_notice_at"])
            c.execute(
                "UPDATE pause_notified SET last_notice_at=?, name=? WHERE tg_id=?",
                (now, (name or "")[:100], tg_id))
            c.commit()
            return (now - last) >= NOTICE_THROTTLE
        c.execute(
            "INSERT INTO pause_notified(tg_id, name, notified_at, last_notice_at)"
            " VALUES(?,?,?,?)",
            (tg_id, (name or "")[:100], now, now))
        c.commit()
        return True
    except Exception as e:
        log.warning("record_notified: %s", e)
        return True


def pop_notified() -> list:
    """Lấy + xóa toàn bộ danh sách chờ báo "mở lại"."""
    try:
        c = db.get_conn()
        rows = c.execute(
            "SELECT tg_id FROM pause_notified ORDER BY notified_at").fetchall()
        ids = [int(r["tg_id"]) for r in rows]
        c.execute("DELETE FROM pause_notified")
        c.commit()
        return ids
    except Exception as e:
        log.warning("pop_notified: %s", e)
        return []


async def _send_reopen(bot, ids: list, auto: bool) -> None:
    if not ids or bot is None:
        return
    text = reopen_notice_text()
    ok = 0
    for tg_id in ids:
        try:
            await bot.send_message(tg_id, text, parse_mode="HTML")
            ok += 1
            await asyncio.sleep(0.05)  # chống flood
        except Exception:
            pass
    log.info("pause reopen notify: %d/%d (%s)", ok, len(ids),
             "auto" if auto else "manual")
    # Báo cho admin đã bật tạm dừng (nếu còn lưu)
    try:
        by = db.get_setting(K_BY)
        if by and int(by) not in ids:
            await bot.send_message(
                int(by),
                f"✅ Bot đã <b>mở lại</b> ({'tự động' if auto else 'thủ công'}). "
                f"Đã báo cho {ok}/{len(ids)} khách.",
                parse_mode="HTML")
    except Exception:
        pass


async def notify_reopened(bot, auto: bool) -> int:
    """Pop danh sách chờ + gửi tin "đã mở lại". Trả về số khách nhận tin."""
    ids = pop_notified()
    db.set_setting(K_REOPEN_PENDING, "0")
    await _send_reopen(bot, ids, auto)
    return len(ids)


async def sweep(bot=None) -> None:
    """Poller gọi mỗi vòng: tự mở lại khi hết giờ + gửi tin mở lại còn pending."""
    try:
        info = get_info()
        now = time.time()
        if info["active"] and info["until"] and info["until"] <= now:
            if try_deactivate(by_id=0, by_name="Hệ thống (hết giờ)"):
                await notify_reopened(bot, auto=True)
                return
        if not is_paused() and db.get_setting(K_REOPEN_PENDING) == "1":
            # Được mở tay từ web (không có bot instance để gửi ngay)
            await notify_reopened(bot, auto=False)
    except Exception as e:
        log.warning("pause sweep: %s", e)
