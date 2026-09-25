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

from .core import TienIchState, _sub_active, log, router
from .common import TrackMenuState, _build_excel_bytes, _is_cancel, _maybe_low_credit_warn, _run_scanlist, _send_card, _trackmenu_main_kb, _vip_limit_for, check_uids_batch, process_fb_check, process_ig_check, process_tiktok_check

class FBNoteState(StatesGroup):
    waiting_for_note = State()
    uid = None

class CookieCheckState(StatesGroup):
    waiting_for_file = State()

class FileCheckState(StatesGroup):
    waiting_for_file = State()

class CookieAddState(StatesGroup):
    waiting_for_cookie = State()

@router.message(Command("scan_all"))
async def on_scan_all(msg: Message):
    wait = await msg.answer("⏳ Đang tổng hợp dữ liệu toàn bộ dàn tài khoản của bạn...")
    try:
        tg_id = msg.from_user.id
        fb_tracks = db.user_fb_tracks(tg_id)
        tk_tracks = db.user_tracks(tg_id)
        ig_tracks = db.user_ig_tracks(tg_id)
        
        import csv
        import io
        
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Platform", "Target", "Status/Followers"])
        
        fb_die = 0
        for t in fb_tracks:
            writer.writerow(["FB", t["fb_uid"], t["last_status"]])
            if t.get("last_status") == "die":
                fb_die += 1
                
        tk_total = 0
        for t in tk_tracks:
            writer.writerow(["TikTok", t["tiktok_username"], t.get("last_followers", 0)])
            tk_total += t.get("last_followers", 0)
            
        for t in ig_tracks:
            writer.writerow(["IG", t["ig_username"], t.get("last_followers", 0)])
            
        output.seek(0)
        from aiogram.types import BufferedInputFile
        file = BufferedInputFile(output.getvalue().encode('utf-8'), filename="scan_all_report.csv")
        
        summary = (
            f"📊 <b>BÁO CÁO TỔNG QUAN</b>\n\n"
            f"🔹 <b>Facebook:</b> {len(fb_tracks)} nick ({fb_die} chết)\n"
            f"🔹 <b>TikTok:</b> {len(tk_tracks)} nick (Tổng {fmt_num(tk_total)} follow)\n"
            f"🔹 <b>Instagram:</b> {len(ig_tracks)} nick\n\n"
            "Tải file CSV đính kèm để xem chi tiết."
        )
        
        await msg.answer_document(document=file, caption=summary, parse_mode="HTML")
        await wait.delete()
    except Exception as e:
        await wait.edit_text(f"❌ Lỗi khi quét: {e}")

@router.message(Command("hdcookie"))
async def on_hdcookie(msg: Message):
    text = (
        "🍪 <b>HƯỚNG DẪN LẤY COOKIE CÁC NỀN TẢNG</b> 🍪\n\n"
        "<b>1. ZALO (Lấy Cookie & IMEI)</b>\n"
        "• Truy cập: <code>chat.zalo.me</code> trên máy tính (F12 hoặc Chuột phải -> Kiểm tra)\n"
        "• Chọn tab <b>Network</b> (Mạng), F5 tải lại trang. Bấm vào một yêu cầu (request) bất kỳ, kéo xuống phần <b>Request Headers</b>, copy toàn bộ đoạn <code>Cookie: ...</code>\n"
        "• Hoặc chọn tab <b>Application</b> (Ứng dụng) -> Cookies. Tìm khóa <code>zpw_sek</code> và copy giá trị.\n"
        "• <b>Lấy IMEI:</b> Ở tab <b>Application</b> -> Local Storage -> tìm khóa <code>z_uuid</code>, đó chính là IMEI.\n\n"
        "<b>2. FACEBOOK (Cookie)</b>\n"
        "• Đăng nhập tài khoản Clone FB trên trình duyệt.\n"
        "• F12 -> tab <b>Network</b> -> F5. Bấm vào request đầu tiên, kéo xuống <b>Request Headers</b> -> copy toàn bộ dòng <code>Cookie: c_user=...</code>\n"
        "• Hoặc dùng tiện ích mở rộng (Extension) như <b>J2TEAM Security</b> hoặc <b>Get Token by Ninja</b> để copy nhanh.\n\n"
        "<b>3. INSTAGRAM</b>\n"
        "• <b>Cách 1 (Dễ nhất - Khuyên dùng):</b> Bạn chỉ cần vào trang <b>Cài đặt</b> trên Web Dashboard, chọn mục Phương thức check Instagram là <code>Instaloader</code>, sau đó điền trực tiếp <b>Tên đăng nhập (Username)</b> và <b>Mật khẩu</b> của acc Clone IG vào. Tool sẽ tự động đăng nhập ngầm và lấy Cookie cho bạn!\n"
        "• <b>Cách 2 (Thủ công):</b> Đăng nhập IG trên Web -> F12 -> <b>Application</b> -> Cookies -> tìm khóa <code>sessionid</code> và copy dán vào mục IG Session Cookie.\n\n"
        "⚠️ <i>Lưu ý: Tuyệt đối KHÔNG sử dụng tài khoản CHÍNH cho việc này để tránh rủi ro bị khóa. Chỉ nên dùng acc Clone/Phụ để nạp vào Tool!</i>"
    )
    await msg.answer(text, parse_mode="HTML")

@router.message(Command("tiktok"))
async def on_tiktok(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await msg.answer("⚠️ Cú pháp: /tiktok &lt;username&gt;\nVí dụ: /tiktok cristiano")
        return
    username = parse_username(parts[1].strip())
    if not username:
        await msg.answer("❌ Không nhận diện được username.")
        return
    await process_tiktok_check(msg, username)

@router.message(Command("fb"))
async def on_fb(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await msg.answer("⚠️ Cú pháp: /fb &lt;uid&gt;\nVí dụ: /fb 100089260699193")
        return
    from ..fb import extract_uid
    uid = extract_uid(parts[1].strip())
    if not uid:
        await msg.answer("❌ Không nhận diện được UID.")
        return
    await process_fb_check(msg, uid)

@router.message(Command("getuid"))
async def on_getuid(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await msg.answer(
            "⚠️ Cú pháp: /getuid &lt;link facebook&gt;\n"
            "Ví dụ: /getuid https://www.facebook.com/zuck"
        )
        return
    link = parts[1].strip()
    wait = await msg.answer("⏳ Đang lấy UID từ link...")
    try:
        from ..fb import resolve_fb_uid
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        import html as html_lib
        uid, fb_name, method = await resolve_fb_uid(link)
        if uid:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔍 Check Live/Die ngay", callback_data=f"fb_quickcheck_{uid}")
            ]])
            name_line = f"👤 Tên: <b>{html_lib.escape(fb_name)}</b>\n" if fb_name else ""
            await wait.edit_text(
                f"✅ <b>Lấy UID thành công!</b>\n\n"
                f"🆔 UID: <code>{uid}</code>\n"
                f"{name_line}"
                f"🔎 Lấy bằng: {method}\n\n"
                f"👆 Bấm vào UID để copy, hoặc bấm nút bên dưới để check luôn.",
                reply_markup=kb,
            )
        else:
            await wait.edit_text(
                "❌ Không lấy được UID từ link này.\n"
                "Hãy kiểm tra lại link (cần là link trang cá nhân hoặc page công khai)."
            )
    except Exception as e:
        log.exception("Lỗi getuid %s", link)
        await wait.edit_text(f"❌ Lỗi: {e}")

