"""Tách từ app/bot.py (refactor 2026-09-25) — giữ nguyên 100% logic, chỉ chia module theo tính năng."""

import asyncio
import html
import logging
import os
import random
import time
from typing import Optional

import httpx
import re
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, CommandObject, StateFilter
from aiogram.types import (
    BotCommand, Message, URLInputFile, FSInputFile, BufferedInputFile,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup



from .. import db
from .. import perms as _perms
from .. import notify_bot as _notify_bot
from .. import config as _config
from ..persist import SQLiteStorage, check_cache_get, check_cache_set, init_cache_db
from ..util import now, parse_check_args, vnd, vn_time_str
from ..tiktok import parse_username, fetch_tiktok_info, fmt_num, build_info_caption
from ..ig import (
    parse_ig_username, parse_ig_post_id,
    fetch_ig_info, fetch_ig_post_info,
    build_ig_info_caption, build_ig_video_caption
)
from ..fb import check_uid, build_fb_caption
from ..poller import poller

from .core import _is_admin, log, router
from .common import AccShopState, _build_excel_bytes, _cat_icon, _do_deposit, _fmt_warranty, _import_stock_rows, _loyalty_gift_desc, _mail_app_link, _parse_stock_file, _parse_warranty, _price_change_ok, _price_warn_pct, _resolve_stock_links, _run_sheet_import, _shop_bulk_pcts, _shop_list, _shop_stall_picker

class SheetImportState(StatesGroup):
    waiting_ncc_cost = State()

async def auto_import_stall(stall: str, cat_id: int, ncc_id: int, cost: int, bot) -> dict:
    """Nhập kho tự động 1 gian hàng từ tab Sheet riêng của nó (headless, chạy nền).

    Tái dùng pipeline _import_stock_rows (chống trùng, giải link FB, ghi trạng thái
    ngược cột I, quét chất lượng nền, trả đơn đặt trước...).
    Trả về {'added': int, 'rows': int} hoặc {'error': str}.
    """
    from types import SimpleNamespace
    c = db.acc_category_get(cat_id)
    if not c:
        return {"error": f"Loại acc #{cat_id} không tồn tại"}
    if ncc_id and not db.supplier_get(ncc_id):
        return {"error": f"NCC #{ncc_id} không tồn tại"}
    sid = (db.get_setting("sheet_import_id") or "").strip()
    if not sid:
        return {"error": "Chưa cài đặt Google Sheet (/setsheet)"}
    default_tab = db.get_setting("sheet_import_tab") or "NhapKho"
    from .. import sheet_import as _si
    tab = _si.tab_for_stall(stall, default_tab)
    try:
        if not await _si.ensure_tab(sid, tab):
            return {"error": f"Không tạo/kiểm tra được tab '{tab}'"}
        sheet_rows = await _si.read_unmarked(sid, tab)
    except Exception as e:
        return {"error": f"Không đọc được sheet: {str(e)[:150]}"}
    if not sheet_rows:
        return {"added": 0, "rows": 0}
    rows = [{
        "uid": cells[0], "password": cells[1], "created_date": cells[2],
        "backup_mail": cells[3], "note": cells[4], "totp": cells[5],
        "cookie": cells[6], "token": cells[7], "_sheet_row": rnum,
    } for rnum, cells in sheet_rows]

    async def _noop(*a, **k):
        return None

    class _Wait:
        async def edit_text(self, *a, **k):
            return None

    owner_id = 0
    try:
        owner_id = int(_perms.super_id() or 0)
    except Exception:
        pass
    shim = SimpleNamespace(bot=bot, document=None,
                           from_user=SimpleNamespace(id=owner_id),
                           answer=_noop)
    before = db.acc_stock_count(cat_id)
    try:
        await _import_stock_rows(rows, cat_id, ncc_id, cost, c, shim, _Wait(),
                                sheet_ctx={"sheet_id": sid, "tab": tab})
    except Exception as e:
        log.warning("auto_import_stall %s: %s", stall, e)
        return {"error": f"Lỗi nhập kho: {str(e)[:150]}"}
    after = db.acc_stock_count(cat_id)
    return {"added": max(0, after - before), "rows": len(rows)}

@router.message(F.text == "\U0001f4ca Nhập kho Sheet")
async def on_sheet_button(msg: Message, state: FSMContext):
    """Nút bấm cố định trên bàn phím admin: mở flow nhập kho từ Sheet."""
    if not _is_admin(msg.from_user.id):
        return
    if not _perms.has_perm(msg.from_user.id, "kho"):
        await msg.answer("🚫 Bạn không có quyền 📦 Kho & loại acc.")
        return
    await state.clear()
    sid = db.get_setting("sheet_import_id") or ""
    tab = db.get_setting("sheet_import_tab") or "NhapKho"
    cats = db.acc_category_list(active_only=False, include_hidden=True)
    if not cats:
        await msg.answer("❌ Chưa có loại acc nào. Tạo trước bằng /themloai")
        return
    kb_rows, row = [], []
    for cc in cats:
        dd = dict(cc)
        name = (dd.get("name") or f"Loại {dd['id']}")[:18]
        row.append(InlineKeyboardButton(text=f"{dd['id']}. {name}",
                                       callback_data=f"sheetpick_{dd['id']}"))
        if len(row) == 2:
            kb_rows.append(row)
            row = []
    if row:
        kb_rows.append(row)
    txt = "📊 <b>NHẬP KHO TỪ GOOGLE SHEET</b>\n"
    if sid:
        txt += (f'🔗 <a href="https://docs.google.com/spreadsheets/d/{sid}/edit">Mở sheet</a> '
                f"(tab {html.escape(tab)})\n")
    txt += ("\n<i>Chỉ quét dòng chưa có đánh dấu ở cột Trạng thái.</i>\n"
            "\n👇 <b>Chọn loại acc để nhập:</b>")
    await msg.answer(txt, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
                     parse_mode="HTML", disable_web_page_preview=True)

@router.callback_query(F.data.startswith("sheetpick_"))
async def on_sheet_pick(cb: CallbackQuery, state: FSMContext):
    if not _is_admin(cb.from_user.id):
        await cb.answer("🚫")
        return
    if not _perms.has_perm(cb.from_user.id, "kho"):
        await cb.answer("🚫 Không có quyền.", show_alert=True)
        return
    try:
        cat_id = int(cb.data.split("_", 1)[1])
    except Exception:
        await cb.answer("❌")
        return
    c = db.acc_category_get(cat_id)
    if not c:
        await cb.answer("❌ Loại không tồn tại", show_alert=True)
        return
    await state.update_data(sheet_cat_id=cat_id)
    await state.set_state(SheetImportState.waiting_ncc_cost)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Nhập luôn (không NCC/vốn)",
                              callback_data=f"sheetgo_{cat_id}_0_0")],
    ])
    await cb.message.answer(
        f"📦 Loại: <b>{html.escape(c['name'])}</b> (#{cat_id})\n\n"
        "Nhập <code>ncc_id giá_vốn</code> (vd: <code>3 15000</code>)\n"
        "hoặc bấm nút để nhập luôn:",
        reply_markup=kb, parse_mode="HTML")
    await cb.answer()

@router.message(SheetImportState.waiting_ncc_cost)
async def on_sheet_ncc_cost(msg: Message, state: FSMContext):
    if not _is_admin(msg.from_user.id):
        await state.clear()
        return
    if not _perms.has_perm(msg.from_user.id, "kho"):
        await state.clear()
        await msg.answer("🚫 Bạn không có quyền 📦 Kho & loại acc.")
        return
    data = await state.get_data()
    cat_id = data.get("sheet_cat_id")
    await state.clear()
    if not cat_id:
        return
    parts = (msg.text or "").split()
    try:
        ncc_id = int(parts[0]) if len(parts) > 0 else 0
        cost = int(parts[1].replace(".", "").replace(",", "")) if len(parts) > 1 else 0
    except Exception:
        await msg.answer("❌ Nhập số thôi, vd: <code>3 15000</code> — hoặc bấm lại nút 📊 để làm lại.",
                         parse_mode="HTML")
        return
    await _run_sheet_import(msg, cat_id, ncc_id, cost)

@router.callback_query(F.data.startswith("sheetgo_"))
async def on_sheet_go(cb: CallbackQuery, state: FSMContext):
    if not _is_admin(cb.from_user.id):
        await cb.answer("🚫")
        return
    if not _perms.has_perm(cb.from_user.id, "kho"):
        await cb.answer("🚫 Không có quyền.", show_alert=True)
        return
    await state.clear()
    try:
        _, cat_id, ncc_id, cost = cb.data.split("_")
    except Exception:
        await cb.answer("❌")
        return
    await cb.answer("⏳ Đang nhập kho...")
    await _run_sheet_import(cb.message, int(cat_id), int(ncc_id), int(cost))

