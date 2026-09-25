"""Nhập kho từ Google Sheet: đọc dòng chưa đánh dấu + ghi trạng thái ngược.

Dùng hatch_gws_cli (auth đã có sẵn qua connector), chạy trong executor
để không block event loop của backend.

Mỗi gian hàng (stall) có 1 tab riêng trong sheet; tab được tự tạo khi
tạo gian hàng mới (ensure_tab).
"""
import asyncio
import json
import logging
import re
import shutil
import subprocess

log = logging.getLogger(__name__)

CLI = shutil.which("hatch_gws_cli") or "/opt/hatch/bin/hatch_gws_cli"

#: Hàng tiêu đề chuẩn ghi khi tự tạo tab mới
SHEET_HEADERS = ["UID", "Mật khẩu", "Ngày tạo", "Mail thay", "Ghi chú",
                 "2FA", "Cookie", "Token", "Trạng thái", "Đã bán", "Tình trạng",
                 "Loại / Gian hàng"]

#: Cột J (1-based) — ghi dấu "đã bán" khi acc được khách mua
SOLD_COL = 10

#: Cột K (1-based) — ghi tình trạng acc (🟢 LIVE / ☠️ DIE) mỗi khi bot check kho.
#: Cột I "Trạng thái" chỉ giữ trạng thái nhập kho, không bị ghi đè nữa.
HEALTH_COL = 11

#: Cột L (1-based) — loại acc + gian hàng chứa acc ("#17 Tên loại · 🏪 Acc Facebook").
#: Ghi lúc nhập kho, refresh lại mỗi lần re-check kho (mọi gian hàng).
CAT_COL = 12
CAT_HEADER = "Loại / Gian hàng"

#: Giá trị hợp lệ của cột K
HEALTH_MARKS = ("🟢 LIVE", "☠️ DIE")

#: Gian hàng mặc định (acc Facebook) dùng tab chung cũ
DEFAULT_STALL = "Acc Facebook"


def sanitize_tab_name(name: str) -> str:
    """Làm sạch tên tab: bỏ ký tự Google cấm ( / \\ ? * [ ] ), cắt 90 ký tự."""
    t = re.sub(r"[\/\\?*\[\]]", " ", (name or "").strip())
    t = re.sub(r"\s+", " ", t).strip()
    return t[:90] or "Sheet"


def tab_for_stall(stall: str, default_tab: str = "NhapKho") -> str:
    """Tên tab Sheet của 1 gian hàng. Sạp FB mặc định dùng tab chung cũ."""
    s = (stall or "").strip() or DEFAULT_STALL
    if s == DEFAULT_STALL:
        return default_tab or "NhapKho"
    return sanitize_tab_name(s)


async def tab_exists(spreadsheet_id: str, tab: str) -> bool:
    """Kiểm tra tab đã có trong spreadsheet chưa."""
    try:
        d = await _cli(["sheets", "spreadsheets", "get", "--params",
                        json.dumps({"spreadsheetId": spreadsheet_id,
                                    "fields": "sheets.properties.title"})])
        titles = [s.get("properties", {}).get("title", "")
                  for s in (d.get("sheets") or [])]
        return tab in titles
    except Exception as e:
        log.warning("tab_exists lỗi: %s", e)
        return False


async def ensure_tab(spreadsheet_id: str, tab: str) -> bool:
    """Đảm bảo tab tồn tại; chưa có thì tạo mới + ghi hàng tiêu đề.
    Trả True nếu tab sẵn sàng dùng, False nếu lỗi."""
    if not spreadsheet_id or not tab:
        return False
    try:
        if await tab_exists(spreadsheet_id, tab):
            return True
        # Tạo tab mới
        await _cli(["sheets", "spreadsheets", "batchUpdate", "--params",
                    json.dumps({"spreadsheetId": spreadsheet_id})],
                   {"requests": [{"addSheet": {"properties": {"title": tab}}}]})
        # Ghi hàng tiêu đề A1:L1
        await _cli(["sheets", "spreadsheets", "values", "update", "--params",
                    json.dumps({"spreadsheetId": spreadsheet_id,
                                "range": f"{tab}!A1:L1",
                                "valueInputOption": "USER_ENTERED"})],
                   {"values": [SHEET_HEADERS]})
        log.info("ensure_tab: đã tạo tab '%s'", tab)
        return True
    except Exception as e:
        log.warning("ensure_tab lỗi (tab=%s): %s", tab, e)
        return False