@router.callback_query(F.data.startswith("fb_quickcheck_"))
async def on_fb_quickcheck(cb: CallbackQuery):
    uid = cb.data.replace("fb_quickcheck_", "")
    await cb.answer()
    try:
        await cb.message.delete()
    except Exception:
        pass
    await process_fb_check(cb.message, uid)

async def process_fb_post_check(msg: Message, url: str):
    wait = await msg.answer("⏳ Đang lấy thông tin bài viết Facebook...")
    from ..fb import fetch_fb_post_info, build_fb_post_caption
    info = await fetch_fb_post_info(url)
    if not info or not info.get("post_id"):
        await wait.edit_text("❌ Không lấy được thông tin bài viết FB. Vui lòng kiểm tra lại link.")
        return
    caption = build_fb_post_caption(info)
    await wait.edit_text(caption, disable_web_page_preview=True)

@router.message(Command("ig"))
async def on_ig(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await msg.answer("⚠️ Cú pháp: /ig &lt;username&gt;\nVí dụ: /ig cristiano")
        return
    username = parse_ig_username(parts[1].strip())
    if not username:
        await msg.answer("❌ Không nhận diện được username IG.")
        return
    await process_ig_check(msg, username)

@router.callback_query(F.data.startswith("fb_note_"))
async def on_fb_note_btn(cb: CallbackQuery, state: FSMContext):
    uid = cb.data.replace("fb_note_", "")
    user = db.get_user(cb.from_user.id)
    if not user or not _sub_active(user):
        await cb.answer("❌ Bạn cần có gói còn hạn để dùng tính năng này.", show_alert=True)
        return
        
    await state.set_state(FBNoteState.waiting_for_note)
    await state.update_data(uid=uid)
    await cb.message.answer(f"✍️ Vui lòng nhập nội dung ghi chú cho UID <b>{uid}</b>:")
    await cb.answer()

@router.message(FBNoteState.waiting_for_note)
async def on_fb_note_input(msg: Message, state: FSMContext):
    _t = (msg.text or "").strip()
    if _t.startswith("/") and _t.split()[0].lower() not in ("/huy", "/cancel"):
        await state.clear()
        await msg.answer("\U0001f6ab \u0110\u00e3 h\u1ee7y thao t\u00e1c \u0111ang nh\u1eadp. B\u1ea1n g\u00f5 l\u1ea1i l\u1ec7nh v\u1eeba r\u1ed3i nh\u00e9.")
        return
    data = await state.get_data()
    uid = data.get("uid")
    note = msg.text.strip()
    await state.clear()
    
    from ..fb import check_uid, avatar_url
    res = await check_uid(uid)
    status = "live" if res["alive"] else "die"
    avatar = res.get("avatar_url") or avatar_url(uid)
    
    wid, is_new = db.add_watch(msg.chat.id, res["uid"], note, 0, 0)
    db.update_watch_status(wid, status, avatar)
    if is_new:
        db.add_log("add", f"Thêm UID {res['uid']} kèm ghi chú", msg.chat.id, res["uid"])

    await msg.answer("✅ Đã lưu ghi chú và thêm vào danh sách theo dõi!" if is_new
                     else "✅ Đã lưu ghi chú (UID này đã được theo dõi từ trước).")
    await _send_card(msg.bot, msg.chat.id, res["uid"], status, note, 0, avatar, "Đã thêm theo dõi (Có ghi chú):")

@router.message(TrackMenuState.waiting_tiktok_username)
async def on_trackmenu_tiktok_input(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        await state.clear()
        await msg.answer("Đã hủy.", reply_markup=_trackmenu_main_kb())
        return
    username = parse_username((msg.text or "").strip())
    if not username:
        await msg.answer("❌ Không nhận diện được username. Gửi lại hoặc /huy để hủy.")
        return
    user = db.get_user(msg.chat.id)
    max_limit = _vip_limit_for(user)
    with db._lock:
        count = db.get_conn().execute("SELECT COUNT(*) FROM tracks WHERE tg_user_id=?", (msg.chat.id,)).fetchone()[0]
    if count >= max_limit:
        await state.clear()
        await msg.answer(f"❌ Giới hạn VIP: tối đa <b>{max_limit}</b> mục.", parse_mode="HTML")
        return
    wait = await msg.answer(f"⏳ Đang thêm theo dõi <b>@{html.escape(username)}</b>...", parse_mode="HTML")
    try:
        info = await fetch_tiktok_info(username)
        u = msg.from_user
        result = db.add_track(u.id, u.username or u.full_name, info["username"],
                              info["followers"], info["following"], info["videos"])
        await state.clear()
        if result == -1:
            await wait.edit_text(f"⚠️ Bạn đã theo dõi <b>@{html.escape(info['username'])}</b> rồi!", parse_mode="HTML")
        else:
            await wait.edit_text(f"✅ Đã thêm theo dõi <b>@{html.escape(info['username'])}</b>!\nMở /theodoi để quản lý.",
                                 parse_mode="HTML", reply_markup=_trackmenu_main_kb())
    except Exception as e:
        await state.clear()
        await wait.edit_text(f"❌ Lỗi: {html.escape(str(e))}")

@router.message(TrackMenuState.waiting_tiktok_video)
async def on_trackmenu_tiktokv_input(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        await state.clear()
        await msg.answer("Đã hủy.", reply_markup=_trackmenu_main_kb())
        return
    from ..tiktok import fetch_video_info, parse_video_id
    video_url = (msg.text or "").strip().split()[0]
    if not parse_video_id(video_url):
        await msg.answer("❌ Link video không hợp lệ. Gửi lại hoặc /huy để hủy.")
        return
    wait = await msg.answer("⏳ Đang lấy thông tin video...")
    try:
        info = await fetch_video_info(video_url)
        u = msg.from_user
        r = db.add_video_track(u.id, u.username or u.full_name, video_url, info["id"],
                               info.get("username", ""), info.get("desc", ""), info.get("cover", ""),
                               3600, info["plays"], info["likes"], info["comments"], info["shares"],
                               info.get("favorites", 0))
        await state.clear()
        if r == -1:
            await wait.edit_text("⚠️ Bạn đã theo dõi video này rồi!")
        else:
            await wait.edit_text("✅ Đã thêm theo dõi video!\nMở /theodoi để quản lý.",
                                 reply_markup=_trackmenu_main_kb())
    except Exception as e:
        await state.clear()
        await wait.edit_text(f"❌ Lỗi: {html.escape(str(e))}")

@router.message(TrackMenuState.waiting_zalo_phone)
async def on_trackmenu_zalo_input(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        await state.clear()
        await msg.answer("Đã hủy.", reply_markup=_trackmenu_main_kb())
        return
    phone = "".join(ch for ch in (msg.text or "") if ch.isdigit() or ch == "+").strip()
    if len(phone) < 9:
        await msg.answer("❌ SĐT không hợp lệ. Gửi lại hoặc /huy để hủy.")
        return
    user = db.get_user(msg.chat.id)
    max_limit = _vip_limit_for(user)
    with db._lock:
        c1 = db.get_conn().execute("SELECT COUNT(*) FROM tracks WHERE tg_user_id=?", (msg.chat.id,)).fetchone()[0]
        c2 = db.get_conn().execute("SELECT COUNT(*) FROM zalo_tracks WHERE tg_user_id=?", (msg.chat.id,)).fetchone()[0]
    if c1 + c2 >= max_limit:
        await state.clear()
        await msg.answer(f"❌ Giới hạn VIP: tối đa <b>{max_limit}</b> mục.", parse_mode="HTML")
        return
    wait = await msg.answer("⏳ Đang xử lý theo dõi SĐT Zalo...")
    try:
        ok, err = db.check_daily_limit(msg.chat.id)
        if not ok:
            await state.clear()
            await wait.edit_text(f"❌ {html.escape(err)}")
            return
        from app.zalo_checker import check_zalo_phone
        cookie = db.get_setting("zalo_cookie", "")
        imei = db.get_setting("zalo_imei", "")
        res = await check_zalo_phone(phone, cookie, imei)
        status = "LIVE" if res.get("live") else "DIE"
        db.add_zalo_track(msg.chat.id, msg.from_user.username or msg.from_user.full_name,
                          phone, res.get("name", ""), res.get("avatar", ""), status)
        await state.clear()
        await wait.edit_text(f"✅ Đã thêm SĐT <b>{html.escape(phone)}</b> ({status})!\nMở /theodoi để quản lý.",
                             parse_mode="HTML", reply_markup=_trackmenu_main_kb())
    except Exception as e:
        await state.clear()
        await wait.edit_text(f"❌ Lỗi: {html.escape(str(e))}")

@router.message(TienIchState.waiting_for_getuid)
async def on_tienich_getuid(msg: Message, state: FSMContext):
    if (msg.text or "").strip().lower() in ("/huy", "/cancel"):
        await state.clear()
        await msg.answer("❌ Đã hủy lấy UID.")
        return
    link = (msg.text or "").strip()
    await state.clear()
    if not link:
        await msg.answer("❌ Link trống, thử lại nhé.")
        return
    wait = await msg.answer("⏳ Đang lấy UID từ link...")
    try:
        from ..fb import resolve_fb_uid
        uid, fb_name, method = await resolve_fb_uid(link)
        if uid:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔍 Check Live/Die ngay", callback_data=f"fb_quickcheck_{uid}")
            ]])
            name_line = f"👤 Tên: <b>{html.escape(fb_name)}</b>\n" if fb_name else ""
            await wait.edit_text(
                f"✅ <b>Lấy UID thành công!</b>\n\n"
                f"🆔 UID: <code>{uid}</code>\n"
                f"{name_line}"
                f"🔎 Lấy bằng: {method}\n\n"
                f"👆 Bấm vào UID để copy, hoặc bấm nút bên dưới để check luôn.",
                parse_mode="HTML", reply_markup=kb)
        else:
            await wait.edit_text("❌ Không lấy được UID từ link này.\nHãy kiểm tra lại link (cần là link trang cá nhân hoặc page công khai).")
    except Exception as e:
        log.exception("tienich getuid %s", link)
        await wait.edit_text(f"❌ Lỗi: {e}")

@router.message(Command("zalo"))
async def on_zalo(msg: Message, command: CommandObject):
    phone = command.args
    if not phone:
        await msg.answer("💡 Gõ /zalo &lt;sđt&gt; để kiểm tra nhanh SĐT Zalo.")
        return
        
    wait = await msg.answer("⏳ Đang kiểm tra Zalo...")
    try:
        cookie = db.get_setting("zalo_cookie", "")
        imei = db.get_setting("zalo_imei", "")
        res = await check_zalo_phone(phone, cookie, imei)
        if res.get("live"):
            await wait.edit_text(f"✅ <b>LIVE</b>\\nSĐT: {phone}\\nTên Zalo: <b>{res['name']}</b>", parse_mode="HTML")
        else:
            await wait.edit_text(f"❌ <b>DIE / KHÔNG TÌM THẤY</b>\\nSĐT: {phone}\\nLỗi: {res.get('error', '')}", parse_mode="HTML")
    except Exception as e:
        await wait.edit_text(f"❌ Lỗi: {str(e)}")

@router.message(Command("trackzalo"))
async def on_trackzalo(msg: Message, command: CommandObject):
    phone = command.args
    if not phone:
        await msg.answer("💡 Gõ /trackzalo &lt;sđt&gt; để theo dõi biến động SĐT Zalo.")
        return
        
    user = db.get_user(msg.chat.id)
    vip_level = dict(user).get("vip_level", 0) if user else 0
    try: max_limit = int(db.get_setting(f"vip{vip_level}_limit", [5, 50, 200, 1000][vip_level if vip_level <= 3 else 3]))
    except: max_limit = [5, 50, 200, 1000][vip_level if vip_level <= 3 else 3]
    
    with db._lock: count = db.get_conn().execute("SELECT COUNT(*) FROM tracks WHERE tg_user_id=?", (msg.chat.id,)).fetchone()[0]
    with db._lock: z_count = db.get_conn().execute("SELECT COUNT(*) FROM zalo_tracks WHERE tg_user_id=?", (msg.chat.id,)).fetchone()[0]
    if count + z_count >= max_limit:
        await msg.answer(f"❌ <b>Giới hạn hạng VIP!</b>\\nHạng của bạn chỉ cho phép tối đa <b>{max_limit}</b> mục.", parse_mode="HTML")
        return
        
    wait = await msg.answer("⏳ Đang xử lý theo dõi SĐT Zalo...")
    try:
        ok, err = db.check_daily_limit(msg.chat.id)
        if not ok:
            await wait.edit_text(f"❌ {err}")
            return
            
        cookie = db.get_setting("zalo_cookie", "")
        imei = db.get_setting("zalo_imei", "")
        res = await check_zalo_phone(phone, cookie, imei)
        
        if res.get("live"):
            status = "LIVE"
            name = res.get("name", "")
            avatar = res.get("avatar", "")
        else:
            status = "DIE"
            name = ""
            avatar = ""
            
        db.add_zalo_track(msg.chat.id, msg.from_user.username or msg.from_user.full_name, phone, name, avatar, status)
        db.add_log("track_add", f"Thêm Zalo {phone}", msg.chat.id, phone)
        await wait.edit_text(f"✅ Đã thêm SĐT Zalo <b>{phone}</b> vào danh sách theo dõi!\\nTrạng thái hiện tại: {status}", parse_mode="HTML")
    except Exception as e:
        await wait.edit_text(f"❌ Lỗi: {str(e)}")

@router.message(Command("scanlist"))
async def on_scanlist_cmd(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await msg.answer("⚠️ Cú pháp: /scanlist &lt;tên danh sách&gt;")
        return
    await _run_scanlist(msg.chat.id, msg.from_user.id, parts[1].strip(),
                        answer=msg.answer, bot=msg.bot)

@router.message(Command("checkfile"))
@router.message(Command("scanfile"))
@router.message(Command("fbfile"))
async def on_checkfile_cmd(msg: Message, state: FSMContext):
    """Lệnh check UID FB bằng file .txt."""
    if msg.document:
        await _process_file_check(msg, state)
        return

    await state.set_state(FileCheckState.waiting_for_file)
    await msg.answer(
        "📁 <b>CHECK UID FACEBOOK BẰNG FILE (.txt / .xlsx)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Vui lòng đính kèm và <b>gửi file</b> chứa danh sách UID Facebook:\n"
        "• File <b>.txt</b>: mỗi UID hoặc đường link 1 dòng.\n"
        "• File <b>.xlsx</b>: mọi cấu trúc cột — ô nào chứa link Facebook "
        "(kể cả link chưa có ID) sẽ <b>tự động giải thành UID</b> rồi mới check. "
        "Xong sẽ trả file mới giữ nguyên cột, ô link được thay bằng UID.\n\n"
        "💡 <i>Gửi file đính kèm ngay tại đây. Nếu muốn huỷ, hãy gõ /cancel</i>",
        parse_mode="HTML"
    )

@router.message(FileCheckState.waiting_for_file, F.document)
async def on_file_received(msg: Message, state: FSMContext):
    await state.clear()
    await _process_file_check(msg, state)

async def _ensure_bulk_credits(msg: Message, wait: Message, n_uids: int) -> bool:
    """Kiểm tra + trừ credits cho check bulk (VIP còn hạn được miễn).
    Trả về True nếu được phép tiếp tục."""
    bulk_cost = int(db.get_setting("bulk_credit_cost", "0") or 0)
    raw_u = db.get_user(msg.from_user.id)
    if bulk_cost > 0 and not _sub_active(dict(raw_u) if raw_u else None):
        need = n_uids * bulk_cost
        have = db.get_credits(msg.from_user.id)
        if have < need:
            await wait.edit_text(
                f"❌ <b>Không đủ credits!</b>\n\n"
                f"Cần: <b>{need}</b> credits ({n_uids} UID × {bulk_cost})\n"
                f"Bạn có: <b>{have}</b> credits\n\n"
                f"Mua thêm bằng /muacredit",
                parse_mode="HTML",
            )
            return False
        db.consume_credits(msg.from_user.id, need)
        await _maybe_low_credit_warn(msg.bot, msg.from_user.id, msg.chat.id)
    return True

async def _process_file_check(msg: Message, state: FSMContext):
    doc = msg.document
    if not doc:
        await msg.answer("❌ Không tìm thấy file đính kèm!")
        return

    file_name = doc.file_name or "list.txt"
    is_xlsx = file_name.lower().endswith(".xlsx")
    if not (file_name.endswith(".txt") or file_name.endswith(".csv") or file_name.endswith(".log") or is_xlsx):
        await msg.answer("❌ Hệ thống chỉ hỗ trợ file .txt, .csv, .log, .xlsx. Vui lòng gửi lại!")
        return

    wait = await msg.answer(f"⏳ Đang đọc file <b>{file_name}</b>...", parse_mode="HTML")

    try:
        file_info = await msg.bot.get_file(doc.file_id)
        file_bytes = await msg.bot.download_file(file_info.file_path)

        if is_xlsx:
            await _process_xlsx_check(msg, wait, file_name, file_bytes.getvalue())
            return

        raw_content = file_bytes.getvalue().decode("utf-8", errors="ignore")

        from ..fb import extract_uid
        lines = [l.strip() for l in raw_content.splitlines() if l.strip()]
        uids = []
        for line in lines:
            uid = extract_uid(line)
            if not uid or not uid.isalnum():
                m = re.search(r'\d{6,}', line)
                if m: uid = m.group(0)
            if uid and uid not in uids:
                uids.append(uid)

        if not uids:
            await wait.edit_text("❌ Không tìm thấy UID hợp lệ nào trong file! Vui lòng kiểm tra lại nội dung file.")
            return

        # Trừ credits cho check bulk (user VIP còn hạn được miễn; mặc định cost=0 = miễn phí)
        if not await _ensure_bulk_credits(msg, wait, len(uids)):
            return

        if len(uids) > 1000:
            await wait.edit_text("⚠️ Số lượng UID vượt quá giới hạn (Tối đa 1,000 UID/lần). Hệ thống sẽ lấy 1,000 UID đầu tiên.")
            uids = uids[:1000]

        await wait.edit_text(f"⚡ Đang quét siêu tốc (Async Batching) <b>{len(uids)}</b> UID từ file <b>{file_name}</b>...", parse_mode="HTML")

        live_list, die_list, error_list = await check_uids_batch(uids, concurrency=15, user_id=msg.from_user.id)

        check_cache_set("file", msg.chat.id, msg.from_user.id, {
            "file_name": file_name,
            "uids": uids,
            "live": live_list,
            "die": die_list,
            "error": error_list,
            "time": time.time()
        })

        text = (
            f"📊 <b>KẾT QUẢ CHECK FILE UID FACEBOOK</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📂 File: <b>{file_name}</b>\n"
            f"🔢 Tổng UID: <b>{len(uids)}</b>\n"
            f"🟢 LIVE: <b>{len(live_list)}</b> tài khoản\n"
            f"🔴 DIE: <b>{len(die_list)}</b> tài khoản\n"
        )
        if error_list:
            text += f"⚠️ Lỗi: <b>{len(error_list)}</b> tài khoản\n"
        text += "\n"

        if live_list:
            text += "🟢 <b>LIVE (Xem trước tối đa 15 UID):</b>\n" + "\n".join(f"• <code>{u}</code>" for u in live_list[:15])
            if len(live_list) > 15:
                text += f"\n<i>...và {len(live_list)-15} UID Live khác</i>"
            text += "\n\n"

        if die_list:
            text += "🔴 <b>DIE (Xem trước tối đa 10 UID):</b>\n" + "\n".join(f"• <code>{u}</code>" for u in die_list[:10])
            if len(die_list) > 10:
                text += f"\n<i>...và {len(die_list)-10} UID Die khác</i>"

        buttons = [
            [InlineKeyboardButton(text=f"📁 Thêm tất cả vào Danh Sách ({len(uids)})", callback_data="fc_addlist")],
            [InlineKeyboardButton(text=f"🔔 Theo Dõi Tự Động UID Live ({len(live_list)})", callback_data="fc_tracklive")],
            [InlineKeyboardButton(text="📥 Tải File CSV Kết Quả", callback_data="fc_exportcsv")],
            [InlineKeyboardButton(text="📊 Xuất File Excel (.xlsx)", callback_data="fc_exportxlsx")],
        ]
        # Upsell: die nhiều (>=5 và >=20%) -> gợi ý mua acc thay thế 1 chạm
        if len(die_list) >= 5 and len(die_list) * 5 >= len(uids):
            text += (f"\n💡 <b>Cần acc thay thế?</b> Bạn vừa check {len(uids)} acc, "
                     f"die tới <b>{len(die_list)}</b> — bấm 1 chạm để vào shop mua ngay!\n")
            buttons.insert(0, [InlineKeyboardButton(text="🛒 Mua acc thay thế ngay", callback_data="accshop_back")])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)

        await wait.delete()
        await msg.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)

    except Exception as e:
        log.error(f"Error processing file check: {e}")
        await wait.edit_text(f"❌ Lỗi xử lý file: {e}")

