"""Kho riêng cho đối tác ký gửi trên Google Sheet.

Mỗi đối tác có 1 spreadsheet RIÊNG (sheet_id lưu ở bảng consignors).
Chủ shop tự tạo Sheet, gắn link vào hồ sơ đối tác và tự mở quyền xem
cho email Google của đối tác (bot không tự share).

Đồng bộ: ghi đè toàn bộ tab "Kho" trong spreadsheet riêng của đối tác.

Chạy trong executor để không block event loop (dùng chung _cli của sheet_import).
"""
import logging
import re
import time

from . import db
from . import sheet_import as _si

log = logging.getLogger(__name__)

#: Tên tab kho trong spreadsheet riêng của đối tác
KG_TAB = "Kho"

#: Tên tab nhập hàng (staging) trong spreadsheet riêng của đối tác
IMPORT_TAB = "Nhập hàng"

#: Tiêu đề tab nhập hàng: 8 cột acc + cột trạng thái
IMPORT_HEADERS = ["UID", "Mật khẩu", "Ngày tạo", "Mail thay", "Ghi chú",
                  "2FA", "Cookie", "Token", "Trạng thái"]

#: Tiêu đề tab kho ký gửi
KG_HEADERS = ["STT", "Mã lô", "UID", "Loại acc", "Giá bán", "Phí shop",
              "Thực nhận", "Trạng thái", "Ngày bán", "Hết BH / Giải ngân", "Ghi chú"]


def extract_sheet_id(url_or_id: str) -> str:
    """Trích spreadsheet ID từ link Google Sheets hoặc trả nguyên nếu đã là ID."""
    s = (url_or_id or "").strip()
    if not s:
        return ""
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", s)
    if m:
        return m.group(1)
    # ID trần của Google Sheet thường dài >= 20 ký tự
    if re.fullmatch(r"[a-zA-Z0-9-_]{20,}", s):
        return s
    return ""


def _fmt_ts(ts) -> str:
    try:
        ts = int(ts or 0)
    except Exception:
        return ""
    if not ts:
        return ""
    return time.strftime("%d/%m/%Y %H:%M", time.localtime(ts))


def _status_label(it: dict, now: int) -> str:
    st = it.get("status") or ""
    if st == "pending":
        return "⏳ Chờ duyệt"
    if st == "listed":
        return "🟢 Đang bán"
    if st == "returned":
        return "🔙 Đã trả hàng"
    if st == "rejected":
        return "❌ Bị loại"
    if st == "sold":
        if it.get("o_dispute"):
            return "⚠️ Tranh chấp"
        wu = int(it.get("o_wuntil") or 0)
        if wu and wu > now:
            return "✅ Đã bán (giữ BH)"
        return "💰 Đã giải ngân"
    return st or "—"


def build_kg_values(cid: int):
    """Dựng ma trận giá trị kho của đối tác. Trả list values (dòng đầu là tiêu đề)."""
    c = db.consignor_get_by_id(cid)
    if not c:
        return []
    items = db.consign_warehouse(cid)
    now = int(time.time())
    # map batch_id -> tên loại (1 query)
    batch_cat = {}
    try:
        conn = db.get_conn()
        for r in conn.execute(
                "SELECT b.id, c.name FROM consignment_batches b"
                " LEFT JOIN acc_categories c ON c.id=b.category_id"
                " WHERE b.consignor_id=?", (cid,)).fetchall():
            batch_cat[r["id"]] = r["name"] or ""
    except Exception:
        pass
    out = [KG_HEADERS]
    tot_sell = tot_fee = tot_net = 0
    for i, it in enumerate(items, 1):
        price = int(it.get("o_price") or it.get("batch_price") or 0)
        fee = int(it.get("o_fee") or 0)
        net = int(it.get("o_net") or 0)
        if it.get("status") == "sold":
            tot_sell += price
            tot_fee += fee
            tot_net += net
        cat = batch_cat.get(it.get("batch_id"), "")
        out.append([
            i,
            it.get("batch_code") or "",
            it.get("uid") or "",
            cat,
            f"{price:,}đ" if price else "",
            f"{fee:,}đ" if fee else "",
            f"{net:,}đ" if net else "",
            _status_label(it, now),
            _fmt_ts(it.get("o_at")),
            _fmt_ts(it.get("o_wuntil")),
            (it.get("note") or "").strip(),
        ])
    if len(out) > 1:
        out.append(["📊 TỔNG", f"{len(out)-1} acc", "", "",
                    f"{tot_sell:,}đ", f"{tot_fee:,}đ", f"{tot_net:,}đ",
                    "", "", "", ""])
    return out