def _run_cli(args, payload=None):
    cmd = [CLI] + args
    if payload is not None:
        cmd += ["--json", json.dumps(payload)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or "").strip()[:300] or f"CLI exit {p.returncode}")
    try:
        return json.loads(p.stdout) if p.stdout.strip() else {}
    except Exception:
        return {}


async def _cli(args, payload=None):
    return await asyncio.to_thread(_run_cli, args, payload)


async def read_unmarked(spreadsheet_id: str, tab: str):
    """Đọc các dòng chưa có đánh dấu ở cột I (Trạng thái).
    Trả về [(số_dòng_sheet, [A..H])]. Dòng 1 là tiêu đề nên đọc từ A2."""
    d = await _cli(["sheets", "spreadsheets", "values", "get", "--params",
                    json.dumps({"spreadsheetId": spreadsheet_id, "range": f"{tab}!A2:I"})])
    out = []
    for i, vals in enumerate(d.get("values") or []):
        cells = [str(v or "").strip() for v in vals]
        while len(cells) < 9:
            cells.append("")
        if cells[8]:
            continue  # đã đánh dấu -> bỏ qua
        if not any(cells[:8]):
            continue  # dòng trống
        out.append((i + 2, cells[:8]))
    return out


async def read_marked(spreadsheet_id: str, tab: str):
    """Đọc các dòng ĐÃ có đánh dấu ở cột I (đã nhập kho trước đó).
    Trả về [(số_dòng_sheet, [A..H])]. Dùng cho cập nhật thông tin từ Sheet."""
    d = await _cli(["sheets", "spreadsheets", "values", "get", "--params",
                    json.dumps({"spreadsheetId": spreadsheet_id, "range": f"{tab}!A2:I"})])
    out = []
    for i, vals in enumerate(d.get("values") or []):
        cells = [str(v or "").strip() for v in vals]
        while len(cells) < 9:
            cells.append("")
        if not cells[8]:
            continue  # chưa đánh dấu -> dòng mới, không thuộc phạm vi cập nhật
        if not any(cells[:8]):
            continue  # dòng trống
        out.append((i + 2, cells[:8]))
    return out