async def _process_xlsx_check(msg: Message, wait: Message, file_name: str, content: bytes):
    """Check UID từ file .xlsx với mọi cấu trúc cột: link FB tự resolve thành UID,
    check từng acc, rồi trả file mới giữ nguyên cấu trúc cột (ô link -> UID)."""
    from ..fb import extract_uids_from_xlsx, build_xlsx_result, check_uid

    await wait.edit_text(
        f"🔗 Đang đọc file <b>{file_name}</b>, tự giải link Facebook thành UID...",
        parse_mode="HTML")

    try:
        parsed = await extract_uids_from_xlsx(content)
    except Exception as e:
        log.error(f"extract_uids_from_xlsx failed: {e}")
        await wait.edit_text(f"❌ Không đọc được file Excel: {e}")
        return

    uids = parsed["uids"]
    if not uids:
        extra = ""
        if parsed["unresolved"]:
            extra = f"\n⚠️ Có {len(parsed['unresolved'])} link không giải được thành UID."
        await wait.edit_text(
            "❌ Không tìm thấy UID hoặc link Facebook hợp lệ nào trong file!" + extra)
        return

    # Trừ credits cho check bulk (VIP còn hạn được miễn)
    if not await _ensure_bulk_credits(msg, wait, len(uids)):
        return

    if len(uids) > 1000:
        await wait.edit_text("⚠️ Số lượng UID vượt quá giới hạn (tối đa 1.000 UID/lần). Hệ thống sẽ lấy 1.000 UID đầu tiên.")
        uids = uids[:1000]

    await wait.edit_text(
        f"⚡ Đang quét siêu tốc <b>{len(uids)}</b> UID từ file <b>{file_name}</b>...",
        parse_mode="HTML")

    # Check từng UID, giữ lại tên để ghi vào file kết quả
    sem = asyncio.Semaphore(15)
    results: dict = {}

    async def _one(uid: str):
        async with sem:
            try:
                res = await check_uid(uid)
                st = res.get("status", "error")
                try:
                    db.log_check_stat(msg.from_user.id, "fb", uid, st, res.get("via", ""))
                except Exception:
                    pass
                results[uid] = {"status": st, "name": res.get("name") or ""}
            except Exception:
                results[uid] = {"status": "error", "name": ""}

    await asyncio.gather(*[_one(u) for u in uids])

    live_list = [u for u in uids if results[u]["status"] == "live"]
    die_list = [u for u in uids if results[u]["status"] == "die"]
    error_list = [u for u in uids if u not in live_list and u not in die_list]
    if live_list or die_list:
        try:
            db.add_check_history(
                msg.from_user.id, "fb",
                f"Check file xlsx: {len(live_list)} Live, {len(die_list)} Die",
                "live" if live_list else "die")
        except Exception:
            pass

    try:
        out_bytes = build_xlsx_result(parsed, results)
    except Exception as e:
        log.error(f"build_xlsx_result failed: {e}")
        out_bytes = None

    check_cache_set("file", msg.chat.id, msg.from_user.id, {
        "file_name": file_name,
        "uids": uids,
        "live": live_list,
        "die": die_list,
        "error": error_list,
        "xlsx_out": out_bytes,
        "time": time.time()
    })

    text = (
        f"📊 <b>KẾT QUẢ CHECK FILE EXCEL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📂 File: <b>{file_name}</b>\n"
        f"🔗 Link đã giải thành UID: <b>{len(parsed['link_cells'])}</b>\n"
        f"🔢 Tổng UID: <b>{len(uids)}</b>\n"
        f"🟢 LIVE: <b>{len(live_list)}</b> tài khoản\n"
        f"🔴 DIE: <b>{len(die_list)}</b> tài khoản\n"
    )
    if error_list:
        text += f"⚠️ Lỗi: <b>{len(error_list)}</b> tài khoản\n"
    if parsed["unresolved"]:
        text += f"❓ Link không giải được: <b>{len(parsed['unresolved'])}</b>\n"
        show_bad = parsed["unresolved"][:10]
        for lk in show_bad:
            short = lk if len(lk) <= 60 else lk[:57] + "..."
            text += f"• <code>{short}</code>\n"
        if len(parsed["unresolved"]) > 10:
            text += f"<i>...và {len(parsed['unresolved']) - 10} link khác (xem cột Trạng thái trong file Excel)</i>\n"
    text += "\n"

    if live_list:
        text += "🟢 <b>LIVE (xem trước tối đa 15):</b>\n" + "\n".join(f"• <code>{u}</code>" for u in live_list[:15])
        if len(live_list) > 15:
            text += f"\n<i>...và {len(live_list)-15} UID Live khác</i>"
        text += "\n\n"

    if die_list:
        text += "🔴 <b>DIE (xem trước tối đa 10):</b>\n" + "\n".join(f"• <code>{u}</code>" for u in die_list[:10])
        if len(die_list) > 10:
            text += f"\n<i>...và {len(die_list)-10} UID Die khác</i>"

    buttons = [
        [InlineKeyboardButton(text=f"📁 Thêm tất cả vào Danh Sách ({len(uids)})", callback_data="fc_addlist")],
        [InlineKeyboardButton(text=f"🔔 Theo Dõi Tự Động UID Live ({len(live_list)})", callback_data="fc_tracklive")],
    ]
    if out_bytes:
        buttons.append([InlineKeyboardButton(text="📥 Tải file Excel", callback_data="fc_export_uidxlsx")])
    # Upsell: die nhiều (>=5 và >=20%) -> gợi ý mua acc thay thế 1 chạm
    if len(die_list) >= 5 and len(die_list) * 5 >= len(uids):
        text += (f"\n💡 <b>Cần acc thay thế?</b> Bạn vừa check {len(uids)} acc, "
                 f"die tới <b>{len(die_list)}</b> — bấm 1 chạm để vào shop mua ngay!\n")
        buttons.insert(0, [InlineKeyboardButton(text="🛒 Mua acc thay thế ngay", callback_data="accshop_back")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    await wait.delete()
    await msg.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)

@router.callback_query(F.data == "fc_export_uidxlsx")
async def on_fc_export_uidxlsx(cb: CallbackQuery):
    """Gửi file Excel kết quả với cấu trúc cột cố định:
    uid | mk | tình trạng | gmail | mail thay | 2fa | Ghi chú | Cookie | Token."""
    cache = check_cache_get("file", cb.message.chat.id, cb.from_user.id)
    if not cache or not cache.get("xlsx_out"):
        await cb.answer("❌ Đã hết phiên lưu trữ, vui lòng gửi lại file!", show_alert=True)
        return
    raw_name = (cache.get("file_name") or "ketqua.xlsx").rsplit(".", 1)[0]
    file = BufferedInputFile(cache["xlsx_out"], filename=f"ketqua_{raw_name}.xlsx")
    await cb.message.answer_document(
        document=file,
        caption="📥 File Excel kết quả: uid | mk | tình trạng | gmail | mail thay | 2fa | Ghi chú | Cookie | Token.")
    await cb.answer()

@router.callback_query(F.data == "fc_addlist")
async def on_fc_addlist(cb: CallbackQuery):
    cache = check_cache_get("file", cb.message.chat.id, cb.from_user.id)
    if not cache:
        await cb.answer("❌ Đã hết phiên lưu trữ, vui lòng gửi lại file!", show_alert=True)
        return

    uids = cache["uids"]
    list_name = f"File_{time.strftime('%d%m_%H%M')}"
    db.create_user_list(cb.from_user.id, list_name)
    for uid in uids:
        db.add_to_user_list(cb.from_user.id, list_name, uid)

    await cb.answer("✅ Đã lưu danh sách!", show_alert=False)
    await cb.message.answer(
        f"✅ <b>ĐÃ THÊM VÀO DANH SÁCH!</b>\n\n"
        f"📁 Đã lưu <b>{len(uids)}</b> UID vào danh sách: <b>{list_name}</b>\n"
        f"👉 Gõ <code>/scanlist {list_name}</code> để kiểm tra lại bất cứ lúc nào.",
        parse_mode="HTML"
    )

@router.callback_query(F.data == "fc_exportxlsx")
async def on_fc_exportxlsx(cb: CallbackQuery):
    cache = check_cache_get("file", cb.message.chat.id, cb.from_user.id)
    if not cache:
        await cb.answer("❌ Đã hết phiên lưu trữ!", show_alert=True)
        return
    try:
        data = _build_excel_bytes({
            "Ket qua": (
                ["UID", "Trạng thái"],
                [[u, "LIVE"] for u in cache["live"]]
                + [[u, "DIE"] for u in cache["die"]]
                + [[u, "ERROR"] for u in cache["error"]],
            )
        })
    except Exception as e:
        await cb.answer(f"❌ Lỗi tạo Excel: {e}", show_alert=True)
        return
    from aiogram.types import BufferedInputFile
    raw_name = cache['file_name'].rsplit('.', 1)[0]
    file = BufferedInputFile(data, filename=f"result_{raw_name}.xlsx")
    await cb.answer()
    await cb.message.answer_document(document=file, caption="📊 File Excel tổng hợp kết quả check UID.")

@router.callback_query(F.data == "fc_exportcsv")
async def on_fc_exportcsv(cb: CallbackQuery):
    cache = check_cache_get("file", cb.message.chat.id, cb.from_user.id)
    if not cache:
        await cb.answer("❌ Đã hết phiên lưu trữ!", show_alert=True)
        return

    import csv, io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["UID", "Status"])
    for u in cache["live"]: writer.writerow([u, "LIVE"])
    for u in cache["die"]: writer.writerow([u, "DIE"])
    for u in cache["error"]: writer.writerow([u, "ERROR"])
    output.seek(0)

    from aiogram.types import BufferedInputFile
    raw_name = cache['file_name'].rsplit('.', 1)[0]
    out_name = f"result_{raw_name}.csv"
    file = BufferedInputFile(output.getvalue().encode('utf-8-sig'), filename=out_name)
    await cb.answer()
    await cb.message.answer_document(document=file, caption="📥 File CSV tổng hợp kết quả check UID.")

