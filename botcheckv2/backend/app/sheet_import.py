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
                 "2FA", "Cookie", "Token", "Trạng thái", "Đã bán"]

#: Cột J (1-based) — ghi dấu "đã bán" khi acc được khách mua
SOLD_COL = 10

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
        # Ghi hàng tiêu đề A1:J1
        await _cli(["sheets", "spreadsheets", "values", "update", "--params",
                    json.dumps({"spreadsheetId": spreadsheet_id,
                                "range": f"{tab}!A1:J1",
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