async def create_partner_spreadsheet(name: str) -> dict:
    """Tạo spreadsheet mới cho đối tác (title 'Kho ký gửi - <tên>') + tab Kho + tiêu đề.

    Trả {'id':..., 'url':...} hoặc {} nếu lỗi.
    LƯU Ý: chỉ tạo file, KHÔNG tự share được (tài khoản Google chưa cấp quyền Drive API)
    -> chủ shop vẫn phải Share tay cho email đối tác 1 lần.
    """
    try:
        import json as _json
        title = f"Kho ký gửi - {name}"[:90]
        d = await _si._cli(["sheets", "spreadsheets", "create"],
                           {"properties": {"title": title}})
        sid = (d.get("spreadsheetId") or "").strip()
        if not sid:
            return {}
        # tạo tab Kho + ghi tiêu đề
        if not await _si.tab_exists(sid, KG_TAB):
            await _si._cli(["sheets", "spreadsheets", "batchUpdate", "--params",
                            _json.dumps({"spreadsheetId": sid})],
                           {"requests": [{"addSheet": {"properties": {"title": KG_TAB}}}]})
        await _si._cli(["sheets", "spreadsheets", "values", "update", "--params",
                        _json.dumps({"spreadsheetId": sid, "range": f"{KG_TAB}!A1:K1",
                                     "valueInputOption": "USER_ENTERED"})],
                       {"values": [KG_HEADERS]})
        log.info("create_partner_ss: đã tạo %s cho '%s'", sid, name)
        return {"id": sid, "url": f"https://docs.google.com/spreadsheets/d/{sid}"}
    except Exception as e:
        log.warning("create_partner_ss lỗi: %s", e)
        return {}


async def push_consign_warehouse(cid: int) -> int:
    """Đồng bộ kho của đối tác lên spreadsheet RIÊNG của họ.

    Đọc sheet_id từ bảng consignors. Trả số dòng dữ liệu, -1 nếu lỗi,
    -2 nếu đối tác chưa gắn Sheet.
    """
    c = db.consignor_get_by_id(cid)
    if not c:
        return -1
    spreadsheet_id = (c.get("sheet_id") or "").strip()
    if not spreadsheet_id:
        return -2
    values = build_kg_values(cid)
    if not values:
        return -1
    try:
        import json as _json
        if not await _si.tab_exists(spreadsheet_id, KG_TAB):
            await _si._cli(["sheets", "spreadsheets", "batchUpdate", "--params",
                            _json.dumps({"spreadsheetId": spreadsheet_id})],
                           {"requests": [{"addSheet": {"properties": {"title": KG_TAB}}}]})
            log.info("push_consign_wh: đã tạo tab '%s' (cid=%s)", KG_TAB, cid)
        # Xóa dữ liệu cũ từ dòng 2 (tránh dòng thừa)
        await _si._cli(["sheets", "spreadsheets", "values", "clear", "--params",
                        _json.dumps({"spreadsheetId": spreadsheet_id,
                                     "range": f"{KG_TAB}!A2:K"})])
        end = len(values)
        # số cột K = 11 -> cột K
        await _si._cli(["sheets", "spreadsheets", "values", "update", "--params",
                        _json.dumps({"spreadsheetId": spreadsheet_id,
                                     "range": f"{KG_TAB}!A1:K{end}",
                                     "valueInputOption": "USER_ENTERED"})],
                       {"values": values})
        log.info("push_consign_wh: đã ghi %d dòng (cid=%s)", end - 1, cid)
        return end - 1
    except Exception as e:
        log.warning("push_consign_wh lỗi (cid=%s): %s", cid, e)
        return -1


# ================= Tab "Nhập hàng" (staging upload của đối tác) =================

async def ensure_import_tab(sid: str) -> bool:
    """Tạo tab 'Nhập hàng' + ghi tiêu đề nếu chưa có. Trả True nếu OK."""
    try:
        import json as _json
        if not await _si.tab_exists(sid, IMPORT_TAB):
            await _si._cli(["sheets", "spreadsheets", "batchUpdate", "--params",
                            _json.dumps({"spreadsheetId": sid})],
                           {"requests": [{"addSheet": {"properties": {"title": IMPORT_TAB}}}]})
            log.info("ensure_import_tab: đã tạo tab '%s' (%s)", IMPORT_TAB, sid)
        await _si._cli(["sheets", "spreadsheets", "values", "update", "--params",
                        _json.dumps({"spreadsheetId": sid, "range": f"{IMPORT_TAB}!A1:I1",
                                     "valueInputOption": "USER_ENTERED"})],
                       {"values": [IMPORT_HEADERS]})
        return True
    except Exception as e:
        log.warning("ensure_import_tab lỗi (%s): %s", sid, e)
        return False