@router.message(Command("checkcookie"))
async def on_checkcookie_cmd(msg: Message, state: FSMContext):
    """Check dàn Cookie format UID|PASS|COOKIE|2FA."""
    parts = msg.text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].strip():
        await _process_cookie_text(msg, parts[1].strip())
        return

    if msg.document:
        await _process_cookie_file(msg, state)
        return

    await state.set_state(CookieCheckState.waiting_for_file)
    await msg.answer(
        "🍪 <b>CHECK COOKIE FACEBOOK (UID|PASS|COOKIE|2FA)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Vui lòng gửi file <b>.txt</b> đính kèm chứa danh sách Cookie/Nick.\n"
        "Hệ thống hỗ trợ các định dạng:\n"
        "• <code>UID|PASS|COOKIE|2FA</code>\n"
        "• <code>COOKIE</code> (Nguyên chuỗi c_user=...)\n"
        "• <code>UID|COOKIE</code>\n\n"
        "💡 <i>Gửi file đính kèm tại đây. Hoặc gõ <code>/checkcookie &lt;đoạn_text&gt;</code></i>",
        parse_mode="HTML"
    )

@router.message(CookieCheckState.waiting_for_file, F.document)
async def on_cookie_file_received(msg: Message, state: FSMContext):
    await state.clear()
    await _process_cookie_file(msg, state)