@router.message(Command("khoakey"))
async def on_khoakey(msg: Message):
    """Admin: khóa/mở API key reseller. Cú pháp: /khoakey <key_id> [on|off]"""
    from ..admin_bot import is_admin
    if not is_admin(msg.chat.id, msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: <code>/khoakey &lt;key_id&gt; [on|off]</code>")
        return
    try:
        key_id = int(parts[1])
    except ValueError:
        await msg.answer("❌ key_id phải là số!")
        return
    active = True
    if len(parts) > 2:
        active = parts[2].lower() not in ("off", "0", "khoa", "khóa", "lock")
    db.set_reseller_active(key_id, active)
    await msg.answer(f"{'🟢 Đã mở' if active else '🔴 Đã khóa'} key <code>{key_id}</code>.", parse_mode="HTML")

@router.callback_query(F.data.startswith("shopstall:"))
async def on_shop_stall(cb: CallbackQuery):
    arg = (cb.data or "").split(":", 1)[1]
    await cb.answer()
    if arg == "__all__":
        try:
            await cb.message.delete()
        except Exception:
            pass
        await _shop_stall_picker(cb.message)
        return
    await _shop_list(cb.message, arg, edit=True, user_id=cb.from_user.id)

@router.message(Command("giasi"))
async def on_giasi(msg: Message):
    """Bảng giá sỉ: giá mỗi acc khi mua 1 / 5 / 10 / 20."""
    cats = db.acc_category_list()
    if not cats:
        await msg.answer("💼 Shop hiện chưa có mặt hàng nào.")
        return
    p5, p10, p20 = _shop_bulk_pcts()
    lines = ["💼 <b>BẢNG GIÁ SỈ</b> <i>(giá mỗi acc)</i>", "━━━━━━━━━━━━", ""]
    for c in cats:
        c = dict(c)
        up = db.shop_unit_price(c)
        base = up["price"]
        n = up["stock"]
        if n <= 0:
            continue
        u5 = base * (100 - p5) // 100
        u10 = base * (100 - p10) // 100
        u20 = base * (100 - p20) // 100
        lines.append(f"{_cat_icon(c['name'])} <b>{html.escape(c['name'])}</b> — còn {n}")
        lines.append(f"   Lẻ: <b>{vnd(base)}</b> | 5: <b>{vnd(u5)}</b> | "
                     f"10: <b>{vnd(u10)}</b> | 20: <b>{vnd(u20)}</b>")
        lines.append("")
    lines.append("<i>Mua càng nhiều càng rẻ. Vào /shop chọn loại rồi bấm nút số lượng.</i>")
    await msg.answer("\n".join(lines), parse_mode="HTML")

@router.message(Command("damua"))
async def on_damua(msg: Message):
    orders = db.acc_user_orders(msg.from_user.id, 10)
    if not orders:
        await msg.answer("🧾 Bạn chưa mua acc nào. Xem shop: /shop")
        return
    t = db.member_tier_info(msg.from_user.id)
    pts = db.loyalty_get(msg.from_user.id)
    lines = ["🧾 <b>ACC ĐÃ MUA</b>", "━━━━━━━━━━━━",
             f"🏅 Hạng: <b>{t['tier']}</b> — ⭐ Điểm loyalty: <b>{pts}</b>",
             f"💰 Tổng đã mua: <b>{vnd(t['spent'])}</b>", ""]
    kb_rows = []
    oids = [dict(o)["id"] for o in orders]
    totp_orders = set()
    if oids:
        try:
            for r in db.get_conn().execute(
                    f"SELECT o.id FROM acc_orders o WHERE o.id IN ({','.join('?' * len(oids))}) "
                    "AND o.totp IS NOT NULL AND o.totp != ''",
                    tuple(oids)).fetchall():
                totp_orders.add(int(r["id"]))
        except Exception:
            pass
    for o in orders:
        o = dict(o)
        lines.append(
            f"#{o['id']} — <b>{html.escape(o['cat_name'])}</b> — {vnd(o['price'])} "
            f"({vn_time_str(ts=o['created_at'])})"
        )
        kb_rows.append([InlineKeyboardButton(
            text=f"🛡 Bảo hành đơn #{o['id']}",
            callback_data=f"accwarranty:{o['id']}")])
        if o["id"] in totp_orders:
            kb_rows.append([InlineKeyboardButton(
                text=f"🔑 Mã 2FA đơn #{o['id']}",
                callback_data=f"acc2fa:{o['id']}")])
        # 4.11 Đánh giá có thưởng (mỗi đơn 1 lần)
        if not db.acc_order_reviewed(o["id"]):
            kb_rows.append([InlineKeyboardButton(
                text=f"⭐ Đánh giá đơn #{o['id']} (+credits)",
                callback_data=f"accreview:{o['id']}")])
    lines += ["", "Acc die trong thời gian bảo hành thì bấm nút bên dưới."]
    await msg.answer("\n".join(lines), parse_mode="HTML",
                     reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))

@router.message(Command("themloai"))
async def on_themloai(msg: Message):
    """Thêm loại acc. Cú pháp: /themloai Tên loại | giá | bảo_hành (30p|24h|2 ngày|1 tuần) | mô tả [| gian hàng]"""
    if not _is_admin(msg.from_user.id):
        return
    raw = (msg.text or "").split(maxsplit=1)
    if len(raw) < 2 or "|" not in raw[1]:
        await msg.answer(
            "⚠️ Cú pháp: <code>/themloai Tên loại | giá | bảo_hành | mô tả [| gian hàng]</code>\n"
            "Bảo hành: <code>30p</code> (30 phút) | <code>24h</code> | <code>2 ngày</code> | <code>1 tuần</code>\n"
            "VD: <code>/themloai Via Việt | 25000 | 24h | Via VN 50-500 bạn</code>\n"
            "VD sạp khác: <code>/themloai Gmail Edu | 15000 | 24h | Gmail edu | Gmail</code>",
            parse_mode="HTML",
        )
        return
    parts = [p.strip() for p in raw[1].split("|")]
    try:
        name = parts[0]
        price = int(parts[1].replace(".", "").replace(",", "").replace(" ", ""))
        desc = parts[3] if len(parts) > 3 else ""
        stall_raw = parts[4] if len(parts) > 4 else ""
    except Exception:
        await msg.answer("❌ Giá phải là số.")
        return
    wh = _parse_warranty(parts[2])
    if wh is None:
        await msg.answer("❌ Bảo hành không hợp lệ. Nhập dạng: <code>30p</code> (30 phút) | <code>24h</code> | <code>2 ngày</code> | <code>1 tuần</code>",
                         parse_mode="HTML")
        return
    # Gian hàng: mặc định sạp Acc Facebook; nếu chỉ định thì phải là sạp đã có
    stall = "Acc Facebook"
    live_check = 1
    if stall_raw:
        canon = ""
        try:
            for s in db.acc_stall_list():
                sn = (s.get("stall") or "").strip()
                if sn and sn.lower() == stall_raw.lower():
                    canon = sn
                    break
        except Exception:
            pass
        if not canon:
            await msg.answer(
                f"❌ Chưa có gian hàng <b>{html.escape(stall_raw)}</b>. "
                f"Tạo sạp mới trước bằng nút 🏪 <b>Thêm gian hàng mới</b> trong /shopadm nhé.",
                parse_mode="HTML")
            return
        stall = canon
        live_check = db.acc_stall_live_check(stall)
    cid = db.acc_category_add(name, price, wh, desc, stall=stall,
                              live_check=live_check)
    if cid == -1:
        await msg.answer("❌ Tên loại này đã tồn tại.")
        return
    await msg.answer(
        f"✅ Đã thêm loại acc <b>#{cid} — {html.escape(name)}</b>\n"
        f"🏪 Gian hàng: <b>{html.escape(stall)}</b>\n"
        f"💰 Giá: {vnd(price)} | 🛡 BH: {_fmt_warranty(wh)}\n"
        f"Nhập hàng: <code>/themacc {cid}</code>",
        parse_mode="HTML",
    )

@router.message(Command("themstall"))
async def on_themstall(msg: Message):
    """Thêm gian hàng mới + loại hàng đầu tiên.
    Cú pháp: /themstall Tên gian hàng | Tên loại | giá | bảo_hành | mô tả
    VD: /themstall Gmail | Gmail Edu | 15000 | 24h | Gmail edu giá rẻ"""
    if not _is_admin(msg.from_user.id):
        return
    raw = (msg.text or "").split(maxsplit=1)
    if len(raw) < 2 or "|" not in raw[1]:
        await msg.answer(
            "⚠️ Cú pháp: <code>/themstall Tên gian hàng | Tên loại | giá | bảo_hành | mô tả</code>\n"
            "VD: <code>/themstall Gmail | Gmail Edu | 15000 | 24h | Gmail edu</code>\n"
            "<i>Gian hàng mới KHÔNG check live/die (chỉ sạp Acc Facebook mới check).</i>",
            parse_mode="HTML",
        )
        return
    parts = [p.strip() for p in raw[1].split("|")]
    if len(parts) < 4:
        await msg.answer("❌ Thiếu tham số. Cần: <code>Tên gian hàng | Tên loại | giá | bảo_hành</code>",
                         parse_mode="HTML")
        return
    try:
        stall, name = parts[0], parts[1]
        price = int(parts[2].replace(".", "").replace(",", "").replace(" ", ""))
        desc = parts[4] if len(parts) > 4 else ""
    except Exception:
        await msg.answer("❌ Giá phải là số.")
        return
    if not stall or not name:
        await msg.answer("❌ Tên gian hàng và tên loại không được trống.")
        return
    wh = _parse_warranty(parts[3])
    if wh is None:
        await msg.answer("❌ Bảo hành không hợp lệ (<code>30p</code>|<code>24h</code>|<code>2 ngày</code>|<code>1 tuần</code>).",
                         parse_mode="HTML")
        return
    cid = db.acc_category_add(name, price, wh, desc, stall=stall, live_check=0)
    if cid == -1:
        await msg.answer("❌ Tên loại này đã tồn tại.")
        return
    # Tự tạo tab Sheet cho gian hàng mới (chạy nền, lỗi thì báo sau)
    sid = db.get_setting("sheet_import_id") or ""
    default_tab = db.get_setting("sheet_import_tab") or "NhapKho"
    from .. import sheet_import as _si
    tab = _si.tab_for_stall(stall, default_tab)

    async def _mk_tab():
        try:
            ok = await _si.ensure_tab(sid, tab) if sid else False
            if not ok and sid:
                await msg.answer(f"⚠️ Đã tạo gian hàng nhưng <b>chưa tạo được tab Sheet</b> "
                                 f"(<code>{html.escape(tab)}</code>). Vào /shopadm → Nhập kho Sheet để thử lại.",
                                 parse_mode="HTML")
        except Exception as e:
            log.warning("themstall ensure_tab lỗi: %s", e)

    await msg.answer(
        f"🏪 Đã tạo gian hàng <b>{html.escape(stall)}</b>\n"
        f"✅ Loại hàng <b>#{cid} — {html.escape(name)}</b> | 💰 {vnd(price)} | 🛡 {_fmt_warranty(wh)}\n"
        f"📊 Tab Sheet: <code>{html.escape(tab)}</code> (đang tạo...)\n"
        f"Nhập hàng: <code>/themacc {cid}</code> hoặc /shopadm → 📊 Nhập kho Sheet",
        parse_mode="HTML",
    )
    asyncio.create_task(_mk_tab())

