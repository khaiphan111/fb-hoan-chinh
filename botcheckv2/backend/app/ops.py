"""Viec van hanh tu dong (chay trong backend, duoc poller._ops_loop goi):

1. Bao cao sang 7h -> admin (qua notify bot): doanh thu hom qua
   (nap PayOS + ban acc), khach moi, ton kho, acc DIE cach ly, rut cho duyet.
2. Quet gian lan moi gio -> admin:
   - trial ao: qua nhieu tai khoan moi kich hoat trial trong 24h
   - claim bao hanh lien tuc: >= 3 claim / 7 ngay
   - rut tien lon dang cho duyet
   - nap roi rut ngay: co don rut trong 24h sau khi nap
3. Qua sinh nhat 8h: user co sinh nhat hom nay (da nhap /sinhnhat tu
   hom truoc tro di) duoc tang giftcode tu dong, moi nam 1 lan.
"""
import logging
import time

from . import db
from .util import vnd

log = logging.getLogger("ops")

# ── Nguong canh bao (admin tu chinh truc tiep o day neu muon) ──
TRIAL_SPIKE_COUNT = 5      # >= N trial moi trong 24h -> bao
WARRANTY_CLAIM_COUNT = 3   # >= N claim BH trong 7 ngay -> bao
BIG_WITHDRAW = 500_000     # don rut cho duyet >= so nay -> bao


def _day_range(ts: float | None = None):
    lt = time.localtime(ts)
    start = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))
    return start, start + 86400


def _notify():
    from .notify_bot import manager as notify_manager
    return notify_manager

def _uname(tg_id: int) -> str:
    """Ten hien thi cua user de dua vao canh bao (khong vo neu thieu)."""
    try:
        u = db.get_user(tg_id)
        if u and u["username"]:
            return "@" + str(u["username"])
    except Exception:
        pass
    return f"ID {tg_id}"


async def morning_report() -> bool:
    """Bao cao tong hop ngay hom qua cho admin. Tra True neu da gui."""
    today = time.strftime("%Y-%m-%d")
    if not db.ops_alert_dedup("morning_report", today, 20 * 3600):
        return False
    ys, ye = _day_range(time.time() - 86400)
    ymd = time.strftime("%d/%m/%Y", time.localtime(ys))
    c = db.get_conn()
    try:
        payos = c.execute(
            "SELECT COALESCE(SUM(amount),0) s, COUNT(*) n FROM payos_orders"
            " WHERE status='PAID' AND updated_at>=? AND updated_at<?", (ys, ye)).fetchone()
        shop = c.execute(
            "SELECT COALESCE(SUM(price),0) s, COUNT(*) n FROM acc_orders"
            " WHERE created_at>=? AND created_at<?", (ys, ye)).fetchone()
        stock = c.execute(
            "SELECT cat.name nm, COUNT(*) n FROM acc_stock s"
            " JOIN acc_categories cat ON cat.id=s.cat_id"
            " WHERE s.status='AVAILABLE' GROUP BY s.cat_id ORDER BY n DESC").fetchall()
        die_n = c.execute(
            "SELECT COUNT(*) n FROM acc_stock WHERE status IN ('DEAD','DIE')").fetchone()["n"]
        new_users = c.execute(
            "SELECT COUNT(*) n FROM tg_users WHERE created_at>=? AND created_at<?",
            (ys, ye)).fetchone()["n"]
        wd = c.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(amount),0) s FROM withdrawal_requests"
            " WHERE status='pending'").fetchone()
    except Exception as e:
        log.warning("morning_report query failed: %s", e)
        return False

    total_stock = sum(r["n"] for r in stock)
    lines = [
        f"☀️ <b>BÁO CÁO NGÀY {ymd}</b>",
        "━━━━━━━━━━━━━━━",
        f"💰 Nạp PayOS: <b>{vnd(payos['s'])}</b> ({payos['n']} đơn)",
        f"🛒 Bán acc: <b>{vnd(shop['s'])}</b> ({shop['n']} đơn)",
        f"👥 Khách mới: <b>{new_users}</b>",
        f"📦 Tồn kho: <b>{total_stock}</b> acc",
    ]
    for r in stock[:10]:
        lines.append(f"   • {r['nm']}: {r['n']}")
    lines += [
        f"☠️ Acc DIE cách ly: <b>{die_n}</b>",
        f"⏳ Rút chờ duyệt: <b>{wd['n']}</b> ({vnd(wd['s'])})",
    ]
    ok = await _notify().send_to_privileged("\n".join(lines))
    log.info("morning_report sent=%s", ok)
    return ok