async def _process_cookie_file(msg: Message, state: FSMContext):
    doc = msg.document
    if not doc:
        await msg.answer("❌ Vui lòng gửi file đính kèm!")
        return

    wait = await msg.answer(f"⏳ Đang tải và kiểm tra file cookie <b>{doc.file_name}</b>...", parse_mode="HTML")
    try:
        file_info = await msg.bot.get_file(doc.file_id)
        file_bytes = await msg.bot.download_file(file_info.file_path)
        raw_text = file_bytes.getvalue().decode("utf-8", errors="ignore")
        await _process_cookie_text(msg, raw_text, wait_msg=wait, file_name=doc.file_name)
    except Exception as e:
        await wait.edit_text(f"❌ Lỗi đọc file: {e}")

async def _process_cookie_text(msg: Message, raw_text: str, wait_msg=None, file_name: str = "cookie.txt"):
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    if not lines:
        if wait_msg: await wait_msg.edit_text("❌ Nội dung trống!")
        else: await msg.answer("❌ Nội dung trống!")
        return

    # Trừ credits cho check cookie bulk (user VIP còn hạn được miễn; mặc định cost=0 = miễn phí)
    bulk_cost = int(db.get_setting("bulk_credit_cost", "0") or 0)
    raw_u2 = db.get_user(msg.from_user.id)
    if bulk_cost > 0 and not _sub_active(dict(raw_u2) if raw_u2 else None):
        need = len(lines) * bulk_cost
        have = db.get_credits(msg.from_user.id)
        if have < need:
            txt = (
                f"❌ <b>Không đủ credits!</b>\n\n"
                f"Cần: <b>{need}</b> credits ({len(lines)} dòng × {bulk_cost})\n"
                f"Bạn có: <b>{have}</b> credits\n\n"
                f"Mua thêm bằng /muacredit"
            )
            if wait_msg:
                await wait_msg.edit_text(txt, parse_mode="HTML")
            else:
                await msg.answer(txt, parse_mode="HTML")
            return
        db.consume_credits(msg.from_user.id, need)
        await _maybe_low_credit_warn(msg.bot, msg.from_user.id, msg.chat.id)

    if not wait_msg:
        wait_msg = await msg.answer(f"⚡ Đang kiểm tra siêu tốc <b>{len(lines)}</b> cookie...", parse_mode="HTML")
    else:
        await wait_msg.edit_text(f"⚡ Đang kiểm tra siêu tốc <b>{len(lines)}</b> cookie...", parse_mode="HTML")

    from ..fb import _check_with_cookie, extract_uid
    sem = asyncio.Semaphore(15)

    live_items = []
    cp282_items = []
    cp956_items = []
    checkpoint_items = []
    die_items = []

    async def _check_line(line):
        async with sem:
            cookie_str = ""
            uid = ""
            parts = line.split("|")
            for p in parts:
                p_clean = p.strip()
                if "c_user=" in p_clean or "xs=" in p_clean or "fr=" in p_clean:
                    cookie_str = p_clean
                elif not uid and p_clean.isdigit() and len(p_clean) >= 6:
                    uid = p_clean

            if not cookie_str:
                cookie_str = line

            if not uid:
                uid = extract_uid(line) or "unknown"

            res = await _check_with_cookie(uid, cookie_str)
            status = res.get("status", "dead")

            if status == "live":
                live_items.append(line)
            elif status == "checkpoint_282":
                cp282_items.append(line)
            elif status == "checkpoint_956":
                cp956_items.append(line)
            elif status == "checkpoint":
                checkpoint_items.append(line)
            else:
                die_items.append(line)

    tasks = [_check_line(line) for line in lines[:500]]
    await asyncio.gather(*tasks, return_exceptions=True)

    check_cache_set("cookie", msg.chat.id, msg.from_user.id, {
        "file_name": file_name,
        "live": live_items,
        "checkpoint_282": cp282_items,
        "checkpoint_956": cp956_items,
        "checkpoint": checkpoint_items,
        "die": die_items,
        "total": len(lines)
    })

    text = (
        f"🍪 <b>KẾT QUẢ CHECK COOKIE FACEBOOK</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📂 File: <b>{file_name}</b>\n"
        f"🔢 Tổng số: <b>{len(lines)}</b> cookie\n"
        f"🟢 Cookie LIVE: <b>{len(live_items)}</b>\n"
        f"🟡 Checkpoint 282 (xác minh): <b>{len(cp282_items)}</b>\n"
        f"🟠 Checkpoint 956 (vi phạm): <b>{len(cp956_items)}</b>\n"
        f"⚪ Checkpoint khác: <b>{len(checkpoint_items)}</b>\n"
        f"🔴 DIE / Expired: <b>{len(die_items)}</b>\n\n"
        "<i>Bấm nút bên dưới để xuất file TXT riêng từng loại:</i>"
    )

    buttons = [
        [
            InlineKeyboardButton(text=f"🟢 Tải Nick LIVE ({len(live_items)})", callback_data="ck_export_live"),
            InlineKeyboardButton(text=f"🔴 Tải Nick DIE ({len(die_items)})", callback_data="fc_export_die_ck"),
        ],
        [
            InlineKeyboardButton(text=f"🟡 Tải CP 282 ({len(cp282_items)})", callback_data="ck_export_282"),
            InlineKeyboardButton(text=f"🟠 Tải CP 956 ({len(cp956_items)})", callback_data="ck_export_956"),
        ],
        [
            InlineKeyboardButton(text="📊 Xuất File Excel (.xlsx)", callback_data="ck_export_xlsx"),
        ],
    ]

    await wait_msg.delete()
    await msg.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(F.data == "ck_export_live")