@router.message(Command("themacc"))
async def on_themacc(msg: Message, state: FSMContext):
    """Nhập kho: /themacc <id_loại> [ncc_id] [giá_vốn] rồi gửi file .txt"""
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: <code>/themacc &lt;id_loại&gt; [ncc_id] [giá_vốn]</code> (xem id: /kho)",
                         parse_mode="HTML")
        return
    try:
        cat_id = int(parts[1])
        ncc_id = int(parts[2]) if len(parts) > 2 else 0
        cost = int(parts[3].replace(".", "").replace(",", "")) if len(parts) > 3 else 0
    except Exception:
        await msg.answer("❌ ID loại/NCC/giá vốn phải là số.")
        return
    c = db.acc_category_get(cat_id)
    if not c:
        await msg.answer("❌ Không có loại acc này. Xem: /kho")
        return
    if ncc_id and not db.supplier_get(ncc_id):
        await msg.answer("❌ Không có NCC này. Xem: /ncc")
        return
    await state.update_data(acc_cat_id=cat_id, acc_ncc_id=ncc_id, acc_cost=cost)
    await state.set_state(AccShopState.waiting_for_stock_file)
    extra = ""
    if ncc_id:
        extra += f"\n🏭 NCC: <b>#{ncc_id}</b>"
    if cost:
        extra += f" — 💰 giá vốn <b>{vnd(cost)}</b>/acc"
    await msg.answer(
        f"📤 Gửi file <b>.txt</b> hoặc <b>.xlsx</b> chứa acc loại <b>{html.escape(c['name'])}</b>.{extra}\n"
        f"Mỗi dòng 1 acc — .txt dùng định dạng:\n"
        f"<code>uid|mk|ngày tạo|mail thay|ghi chú|2fa|cookie|token</code>\n"
        f"💡 Cột đầu có thể là <b>link Facebook</b> (profile/share/fb.watch) — bot tự giải ra UID.\n"
        f"📊 .xlsx: 8 cột đầu theo thứ tự trên (dòng đầu có thể là tiêu đề).\n"
        f"🔑 Cột <b>mk</b> để trống → tự điền <code>khai2006</code>.",
        parse_mode="HTML",
    )

@router.message(AccShopState.waiting_for_stock_file, F.document)
async def on_stock_file(msg: Message, state: FSMContext):
    if not _is_admin(msg.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    await state.clear()
    cat_id = data.get("acc_cat_id")
    ncc_id = int(data.get("acc_ncc_id") or 0)
    cost = int(data.get("acc_cost") or 0)
    c = db.acc_category_get(cat_id)
    if not c:
        await msg.answer("❌ Loại acc không tồn tại.")
        return
    doc = msg.document
    wait = await msg.answer("⏳ Đang nhập kho...")
    try:
        file_info = await msg.bot.get_file(doc.file_id)
        raw = await msg.bot.download_file(file_info.file_path)
    except Exception as e:
        await wait.edit_text(f"❌ Không đọc được file: {e}")
        return
    rows, err = _parse_stock_file(raw.getvalue(), doc.file_name or "")
    if err:
        await wait.edit_text(err)
        return
    await _import_stock_rows(rows, cat_id, ncc_id, cost, c, msg, wait)

async def _update_stock_rows(rows, cat_id, c, msg, wait, source="file"):
    """Cập nhật thông tin các acc ĐÃ CÓ trong kho theo UID (không nhập mới).

    Ô trống trong dữ liệu mới = giữ nguyên giá trị cũ. Chỉ đụng acc còn hàng
    (AVAILABLE/DIE); acc đã bán (SOLD) giữ nguyên làm lịch sử.
    Trả về text báo cáo (đã tự edit vào wait)."""
    _resolved, _failed, failed_lines, _, _ = await _resolve_stock_links(rows, wait, "cập nhật")
    updated = unchanged = notfound = 0
    changed_total = 0
    notfound_uids, detail_lines = [], []
    for r in rows:
        uid = (r.get("uid") or "").strip()
        if not uid:
            continue
        old = db.acc_stock_find(cat_id, uid)
        if not old:
            notfound += 1
            if len(notfound_uids) < 15:
                notfound_uids.append(uid)
            continue
        new_vals = {}
        for f in db.STOCK_EDITABLE_FIELDS:
            v = (r.get(f) or "").strip()
            if v:  # ô trống = giữ nguyên
                new_vals[f] = v
        n = db.acc_stock_update_fields(old["id"], new_vals)
        if n:
            updated += 1
            changed_total += n
            if len(detail_lines) < 15:
                labels = [db.STOCK_FIELD_LABELS.get(k, k) for k in new_vals
                          if (old.get(k) or "") != new_vals[k]]
                detail_lines.append(
                    f"• <code>{html.escape(uid)}</code>: {html.escape(', '.join(labels))}")
        else:
            unchanged += 1
    try:
        u = msg.from_user
        db.admin_audit_add(u.id, getattr(u, "full_name", ""),
                           "cap_nhat_acc",
                           f"{source} cat#{cat_id}: +{updated} acc/{changed_total} trường, "
                           f"không đổi {unchanged}, lạ {notfound}")
    except Exception:
        pass
    lines = [
        f"🔄 <b>CẬP NHẬT XONG:</b> {html.escape(c['name'])}",
        f"✅ Đã cập nhật: <b>{updated}</b> acc ({changed_total} trường thay đổi)",
        f"⏭ Không thay đổi: <b>{unchanged}</b> acc",
        f"❓ UID không có trong kho: <b>{notfound}</b>" + (" (không tự nhập mới)" if notfound else ""),
    ]
    if detail_lines:
        lines += ["", "<b>Chi tiết:</b>"] + detail_lines
        if updated > len(detail_lines):
            lines.append(f"<i>... và {updated - len(detail_lines)} acc nữa</i>")
    if notfound_uids:
        lines += ["", "UID lạ: " + ", ".join(f"<code>{html.escape(x)}</code>" for x in notfound_uids)]
        if notfound > len(notfound_uids):
            lines.append(f"<i>... và {notfound - len(notfound_uids)} UID nữa</i>")
    if failed_lines:
        lines += ["", "⚠️ Link không giải được UID:"] + [html.escape(x) for x in failed_lines[:10]]
    await wait.edit_text("\n".join(lines), parse_mode="HTML")

@router.message(Command("capnhatacc"))
async def on_capnhatacc(msg: Message, state: FSMContext):
    """Cập nhật thông tin acc từ file: /capnhatacc <id_loại> rồi gửi file .txt/.xlsx.
    Đối chiếu theo UID — UID có trong kho thì update, UID lạ thì báo (không nhập mới)."""
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: <code>/capnhatacc &lt;id_loại&gt;</code> (xem id: /kho)",
                         parse_mode="HTML")
        return
    try:
        cat_id = int(parts[1])
    except Exception:
        await msg.answer("❌ ID loại phải là số.")
        return
    c = db.acc_category_get(cat_id)
    if not c:
        await msg.answer("❌ Không có loại acc này. Xem: /kho")
        return
    await state.update_data(acc_cat_id=cat_id)
    await state.set_state(AccShopState.waiting_for_update_file)
    await msg.answer(
        f"🔄 <b>CẬP NHẬT KHO:</b> {html.escape(c['name'])}\n\n"
        f"Gửi file <b>.txt</b> hoặc <b>.xlsx</b> (cùng format như file nhập kho):\n"
        f"<code>uid|mk|ngày tạo|mail thay|ghi chú|2fa|cookie|token</code>\n\n"
        f"• UID <b>có trong kho</b> → cập nhật các ô có giá trị\n"
        f"• Ô <b>trống</b> → giữ nguyên giá trị cũ\n"
        f"• UID <b>lạ</b> → báo danh sách, <b>không</b> tự nhập mới\n"
        f"• Acc đã bán → không đụng",
        parse_mode="HTML",
    )

@router.message(AccShopState.waiting_for_update_file, F.document)
async def on_update_file(msg: Message, state: FSMContext):
    if not _is_admin(msg.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    await state.clear()
    cat_id = data.get("acc_cat_id")
    c = db.acc_category_get(cat_id)
    if not c:
        await msg.answer("❌ Loại acc không tồn tại.")
        return
    doc = msg.document
    wait = await msg.answer("⏳ Đang cập nhật...")
    try:
        file_info = await msg.bot.get_file(doc.file_id)
        raw = await msg.bot.download_file(file_info.file_path)
    except Exception as e:
        await wait.edit_text(f"❌ Không đọc được file: {e}")
        return
    rows, err = _parse_stock_file(raw.getvalue(), doc.file_name or "")
    if err:
        await wait.edit_text(err)
        return
    await _update_stock_rows(rows, cat_id, c, msg, wait, source="file")

@router.message(Command("setsheet"))
async def on_setsheet(msg: Message):
    """Cài đặt Google Sheet dùng để nhập kho: /setsheet <link|id> [tab]"""
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) == 1:
        cur = db.get_setting("sheet_import_id") or ""
        tab = db.get_setting("sheet_import_tab") or "NhapKho"
        if cur:
            await msg.answer(
                f"📊 <b>Sheet nhập kho hiện tại:</b>\n"
                f"🔗 <code>{html.escape(cur)}</code>\n"
                f"📑 Tab: <b>{html.escape(tab)}</b>\n\n"
                f"Đổi: <code>/setsheet &lt;link_sheet&gt; [tab]</code>",
                parse_mode="HTML")
        else:
            await msg.answer("📊 Chưa cài đặt sheet nhập kho.\n"
                             "Dùng: <code>/setsheet &lt;link_google_sheet&gt; [tab]</code>")
        return
    raw = parts[1]
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9-_]+)", raw)
    sid = m.group(1) if m else raw.strip()
    tab = parts[2].strip() if len(parts) > 2 else (db.get_setting("sheet_import_tab") or "NhapKho")
    db.set_setting("sheet_import_id", sid)
    db.set_setting("sheet_import_tab", tab)
    await msg.answer(
        f"✅ <b>Đã cài đặt sheet nhập kho</b>\n📑 Tab: <b>{html.escape(tab)}</b>\n\n"
        f"Nhập kho: <code>/nhapkhosheet &lt;id_loại&gt; [ncc_id] [giá_vốn]</code>",
        parse_mode="HTML")

