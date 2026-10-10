"""Báo cáo lãi/lỗ hằng ngày cho chủ shop.

Tính theo từng mảng kinh doanh:
- Shop acc:  doanh thu = SUM(acc_orders.price),
              giá vốn = SUM(acc_stock.cost_price) join theo stock_id
- Buff:      doanh thu = SUM(total_price), giá vốn = SUM(total_cost)
             (loại đơn failed/cancelled vì đã hoàn tiền)
- Thuê số:   doanh thu = SUM(sell_price), giá vốn = SUM(cost_price)
             (chỉ đơn done — đơn expired/refunded đã hoàn tiền)

Kèm dòng tiền: nạp PayOS PAID, rút tiền đã duyệt.
Mốc ngày theo giờ +07. created_at các bảng là unix timestamp.
"""
import time
from . import db


def day_bounds(day_ts=None):
    """Trả về (start, end, day_str) của ngày chứa day_ts (mặc định hôm nay)."""
    base = day_ts if day_ts else time.time()
    # +07: tính ngày theo giờ VN
    lt = time.localtime(base)
    day_key = time.strftime("%Y-%m-%d", lt)
    # mktime theo localtime của server (server để +07)
    start = int(time.mktime(time.strptime(day_key + " 00:00", "%Y-%m-%d %H:%M")))
    return start, start + 86400, time.strftime("%d/%m/%Y", lt)


def _one(sql, params):
    c = db.get_conn()
    r = c.execute(sql, params).fetchone()
    return r


def daily_profit(day_ts=None):
    start, end, day_str = day_bounds(day_ts)

    # Shop acc: giá vốn = acc_batches.cost_per_acc (join qua acc_stock.batch)
    r = _one(
        "SELECT COUNT(*), COALESCE(SUM(o.price),0), "
        "COALESCE(SUM(b.cost_per_acc),0) "
        "FROM acc_orders o "
        "LEFT JOIN acc_stock s ON s.id=o.stock_id "
        "LEFT JOIN acc_batches b ON b.batch=s.batch "
        "WHERE o.created_at>=? AND o.created_at<?",
        (start, end))
    shop = {"don": int(r[0]), "doanh_thu": int(r[1]), "von": int(r[2])}

    # Buff (loại đơn hỏng đã hoàn)
    r = _one(
        "SELECT COUNT(*), COALESCE(SUM(total_price),0), COALESCE(SUM(total_cost),0) "
        "FROM buff_orders WHERE created_at>=? AND created_at<? "
        "AND status NOT IN ('failed','cancelled')",
        (start, end))
    buff = {"don": int(r[0]), "doanh_thu": int(r[1]), "von": int(r[2])}

    # Thuê số (chỉ đơn done)
    r = _one(
        "SELECT COUNT(*), COALESCE(SUM(sell_price),0), COALESCE(SUM(cost_price),0) "
        "FROM viotp_rentals WHERE created_at>=? AND created_at<? AND status='done'",
        (start, end))
    thueso = {"don": int(r[0]), "doanh_thu": int(r[1]), "von": int(r[2])}

    # Dòng tiền
    r = _one(
        "SELECT COUNT(*), COALESCE(SUM(amount),0) FROM payos_orders "
        "WHERE created_at>=? AND created_at<? AND status='PAID'",
        (start, end))
    nap = {"don": int(r[0]), "tien": int(r[1])}
    r = _one(
        "SELECT COUNT(*), COALESCE(SUM(amount),0) FROM withdrawal_requests "
        "WHERE created_at>=? AND created_at<? AND status IN ('approved','done')",
        (start, end))
    rut = {"don": int(r[0]), "tien": int(r[1])}

    for sec in (shop, buff, thueso):
        sec["lai"] = sec["doanh_thu"] - sec["von"]
    tong_lai = shop["lai"] + buff["lai"] + thueso["lai"]
    return {"day": day_str, "shop": shop, "buff": buff, "thueso": thueso,
            "nap": nap, "rut": rut, "tong_lai": tong_lai}


def _fmt_sec(icon, name, sec):
    return (f"{icon} <b>{name}</b>: {sec['don']} đơn\n"
            f"   DT: {sec['doanh_thu']:,}đ — Vốn: {sec['von']:,}đ\n"
            f"   Lãi: <b>{sec['lai']:,}đ</b>")


def format_report(data):
    lines = [
        f"💹 <b>LÃI/LỖ NGÀY {data['day']}</b>",
        "━━━━━━━━━━━━━━━",
        _fmt_sec("🛒", "Shop acc", data["shop"]),
        _fmt_sec("🚀", "Buff", data["buff"]),
        _fmt_sec("📱", "Thuê số", data["thueso"]),
        "━━━━━━━━━━━━━━━",
        f"💰 <b>TỔNG LÃI GỘP: {data['tong_lai']:,}đ</b>",
        "",
        f"📥 Nạp (PayOS): {data['nap']['don']} đơn — {data['nap']['tien']:,}đ",
        f"📤 Rút đã duyệt: {data['rut']['don']} đơn — {data['rut']['tien']:,}đ",
    ]
    return "\n".join(lines)