async def on_ck_export_live(cb: CallbackQuery):
    cache = check_cache_get("cookie", cb.message.chat.id, cb.from_user.id)
    if not cache or not cache["live"]:
        await cb.answer("❌ Không có nick LIVE nào!", show_alert=True)
        return

    content = "\n".join(cache["live"])
    from aiogram.types import BufferedInputFile
    file = BufferedInputFile(content.encode("utf-8"), filename="live_cookies.txt")
    await cb.answer()
    await cb.message.answer_document(document=file, caption="🟢 File danh sách Cookie LIVE.")

@router.callback_query(F.data == "fc_export_die_ck")
async def on_fc_export_die_ck(cb: CallbackQuery):
    cache = check_cache_get("cookie", cb.message.chat.id, cb.from_user.id)
    if not cache or not cache["die"]:
        await cb.answer("❌ Không có nick DIE nào!", show_alert=True)
        return

    content = "\n".join(cache["die"])
    from aiogram.types import BufferedInputFile
    file = BufferedInputFile(content.encode("utf-8"), filename="die_cookies.txt")
    await cb.answer()
    await cb.message.answer_document(document=file, caption="🔴 File danh sách Cookie DIE / Expired.")

@router.callback_query(F.data == "ck_export_282")
async def on_ck_export_282(cb: CallbackQuery):
    cache = check_cache_get("cookie", cb.message.chat.id, cb.from_user.id)
    if not cache or not cache.get("checkpoint_282"):
        await cb.answer("❌ Không có nick Checkpoint 282 nào!", show_alert=True)
        return
    content = "\n".join(cache["checkpoint_282"])
    from aiogram.types import BufferedInputFile
    file = BufferedInputFile(content.encode("utf-8"), filename="checkpoint_282.txt")
    await cb.answer()
    await cb.message.answer_document(document=file, caption="🟡 File danh sách Cookie Checkpoint 282 (xác minh danh tính).")