@router.message(Command("nhapkhosheet"))
async def on_nhapkhosheet(msg: Message):
    """Nhập kho từ Google Sheet: quét dòng chưa đánh dấu -> giải link -> nhập kho."""
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: <code>/nhapkhosheet &lt;id_loại&gt; [ncc_id] [giá_vốn]</code> (xem id: /kho)",
                         parse_mode="HTML")
        return
    try:
        cat_id = int(parts[1])
        ncc_id = int(parts[2]) if len(parts) > 2 else 0
        cost = int(parts[3].replace(".", "").replace(",", "")) if len(parts) > 3 else 0
    except Exception:
        await msg.answer("❌ ID loại/NCC/giá vốn phải là số.")
        return
    await _run_sheet_import(msg, cat_id, ncc_id, cost)

async def _run_sheet_update(msg, cat_id: int):
    """Cập nhật thông tin acc từ Google Sheet (dùng chung cho /capnhatsheet và nút bấm).

    Đọc các dòng ĐÃ đánh dấu (đã nhập kho trước đó), đối chiếu theo UID trong
    từng loại, cập nhật ô nào khác — ô trống trên Sheet = giữ nguyên."""
    c = db.acc_category_get(cat_id)
    if not c:
        await msg.answer("❌ Không có loại acc này. Xem: /kho")
        return
    sid = db.get_setting("sheet_import_id") or ""
    default_tab = db.get_setting("sheet_import_tab") or "NhapKho"
    if not sid:
        await msg.answer("📊 Chưa cài đặt sheet nhập kho. Dùng: <code>/setsheet &lt;link&gt;</code>",
                         parse_mode="HTML")
        return
    from .. import sheet_import as _si
    try:
        stall = (c["stall"] if "stall" in c.keys() else "") or "Acc Facebook"
    except Exception:
        stall = "Acc Facebook"
    tab = _si.tab_for_stall(stall, default_tab)
    wait = await msg.answer("⏳ Đang đọc Google Sheet...")
    try:
        sheet_rows = await _si.read_marked(sid, tab)
    except Exception as e:
        await wait.edit_text(f"❌ Không đọc được sheet: {html.escape(str(e)[:200])}")
        return
    if not sheet_rows:
        await wait.edit_text("📭 Sheet không có dòng nào đã nhập kho.\n"
                             "<i>Sửa thông tin trực tiếp trên Sheet rồi chạy lại lệnh này.</i>",
                             parse_mode="HTML")
        return
    await wait.edit_text(f"⏳ Đọc được <b>{len(sheet_rows)}</b> dòng đã nhập — đang so sánh với kho...",
                         parse_mode="HTML")
    keys = ["password", "created_date", "backup_mail", "note", "totp", "cookie", "token"]
    updated = unchanged = skipped = 0
    changed_total = 0
    detail_lines, skip_lines = [], []
    for rnum, cells in sheet_rows:
        uid = (cells[0] or "").strip()
        if not uid:
            skipped += 1
            continue
        old = db.acc_stock_find(cat_id, uid)
        if not old:
            # Dòng TRÙNG/LỖI hoặc của loại khác trong cùng tab -> bỏ qua
            skipped += 1
            continue
        new_vals = {}
        for k, v in zip(keys, cells[1:8]):
            v = (v or "").strip()
            if v:  # ô trống = giữ nguyên
                new_vals[k] = v
        n = db.acc_stock_update_fields(old["id"], new_vals)
        if n:
            updated += 1
            changed_total += n
            if len(detail_lines) < 15:
                labels = [db.STOCK_FIELD_LABELS.get(k, k) for k in new_vals
                          if (old.get(k) or "") != new_vals[k]]
                detail_lines.append(
                    f"• dòng {rnum} <code>{html.escape(uid)}</code>: {html.escape(', '.join(labels))}")
        else:
            unchanged += 1
    try:
        u = msg.from_user
        db.admin_audit_add(u.id, getattr(u, "full_name", ""),
                           "cap_nhat_sheet",
                           f"cat#{cat_id}: +{updated} acc/{changed_total} trường, "
                           f"không đổi {unchanged}, bỏ qua {skipped}")
    except Exception:
        pass
    lines = [
        f"🔄 <b>CẬP NHẬT TỪ SHEET XONG:</b> {html.escape(c['name'])}",
        f"✅ Đã cập nhật: <b>{updated}</b> acc ({changed_total} trường thay đổi)",
        f"⏭ Không thay đổi: <b>{unchanged}</b> acc",
        f"⏭ Bỏ qua: <b>{skipped}</b> dòng (không khớp kho / đã bán)",
    ]
    if detail_lines:
        lines += ["", "<b>Chi tiết:</b>"] + detail_lines
        if updated > len(detail_lines):
            lines.append(f"<i>... và {updated - len(detail_lines)} acc nữa</i>")
    await wait.edit_text("\n".join(lines), parse_mode="HTML")

@router.message(Command("capnhatsheet"))
async def on_capnhatsheet(msg: Message):
    """Cập nhật thông tin acc từ Google Sheet: /capnhatsheet <id_loại>.
    Sửa trực tiếp trên Sheet trước, bot sẽ đối chiếu theo UID và update ô khác."""
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: <code>/capnhatsheet &lt;id_loại&gt;</code> (xem id: /kho)",
                         parse_mode="HTML")
        return
    try:
        cat_id = int(parts[1])
    except Exception:
        await msg.answer("❌ ID loại phải là số.")
        return
    await _run_sheet_update(msg, cat_id)

def _mask_stock_val(v: str, keep: int = 6) -> str:
    """Che bớt giá trị nhạy cảm khi hiển thị (cookie/token dài)."""
    v = v or ""
    if len(v) <= keep + 3:
        return v
    return v[:keep] + "..."

def _suaacc_cat_kb():
    cats = db.acc_category_list(active_only=False, include_hidden=True)
    rows = []
    for i in range(0, len(cats), 2):
        row = [InlineKeyboardButton(text=f"#{cats[i]['id']} {cats[i]['name']}",
                                    callback_data=f"suaacc:cat:{cats[i]['id']}")]
        if i + 1 < len(cats):
            row.append(InlineKeyboardButton(text=f"#{cats[i+1]['id']} {cats[i+1]['name']}",
                                            callback_data=f"suaacc:cat:{cats[i+1]['id']}"))
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _suaacc_field_kb(stock_id: int):
    rows = []
    fields = list(db.STOCK_EDITABLE_FIELDS)
    for i in range(0, len(fields), 2):
        row = [InlineKeyboardButton(
            text=f"✏️ {db.STOCK_FIELD_LABELS[fields[i]]}",
            callback_data=f"suaacc:field:{stock_id}:{fields[i]}")]
        if i + 1 < len(fields):
            row.append(InlineKeyboardButton(
                text=f"✏️ {db.STOCK_FIELD_LABELS[fields[i+1]]}",
                callback_data=f"suaacc:field:{stock_id}:{fields[i+1]}"))
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def _suaacc_show_fields(target, cat_id: int, uid: str):
    """Hiện thông tin acc + nút chọn trường cần sửa. target: Message hoặc (bot, chat_id)."""
    old = db.acc_stock_find(cat_id, uid)
    if not old:
        text = (f"❌ Không tìm thấy acc còn hàng với UID <code>{html.escape(uid)}</code> "
                f"trong loại #{cat_id}.\n<i>Có thể acc đã bán, hoặc bạn nhập sai UID.</i>")
        if isinstance(target, Message):
            await target.answer(text, parse_mode="HTML")
        else:
            bot, chat_id = target
            await bot.send_message(chat_id, text, parse_mode="HTML")
        return
    c = db.acc_category_get(cat_id) or {}
    lines = [f"✏️ <b>SỬA ACC</b> — {html.escape(c.get('name', ''))}",
             f"👤 UID: <code>{html.escape(old['uid'])}</code>",
             f"📦 Trạng thái: <b>{old['status']}</b>", ""]
    for f in db.STOCK_EDITABLE_FIELDS:
        v = _mask_stock_val(old.get(f) or "")
        lines.append(f"• {db.STOCK_FIELD_LABELS[f]}: <code>{html.escape(v) or '—'}</code>")
    lines += ["", "Chọn <b>trường</b> cần sửa bên dưới:"]
    kb = _suaacc_field_kb(old["id"])
    if isinstance(target, Message):
        await target.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb)
    else:
        bot, chat_id = target
        await bot.send_message(chat_id, "\n".join(lines), parse_mode="HTML", reply_markup=kb)

@router.message(Command("suaacc"))
async def on_suaacc(msg: Message, state: FSMContext):
    """Sửa thông tin 1 acc: /suaacc [id_loại] [uid] — chọn trường bằng nút bấm."""
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) >= 3:
        try:
            cat_id = int(parts[1])
        except Exception:
            await msg.answer("❌ ID loại phải là số.")
            return
        await state.clear()
        await _suaacc_show_fields(msg, cat_id, parts[2])
        return
    if len(parts) == 2:
        try:
            cat_id = int(parts[1])
        except Exception:
            await msg.answer("❌ ID loại phải là số.")
            return
        if not db.acc_category_get(cat_id):
            await msg.answer("❌ Không có loại acc này. Xem: /kho")
            return
        await state.update_data(suaacc_cat_id=cat_id)
        await state.set_state(AccShopState.waiting_for_edit_uid)
        await msg.answer(f"✏️ <b>SỬA ACC</b> loại #{cat_id}\n\nGửi <b>UID</b> của acc cần sửa:",
                         parse_mode="HTML")
        return
    await state.clear()
    await msg.answer("✏️ <b>SỬA THÔNG TIN ACC</b>\n\nChọn <b>loại acc</b> bên dưới:",
                     parse_mode="HTML", reply_markup=_suaacc_cat_kb())

@router.callback_query(F.data.startswith("suaacc:cat:"))
async def on_suaacc_pick_cat(cb: CallbackQuery, state: FSMContext):
    if not _is_admin(cb.from_user.id):
        await cb.answer()
        return
    try:
        cat_id = int(cb.data.split(":")[2])
    except Exception:
        await cb.answer("❌ Loại không hợp lệ.", show_alert=True)
        return
    if not db.acc_category_get(cat_id):
        await cb.answer("❌ Không có loại acc này.", show_alert=True)
        return
    await state.update_data(suaacc_cat_id=cat_id)
    await state.set_state(AccShopState.waiting_for_edit_uid)
    await cb.message.answer(f"✏️ <b>SỬA ACC</b> loại #{cat_id}\n\nGửi <b>UID</b> của acc cần sửa:",
                            parse_mode="HTML")
    await cb.answer()