def _col_name(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


async def write_updates(spreadsheet_id: str, tab: str, updates):
    """Ghi 1 lần batchUpdate. updates: [(số_dòng, số_cột(1=A), giá_trị)]."""
    if not updates:
        return
    data = [{"range": f"{tab}!{_col_name(c)}{r}:{_col_name(c)}{r}",
             "values": [[v]]}
            for (r, c, v) in updates]
    await _cli(["sheets", "spreadsheets", "values", "batchUpdate", "--params",
                json.dumps({"spreadsheetId": spreadsheet_id})],
               {"valueInputOption": "USER_ENTERED", "data": data})


def build_health_items(qrows, live_ids, die_ids):
    """Dựng danh sách ghi cột K từ các dòng kho có sheet_ref.

    qrows: iterable dict {id, uid, sheet_ref, status}.
    live_ids: id vừa check live; die_ids: id vừa bị cách ly.
    Acc DIE (kể cả cách ly từ trước khi có cột K) -> "☠️ DIE";
    acc vừa check live -> "🟢 LIVE"; còn lại bỏ qua (giữ nguyên ô cũ).
    Trả list {"uid","tab","row","mark"} (dedupe theo cặp tab:row).
    """
    live_ids = {int(i) for i in (live_ids or [])}
    die_ids = {int(i) for i in (die_ids or [])}
    items, seen = [], set()
    for r in qrows or []:
        try:
            sid = int(r["id"])
            uid = str(r["uid"])
            tab, row = str(r["sheet_ref"] or "").rsplit(":", 1)
            row = int(row)
            status = str(r.get("status") or "")
        except Exception:
            continue
        if sid in die_ids or status == "DIE":
            mark = "☠️ DIE"
        elif sid in live_ids:
            mark = "🟢 LIVE"
        else:
            continue
        if (tab, row) in seen:
            continue
        seen.add((tab, row))
        items.append({"uid": uid, "tab": tab, "row": row, "mark": mark})
    return items


async def push_health_marks(spreadsheet_id: str, items):
    """Ghi tình trạng acc (🟢 LIVE / ☠️ DIE) lên cột K cho các dòng đã nhập kho.

    items: list dict {"uid","tab","row","mark"}.
    Mỗi tab: đảm bảo tiêu đề K1, đọc cột A để kiểm tra UID khớp mới ghi
    (tránh ghi nhầm khi dòng trên Sheet bị xóa/sắp xếp lại).
    Trả (done, skip) — số dòng đã ghi / bỏ qua (không khớp UID).
    """
    done, skip = 0, 0
    if not spreadsheet_id or not items:
        return done, skip
    by_tab = {}
    for it in items:
        try:
            mark = str(it.get("mark") or "")
            if mark not in HEALTH_MARKS:
                continue
            by_tab.setdefault(it["tab"], []).append(
                {"uid": str(it["uid"]).strip(),
                 "row": int(it["row"]), "mark": mark})
        except Exception:
            continue
    for tab, lst in by_tab.items():
        try:
            # 1. Đọc cột A các dòng để kiểm tra UID
            ranges = [f"{tab}!A{r['row']}" for r in lst]
            d = await _cli(["sheets", "spreadsheets", "values", "batchGet",
                            "--params",
                            json.dumps({"spreadsheetId": spreadsheet_id,
                                        "ranges": ranges})])
            cell_by_row = {}
            for vr in d.get("valueRanges") or []:
                try:
                    rng = str(vr.get("range") or "")
                    rr = int(rng.rsplit("!A", 1)[1])
                    vals = (vr.get("values") or [[]])[0]
                    cell_by_row[rr] = str(vals[0]).strip() if vals else ""
                except Exception:
                    continue
            # 2. Ghi: tiêu đề K1 + tình trạng cho các dòng khớp UID
            data = [{"range": f"{tab}!K1:K1", "values": [["Tình trạng"]]}]
            for r in lst:
                if cell_by_row.get(r["row"], "") == r["uid"]:
                    data.append({"range": f"{tab}!K{r['row']}:K{r['row']}",
                                 "values": [[r["mark"]]]})
                    done += 1
                else:
                    skip += 1
            await _cli(["sheets", "spreadsheets", "values", "batchUpdate",
                        "--params", json.dumps({"spreadsheetId": spreadsheet_id})],
                       {"valueInputOption": "USER_ENTERED", "data": data})
        except Exception as e:
            log.warning("push_health_marks lỗi (tab=%s): %s", tab, e)
    return done, skip


def cat_label(cat_id, name, stall) -> str:
    """Nhãn cột L 'Loại / Gian hàng'. VD: '#17 Acc clone · 🏪 Acc Facebook'."""
    name = (str(name or "").strip()) or f"loại #{cat_id}"
    stall = (str(stall or "").strip()) or DEFAULT_STALL
    return f"#{cat_id} {name} · 🏪 {stall}"


def build_cat_items(qrows):
    """Dựng danh sách ghi cột L từ các dòng kho có sheet_ref.

    qrows: iterable dict {uid, sheet_ref, cat_id, cat_name, stall}.
    Trả list {"uid","tab","row","label"} (dedupe theo cặp tab:row).
    """
    items, seen = [], set()
    for r in qrows or []:
        try:
            uid = str(r["uid"])
            tab, row = str(r["sheet_ref"] or "").rsplit(":", 1)
            row = int(row)
            cid = int(r.get("cat_id") or 0)
        except Exception:
            continue
        if (tab, row) in seen:
            continue
        seen.add((tab, row))
        items.append({"uid": uid, "tab": tab, "row": row,
                      "label": cat_label(cid, r.get("cat_name"), r.get("stall"))})
    return items


async def push_cat_marks(spreadsheet_id: str, items):
    """Ghi nhãn loại/gian hàng lên cột L cho các dòng đã nhập kho.

    items: list dict {"uid","tab","row","label"}.
    Mỗi tab: đảm bảo tiêu đề L1, đọc cột A để kiểm tra UID khớp mới ghi
    (tránh ghi nhầm khi dòng trên Sheet bị xóa/sắp xếp lại).
    Trả (done, skip) — số dòng đã ghi / bỏ qua (không khớp UID).
    """
    done, skip = 0, 0
    if not spreadsheet_id or not items:
        return done, skip
    by_tab = {}
    for it in items:
        try:
            label = str(it.get("label") or "").strip()
            if not label:
                continue
            by_tab.setdefault(it["tab"], []).append(
                {"uid": str(it["uid"]).strip(),
                 "row": int(it["row"]), "label": label})
        except Exception:
            continue
    for tab, lst in by_tab.items():
        try:
            # 1. Đọc cột A các dòng để kiểm tra UID
            ranges = [f"{tab}!A{r['row']}" for r in lst]
            d = await _cli(["sheets", "spreadsheets", "values", "batchGet",
                            "--params",
                            json.dumps({"spreadsheetId": spreadsheet_id,
                                        "ranges": ranges})])
            cell_by_row = {}
            for vr in d.get("valueRanges") or []:
                try:
                    rng = str(vr.get("range") or "")
                    rr = int(rng.rsplit("!A", 1)[1])
                    vals = (vr.get("values") or [[]])[0]
                    cell_by_row[rr] = str(vals[0]).strip() if vals else ""
                except Exception:
                    continue
            # 2. Ghi: tiêu đề L1 + nhãn loại/gian hàng cho các dòng khớp UID
            col = _col_name(CAT_COL)
            data = [{"range": f"{tab}!{col}1:{col}1", "values": [[CAT_HEADER]]}]
            for r in lst:
                if cell_by_row.get(r["row"], "") == r["uid"]:
                    data.append({"range": f"{tab}!{col}{r['row']}:{col}{r['row']}",
                                 "values": [[r["label"]]]})
                    done += 1
                else:
                    skip += 1
            await _cli(["sheets", "spreadsheets", "values", "batchUpdate",
                        "--params", json.dumps({"spreadsheetId": spreadsheet_id})],
                       {"valueInputOption": "USER_ENTERED", "data": data})
        except Exception as e:
            log.warning("push_cat_marks lỗi (tab=%s): %s", tab, e)
    return done, skip


def _fmt_sold(sold_at: int) -> str:
    import time as _t
    try:
        return "🛒 ĐÃ BÁN " + _t.strftime("%d/%m %H:%M", _t.localtime(int(sold_at or 0)))
    except Exception:
        return "🛒 ĐÃ BÁN"


async def push_sold_marks(spreadsheet_id: str, items):
    """Ghi dấu 'đã bán' lên cột J cho các acc đã bán.

    items: list dict {"id","uid","tab","row","sold_at"}.
    Mỗi tab: đảm bảo tiêu đề J1, đọc cột A để kiểm tra UID khớp mới ghi
    (tránh ghi nhầm khi dòng trên Sheet bị xóa/sắp xếp lại).
    Trả (done_ids, skip_ids) — skip là dòng không khớp UID (vẫn đánh dấu
    đã xử lý để không thử lại vô hạn).
    """
    done, skip = [], []
    if not spreadsheet_id or not items:
        return done, skip
    by_tab = {}
    for it in items:
        try:
            by_tab.setdefault(it["tab"], []).append(it)
        except Exception:
            continue
    for tab, lst in by_tab.items():
        rows = []
        for it in lst:
            try:
                r = int(it["row"])
            except Exception:
                continue
            rows.append((r, it))
        if not rows:
            continue
        try:
            # 1. Đọc cột A các dòng để kiểm tra UID
            ranges = [f"{tab}!A{r}" for r, _ in rows]
            d = await _cli(["sheets", "spreadsheets", "values", "batchGet",
                            "--params",
                            json.dumps({"spreadsheetId": spreadsheet_id,
                                        "ranges": ranges})])
            vrs = d.get("valueRanges") or []
            cell_by_row = {}
            for vr in vrs:
                try:
                    rng = str(vr.get("range") or "")
                    rr = int(rng.rsplit("!A", 1)[1])
                    vals = (vr.get("values") or [[]])[0]
                    cell_by_row[rr] = str(vals[0]).strip() if vals else ""
                except Exception:
                    continue
            matched, mismatched = [], []
            for r, it in rows:
                if cell_by_row.get(r, "") == str(it["uid"]).strip():
                    matched.append((r, it))
                else:
                    mismatched.append(it)
            # 2. Ghi: tiêu đề J1 + dấu đã bán cho các dòng khớp
            data = [{"range": f"{tab}!J1:J1", "values": [["Đã bán"]]}]
            for r, it in matched:
                data.append({"range": f"{tab}!J{r}:J{r}",
                             "values": [[_fmt_sold(it.get("sold_at"))]]})
            await _cli(["sheets", "spreadsheets", "values", "batchUpdate",
                        "--params", json.dumps({"spreadsheetId": spreadsheet_id})],
                       {"valueInputOption": "USER_ENTERED", "data": data})
            done.extend(it["id"] for _, it in matched)
            skip.extend(it["id"] for it in mismatched)
        except Exception as e:
            log.warning("push_sold_marks lỗi (tab=%s): %s", tab, e)
    return done, skip