@router.callback_query(F.data == "ck_export_956")
async def on_ck_export_956(cb: CallbackQuery):
    cache = check_cache_get("cookie", cb.message.chat.id, cb.from_user.id)
    if not cache or not cache.get("checkpoint_956"):
        await cb.answer("❌ Không có nick Checkpoint 956 nào!", show_alert=True)
        return
    content = "\n".join(cache["checkpoint_956"])
    from aiogram.types import BufferedInputFile
    file = BufferedInputFile(content.encode("utf-8"), filename="checkpoint_956.txt")
    await cb.answer()
    await cb.message.answer_document(document=file, caption="🟠 File danh sách Cookie Checkpoint 956 (vi phạm/khóa).")

@router.callback_query(F.data == "ck_export_xlsx")
async def on_ck_export_xlsx(cb: CallbackQuery):
    cache = check_cache_get("cookie", cb.message.chat.id, cb.from_user.id)
    if not cache:
        await cb.answer("❌ Đã hết phiên lưu trữ!", show_alert=True)
        return
    try:
        sheets = {}
        def _rows(items):
            rows = []
            for i, line in enumerate(items, 1):
                parts = line.split("|")
                uid = next((p.strip() for p in parts if p.strip().isdigit() and len(p.strip()) >= 6), "")
                rows.append([i, uid or line[:40], line])
            return rows
        if cache.get("live"):
            sheets["LIVE"] = (["STT", "UID", "Dòng gốc"], _rows(cache["live"]))
        if cache.get("checkpoint_282"):
            sheets["CP 282"] = (["STT", "UID", "Dòng gốc"], _rows(cache["checkpoint_282"]))
        if cache.get("checkpoint_956"):
            sheets["CP 956"] = (["STT", "UID", "Dòng gốc"], _rows(cache["checkpoint_956"]))
        if cache.get("checkpoint"):
            sheets["CP khac"] = (["STT", "UID", "Dòng gốc"], _rows(cache["checkpoint"]))
        if cache.get("die"):
            sheets["DIE"] = (["STT", "UID", "Dòng gốc"], _rows(cache["die"]))
        if not sheets:
            await cb.answer("❌ Không có dữ liệu!", show_alert=True)
            return
        data = _build_excel_bytes(sheets)
    except Exception as e:
        await cb.answer(f"❌ Lỗi tạo Excel: {e}", show_alert=True)
        return
    from aiogram.types import BufferedInputFile
    file = BufferedInputFile(data, filename="ket_qua_check_cookie.xlsx")
    await cb.answer()
    await cb.message.answer_document(document=file, caption="📊 File Excel kết quả check cookie (chia sheet theo loại).")

@router.message(Command("accuracy"))
async def on_accuracy(msg: Message):
    """Thống kê độ chính xác / nhất quán của kết quả check (90 ngày)."""
    st = db.get_accuracy_stats(msg.from_user.id)
    if not st["total"]:
        await msg.answer(
            "📊 <b>THỐNG KÊ ĐỘ CHÍNH XÁC</b>\n\n"
            "Bạn chưa có lượt check nào được ghi nhận.\n"
            "Hãy check vài UID bằng /fb hoặc /checkfile rồi quay lại nhé!",
            parse_mode="HTML",
        )
        return
    total = st["total"]
    live_pct = st["live"] * 100 / total
    die_pct = st["die"] * 100 / total
    if st["rechecked"]:
        cons_pct = st["consistent"] * 100 / st["rechecked"]
        cons_line = (
            f"🔁 UID check lại ≥2 lần: <b>{st['rechecked']}</b>\n"
            f"🎯 Nhất quán kết quả: <b>{st['consistent']}/{st['rechecked']} ({cons_pct:.1f}%)</b>\n"
        )
    else:
        cons_line = "🔁 Chưa có UID nào được check lại để đánh giá độ nhất quán.\n"
    await msg.answer(
        "📊 <b>THỐNG KÊ ĐỘ CHÍNH XÁC (90 ngày)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🔢 Tổng lượt check: <b>{total}</b>\n"
        f"🆔 Số UID riêng biệt: <b>{st['unique']}</b>\n"
        f"🟢 LIVE: <b>{st['live']}</b> ({live_pct:.1f}%)\n"
        f"🔴 DIE: <b>{st['die']}</b> ({die_pct:.1f}%)\n"
        f"⚠️ Lỗi: <b>{st['error']}</b>\n\n"
        f"{cons_line}\n"
        "<i>Độ nhất quán = tỉ lệ UID cho cùng kết quả khi check lại nhiều lần.</i>",
        parse_mode="HTML",
    )