@router.message(AccShopState.waiting_for_edit_uid, F.text)
async def on_suaacc_uid(msg: Message, state: FSMContext):
    if not _is_admin(msg.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    await state.clear()
    cat_id = data.get("suaacc_cat_id")
    uid = (msg.text or "").strip()
    if not cat_id or not uid:
        await msg.answer("❌ Thiếu thông tin. Thử lại: /suaacc")
        return
    await _suaacc_show_fields(msg, cat_id, uid)

@router.callback_query(F.data.startswith("suaacc:field:"))
async def on_suaacc_pick_field(cb: CallbackQuery, state: FSMContext):
    if not _is_admin(cb.from_user.id):
        await cb.answer()
        return
    try:
        _, _, stock_id, field = cb.data.split(":", 3)
        stock_id = int(stock_id)
    except Exception:
        await cb.answer("❌ Dữ liệu không hợp lệ.", show_alert=True)
        return
    if field not in db.STOCK_EDITABLE_FIELDS:
        await cb.answer("❌ Trường không hợp lệ.", show_alert=True)
        return
    old = db.acc_stock_get_by_id(stock_id)
    if not old or old.get("status") not in ("AVAILABLE", "DIE"):
        await cb.answer("❌ Acc này không còn sửa được (đã bán hoặc không tồn tại).",
                        show_alert=True)
        return
    await state.update_data(suaacc_stock_id=stock_id, suaacc_field=field)
    await state.set_state(AccShopState.waiting_for_edit_value)
    cur = _mask_stock_val(old.get(field) or "", keep=20)
    label = db.STOCK_FIELD_LABELS[field]
    await cb.message.answer(
        f"✏️ <b>SỬA {label.upper()}</b>\n"
        f"👤 UID: <code>{html.escape(old['uid'])}</code>\n"
        f"Giá trị hiện tại: <code>{html.escape(cur) or '—'}</code>\n\n"
        f"Gửi <b>giá trị mới</b> (gửi <code>-</code> để xóa trắng). Gõ /huy để hủy.",
        parse_mode="HTML")
    await cb.answer()

@router.message(AccShopState.waiting_for_edit_value, F.text)
async def on_suaacc_value(msg: Message, state: FSMContext):
    if not _is_admin(msg.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    await state.clear()
    stock_id = data.get("suaacc_stock_id")
    field = data.get("suaacc_field")
    if not stock_id or field not in db.STOCK_EDITABLE_FIELDS:
        await msg.answer("❌ Phiên sửa đã hết hạn. Thử lại: /suaacc")
        return
    val = (msg.text or "").strip()
    if val.lower() in ("/huy", "/cancel", "hủy", "huỷ"):
        await msg.answer("Đã hủy.")
        return
    if val == "-":
        val = ""
    n = db.acc_stock_update_fields(stock_id, {field: val})
    old = db.acc_stock_get_by_id(stock_id) or {}
    label = db.STOCK_FIELD_LABELS[field]
    try:
        db.admin_audit_add(msg.from_user.id, msg.from_user.full_name, "sua_acc",
                           f"uid {old.get('uid')}: {field}")
    except Exception:
        pass
    if n:
        shown = _mask_stock_val(val, keep=20)
        await msg.answer(
            f"✅ Đã cập nhật <b>{label}</b> của UID <code>{html.escape(old.get('uid') or '')}</code>\n"
            f"Giá trị mới: <code>{html.escape(shown) or '— (đã xóa trắng)'}</code>",
            parse_mode="HTML")
    else:
        await msg.answer("⏭ Giá trị không thay đổi (giống giá trị cũ).")

@router.message(Command("kho"))
async def on_kho(msg: Message):
    if not _is_admin(msg.from_user.id):
        return
    cats = db.acc_category_list(active_only=False, include_hidden=True)
    if not cats:
        await msg.answer("📦 Chưa có loại acc nào. Thêm: /themloai")
        return
    lines = ["📦 <b>KHO ACC</b>", "━━━━━━━━━━━━", ""]
    for c in cats:
        c = dict(c)
        n = db.acc_stock_count(c["id"])
        n_die = db.acc_stock_die_count(c["id"])
        n_sold = db.acc_stock_sold_count(c["id"])
        st = "✅ đang bán" if c["active"] else "⏸ đã ẩn"
        if int(c.get("hidden") or 0):
            st += " [ẨN KHỎI SHOP]"
        bonus = int(c.get("credit_bonus") or 0)
        bonus_txt = f" — 🎁+{bonus}cr" if bonus else ""
        lines.append(
            f"#{c['id']} <b>{html.escape(c['name'])}</b> — {vnd(c['price'])} — "
            f"BH {_fmt_warranty(c['warranty_hours'])} — kho: <b>{n}</b>"
            + (f" 🗑<b>{n_die}</b> die" if n_die else "")
            + (f" 🛒<b>{n_sold}</b> đã bán" if n_sold else "")
            + f"{bonus_txt} — {st}"
        )
    lines += ["", "Đổi giá: <code>/gia &lt;id&gt; &lt;giá&gt;</code>",
              "Đổi giờ BH: <code>/suabh &lt;id&gt; &lt;giờ&gt;</code>",
              "Tặng credits: <code>/creditbonus &lt;id&gt; &lt;số&gt;</code>",
              "Quà đổi điểm: <code>/quadoi &lt;id&gt; [điểm]</code>",
              "Điểm ngẫu nhiên sau mua: <code>/loyaltyrandom &lt;min&gt; &lt;max&gt; &lt;cap_user&gt;</code>",
              "Giờ vàng: <code>/giovang &lt;id|0&gt; [giờ_bd-giờ_kt] [%]</code>",
              "Hộp mù: <code>/hopmu &lt;id&gt;</code> — giá: <code>/hopmugia &lt;giá|0&gt;</code>",
              "NCC: <code>/themncc</code> / <code>/ncc</code>",
              "Nhập hàng: <code>/themacc &lt;id&gt; [ncc_id] [giá_vốn]</code>",
              "Cập nhật T.tin: <code>/capnhatacc &lt;id&gt;</code> (file) | <code>/capnhatsheet &lt;id&gt;</code> (Sheet) | <code>/suaacc</code> (sửa 1 acc)",
              "Sao lưu toàn kho: <code>/xuatkho [id_loại]</code>"]
    await msg.answer("\n".join(lines), parse_mode="HTML")

@router.message(Command("xuatkho"))
async def on_xuatkho(msg: Message):
    """Admin: xuất toàn bộ acc trong kho ra file .xlsx (sao lưu dự phòng).
    Mỗi loại acc = 1 sheet, 8 cột đúng chuẩn nhập kho, hàng vẫn nằm trong kho."""
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    cat_ids = None
    if len(parts) > 1:
        try:
            cat_ids = {int(parts[1])}
        except ValueError:
            await msg.answer("❌ ID loại phải là số (xem id: /kho).")
            return
    cats = db.acc_category_list(active_only=False, include_hidden=True)
    if cat_ids:
        cats = [c for c in cats if int(c["id"]) in cat_ids]
    if not cats:
        await msg.answer("📦 Không có loại acc nào.")
        return
    sheets = {}
    total = 0
    for c in cats:
        c = dict(c)
        rows = db.get_conn().execute(
            "SELECT uid, password, created_date, backup_mail, note, totp, cookie, token "
            "FROM acc_stock WHERE cat_id=? AND status='AVAILABLE' ORDER BY id",
            (c["id"],)).fetchall()
        if not rows:
            continue
        sheet_name = "".join(ch for ch in c["name"] if ch not in r'\/\?*[]:')[:25] or f"Loai_{c['id']}"
        sheets[f"#{c['id']} {sheet_name}"] = (
            ["UID", "MK", "Ngày tạo", "Mail thay", "Ghi chú", "2FA", "Cookie", "Token"],
            [[r["uid"], r["password"], r["created_date"], r["backup_mail"],
              r["note"], r["totp"], r["cookie"], r["token"]] for r in rows],
        )
        total += len(rows)
    if not sheets:
        await msg.answer("📦 Kho đang trống, không có gì để xuất.")
        return
    from aiogram.types import BufferedInputFile
    import datetime
    data = _build_excel_bytes(sheets)
    fname = f"kho_acc_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    await msg.answer_document(
        document=BufferedInputFile(data, filename=fname),
        caption=f"📦 <b>SAO LƯU KHO ACC</b>\n━━━━━━━━━━━━\n"
                f"Tổng: <b>{total}</b> acc / {len(sheets)} loại\n"
                f"⚠️ Hàng vẫn còn trong kho (không bị xóa).\n"
                f"Gửi lại file này vào <code>/themacc</code> để khôi phục kho nếu hệ thống có vấn đề.",
        parse_mode="HTML",
    )

@router.message(Command("gia"))
async def on_gia(msg: Message):
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 3:
        await msg.answer("⚠️ Cú pháp: <code>/gia &lt;id_loại&gt; &lt;giá_mới&gt;</code>",
                         parse_mode="HTML")
        return
    try:
        cid = int(parts[1])
        price = int(parts[2].replace(".", "").replace(",", ""))
    except Exception:
        await msg.answer("❌ ID và giá phải là số.")
        return
    cat = db.acc_category_get(cid)
    if not cat:
        await msg.answer("❌ Không tìm thấy loại này.")
        return
    old = int(cat["price"] or 0)
    if not _price_change_ok(old, price):
        # Giá chênh lệch bất thường -> hỏi xác nhận trước khi lưu
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Vẫn đổi giá",
                                 callback_data=f"giacf:ok:{cid}:{price}"),
            InlineKeyboardButton(text="❌ Huỷ",
                                 callback_data=f"giacf:no:{cid}:{price}"),
        ]])
        await msg.answer(
            f"⚠️ <b>GIÁ BẤT THƯỜNG!</b>\n\n"
            f"Loại <b>#{cid}</b> {html.escape(cat['name'] or '')}\n"
            f"Giá cũ: <b>{vnd(old)}</b> → Giá mới: <b>{vnd(price)}</b>\n\n"
            f"Chênh lệch quá {_price_warn_pct()}% so với giá hiện tại. "
            f"Bạn có chắc không nhập nhầm?",
            parse_mode="HTML", reply_markup=kb)
        return
    if db.acc_category_update(cid, price=price):
        await msg.answer(f"✅ Loại <b>#{cid}</b> đổi giá thành <b>{vnd(price)}</b>.",
                         parse_mode="HTML")
    else:
        await msg.answer("❌ Không tìm thấy loại này.")