async def fraud_scan() -> int:
    """Quet cac dau hieu gian lan, bao admin. Tra so canh bao da gui."""
    now = int(time.time())
    today = time.strftime("%Y-%m-%d")
    c = db.get_conn()
    sent = 0
    nm = _notify()

    # 1. Trial ao: nhieu tai khoan moi kich hoat trial trong 24h
    try:
        n = c.execute("SELECT COUNT(*) n FROM tg_users WHERE trial_activated=1 AND created_at>=?",
                      (now - 86400,)).fetchone()["n"]
        if n >= TRIAL_SPIKE_COUNT and db.ops_alert_dedup("trial_spike", today, 20 * 3600):
            if await nm.send_to_privileged(
                    "⚠️ <b>CẢNH BÁO TRIAL ẢO</b>\n"
                    f"24h qua có <b>{n}</b> tài khoản mới kích hoạt trial "
                    f"(ngưỡng {TRIAL_SPIKE_COUNT}). Kiểm tra xem có người tạo nhiều nick không."):
                sent += 1
    except Exception as e:
        log.warning("fraud trial_spike failed: %s", e)

    # 2. Claim bao hanh lien tuc
    try:
        rows = c.execute(
            "SELECT tg_id, COUNT(*) n FROM acc_warranty_claims WHERE created_at>=?"
            " GROUP BY tg_id HAVING n>=?", (now - 7 * 86400, WARRANTY_CLAIM_COUNT)).fetchall()
        for r in rows:
            tg_id = r["tg_id"]
            if not db.ops_alert_dedup("warranty_abuse", str(tg_id), 7 * 86400):
                continue
            uname = _uname(tg_id)
            if await nm.send_to_privileged(
                    "⚠️ <b>CẢNH BÁO LẠM DỤNG BẢO HÀNH</b>\n"
                    f"Khách {uname} đã tạo <b>{r['n']}</b> khiếu nại BH trong 7 ngày qua."):
                sent += 1
    except Exception as e:
        log.warning("fraud warranty failed: %s", e)

    # 3. Don rut lon dang cho duyet
    try:
        rows = c.execute(
            "SELECT id, tg_id, amount FROM withdrawal_requests"
            " WHERE status='pending' AND amount>=?", (BIG_WITHDRAW,)).fetchall()
        for r in rows:
            if not db.ops_alert_dedup("big_withdraw", str(r["id"]), 24 * 3600):
                continue
            uname = _uname(r["tg_id"])
            if await nm.send_to_privileged(
                    "⚠️ <b>RÚT TIỀN LỚN CHỜ DUYỆT</b>\n"
                    f"Khách {uname} yêu cầu rút <b>{vnd(r['amount'])}</b> (đơn #{r['id']}). "
                    "Nhớ kiểm tra kỹ trước khi duyệt."):
                sent += 1
    except Exception as e:
        log.warning("fraud big_withdraw failed: %s", e)

    # 4. Nap roi rut ngay (trong 24h sau khi nap)
    try:
        rows = c.execute(
            "SELECT w.id, w.tg_id, w.amount FROM withdrawal_requests w WHERE w.status='pending'"
            " AND EXISTS (SELECT 1 FROM payos_orders p WHERE p.tg_id=w.tg_id AND p.status='PAID'"
            " AND p.updated_at < w.created_at AND p.updated_at >= w.created_at - 86400)").fetchall()
        for r in rows:
            if not db.ops_alert_dedup("topup_withdraw", str(r["id"]), 24 * 3600):
                continue
            uname = _uname(r["tg_id"])
            if await nm.send_to_privileged(
                    "⚠️ <b>NGHI RỬA TIỀN / LỢI DỤNG</b>\n"
                    f"Khách {uname} vừa nạp tiền rồi tạo đơn rút <b>{vnd(r['amount'])}</b> "
                    f"(đơn #{r['id']}) trong vòng 24h. Kiểm tra trước khi duyệt."):
                sent += 1
    except Exception as e:
        log.warning("fraud topup_withdraw failed: %s", e)

    if sent:
        log.info("fraud_scan sent %d alerts", sent)
    return sent


async def birthday_job(bot) -> int:
    """Tang giftcode sinh nhat tu dong. Tra so user da tang."""
    if bot is None:
        return 0
    lt = time.localtime()
    mmdd = f"{lt.tm_mon:02d}-{lt.tm_mday:02d}"
    year = lt.tm_year
    today0, _ = _day_range()
    c = db.get_conn()
    try:
        rows = c.execute(
            "SELECT tg_id, name, dob FROM tg_users"
            " WHERE dob LIKE ? AND birthday_gift_year < ? AND dob_set_at < ? AND is_blocked=0",
            (f"____-{mmdd}", year, today0)).fetchall()
    except Exception as e:
        log.warning("birthday query failed: %s", e)
        return 0
    try:
        amount = int(db.get_setting("birthday_gift_amount", "50000") or 50000)
    except Exception:
        amount = 50000
    done = 0
    for r in rows:
        tg_id = r["tg_id"]
        try:
            code = db.generate_code(amount, prefix="SN", max_uses=1,
                                    expire_at=int(time.time()) + 30 * 86400)
        except Exception as e:
            log.warning("birthday generate_code failed: %s", e)
            continue
        db.mark_birthday_gift(tg_id, year)
        name = (r["name"] or "bạn").strip() or "bạn"
        try:
            await bot.send_message(
                tg_id,
                "🎂 <b>CHÚC MỪNG SINH NHẬT!</b> 🎉\n\n"
                f"{name} ơi, bot tặng bạn 1 giftcode sinh nhật trị giá "
                f"<b>{vnd(amount)}</b>:\n"
                f"🎁 <code>{code}</code>\n\n"
                "Nhập lệnh <code>/code</code> rồi gửi mã này để nhận quà nhé. "
                "Chúc bạn một tuổi mới nhiều niềm vui!",
                parse_mode="HTML")
            done += 1
        except Exception as e:
            log.warning("birthday send to %s failed: %s", tg_id, e)
    if done:
        log.info("birthday_job gifted %d users", done)
    return done
