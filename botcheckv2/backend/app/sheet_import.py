"""Nhập kho từ Google Sheet: đọc dòng chưa đánh dấu + ghi trạng thái ngược.

Dùng hatch_gws_cli (auth đã có sẵn qua connector), chạy trong executor
để không block event loop của backend.
"""
import asyncio
import json
import logging
import shutil
import subprocess

log = logging.getLogger(__name__)

CLI = shutil.which("hatch_gws_cli") or "/opt/hatch/bin/hatch_gws_cli"


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