@router.callback_query(F.data.startswith("giacf:"))
async def on_giacf_cb(cb: CallbackQuery):
    """Xác nhận đổi giá khi giá mới chênh lệch bất thường."""
    if not (_perms.is_super(cb.from_user.id)
            or _perms.has_perm(cb.from_user.id, "price")):
        await cb.answer("🚫 Bạn không có quyền đổi giá.", show_alert=True)
        return
    parts = (cb.data or "").split(":")
    if len(parts) != 4:
        await cb.answer()
        return
    _, verdict, cid_s, price_s = parts
    try:
        cid, price = int(cid_s), int(price_s)
    except Exception:
        await cb.answer("Lỗi dữ liệu.", show_alert=True)
        return
    if verdict == "ok":
        cat = db.acc_category_get(cid)
        old = int(cat["price"] or 0) if cat else 0
        if db.acc_category_update(cid, price=price):
            db.admin_audit_add(cb.from_user.id, cb.from_user.full_name,
                               "doi_gia_xac_nhan",
                               f"loai #{cid} {vnd(old)} -> {vnd(price)}")
            await cb.message.edit_text(
                f"✅ Loại <b>#{cid}</b> đổi giá thành <b>{vnd(price)}</b> "
                f"(đã xác nhận giá bất thường).",
                parse_mode="HTML")
        else:
            await cb.message.edit_text("❌ Không tìm thấy loại này.")
    else:
        await cb.message.edit_text("❌ Đã huỷ đổi giá.")
    await cb.answer()

@router.message(Command("setmailapp"))
async def on_setmailapp(msg: Message):
    """Admin: đặt link tải app mail ảo hiện cho khách sau khi mua acc.
    /setmailapp <link> — đặt link mới | /setmailapp xoa — tắt | không tham số — xem link hiện tại."""
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split(None, 1)
    if len(parts) < 2:
        cur = _mail_app_link()
        await msg.answer(
            "📧 <b>Link app mail hiện tại:</b>\n"
            f"{html.escape(cur) if cur else '<i>chưa đặt (khách sẽ không thấy dòng tải app)</i>'}\n\n"
            "Đổi link mới: <code>/setmailapp &lt;link&gt;</code>\n"
            "Tắt hẳn: <code>/setmailapp xoa</code>",
            parse_mode="HTML")
        return
    arg = parts[1].strip()
    if arg.lower() in ("xoa", "xóa", "off", "none"):
        db.set_setting("mail_app_link", "")
        await msg.answer("✅ Đã tắt link app mail — khách sẽ không thấy dòng tải app nữa.")
        return
    if not arg.lower().startswith(("http://", "https://")):
        await msg.answer("❌ Link phải bắt đầu bằng http:// hoặc https://")
        return
    db.set_setting("mail_app_link", arg)
    await msg.answer(f"✅ Đã đặt link app mail mới:\n{html.escape(arg)}", parse_mode="HTML")

@router.message(Command("anhbia"))
async def on_anhbia(msg: Message, state: FSMContext):
    """Đặt ảnh bìa cho loại acc: /anhbia <id> rồi gửi ảnh; /anhbia <id> xoa để gỡ."""
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: <code>/anhbia &lt;id_loại&gt;</code> rồi gửi ảnh bìa.\n"
                         "Gỡ ảnh: <code>/anhbia &lt;id_loại&gt; xoa</code>", parse_mode="HTML")
        return
    try:
        cid = int(parts[1])
    except Exception:
        await msg.answer("❌ ID loại phải là số.")
        return
    c = db.acc_category_get(cid)
    if not c:
        await msg.answer("❌ Không tìm thấy loại này.")
        return
    if len(parts) >= 3 and parts[2].lower() in ("xoa", "xóa", "del"):
        db.acc_category_update(cid, cover_photo="")
        await msg.answer(f"🗑 Đã gỡ ảnh bìa của <b>{html.escape(c['name'])}</b>.", parse_mode="HTML")
        return
    await state.update_data(cover_cat_id=cid)
    await state.set_state(AccShopState.waiting_for_cover)
    await msg.answer(f"📸 Gửi ảnh bìa cho <b>{html.escape(c['name'])}</b> (gửi 1 ảnh bất kỳ).",
                     parse_mode="HTML")

@router.message(AccShopState.waiting_for_cover, F.photo)
async def on_anhbia_photo(msg: Message, state: FSMContext):
    data = await state.get_data()
    cid = data.get("cover_cat_id")
    await state.clear()
    if not _is_admin(msg.from_user.id):
        return
    if not cid or not db.acc_category_get(cid):
        await msg.answer("❌ Loại acc không còn tồn tại.")
        return
    file_id = msg.photo[-1].file_id
    db.acc_category_update(cid, cover_photo=file_id)
    await msg.answer(f"✅ Đã đặt ảnh bìa cho loại <b>#{cid}</b>. Khách vào xem chi tiết sẽ thấy ảnh này.",
                     parse_mode="HTML")

@router.message(Command("suabh"))
async def on_suabh(msg: Message):
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 3:
        await msg.answer("⚠️ Cú pháp: <code>/suabh &lt;id_loại&gt; &lt;bảo_hành: 30p|24h|2 ngày|1 tuần&gt;</code>",
                         parse_mode="HTML")
        return
    try:
        cid = int(parts[1])
    except Exception:
        await msg.answer("❌ ID phải là số.")
        return
    wh = _parse_warranty(parts[2])
    if wh is None:
        await msg.answer("❌ Bảo hành không hợp lệ. Nhập dạng: <code>30p</code> | <code>24h</code> | <code>2 ngày</code> | <code>1 tuần</code>",
                         parse_mode="HTML")
        return
    if db.acc_category_update(cid, warranty_hours=wh):
        await msg.answer(f"✅ Loại <b>#{cid}</b> đổi bảo hành thành <b>{_fmt_warranty(wh)}</b>.",
                         parse_mode="HTML")
    else:
        await msg.answer("❌ Không tìm thấy loại này.")

@router.message(Command("quadoi"))
async def on_quadoi(msg: Message):
    """Admin: chọn quà đổi điểm loyalty.
    /quadoi <id_loại> [số_điểm] — quà acc cố định (xem id: /kho)
    /quadoi stall [số_điểm] — quà acc bất kỳ trong gian hàng Acc Facebook
    /quadoi tien <số_tiền> <chinh|shop|credits> [số_điểm] — quà tiền/credits về ví
    /quadoi 0 — tắt"""
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer(
            f"🎁 Quà đổi điểm hiện tại: {_loyalty_gift_desc()}.\n"
            f"Cú pháp:<br>"
            f"• <code>/quadoi &lt;id_loại&gt; [số_điểm]</code> — quà acc cố định (xem id: /kho)<br>"
            f"• <code>/quadoi stall [số_điểm]</code> — quà acc bất kỳ trong gian hàng Acc Facebook<br>"
            f"• <code>/quadoi tien &lt;số_tiền&gt; &lt;chinh|shop|credits&gt; [số_điểm]</code> — quà tiền/credits về ví<br>"
            f"• <code>/quadoi 0</code> — tắt",
            parse_mode="HTML")
        return
    # Chế độ quà tiền/credits: /quadoi tien <số_tiền> <chinh|shop|credits> [số_điểm]
    if parts[1].lower() == "tien":
        if len(parts) < 4:
            await msg.answer("❌ Cú pháp: <code>/quadoi tien &lt;số_tiền&gt; &lt;chinh|shop|credits&gt; [số_điểm]</code>",
                             parse_mode="HTML")
            return
        try:
            amt = int(parts[2].replace(",", "").replace("k", "000").replace("K", "000"))
            if amt <= 0:
                raise ValueError
        except ValueError:
            await msg.answer("❌ Số tiền không hợp lệ.")
            return
        wallet = db.parse_wallet(parts[3])
        db.set_setting("loyalty_redeem_mode", "money")
        db.set_setting("loyalty_redeem_amount", str(amt))
        db.set_setting("loyalty_redeem_wallet", wallet)
        if len(parts) >= 5:
            try:
                pts = int(parts[4])
                if pts > 0:
                    db.set_setting("loyalty_redeem_points", str(pts))
            except Exception:
                pass
        await msg.answer(f"✅ Quà đổi điểm: {_loyalty_gift_desc()}.", parse_mode="HTML")
        return
    if parts[1].lower() == "stall":
        db.set_setting("loyalty_redeem_mode", "acc")
        db.set_setting("loyalty_redeem_scope", "stall")
        db.set_setting("loyalty_redeem_cat", "0")
        if len(parts) >= 3:
            try:
                pts = int(parts[2])
                if pts > 0:
                    db.set_setting("loyalty_redeem_points", str(pts))
            except Exception:
                pass
        await msg.answer(f"✅ Quà đổi điểm: {_loyalty_gift_desc()}.", parse_mode="HTML")
        return
    try:
        cid = int(parts[1])
    except Exception:
        await msg.answer("❌ ID phải là số hoặc <code>stall</code>.")
        return
    if cid and not db.acc_category_get(cid):
        await msg.answer("❌ Không tìm thấy loại này.")
        return
    db.set_setting("loyalty_redeem_mode", "acc")
    db.set_setting("loyalty_redeem_scope", "cat")
    db.set_setting("loyalty_redeem_cat", str(cid))
    pts_txt = ""
    if len(parts) >= 3:
        try:
            pts = int(parts[2])
            if pts > 0:
                db.set_setting("loyalty_redeem_points", str(pts))
                pts_txt = f" — <b>{pts}</b> điểm"
        except Exception:
            pass
    c = db.acc_category_get(cid) if cid else None
    await msg.answer(f"✅ Quà đổi điểm: <b>{html.escape(c['name']) if c else 'đã tắt'}</b>{pts_txt}.",
                     parse_mode="HTML")

