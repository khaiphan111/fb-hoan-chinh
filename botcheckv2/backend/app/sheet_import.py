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
                 "2FA", "Cookie", "Token", "Trạng thái"]

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
        # Ghi hàng tiêu đề A1:I1
        await _cli(["sheets", "spreadsheets", "values", "update", "--params",
                    json.dumps({"spreadsheetId": spreadsheet_id,
                                "range": f"{tab}!A1:I1",
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