def _mask_cookie(ck: str) -> str:
    ck = ck or ""
    return (ck[:10] + "..." + ck[-6:]) if len(ck) > 20 else "***"

@router.message(Command("cookieadd"))
async def on_cookieadd(msg: Message, state: FSMContext):
    """Admin: thêm cookie vào pool xoay vòng.
    Chấp nhận cả 2 cách: /cookieadd <cookie> trong 1 tin nhắn,
    hoặc gõ /cookieadd rồi gửi cookie ở tin nhắn tiếp theo."""
    from ..admin_bot import is_admin
    if not is_admin(msg.chat.id, msg.from_user.id):
        return
    inline_ck = (msg.text or "").partition(" ")[2].strip()
    if inline_ck:
        await _save_pool_cookie(msg, state, inline_ck)
        return
    await state.set_state(CookieAddState.waiting_for_cookie)
    await msg.answer(
        "🍪 <b>THÊM COOKIE VÀO POOL</b>\n\n"
        "Hãy gửi <b>chuỗi cookie Facebook</b> (1 tin nhắn).\n"
        "Sau khi lưu, tin nhắn chứa cookie sẽ được <b>xóa ngay</b> để bảo mật.\n\n"
        "Gõ /cancel để hủy.",
        parse_mode="HTML",
    )

async def _save_pool_cookie(msg: Message, state: FSMContext, cookie: str):
    """Lưu 1 chuỗi cookie vào pool (dùng chung cho cả 2 cách nhập)."""
    if "c_user" not in cookie and "xs" not in cookie:
        await msg.answer("❌ Chuỗi này không giống cookie Facebook (thiếu c_user/xs).")
        return
    from ..fb import get_fb_cookie_pool, set_fb_cookie_pool
    pool = get_fb_cookie_pool()
    if cookie in pool:
        await msg.answer("⚠️ Cookie này đã có trong pool rồi.")
    else:
        pool.append(cookie)
        set_fb_cookie_pool(pool)
        await msg.answer(f"✅ Đã thêm cookie vào pool. Pool hiện có <b>{len(pool)}</b> cookie.", parse_mode="HTML")
    try:
        await msg.delete()
    except Exception:
        pass
    await state.clear()

@router.message(CookieAddState.waiting_for_cookie)
async def on_cookieadd_received(msg: Message, state: FSMContext):
    from ..admin_bot import is_admin
    if not is_admin(msg.chat.id, msg.from_user.id):
        await state.clear()
        return
    if (msg.text or "").strip() == "/cancel":
        await state.clear()
        await msg.answer("Đã hủy.")
        return
    _t = (msg.text or "").strip()
    if _t.startswith("/") and _t.split()[0].lower() not in ("/huy", "/cancel"):
        await state.clear()
        await msg.answer("\U0001f6ab \u0110\u00e3 h\u1ee7y thao t\u00e1c \u0111ang nh\u1eadp. B\u1ea1n g\u00f5 l\u1ea1i l\u1ec7nh v\u1eeba r\u1ed3i nh\u00e9.")
        return
    cookie = (msg.text or "").strip()
    # Nếu user gõ lại lệnh /cookieadd kèm cookie trong lúc đang chờ -> bỏ prefix lệnh
    if cookie.startswith("/cookieadd"):
        cookie = cookie.partition(" ")[2].strip()
    await _save_pool_cookie(msg, state, cookie)

@router.message(Command("cookielist"))
async def on_cookielist(msg: Message):
    """Admin: xem danh sách cookie trong pool (đã che)."""
    from ..admin_bot import is_admin
    if not is_admin(msg.chat.id, msg.from_user.id):
        return
    from ..fb import get_fb_cookie_pool
    pool = get_fb_cookie_pool()
    if not pool:
        await msg.answer("🍪 Pool đang trống. Thêm bằng /cookieadd")
        return
    lines = ["🍪 <b>COOKIE POOL</b>", "━━━━━━━━━━━━", ""]
    for i, ck in enumerate(pool, 1):
        lines.append(f"{i}. <code>{html.escape(_mask_cookie(ck))}</code> ({len(ck)} ký tự)")
    lines += ["", f"Tổng: <b>{len(pool)}</b> cookie", "", "Xóa: /cookiedel &lt;số thứ tự&gt;"]
    await msg.answer("\n".join(lines), parse_mode="HTML")

@router.message(Command("cookiedel"))
async def on_cookiedel(msg: Message):
    """Admin: xóa cookie khỏi pool. Cú pháp: /cookiedel <số thứ tự>"""
    from ..admin_bot import is_admin
    if not is_admin(msg.chat.id, msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: <code>/cookiedel &lt;số thứ tự&gt;</code> (xem số bằng /cookielist)")
        return
    try:
        idx = int(parts[1]) - 1
    except ValueError:
        await msg.answer("❌ Số thứ tự phải là số!")
        return
    from ..fb import get_fb_cookie_pool, set_fb_cookie_pool
    pool = get_fb_cookie_pool()
    if idx < 0 or idx >= len(pool):
        await msg.answer("❌ Số thứ tự không đúng!")
        return
    pool.pop(idx)
    set_fb_cookie_pool(pool)
    await msg.answer(f"✅ Đã xóa. Pool còn <b>{len(pool)}</b> cookie.", parse_mode="HTML")

__all__ = [
    "FBNoteState",
    "CookieCheckState",
    "FileCheckState",
    "CookieAddState",
    "on_scan_all",
    "on_hdcookie",
    "on_tiktok",
    "on_fb",
    "on_getuid",
    "on_fb_quickcheck",
    "process_fb_post_check",
    "on_ig",
    "on_fb_note_btn",
    "on_fb_note_input",
    "on_trackmenu_tiktok_input",
    "on_trackmenu_tiktokv_input",
    "on_trackmenu_zalo_input",
    "on_tienich_getuid",
    "on_zalo",
    "on_trackzalo",
    "on_scanlist_cmd",
    "on_checkfile_cmd",
    "on_file_received",
    "_ensure_bulk_credits",
    "_process_file_check",
    "_process_xlsx_check",
    "on_fc_export_uidxlsx",
    "on_fc_addlist",
    "on_fc_exportxlsx",
    "on_fc_exportcsv",
    "on_checkcookie_cmd",
    "on_cookie_file_received",
    "_process_cookie_file",
    "_process_cookie_text",
    "on_ck_export_live",
    "on_fc_export_die_ck",
    "on_ck_export_282",
    "on_ck_export_956",
    "on_ck_export_xlsx",
    "on_accuracy",
    "_mask_cookie",
    "on_cookieadd",
    "_save_pool_cookie",
    "on_cookieadd_received",
    "on_cookielist",
    "on_cookiedel",
]