@router.message(Command("xoaloai"))
async def on_xoaloai(msg: Message):
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: <code>/xoaloai &lt;id_loại&gt;</code>",
                         parse_mode="HTML")
        return
    try:
        cid = int(parts[1])
    except Exception:
        await msg.answer("❌ ID phải là số.")
        return
    if db.acc_category_update(cid, active=0):
        await msg.answer(f"✅ Đã ẩn loại <b>#{cid}</b> khỏi shop.", parse_mode="HTML")
    else:
        await msg.answer("❌ Không tìm thấy loại này.")

@router.message(Command("hienloai"))
async def on_hienloai(msg: Message):
    """Admin: hiện lại loại acc đã ẩn bằng /xoaloai. Cú pháp: /hienloai <id_loại>"""
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: <code>/hienloai &lt;id_loại&gt;</code> (xem id: /kho)",
                         parse_mode="HTML")
        return
    try:
        cid = int(parts[1])
    except Exception:
        await msg.answer("❌ ID phải là số.")
        return
    if db.acc_category_update(cid, active=1, hidden=0):
        c = db.acc_category_get(cid)
        nm = html.escape(c["name"]) if c else f"#{cid}"
        await msg.answer(f"✅ Đã hiện lại loại <b>{nm}</b> trên shop.\n"
                         f"Nhập hàng: <code>/themacc {cid}</code>",
                         parse_mode="HTML")
    else:
        await msg.answer("❌ Không tìm thấy loại này.")

@router.message(Command("daban"))
async def on_daban(msg: Message):
    """Admin: đánh dấu 1 acc đã bán thủ công (bán ngoài bot).
    Cú pháp: /daban <uid>"""
    if not (_perms.is_super(msg.from_user.id)
            or _perms.has_perm(msg.from_user.id, "kho")):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: <code>/daban &lt;uid&gt;</code>\n"
                         "Đánh dấu acc bán ngoài bot là đã bán.",
                         parse_mode="HTML")
        return
    acc = db.acc_stock_find_by_uid(parts[1])
    if not acc:
        await msg.answer(f"❌ Không tìm thấy acc UID <code>{html.escape(parts[1])}</code> trong kho.",
                         parse_mode="HTML")
        return
    if acc.get("status") != "AVAILABLE":
        await msg.answer(f"⚠️ Acc UID <code>{html.escape(acc['uid'])}</code> đang ở trạng thái "
                         f"<b>{html.escape(str(acc.get('status')))}</b>, không cần đánh dấu.",
                         parse_mode="HTML")
        return
    cat = db.acc_category_get(acc.get("cat_id"))
    cat_txt = (f"#{acc.get('cat_id')} {html.escape(cat['name'])}"
               if cat else f"#{acc.get('cat_id')}")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Đã bán — đánh dấu",
                             callback_data=f"daban:ok:{acc['id']}"),
        InlineKeyboardButton(text="❌ Hủy", callback_data="daban:no"),
    ]])
    await msg.answer(
        f"🛒 <b>ĐÁNH DẤU ĐÃ BÁN THỦ CÔNG</b>\n\n"
        f"UID: <code>{html.escape(acc['uid'])}</code>\n"
        f"Loại: {cat_txt}\n"
        f"Trạng thái: <b>{html.escape(str(acc.get('status')))}</b>\n\n"
        f"Xác nhận acc này đã bán ngoài bot?\n"
        f"(Dấu '🛒 ĐÃ BÁN' sẽ tự lên cột J Sheet trong ~5 phút)",
        parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data.startswith("daban:"))
async def on_daban_cb(cb: CallbackQuery):
    """Xác nhận đánh dấu acc bán thủ công."""
    if not (_perms.is_super(cb.from_user.id)
            or _perms.has_perm(cb.from_user.id, "kho")):
        await cb.answer("🚫 Bạn không có quyền kho.", show_alert=True)
        return
    parts = (cb.data or "").split(":")
    if len(parts) < 2:
        await cb.answer()
        return
    if parts[1] == "no":
        await cb.message.edit_text("❌ Đã hủy.")
        return
    try:
        sid = int(parts[2])
    except Exception:
        await cb.answer("Lỗi dữ liệu.", show_alert=True)
        return
    ok, why = db.acc_mark_sold_manual(sid)
    if ok:
        acc = db.acc_stock_get_by_id(sid)
        uid_txt = html.escape(acc["uid"]) if acc else f"#{sid}"
        db.admin_audit_add(cb.from_user.id, cb.from_user.full_name,
                           "danh_dau_ban_tay", f"acc #{sid} uid={acc['uid'] if acc else '?'}")
        await cb.message.edit_text(
            f"✅ Đã đánh dấu acc <code>{uid_txt}</code> là <b>ĐÃ BÁN</b>.\n"
            f"🛒 Dấu 'ĐÃ BÁN' sẽ tự lên cột J Google Sheet trong ~5 phút.",
            parse_mode="HTML")
    else:
        await cb.message.edit_text(
            f"❌ Không đánh dấu được ({html.escape(why)}). Acc có thể đã đổi trạng thái.")

@router.message(Command("recheck"))
async def on_recheck(msg: Message):
    """Admin: chạy re-check LIVE toàn bộ kho ngay (không đợi lịch định kỳ).
    Acc DIE → cách ly + báo cáo. Đổi chu kỳ: /setrecheck <số_ngày>."""
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) > 1 and parts[1].isdigit():
        days = max(1, min(30, int(parts[1])))
        db.set_setting("stock_recheck_days", str(days))
        await msg.answer(f"✅ Đã đặt re-check kho mỗi <b>{days} ngày</b> (3h sáng).", parse_mode="HTML")
        return
    n = db.get_conn().execute(
        "SELECT COUNT(*) n FROM acc_stock WHERE status='AVAILABLE'").fetchone()["n"]
    await msg.answer(f"🔄 Bắt đầu re-check <b>{n}</b> acc trong kho... Xong mik báo kết quả.",
                     parse_mode="HTML")
    asyncio.create_task(poller._run_stock_recheck(manual=True))

@router.message(Command("xoadie"))
async def on_xoadie(msg: Message):
    """Admin: xóa hẳn các acc đã bị cách ly DIE. /xoadie [id_loại] yes"""
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    cid = None
    if len(parts) > 1 and parts[1].isdigit():
        cid = int(parts[1])
    confirm = (parts[-1].lower() == "yes")
    if cid is not None:
        total = db.acc_stock_die_count(cid)
        scope = f"loại #{cid}"
    else:
        total = db.get_conn().execute(
            "SELECT COUNT(*) n FROM acc_stock WHERE status='DIE'").fetchone()["n"]
        scope = "toàn bộ các loại"
    if total <= 0:
        await msg.answer("✅ Không có acc DIE nào cần dọn.")
        return
    if not confirm:
        await msg.answer(
            f"🗑 Có <b>{total}</b> acc DIE đang bị cách ly ({scope}).\n"
            f"Xóa hẳn: <code>/xoadie{f' {cid}' if cid else ''} yes</code>\n"
            "⚠️ Không khôi phục được.",
            parse_mode="HTML")
        return
    n = db.acc_stock_delete_die(cid)
    await msg.answer(f"✅ Đã xóa hẳn <b>{n}</b> acc DIE ({scope}).", parse_mode="HTML")

@router.message(Command("xoakho"))
async def on_xoakho(msg: Message):
    """Admin: XÓA TOÀN BỘ acc CHƯA BÁN trong kho của 1 loại (không khôi phục được).
    Cú pháp: /xoakho <id_loại> yes"""
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: <code>/xoakho &lt;id_loại&gt; yes</code>\n"
                         "⚠️ Xóa toàn bộ acc <b>chưa bán</b> của loại đó, "
                         "<b>không khôi phục được</b>.",
                         parse_mode="HTML")
        return
    try:
        cid = int(parts[1])
    except Exception:
        await msg.answer("❌ ID phải là số.")
        return
    n_av = db.get_conn().execute(
        "SELECT COUNT(*) n FROM acc_stock WHERE cat_id=? AND status='AVAILABLE'",
        (cid,)).fetchone()["n"]
    if not n_av:
        await msg.answer("📦 Loại này không còn acc chưa bán nào.")
        return
    if len(parts) < 3 or parts[2].lower() != "yes":
        c = db.acc_category_get(cid)
        nm = html.escape(c["name"]) if c else f"#{cid}"
        await msg.answer(
            f"⚠️ Bạn chắc chắn muốn <b>XÓA {n_av} ACC CHƯA BÁN</b> của loại "
            f"<b>{nm}</b>?\n"
            f"Nhớ <code>/xuatkho {cid}</code> sao lưu trước nếu cần.\n"
            f"Gõ lại: <code>/xoakho {cid} yes</code> để xác nhận.",
            parse_mode="HTML")
        return
    n = db.acc_stock_delete_available(cid)
    await msg.answer(f"🗑 Đã xóa <b>{n}</b> acc chưa bán của loại <b>#{cid}</b>.",
                     parse_mode="HTML")

@router.message(Command("xoahan"))
async def on_xoahan(msg: Message):
    """Admin: XÓA HẲN loại acc khỏi DB (không khôi phục được).
    Cú pháp: /xoahan <id_loại> yes"""
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: <code>/xoahan &lt;id_loại&gt; yes</code>\n"
                         "⚠️ Xóa loại acc, <b>không khôi phục được</b>. "
                         "Chỉ xóa được khi không còn acc <b>chưa bán</b> "
                         "(xóa kho trước: <code>/xoakho &lt;id&gt; yes</code>).",
                         parse_mode="HTML")
        return
    try:
        cid = int(parts[1])
    except Exception:
        await msg.answer("❌ ID phải là số.")
        return
    if len(parts) < 3 or parts[2].lower() != "yes":
        c = db.acc_category_get(cid)
        nm = html.escape(c["name"]) if c else f"#{cid}"
        await msg.answer(
            f"⚠️ Bạn chắc chắn muốn <b>XÓA HẲN</b> loại <b>{nm}</b>?\n"
            f"Gõ lại: <code>/xoahan {cid} yes</code> để xác nhận.",
            parse_mode="HTML")
        return
    ok, info = db.acc_category_delete_hard(cid)
    await msg.answer(f"{'🗑' if ok else '❌'} {info}", parse_mode="HTML")