async def ensure_partner_sheet(cid: int, name: str) -> str:
    """Lấy sheet_id của đối tác, tự tạo file + tab Nhập hàng nếu chưa có.

    Trả spreadsheet ID hoặc '' nếu lỗi.
    """
    c = db.consignor_get_by_id(cid)
    if not c:
        return ""
    sid = (c.get("sheet_id") or "").strip()
    if not sid:
        d = await create_partner_spreadsheet(name or f"doi-tac-{cid}")
        sid = (d.get("id") or "").strip()
        if not sid:
            return ""
        try:
            db.get_conn().execute(
                "UPDATE consignors SET sheet_id=?, sheet_url=? WHERE id=?",
                (sid, d.get("url", ""), cid))
            db.get_conn().commit()
        except Exception:
            pass
        log.info("ensure_partner_sheet: đã tạo sheet %s cho cid=%s", sid, cid)
    await ensure_import_tab(sid)
    return sid


async def append_import_rows(sid: str, items: list) -> int:
    """Ghi thêm acc vào tab 'Nhập hàng'. Trả số dòng đã ghi, -1 nếu lỗi.

    items: list dict {uid, password, backup_mail, totp, cookie, token, note}
    (note đã gộp [NSX ...] nếu có ngày tạo).
    """
    if not items:
        return 0
    try:
        import json as _json
        # tách ngày tạo khỏi note để ghi đúng cột C
        vals = []
        for it in items:
            note = it.get("note") or ""
            nsx = ""
            m = __import__("re").match(r"\[NSX ([^\]]+)\]\s*(.*)", note, __import__("re").S)
            if m:
                nsx, note = m.group(1), m.group(2)
            vals.append([it.get("uid", ""), it.get("password", ""), nsx,
                         it.get("backup_mail", ""), note, it.get("totp", ""),
                         it.get("cookie", ""), it.get("token", ""), ""])
        await _si._cli(["sheets", "spreadsheets", "values", "append", "--params",
                        _json.dumps({"spreadsheetId": sid, "range": f"{IMPORT_TAB}!A:I",
                                     "valueInputOption": "USER_ENTERED"})],
                       {"values": vals})
        log.info("append_import_rows: đã ghi %d dòng (%s)", len(vals), sid)
        return len(vals)
    except Exception as e:
        log.warning("append_import_rows lỗi (%s): %s", sid, e)
        return -1


async def read_import_rows(sid: str):
    """Đọc các dòng chưa nhập (cột I trống) từ tab 'Nhập hàng'.

    Trả (items, row_numbers): items như _parse_items, row_numbers là số dòng
    sheet (1-based, tính cả tiêu đề) để đánh dấu sau khi nhập.
    """
    try:
        import json as _json
        d = await _si._cli(["sheets", "spreadsheets", "values", "get", "--params",
                            _json.dumps({"spreadsheetId": sid,
                                         "range": f"{IMPORT_TAB}!A2:I"})])
        rows = d.get("values") or []
    except Exception as e:
        log.warning("read_import_rows lỗi (%s): %s", sid, e)
        return [], []
    items, rnums = [], []
    for i, cells in enumerate(rows):
        cells = [str(v or "").strip() for v in cells]
        while len(cells) < 9:
            cells.append("")
        if not cells[0]:
            continue
        if (cells[8] or "").strip():
            continue  # đã nhập rồi
        note = cells[4]
        if cells[2]:
            note = f"[NSX {cells[2]}] {note}".strip()
        items.append({"uid": cells[0], "password": cells[1],
                      "backup_mail": cells[3], "totp": cells[5],
                      "cookie": cells[6], "token": cells[7], "note": note})
        rnums.append(i + 2)  # dòng 1 là tiêu đề
    return items, rnums


async def mark_imported(sid: str, row_numbers: list, label: str) -> bool:
    """Đánh dấu các dòng đã nhập vào lô (cột I)."""
    if not row_numbers:
        return True
    try:
        import json as _json
        data = [{"range": f"{IMPORT_TAB}!I{r}",
                 "values": [[label]]} for r in row_numbers]
        await _si._cli(["sheets", "spreadsheets", "values", "batchUpdate", "--params",
                        _json.dumps({"spreadsheetId": sid,
                                     "valueInputOption": "USER_ENTERED"})],
                       {"data": data, "valueInputOption": "USER_ENTERED"})
        return True
    except Exception as e:
        log.warning("mark_imported lỗi (%s): %s", sid, e)
        return False