@router.message(Command("accinfo"))
async def on_accinfo(msg: Message):
    """5.9 Admin: truy xuất hành trình 1 acc. Cú pháp: /accinfo <uid>"""
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: <code>/accinfo &lt;uid&gt;</code>",
                         parse_mode="HTML")
        return
    e = html.escape
    s = db.acc_stock_by_uid(parts[1])
    if not s:
        # acc đã bán (đánh dấu SOLD trong kho) -> tra trong đơn hàng
        o = db.get_conn().execute(
            "SELECT o.*, c.name AS cat_name, u.username FROM acc_orders o "
            "LEFT JOIN acc_categories c ON c.id=o.cat_id "
            "LEFT JOIN tg_users u ON u.tg_id=o.tg_id "
            "WHERE o.uid=? ORDER BY o.id DESC LIMIT 1", (parts[1].strip(),)).fetchone()
        if not o:
            await msg.answer("❌ Không tìm thấy acc này trong kho.")
            return
        o = dict(o)
        who = f"@{o['username']}" if o.get("username") else ""
        await msg.answer(
            "🔎 <b>HÀNH TRÌNH ACC</b>\n━━━━━━━━━━━━\n"
            f"👤 UID: <code>{e(o['uid'] or '')}</code>\n"
            f"📦 Loại: <b>{e(o['cat_name'] or '')}</b>\n"
            f"📌 Trạng thái: <b>ĐÃ BÁN</b>\n\n"
            f"🧾 Đơn #{o['id']} — khách <code>{o['tg_id']}</code> {e(who)} — "
            f"{vnd(o['price'])} — {vn_time_str(ts=o['created_at'])}",
            parse_mode="HTML")
        return
    lines = ["🔎 <b>HÀNH TRÌNH ACC</b>", "━━━━━━━━━━━━",
             f"👤 UID: <code>{e(s['uid'] or '')}</code>",
             f"📦 Loại: <b>{e(s['cat_name'] or '')}</b>",
             f"📥 Nhập kho: {vn_time_str(ts=s['added_at'])}"
             + (f" — {e(s['batch'])}" if s.get("batch") else ""),
             f"📌 Trạng thái: <b>{e(s['status'] or '')}</b>", ""],
    orders = db.acc_stock_orders(s["id"])
    if orders:
        lines.append("🧾 <b>Đã bán:</b>")
        for o in orders:
            who = f"@{o['username']}" if o.get("username") else ""
            lines.append(f"• Đơn #{o['id']} — khách <code>{o['tg_id']}</code> {e(who)} — "
                         f"{vnd(o['price'])} — {vn_time_str(ts=o['created_at'])}")
    else:
        lines.append("🆕 Chưa bán cho ai.")
    n_claim = db.acc_stock_claim_count(s["id"])
    lines.append(f"\n🛡 Số lần bảo hành: <b>{n_claim}</b>")
    await msg.answer("\n".join(lines), parse_mode="HTML")

@router.callback_query(F.data.startswith("acccoc:"))
async def on_acc_deposit_cb(cb: CallbackQuery):
    await cb.answer()
    try:
        cat_id = int(cb.data.split(":", 1)[1])
    except Exception:
        return
    await _do_deposit(cb.from_user.id, cat_id, cb.message, cb.bot)

@router.message(Command("themncc"))
async def on_themncc(msg: Message):
    if not _is_admin(msg.from_user.id):
        return
    rest = (msg.text or "").split(None, 1)
    if len(rest) < 2:
        await msg.answer("⚠️ Cú pháp: <code>/themncc &lt;tên&gt; | &lt;liên hệ&gt;</code>",
                         parse_mode="HTML")
        return
    name, _, contact = rest[1].partition("|")
    sid = db.supplier_add(name.strip(), contact.strip())
    await msg.answer(f"✅ Đã thêm NCC <b>#{sid}</b>: {html.escape(name.strip())}",
                     parse_mode="HTML")

@router.message(Command("ncc"))
async def on_ncc(msg: Message):
    if not _is_admin(msg.from_user.id):
        return
    sups = db.supplier_list()
    if not sups:
        await msg.answer("🏭 Chưa có NCC nào. Thêm: /themncc")
        return
    lines = ["🏭 <b>SỔ NHÀ CUNG CẤP</b>", "━━━━━━━━━━━━", ""]
    for s in sups:
        s = dict(s)
        pct, alive, total = db.supplier_live_rate(s["id"])
        rate_txt = f"{pct}% sống ({alive}/{total})" if total else "chưa chấm điểm"
        stars = "⭐" * int(s["rating"] or 0) if s["rating"] else "—"
        lines.append(
            f"#{s['id']} <b>{html.escape(s['name'])}</b> {stars}\n"
            f"   📞 {html.escape(s['contact'] or '—')} | 📊 {rate_txt}")
    lines += ["", "Đánh giá tay: <code>/danhgiancc &lt;id&gt; &lt;sao 1-5&gt;</code>",
              "Chấm tỉ lệ sống: <code>/chamdiem &lt;id&gt; [số_ngày=7]</code>"]
    await msg.answer("\n".join(lines), parse_mode="HTML")

@router.message(Command("lo"))
async def on_lo(msg: Message):
    if not _is_admin(msg.from_user.id):
        return
    # Giá vốn/lãi chỉ chủ shop được xem — admin phụ không thấy
    if not _perms.is_super(msg.from_user.id):
        await msg.answer("🚫 Chỉ chủ shop mới xem được giá vốn/lãi.",
                         parse_mode="HTML")
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: <code>/lo &lt;id_loại&gt;</code>",
                         parse_mode="HTML")
        return
    try:
        cid = int(parts[1])
    except Exception:
        await msg.answer("❌ ID phải là số.")
        return
    batches = db.acc_batches_by_cat(cid)
    if not batches:
        await msg.answer("📦 Loại này chưa ghi nhận lô nào có giá vốn.\n"
                         "Nhập kho kèm giá vốn: <code>/themacc &lt;id&gt; [ncc_id] [giá_vốn]</code>",
                         parse_mode="HTML")
        return
    lines = ["💰 <b>LÃI THEO LÔ</b>", "━━━━━━━━━━━━", ""]
    t_cost = t_rev = 0
    for b in batches:
        p = db.acc_profit_by_batch(b["batch"])
        t_cost += p["cost"]
        t_rev += p["revenue"]
        emo = "🟢" if p["profit"] >= 0 else "🔴"
        lines.append(
            f"{emo} <b>{html.escape(p['batch'])}</b> ({p['count']} acc)\n"
            f"   Vốn: {vnd(p['cost'])} | Thu: {vnd(p['revenue'])} | "
            f"Lãi: <b>{vnd(p['profit'])}</b>")
    lines += ["", f"📊 <b>Tổng:</b> vốn {vnd(t_cost)} — thu {vnd(t_rev)} — "
                 f"<b>lãi {vnd(t_rev - t_cost)}</b>"]
    await msg.answer("\n".join(lines), parse_mode="HTML")

@router.message(Command("nccauto"))
async def on_nccauto(msg: Message):
    if not _is_admin(msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) >= 2 and parts[1].lower() == "off":
        db.set_setting("supplier_auto_url", "")
        await msg.answer("✅ Đã tắt nhập kho tự động từ NCC.")
        return
    if len(parts) < 3:
        cur = db.get_setting("supplier_auto_url", "")
        await msg.answer(
            "⚠️ Cú pháp: <code>/nccauto &lt;url_file_txt&gt; &lt;id_loại&gt; [ncc_id]</code>\n"
            "Tắt: <code>/nccauto off</code>\n\n"
            f"Cấu hình hiện tại: <code>{html.escape(cur or 'chưa có')}</code>\n"
            "Bot sẽ tự tải file mỗi sáng 6h, nhập kho + quét chất lượng.",
            parse_mode="HTML")
        return
    url = parts[1]
    if not (url.startswith("http://") or url.startswith("https://")):
        await msg.answer("❌ URL phải bắt đầu bằng http:// hoặc https://")
        return
    try:
        cid = int(parts[2])
        sid = int(parts[3]) if len(parts) > 3 else 0
    except Exception:
        await msg.answer("❌ ID loại/NCC phải là số.")
        return
    if not db.acc_category_get(cid):
        await msg.answer("❌ Không có loại acc này.")
        return
    db.set_setting("supplier_auto_url", url)
    db.set_setting("supplier_auto_cat", str(cid))
    db.set_setting("supplier_auto_supplier", str(sid))
    await msg.answer(f"✅ Đã bật nhập kho tự động mỗi sáng 6h:\n🔗 {html.escape(url)}\n"
                     f"📦 Loại #{cid}" + (f" — NCC #{sid}" if sid else ""),
                     parse_mode="HTML")

__all__ = [
    "SheetImportState",
    "auto_import_stall",
    "on_sheet_button",
    "on_sheet_pick",
    "on_sheet_ncc_cost",
    "on_sheet_go",
    "on_khoakey",
    "on_shop_stall",
    "on_giasi",
    "on_damua",
    "on_themloai",
    "on_themstall",
    "on_themacc",
    "on_stock_file",
    "_update_stock_rows",
    "on_capnhatacc",
    "on_update_file",
    "on_setsheet",
    "on_nhapkhosheet",
    "_run_sheet_update",
    "on_capnhatsheet",
    "_mask_stock_val",
    "_suaacc_cat_kb",
    "_suaacc_field_kb",
    "_suaacc_show_fields",
    "on_suaacc",
    "on_suaacc_pick_cat",
    "on_suaacc_uid",
    "on_suaacc_pick_field",
    "on_suaacc_value",
    "on_kho",
    "on_xuatkho",
    "on_gia",
    "on_giacf_cb",
    "on_setmailapp",
    "on_anhbia",
    "on_anhbia_photo",
    "on_suabh",
    "on_quadoi",
    "on_xoaloai",
    "on_hienloai",
    "on_daban",
    "on_daban_cb",
    "on_recheck",
    "on_xoadie",
    "on_xoakho",
    "on_xoahan",
    "on_accinfo",
    "on_acc_deposit_cb",
    "on_themncc",
    "on_ncc",
    "on_lo",
    "on_nccauto",
]