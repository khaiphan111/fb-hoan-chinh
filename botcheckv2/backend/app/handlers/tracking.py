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

from .core import DAY, _sub_active, log, router
from .common import TrackMenuState, _esc_list_name, _is_cancel, _run_scanlist, _send_card, _trackmenu_main_kb, _trackmenu_sub_kb

@router.message(Command("track"))
async def on_track(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await msg.answer("⚠️ Cú pháp: /track &lt;username&gt;\nVí dụ: /track cristiano")
        return
    username = parse_username(parts[1].strip())
    if not username:
        await msg.answer("❌ Không nhận diện được username.")
        return

    wait = await msg.answer(f"⏳ Đang thêm theo dõi <b>@{username}</b>...")
    
    # VIP Limit check
    user = db.get_user(msg.chat.id)
    vip_level = dict(user).get("vip_level", 0) if user else 0
    try:
        max_limit = int(db.get_setting(f"vip{vip_level}_limit", [5, 50, 200, 1000][vip_level if vip_level <= 3 else 3]))
    except:
        max_limit = [5, 50, 200, 1000][vip_level if vip_level <= 3 else 3]
    with db._lock:
        count = db.get_conn().execute("SELECT COUNT(*) FROM tracks WHERE tg_user_id=?", (msg.chat.id,)).fetchone()[0]
    if count >= max_limit:
        await wait.edit_text(f"❌ <b>Giới hạn hạng VIP!</b>\nHạng của bạn chỉ cho phép theo dõi tối đa <b>{max_limit}</b> mục.\nVui lòng /untrack các mục cũ hoặc nâng cấp VIP.")
        return
    try:
        info = await fetch_tiktok_info(username)
        u = msg.from_user
        result = db.add_track(
            u.id, u.username or u.full_name,
            info["username"],
            info["followers"], info["following"], info["videos"]
        )
        if result == -1:
            await wait.edit_text(f"⚠️ Bạn đã theo dõi <b>@{info['username']}</b> rồi!")
            return
        db.add_log("track_add", f"Thêm theo dõi @{info['username']}", u.id, info["username"])
        
        caption = (
            f"✅ <b>Đã thêm theo dõi tài khoản!</b>\n\n"
            f"📱 Kênh: <b><a href='https://www.tiktok.com/@{info['username']}'>@{info['username']}</a></b>\n"
            f"👥 Followers hiện tại: <b>{fmt_num(info['followers'])}</b>\n"
            f"➡️ Đang follow: <b>{fmt_num(info['following'])}</b>\n"
            f"🎬 Tổng videos: <b>{fmt_num(info['videos'])}</b>\n\n"
            f"📩 <i>Bot sẽ thông báo khi có thay đổi follower.</i>"
        )
        if info.get("avatar"):
            try:
                await msg.answer_photo(photo=URLInputFile(info["avatar"], filename="avatar.jpg"), caption=caption)
                await wait.delete()
            except Exception:
                await wait.edit_text(caption, disable_web_page_preview=False)
        else:
            await wait.edit_text(caption, disable_web_page_preview=False)
    except ValueError as e:
        await wait.edit_text(f"❌ {e}")
    except Exception as e:
        await wait.edit_text(f"❌ Lỗi: {e}")

@router.message(Command("untrack"))
async def on_untrack(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await msg.answer("⚠️ Cú pháp: /untrack &lt;username&gt;\nVí dụ: /untrack cristiano")
        return
    username = parse_username(parts[1].strip())
    if not username:
        await msg.answer("❌ Không nhận diện được username.")
        return
    ok = db.remove_track(msg.chat.id, username)
    if ok:
        db.add_log("track_remove", f"Huỷ theo dõi @{username}", msg.chat.id, username)
        await msg.answer(f"✅ Đã huỷ theo dõi <b>@{username}</b>.")
    else:
        await msg.answer(f"❌ Không tìm thấy <b>@{username}</b> trong danh sách của bạn.")

@router.message(Command("tracklist"))
async def on_tracklist(msg: Message):
    tracks = db.user_tracks(msg.chat.id)
    if not tracks:
        await msg.answer(
            "📋 Bạn chưa theo dõi tài khoản nào.\n\n"
            "Dùng /track &lt;username&gt; để thêm."
        )
        return
    lines = ["📋 <b>Danh sách đang theo dõi:</b>\n"]
    for i, t in enumerate(tracks, 1):
        lines.append(
            f"{i}. <b>@{t['tiktok_username']}</b>\n"
            f"   👥 {fmt_num(t['last_followers'])} followers"
        )
    await msg.answer("\n".join(lines))

@router.message(Command("trackvlist"))
async def on_trackvlist(msg: Message):
    vtracks = db.user_video_tracks(msg.chat.id)
    if not vtracks:
        await msg.answer("📋 Bạn chưa theo dõi video nào.\n\nDùng /trackv &lt;link_video&gt; để thêm.")
        return
    lines = ["🎬 <b>Video đang theo dõi:</b>\n"]
    for i, v in enumerate(vtracks, 1):
        interval_min = v["check_interval"] // 60
        desc = (v["video_desc"][:50] + "...") if len(v.get("video_desc","")) > 50 else v.get("video_desc","")
        lines.append(
            f"{i}. <a href=\"{v['video_url']}\">@{v['tiktok_username']}</a>\n"
            f"   📝 {desc or 'Khong co mo ta'}\n"
            f"   ▶️ {v['last_plays']:,}  ❤️ {v['last_likes']:,}  💬 {v['last_comments']:,}  🔁 {v['last_shares']:,}  ⭐ {v.get('last_favorites', 0):,}\n"
            f"   ⏱ Check mỗi {interval_min} phút"
        )
    await msg.answer("\n\n".join(lines))

@router.message(Command("trackv"))
async def on_trackv(msg: Message):
    parts = (msg.text or "").split(maxsplit=2)
    if len(parts) < 2:
        await msg.answer(
            "⚠️ Cú pháp: /trackv &lt;link_video&gt; [phút]\n\n"
            "Ví dụ:\n"
            "  /trackv https://tiktok.com/@user/video/123\n"
            "  /trackv https://tiktok.com/@user/video/123 30   (check mỗi 30 phút)"
        )
        return

    from ..tiktok import fetch_video_info, parse_video_id
    video_url = parts[1].strip()
    interval_min = 60
    if len(parts) >= 3:
        try:
            interval_min = max(1, int(parts[2]))
        except ValueError:
            pass

    if not parse_video_id(video_url):
        await msg.answer("❌ Link video không hợp lệ. Cần dạng: tiktok.com/@user/video/ID")
        return

    wait = await msg.answer(f"⏳ Đang lấy thông tin video...")
    try:
        info = await fetch_video_info(video_url)
        u = msg.from_user
        r = db.add_video_track(
            u.id, u.username or u.full_name,
            video_url, info["id"], info.get("username",""),
            info.get("desc",""), info.get("cover",""),
            interval_min * 60,
            info["plays"], info["likes"], info["comments"], info["shares"], info.get("favorites", 0)
        )
        if r == -1:
            await wait.edit_text("⚠️ Bạn đã theo dõi video này rồi!")
            return
        db.add_log("video_track_add", f"Them video @{info.get('username','')}", u.id, info.get("username",""))
        desc = (info.get("desc", "")[:80]+"...") if len(info.get("desc","")) > 80 else info.get("desc","")
        caption = (
            f"✅ <b>Đã thêm theo dõi video!</b>\n\n"
            f"📱 Kênh: <b><a href='https://www.tiktok.com/@{info.get('username','')}'>@{info.get('username','')}</a></b>\n"
            f"📝 Mô tả: <i>{desc or 'Không có mô tả'}</i>\n\n"
            f"📊 <b>Thống kê hiện tại:</b>\n"
            f" ┣ ▶️ Lượt xem: <b>{info['plays']:,}</b>\n"
            f" ┣ ❤️ Lượt thích: <b>{info['likes']:,}</b>\n"
            f" ┣ 💬 Bình luận: <b>{info['comments']:,}</b>\n"
            f" ┣ 🔁 Chia sẻ: <b>{info['shares']:,}</b>\n"
            f" ┗ ⭐ Yêu thích: <b>{info.get('favorites', 0):,}</b>\n\n"
            f"⏱ <i>Tự động check mỗi <b>{interval_min} phút</b></i>\n"
            f"📩 <i>Bot sẽ thông báo khi có tương tác mới!</i>"
        )
        if info.get("cover"):
            try:
                await msg.answer_photo(photo=URLInputFile(info["cover"], filename="cover.jpg"), caption=caption)
                await wait.delete()
            except Exception:
                await wait.edit_text(caption, disable_web_page_preview=False)
        else:
            await wait.edit_text(caption, disable_web_page_preview=False)
    except Exception as e:
        await wait.edit_text(f"❌ Lỗi: {e}")

@router.message(Command("untrackv"))
async def on_untrackv(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: /untrackv &lt;link_video&gt;")
        return
    from ..tiktok import parse_video_id
    vid_id = parse_video_id(parts[1].strip())
    if not vid_id:
        await msg.answer("❌ Không nhận diện được Video ID.")
        return
    ok = db.remove_video_track(msg.chat.id, vid_id)
    if ok:
        db.add_log("video_track_remove", f"Huy video ID {vid_id}", msg.chat.id)
        await msg.answer("✅ Đã huỷ theo dõi video.")
    else:
        await msg.answer("❌ Không tìm thấy video này trong danh sách của bạn.")

@router.message(Command("trackig"))
async def on_trackig(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await msg.answer("⚠️ Cú pháp: /trackig &lt;username&gt;\nVí dụ: /trackig cristiano")
        return
    username = parse_ig_username(parts[1].strip())
    if not username:
        await msg.answer("❌ Không nhận diện được username IG.")
        return

    wait = await msg.answer(f"⏳ Đang thêm theo dõi IG <b>@{username}</b>...")
    try:
        info = await fetch_ig_info(username)
        u = msg.from_user
        result = db.add_ig_track(
            u.id, u.username or u.full_name,
            info["username"],
            info["followers"], info["following"], info["posts"]
        )
        if result == -1:
            await wait.edit_text(f"⚠️ Bạn đã theo dõi IG <b>@{info['username']}</b> rồi!")
            return
        db.add_log("track_add", f"Thêm theo dõi IG @{info['username']}", u.id, info["username"])
        caption = (
            f"✅ <b>Đã thêm theo dõi tài khoản IG!</b>\n\n"
            f"📸 Kênh: <b><a href='https://www.instagram.com/{info['username']}'>@{info['username']}</a></b>\n"
            f"👥 Followers hiện tại: <b>{fmt_num(info['followers'])}</b>\n"
            f"➡️ Đang follow: <b>{fmt_num(info['following'])}</b>\n"
            f"🖼 Bài viết: <b>{fmt_num(info['posts'])}</b>\n\n"
            f"📩 <i>Bot sẽ thông báo khi có thay đổi follower.</i>"
        )
        if info.get("avatar"):
            try:
                await msg.answer_photo(photo=URLInputFile(info["avatar"], filename="avatar.jpg"), caption=caption)
                await wait.delete()
            except Exception:
                await wait.edit_text(caption, disable_web_page_preview=False)
        else:
            await wait.edit_text(caption, disable_web_page_preview=False)
    except Exception as e:
        await wait.edit_text(f"❌ Lỗi: {e}")

@router.message(Command("untrackig"))
async def on_untrackig(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await msg.answer("⚠️ Cú pháp: /untrackig &lt;username&gt;")
        return
    username = parse_ig_username(parts[1].strip())
    if not username: return
    ok = db.remove_ig_track(msg.chat.id, username)
    if ok:
        db.add_log("track_remove", f"Huỷ theo dõi IG @{username}", msg.chat.id, username)
        await msg.answer(f"✅ Đã huỷ theo dõi IG <b>@{username}</b>.")
    else:
        await msg.answer(f"❌ Không tìm thấy IG <b>@{username}</b> trong danh sách của bạn.")

@router.message(Command("trackiglist"))
async def on_trackiglist(msg: Message):
    tracks = db.user_ig_tracks(msg.chat.id)
    if not tracks:
        await msg.answer("📋 Bạn chưa theo dõi tài khoản IG nào.")
        return
    lines = ["📸 <b>Danh sách IG đang theo dõi:</b>\n"]
    for i, t in enumerate(tracks, 1):
        lines.append(
            f"{i}. <b>@{t['ig_username']}</b>\n"
            f"   👥 {fmt_num(t['last_followers'])} followers"
        )
    await msg.answer("\n".join(lines))

@router.message(Command("trackvig"))
async def on_trackvig(msg: Message):
    parts = (msg.text or "").split(maxsplit=2)
    if len(parts) < 2:
        await msg.answer(
            "⚠️ Cú pháp: /trackvig &lt;link_bài_viết_ig&gt; [phút]\n\n"
            "Ví dụ:\n"
            "  /trackvig https://www.instagram.com/p/C123456/\n"
            "  /trackvig https://www.instagram.com/p/C123456/ 30"
        )
        return

    post_url = parts[1].strip()
    interval_min = 60
    if len(parts) >= 3:
        try: interval_min = max(1, int(parts[2]))
        except ValueError: pass

    post_id = parse_ig_post_id(post_url)
    if not post_id:
        await msg.answer("❌ Link bài viết IG không hợp lệ.")
        return

    wait = await msg.answer(f"⏳ Đang lấy thông tin bài viết IG...")
    try:
        info = await fetch_ig_post_info(post_url)
        u = msg.from_user
        r = db.add_ig_video_track(
            u.id, u.username or u.full_name,
            post_url, info["id"], info.get("username",""),
            info.get("desc",""), info.get("cover",""),
            interval_min * 60,
            info["likes"], info["comments"], info.get("views", 0)
        )
        if r == -1:
            await wait.edit_text("⚠️ Bạn đã theo dõi bài viết IG này rồi!")
            return
        db.add_log("video_track_add", f"Them IG post {info['id']}", u.id, info.get("username",""))
        desc = (info.get("desc","")[:80]+"...") if len(info.get("desc","")) > 80 else info.get("desc","")
        caption = (
            f"✅ <b>Đã thêm theo dõi bài viết IG!</b>\n\n"
            f"📸 Kênh: <b><a href='https://www.instagram.com/{info.get('username','')}'>@{info.get('username','')}</a></b>\n"
            f"📝 Mô tả: <i>{desc or 'Không có mô tả'}</i>\n\n"
            f"📊 <b>Thống kê hiện tại:</b>\n"
            f" ┣ ❤️ Lượt thích: <b>{info['likes']:,}</b>\n"
            f" ┣ 💬 Bình luận: <b>{info['comments']:,}</b>\n"
            f" ┗ 👁️ Lượt xem: <b>{info.get('views', 0):,}</b>\n\n"
            f"⏱ <i>Tự động check mỗi <b>{interval_min} phút</b></i>\n"
            f"📩 <i>Bot sẽ thông báo khi có tương tác mới!</i>"
        )
        if info.get("cover"):
            try:
                await msg.answer_photo(photo=URLInputFile(info["cover"], filename="cover.jpg"), caption=caption)
                await wait.delete()
            except Exception:
                await wait.edit_text(caption, disable_web_page_preview=False)
        else:
            await wait.edit_text(caption, disable_web_page_preview=False)
    except Exception as e:
        await wait.edit_text(f"❌ Lỗi: {e}")

@router.message(Command("untrackvig"))
async def on_untrackvig(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2: return
    post_id = parse_ig_post_id(parts[1].strip())
    if not post_id: return
    ok = db.remove_ig_video_track(msg.chat.id, post_id)
    if ok:
        await msg.answer("✅ Đã huỷ theo dõi bài viết IG.")
    else:
        await msg.answer("❌ Không tìm thấy bài viết IG này.")

@router.message(Command("trackviglist"))
async def on_trackviglist(msg: Message):
    vtracks = db.user_ig_video_tracks(msg.chat.id)
    if not vtracks:
        await msg.answer("📋 Bạn chưa theo dõi bài viết IG nào.")
        return
    lines = ["🎬 <b>Bài viết IG đang theo dõi:</b>\n"]
    for i, v in enumerate(vtracks, 1):
        interval_min = v["check_interval"] // 60
        desc = (v["post_desc"][:50] + "...") if len(v.get("post_desc","")) > 50 else v.get("post_desc","")
        lines.append(
            f"{i}. <a href=\"{v['post_url']}\">@{v['ig_username']}</a>\n"
            f"   📝 {desc or 'Khong co mo ta'}\n"
            f"   ❤️ {v['last_likes']:,}  💬 {v['last_comments']:,}\n"
            f"   ⏱ Check mỗi {interval_min} phút"
        )
    await msg.answer("\n\n".join(lines))

@router.message(Command("list", "trackfblist"))
async def on_list(msg: Message):
    rows = db.user_watches(msg.chat.id)
    if not rows:
        await msg.answer("Bạn chưa theo dõi UID nào. Dùng /check để thêm.")
        return
    lines = ["<b>Danh sách đang theo dõi</b>"]
    for w in rows:
        st = w["last_status"] or "?"
        icon = "🟢" if st == "live" else ("🔴" if st == "die" else "⚪")
        extra = f" — {w['note']}" if w["note"] else ""
        lines.append(f"{icon} {w['uid']}{extra}")
    await msg.answer("\n".join(lines))

@router.message(Command("remove", "untrackfb"))
async def on_remove(msg: Message):
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("Cú pháp: /remove {uid}")
        return
    n = db.remove_watch(msg.chat.id, parts[1].strip())
    await msg.answer("Đã bỏ theo dõi." if n else "Không tìm thấy UID này.")

@router.message(Command("check", "trackfb"))
async def on_check(msg: Message):
    user = db.get_user(msg.chat.id)
    if not user:
        await msg.answer("Bạn chưa /start. Gõ /start trước nhé.")
        return
    if not _sub_active(user):
        await msg.answer("Bạn cần có gói còn hạn để dùng /check. Gõ /sub để mua gói.")
        return

    uid, note, price, days = parse_check_args(msg.text or "")
    if not uid:
        await msg.answer("Cú pháp: /check {uid} [ghi chú] [giá] [số ngày]")
        return

    from ..fb import check_uid, avatar_url
    res = await check_uid(uid)
    status = "live" if res["alive"] else "die"
    avatar = res["avatar_url"] or avatar_url(uid)
    expire_at = now() + days * DAY if days else 0
    wid, is_new = db.add_watch(msg.chat.id, res["uid"], note or "", price or 0, expire_at)
    db.update_watch_status(wid, status, avatar)
    if is_new:
        db.add_log("add", f"Thêm UID {res['uid']} ({status})", msg.chat.id, res["uid"])

    header = "Đã thêm theo dõi:" if is_new else "UID này đã trong danh sách theo dõi:"
    if days:
        header += f" trong {days} ngày"
    await _send_card(msg.bot, msg.chat.id, res["uid"], status, note, price, avatar, header)

@router.callback_query(F.data.startswith("fb_track_"))
async def on_fb_track_btn(cb: CallbackQuery):
    uid = cb.data.replace("fb_track_", "")
    user = db.get_user(cb.from_user.id)
    if not user or not _sub_active(user):
        await cb.answer("❌ Bạn cần có gói còn hạn để dùng tính năng theo dõi.", show_alert=True)
        return
        
    from ..fb import check_uid, avatar_url
    res = await check_uid(uid)
    status = "live" if res["alive"] else "die"
    avatar = res.get("avatar_url") or avatar_url(uid)
    
    wid, is_new = db.add_watch(cb.from_user.id, res["uid"], "", 0, 0)
    db.update_watch_status(wid, status, avatar)
    if is_new:
        db.add_log("add", f"Thêm UID {res['uid']} ({status})", cb.from_user.id, res["uid"])

    await cb.answer("✅ Đã thêm vào danh sách theo dõi!" if is_new else "ℹ️ UID này đã được theo dõi rồi.", show_alert=True)
    await _send_card(cb.bot, cb.message.chat.id, res["uid"], status, "", 0, avatar, "Đã thêm theo dõi:")

@router.message(TrackMenuState.waiting_fb_uid)
async def on_trackmenu_fb_input(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        await state.clear()
        await msg.answer("Đã hủy.", reply_markup=_trackmenu_main_kb())
        return
    uid = (msg.text or "").strip().split()[0]
    if not uid:
        await msg.answer("❌ UID không hợp lệ. Gửi lại hoặc /huy để hủy.")
        return
    user = db.get_user(msg.chat.id)
    if not user or not _sub_active(user):
        await state.clear()
        await msg.answer("❌ Bạn cần có gói còn hạn để dùng tính năng theo dõi.")
        return
    wait = await msg.answer("⏳ Đang kiểm tra UID...")
    try:
        from ..fb import check_uid, avatar_url
        res = await check_uid(uid)
        status = "live" if res.get("alive") else "die"
        avatar = res.get("avatar_url") or avatar_url(uid)
        wid, is_new = db.add_watch(msg.chat.id, res.get("uid") or uid, "", 0, 0)
        db.update_watch_status(wid, status, avatar)
        await state.clear()
        await wait.edit_text(
            f"{'✅ <b>Đã thêm theo dõi!</b>' if is_new else 'ℹ️ <b>UID này đã được theo dõi rồi.</b>'}\n"
            f"🆔 UID: <code>{html.escape(res.get('uid') or uid)}</code> — <b>{status.upper()}</b>\n\n"
            f"Mở /theodoi để quản lý.",
            parse_mode="HTML", reply_markup=_trackmenu_main_kb())
    except Exception as e:
        await state.clear()
        await wait.edit_text(f"❌ Lỗi: {html.escape(str(e))}")

@router.message(TrackMenuState.waiting_ig_username)
async def on_trackmenu_ig_input(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        await state.clear()
        await msg.answer("Đã hủy.", reply_markup=_trackmenu_main_kb())
        return
    username = parse_ig_username((msg.text or "").strip())
    if not username:
        await msg.answer("❌ Không nhận diện được username IG. Gửi lại hoặc /huy để hủy.")
        return
    wait = await msg.answer(f"⏳ Đang thêm theo dõi IG <b>@{html.escape(username)}</b>...", parse_mode="HTML")
    try:
        info = await fetch_ig_info(username)
        u = msg.from_user
        result = db.add_ig_track(u.id, u.username or u.full_name, info["username"],
                                 info["followers"], info["following"], info["posts"])
        await state.clear()
        if result == -1:
            await wait.edit_text(f"⚠️ Bạn đã theo dõi IG <b>@{html.escape(info['username'])}</b> rồi!", parse_mode="HTML")
        else:
            await wait.edit_text(f"✅ Đã thêm theo dõi IG <b>@{html.escape(info['username'])}</b>!\nMở /theodoi để quản lý.",
                                 parse_mode="HTML", reply_markup=_trackmenu_main_kb())
    except Exception as e:
        await state.clear()
        await wait.edit_text(f"❌ Lỗi: {html.escape(str(e))}")

@router.message(TrackMenuState.waiting_ig_post)
async def on_trackmenu_igp_input(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        await state.clear()
        await msg.answer("Đã hủy.", reply_markup=_trackmenu_main_kb())
        return
    post_url = (msg.text or "").strip().split()[0]
    post_id = parse_ig_post_id(post_url)
    if not post_id:
        await msg.answer("❌ Link bài viết IG không hợp lệ. Gửi lại hoặc /huy để hủy.")
        return
    wait = await msg.answer("⏳ Đang lấy thông tin bài viết IG...")
    try:
        info = await fetch_ig_post_info(post_url)
        u = msg.from_user
        r = db.add_ig_video_track(u.id, u.username or u.full_name, post_url, info["id"],
                                  info.get("username", ""), info.get("desc", ""), info.get("cover", ""),
                                  3600, info["likes"], info["comments"], info.get("views", 0))
        await state.clear()
        if r == -1:
            await wait.edit_text("⚠️ Bạn đã theo dõi bài viết này rồi!")
        else:
            await wait.edit_text("✅ Đã thêm theo dõi bài viết IG!\nMở /theodoi để quản lý.",
                                 reply_markup=_trackmenu_main_kb())
    except Exception as e:
        await state.clear()
        await wait.edit_text(f"❌ Lỗi: {html.escape(str(e))}")

@router.message(TrackMenuState.waiting_list_name)
async def on_trackmenu_listname_input(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        await state.clear()
        await msg.answer("Đã hủy.", reply_markup=_trackmenu_main_kb())
        return
    name = (msg.text or "").strip()
    if not name:
        await msg.answer("❌ Tên không hợp lệ. Gửi lại hoặc /huy để hủy.")
        return
    ok, reason = db.create_user_list(msg.chat.id, name)
    await state.clear()
    if ok:
        await msg.answer(f"✅ Đã tạo danh sách <b>{html.escape(name)}</b>.", parse_mode="HTML",
                         reply_markup=_trackmenu_main_kb())
    else:
        await msg.answer(f"❌ {html.escape(reason)}")

@router.message(TrackMenuState.waiting_list_add)
async def on_trackmenu_listadd_input(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        await state.clear()
        await msg.answer("Đã hủy.", reply_markup=_trackmenu_main_kb())
        return
    raw = (msg.text or "").strip()
    if "|" not in raw:
        await msg.answer("❌ Sai dạng. Gửi: <code>tên danh sách | uid</code>\nVD: <code>khach-vip | 1000123456789</code>\n\n/huy để hủy.",
                         parse_mode="HTML")
        return
    name, uid_raw = [p.strip() for p in raw.split("|", 1)]
    try:
        from ..fb import extract_uid
        uid = extract_uid(uid_raw) or uid_raw
    except Exception:
        uid = uid_raw
    ok, reason = db.add_to_user_list(msg.chat.id, name, uid)
    await state.clear()
    if ok:
        await msg.answer(f"✅ Đã thêm <code>{html.escape(uid)}</code> vào <b>{html.escape(name)}</b>.",
                         parse_mode="HTML", reply_markup=_trackmenu_main_kb())
    else:
        await msg.answer(f"❌ {html.escape(reason)}")

@router.message(TrackMenuState.waiting_alert_add)
async def on_trackmenu_alert_input(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        await state.clear()
        await msg.answer("Đã hủy.", reply_markup=_trackmenu_main_kb())
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("❌ Sai dạng. Gửi: <code>platform target</code>\nVD: <code>fb_watch 1000123456789</code>\n\n/huy để hủy.",
                         parse_mode="HTML")
        return
    rule_id = db.create_alert_rule(str(msg.chat.id), parts[0], parts[1])
    await state.clear()
    await msg.answer(f"✅ Đã thêm cảnh báo [{html.escape(parts[0])}] {html.escape(parts[1])} (ID: {rule_id}).",
                     parse_mode="HTML", reply_markup=_trackmenu_main_kb())

@router.message(Command("alert"))
async def on_alert_cmd(msg: Message):
    parts = msg.text.split()
    if len(parts) < 3:
        await msg.answer("⚠️ Cú pháp: /alert <platform> <target>\nVD: /alert fb_watch 123456789")
        return
    platform = parts[1]
    target = parts[2]
    rule_id = db.create_alert_rule(str(msg.chat.id), platform, target)
    await msg.answer(f"✅ Đã thêm cảnh báo cho {platform} mục {target} (ID: {rule_id})")

@router.message(Command("newlist"))
async def on_newlist_cmd(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await msg.answer("⚠️ Cú pháp: /newlist &lt;tên danh sách&gt;")
        return
    name = parts[1].strip()
    ok, reason = db.create_user_list(msg.chat.id, name)
    if ok:
        await msg.answer(f"✅ Đã tạo danh sách <b>{_esc_list_name(name)}</b>.", parse_mode="HTML")
    else:
        await msg.answer(f"❌ {html.escape(reason, quote=False)}")

@router.message(Command("lists"))
async def on_lists_cmd(msg: Message):
    lists = db.get_user_lists(msg.chat.id)
    if not lists:
        await msg.answer("📁 Bạn chưa có danh sách nào.\nGõ <code>/newlist tên</code> để tạo.", parse_mode="HTML")
        return
    lines = ["📁 <b>Danh sách của bạn:</b>\n"]
    for l in lists:
        l = dict(l)
        d = time.strftime("%d/%m/%Y", time.localtime(l.get("created_at") or 0))
        lines.append(f"• <b>{_esc_list_name(l.get('name'))}</b> — {l.get('item_count', 0)} UID ({d})")
    lines.append("\nGõ <code>/addtolist tên uid</code> để thêm UID.")
    await msg.answer("\n".join(lines), parse_mode="HTML")

@router.message(Command("addtolist"))
async def on_addtolist_cmd(msg: Message):
    parts = (msg.text or "").rsplit(maxsplit=2)
    if len(parts) < 3 or not parts[1].strip():
        await msg.answer("⚠️ Cú pháp: /addtolist &lt;tên danh sách&gt; &lt;uid&gt;")
        return
    name = parts[1].strip()
    raw_val = parts[2].strip()
    try:
        from ..fb import extract_uid
        uid = extract_uid(raw_val) or raw_val
    except Exception:
        uid = raw_val
    ok, reason = db.add_to_user_list(msg.chat.id, name, uid)
    if ok:
        await msg.answer(f"✅ Đã thêm <code>{_esc_list_name(uid)}</code> vào <b>{_esc_list_name(name)}</b>.", parse_mode="HTML")
    else:
        await msg.answer(f"❌ {html.escape(reason, quote=False)}")

@router.message(Command("deletelist"))
async def on_deletelist_cmd(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await msg.answer("⚠️ Cú pháp: /deletelist &lt;tên danh sách&gt;")
        return
    name = parts[1].strip()
    if db.delete_user_list(msg.chat.id, name):
        await msg.answer(f"🗑 Đã xóa danh sách <b>{_esc_list_name(name)}</b>.", parse_mode="HTML")
    else:
        await msg.answer(f"❌ Không tìm thấy danh sách <b>{_esc_list_name(name)}</b>.", parse_mode="HTML")

@router.message(Command("alertlist"))
async def on_alertlist_cmd(msg: Message):
    rules = db.get_alert_rules(tg_id=str(msg.chat.id))
    if not rules:
        await msg.answer("Bạn không có cảnh báo nào.")
        return
    lines = ["🚨 <b>Danh sách Cảnh báo</b>\n"]
    for r in rules:
        lines.append(f"• ID {r['id']}: [{r['platform']}] {r['target']} ({r['condition']})")
    await msg.answer("\n".join(lines), parse_mode="HTML")

@router.message(Command("alertoff"))
async def on_alertoff_cmd(msg: Message):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: /alertoff &lt;target_hoặc_id&gt;")
        return
    target = parts[1]
    rules = db.get_alert_rules(tg_id=str(msg.chat.id))
    deleted = False
    for r in rules:
        if str(r["id"]) == target or r["target"] == target:
            db.delete_alert_rule(r["id"])
            deleted = True
    if deleted:
        await msg.answer(f"✅ Đã xoá cảnh báo cho {target}.")
    else:
        await msg.answer("❌ Không tìm thấy cảnh báo phù hợp.")

@router.callback_query(lambda c: c.data and c.data.startswith("camp_giveaway_"))
async def on_camp_giveaway(cb: CallbackQuery):
    camp_id = int(cb.data.split("_")[-1])
    tg_id = cb.from_user.id
    
    if db.check_campaign_participation(camp_id, tg_id):
        await cb.answer("Bạn đã nhận lì xì từ chiến dịch này rồi!", show_alert=True)
        return
        
    camp = db.get_campaign(camp_id)
    if not camp or camp["status"] != "finished":
        await cb.answer("Chiến dịch này không khả dụng!", show_alert=True)
        return
        
    import json
    import random
    try: config = json.loads(camp.get("config", "{}"))
    except: config = {}
    
    min_reward = int(config.get("min_reward") or 1000)
    max_reward = int(config.get("max_reward") or 5000)
    max_winners = int(config.get("max_winners") or 0)
    
    if min_reward > max_reward:
        min_reward, max_reward = max_reward, min_reward
    
    # check max winners
    try: stats = json.loads(camp.get("stats", "{}"))
    except: stats = {}
    claims = stats.get("claims", 0)
    
    if max_winners > 0 and claims >= max_winners:
        await cb.answer("Rất tiếc, số lượng lì xì đã hết!", show_alert=True)
        return
        
    reward = random.randint(min_reward, max_reward)
    
    db.adjust_balance(tg_id, reward, f"Lì xì chiến dịch #{camp_id}")
    db.add_campaign_participant(camp_id, tg_id, "claimed", f"{reward}")
    
    stats["claims"] = claims + 1
    db.update_campaign_stats(camp_id, json.dumps(stats))
    
    await cb.answer(f"🎉 Chúc mừng bạn nhận được {fmt_num(reward)}đ lì xì!", show_alert=True)

@router.callback_query(lambda c: c.data and c.data.startswith("camp_bounty_"))
async def on_camp_bounty(cb: CallbackQuery):
    camp_id = int(cb.data.split("_")[-1])
    tg_id = cb.from_user.id
    
    if db.check_campaign_participation(camp_id, tg_id):
        await cb.answer("Bạn đã tham gia nhiệm vụ này rồi! (Đang chờ duyệt)", show_alert=True)
        return
        
    db.add_campaign_participant(camp_id, tg_id, "pending_review", "")
    await cb.answer("Hệ thống đã ghi nhận yêu cầu tham gia của bạn! Admin sẽ kiểm tra và duyệt sau.", show_alert=True)

@router.callback_query(F.data == "fc_tracklive")
async def on_fc_tracklive(cb: CallbackQuery):
    cache = check_cache_get("file", cb.message.chat.id, cb.from_user.id)
    if not cache:
        await cb.answer("❌ Đã hết phiên lưu trữ, vui lòng gửi lại file!", show_alert=True)
        return

    live_uids = cache["live"]
    if not live_uids:
        await cb.answer("❌ Không có UID Live nào để theo dõi!", show_alert=True)
        return

    added = 0
    for uid in live_uids:
        try:
            db.create_alert_rule(str(cb.from_user.id), "fb", uid, "status_change")
            added += 1
        except Exception:
            pass

    await cb.answer(f"🔔 Đã bật theo dõi cho {added} UID Live!", show_alert=True)
    await cb.message.answer(
        f"🔔 <b>ĐÃ BẬT THEO DÕI TỰ ĐỘNG!</b>\n\n"
        f"Đã thêm <b>{added}</b> UID Live vào danh sách Cảnh Báo Tự Động.\n"
        f"Bot sẽ thông báo ngay lập tức nếu có UID bị Die trong tương lai!",
        parse_mode="HTML"
    )

@router.message(Command("mywatches"))
async def on_mywatches(msg: Message):
    """Liệt kê các UID đang theo dõi + chế độ báo."""
    rows = db.get_user_watches(msg.from_user.id)
    if not rows:
        await msg.answer("👁 Bạn chưa theo dõi UID nào. Check 1 UID bằng /fb rồi bấm nút 👁 Theo dõi.")
        return
    lines = ["👁 <b>DANH SÁCH ĐANG THEO DÕI</b>", "━━━━━━━━━━━━", ""]
    for r in rows:
        d = dict(r)
        mode = d.get("alert_mode") or "all"
        mode_txt = "🔔 mọi thay đổi" if mode == "all" else "🔕 chỉ khi DIE"
        st = (d.get("last_status") or "?").upper()
        lines.append(f"🆔 <code>{d['uid']}</code> — <b>{st}</b> ({mode_txt})")
    lines += ["", "Đổi chế độ báo: <code>/trackmode &lt;uid&gt; &lt;all|die&gt;</code>"]
    await msg.answer("\n".join(lines), parse_mode="HTML")

@router.message(Command("trackmode"))
async def on_trackmode(msg: Message):
    """Đặt chế độ báo cho 1 watch. Cú pháp: /trackmode <uid> <all|die>"""
    parts = (msg.text or "").split()
    if len(parts) < 3:
        await msg.answer(
            "⚠️ Cú pháp: <code>/trackmode &lt;uid&gt; &lt;all|die&gt;</code>\n"
            "• <b>all</b> — báo mọi lần đổi trạng thái\n"
            "• <b>die</b> — chỉ báo khi acc chuyển sang DIE",
            parse_mode="HTML",
        )
        return
    uid, mode = parts[1], parts[2].lower()
    if mode not in ("all", "die"):
        await msg.answer("❌ Chế độ chỉ có <b>all</b> hoặc <b>die</b>!")
        return
    rows = db.get_user_watches(msg.from_user.id)
    target = next((dict(r) for r in rows if str(r["uid"]) == uid), None)
    if not target:
        await msg.answer("❌ Bạn không theo dõi UID này. Xem danh sách: /mywatches")
        return
    db.set_watch_alert_mode(target["id"], msg.from_user.id, "die_only" if mode == "die" else "all")
    await msg.answer(
        f"✅ UID <code>{html.escape(uid)}</code>: chế độ báo = <b>{'chỉ khi DIE' if mode == 'die' else 'mọi thay đổi'}</b>.",
        parse_mode="HTML",
    )

def _trackmenu_text_main() -> str:
    return (
        "👁️ <b>TRUNG TÂM THEO DÕI</b>\n"
        "━━━━━━━━━━━━\n\n"
        "Chọn loại muốn quản lý — khỏi cần nhớ lệnh:\n\n"
        "📘 <b>Facebook UID</b> — theo dõi LIVE/DIE\n"
        "🎵 <b>TikTok</b> — tài khoản & video\n"
        "📸 <b>Instagram</b> — tài khoản & bài viết\n"
        "💬 <b>Zalo</b> — theo dõi SĐT\n"
        "📁 <b>Danh sách UID</b> — gom UID để quét hàng loạt\n"
        "🔔 <b>Cảnh báo</b> — báo biến động tự động"
    )

@router.message(Command("theodoi"))
async def on_theodoi(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(_trackmenu_text_main(), parse_mode="HTML", reply_markup=_trackmenu_main_kb())

@router.callback_query(F.data.startswith("trackmenu:"))
async def on_trackmenu_cb(cb: CallbackQuery, state: FSMContext):
    data = cb.data or ""
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else "main"
    sub = parts[2] if len(parts) > 2 else ""
    tg_id = cb.from_user.id

    try:
        # ── Main menu ──
        if action == "main":
            await state.clear()
            await cb.message.edit_text(_trackmenu_text_main(), parse_mode="HTML", reply_markup=_trackmenu_main_kb())
            await cb.answer()
            return

        # ── Submenus ──
        if action in ("fb", "tiktok", "ig", "zalo", "lists", "alerts") and not sub:
            titles = {
                "fb": "📘 <b>FACEBOOK UID</b>\nTheo dõi biến động LIVE/DIE của UID FB.\n\nChọn thao tác:",
                "tiktok": "🎵 <b>TIKTOK</b>\nTheo dõi tài khoản & video TikTok.\n\nChọn thao tác:",
                "ig": "📸 <b>INSTAGRAM</b>\nTheo dõi tài khoản & bài viết IG.\n\nChọn thao tác:",
                "zalo": "💬 <b>ZALO</b>\nTheo dõi biến động SĐT Zalo.\n\nChọn thao tác:",
                "lists": "📁 <b>DANH SÁCH UID</b>\nGom UID thành danh sách để quét hàng loạt.\n\nChọn thao tác:",
                "alerts": "🔔 <b>CẢNH BÁO</b>\nBáo tự động khi có biến động.\n\nChọn thao tác:",
            }
            await cb.message.edit_text(titles.get(action, ""), parse_mode="HTML",
                                       reply_markup=_trackmenu_sub_kb(action))
            await cb.answer()
            return

        # ── Facebook: prompt thêm ──
        if data == "trackmenu:fb_add":
            await state.set_state(TrackMenuState.waiting_fb_uid)
            await cb.message.edit_text(
                "📘 <b>THÊM THEO DÕI UID FB</b>\n\nGửi UID Facebook cần theo dõi.\nVD: <code>1000123456789</code>\n\nGõ /huy để hủy.",
                parse_mode="HTML", reply_markup=_trackmenu_back_kb("trackmenu:fb"))
            await cb.answer()
            return

        # ── Facebook: danh sách ──
        if data == "trackmenu:fb_list":
            rows = db.get_user_watches(tg_id)
            if not rows:
                await cb.message.edit_text(
                    "📭 Bạn chưa theo dõi UID nào.\n\nBấm ➕ Thêm UID FB để bắt đầu.",
                    reply_markup=_trackmenu_sub_kb("fb"))
                await cb.answer()
                return
            lines = ["📘 <b>UID ĐANG THEO DÕI</b>", "━━━━━━━━━━━━", ""]
            kb_rows = []
            for r in rows[:10]:
                d = dict(r)
                uid = str(d.get("uid") or "")
                st = (d.get("last_status") or "?").upper()
                mode = d.get("alert_mode") or "all"
                mode_icon = "🔔" if mode == "all" else "🔕"
                lines.append(f"{mode_icon} <code>{html.escape(uid)}</code> — <b>{st}</b>")
                kb_rows.append([
                    InlineKeyboardButton(text=f"{mode_icon} {uid[:12]}", callback_data=f"trackmenu:fb_toggle:{d.get('id')}"),
                    InlineKeyboardButton(text="❌", callback_data=f"trackmenu:fb_del:{uid}"),
                ])
            if len(rows) > 10:
                lines.append(f"\n<i>…và {len(rows) - 10} UID nữa — gõ /mywatches để xem hết</i>")
            lines += ["", "🔔 = báo mọi thay đổi | 🔕 = chỉ khi DIE", "Bấm 🔔/🔕 để đổi chế độ, ❌ để hủy."]
            kb_rows.append([InlineKeyboardButton(text="◀️ Quay lại", callback_data="trackmenu:fb")])
            await cb.message.edit_text("\n".join(lines), parse_mode="HTML",
                                       reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
            await cb.answer()
            return

        # ── Facebook: toggle chế độ ──
        if action == "fb_toggle" and sub:
            try:
                wid = int(sub)
            except ValueError:
                await cb.answer("❌")
                return
            rows = db.get_user_watches(tg_id)
            target = next((dict(r) for r in rows if str(r["id"]) == str(wid)), None)
            if not target:
                await cb.answer("❌ Không tìm thấy.", show_alert=True)
                return
            cur = target.get("alert_mode") or "all"
            new_mode = "die_only" if cur == "all" else "all"
            db.set_watch_alert_mode(wid, tg_id, new_mode)
            await cb.answer("✅ Đã đổi thành " + ("chỉ khi DIE 🔕" if new_mode == "die_only" else "mọi thay đổi 🔔"))
            # refresh list
            cb2 = cb.model_copy(update={"data": "trackmenu:fb_list"})
            await on_trackmenu_cb(cb2, state)
            return

        # ── Facebook: xóa ──
        if action == "fb_del" and sub:
            n = db.remove_watch(tg_id, sub)
            await cb.answer("✅ Đã hủy theo dõi." if n else "❌ Không tìm thấy.")
            cb2 = cb.model_copy(update={"data": "trackmenu:fb_list"})
            await on_trackmenu_cb(cb2, state)
            return

        # ── TikTok / IG / Zalo: prompt thêm ──
        prompts = {
            "trackmenu:tiktok_add": (TrackMenuState.waiting_tiktok_username,
                "🎵 <b>THEO DÕI TIKTOK</b>\n\nGửi username TikTok (không cần @).\nVD: <code>cristiano</code>\n\nGõ /huy để hủy."),
            "trackmenu:tiktok_video_add": (TrackMenuState.waiting_tiktok_video,
                "🎬 <b>THEO DÕI VIDEO TIKTOK</b>\n\nGửi link video TikTok.\nVD: <code>https://tiktok.com/@user/video/123</code>\n\nGõ /huy để hủy."),
            "trackmenu:ig_add": (TrackMenuState.waiting_ig_username,
                "📸 <b>THEO DÕI INSTAGRAM</b>\n\nGửi username IG (không cần @).\nVD: <code>cristiano</code>\n\nGõ /huy để hủy."),
            "trackmenu:ig_post_add": (TrackMenuState.waiting_ig_post,
                "🎞️ <b>THEO DÕI BÀI VIẾT IG</b>\n\nGửi link bài viết Instagram.\nVD: <code>https://www.instagram.com/p/C123456/</code>\n\nGõ /huy để hủy."),
            "trackmenu:zalo_add": (TrackMenuState.waiting_zalo_phone,
                "💬 <b>THEO DÕI ZALO</b>\n\nGửi số điện thoại cần theo dõi.\nVD: <code>0901234567</code>\n\nGõ /huy để hủy."),
            "trackmenu:lists_new": (TrackMenuState.waiting_list_name,
                "📁 <b>TẠO DANH SÁCH MỚI</b>\n\nGửi tên danh sách.\nVD: <code>khach-vip</code>\n\nGõ /huy để hủy."),
            "trackmenu:lists_add": (TrackMenuState.waiting_list_add,
                "📁 <b>THÊM UID VÀO DANH SÁCH</b>\n\nGửi theo dạng:\n<code>tên danh sách | uid</code>\nVD: <code>khach-vip | 1000123456789</code>\n\nGõ /huy để hủy."),
            "trackmenu:alert_add": (TrackMenuState.waiting_alert_add,
                "🔔 <b>THÊM CẢNH BÁO</b>\n\nGửi theo dạng:\n<code>platform target</code>\nVD: <code>fb_watch 1000123456789</code>\n\nGõ /huy để hủy."),
        }
        if data in prompts:
            st, txt = prompts[data]
            back_map = {
                "trackmenu:tiktok_add": "trackmenu:tiktok", "trackmenu:tiktok_video_add": "trackmenu:tiktok",
                "trackmenu:ig_add": "trackmenu:ig", "trackmenu:ig_post_add": "trackmenu:ig",
                "trackmenu:zalo_add": "trackmenu:zalo", "trackmenu:lists_new": "trackmenu:lists",
                "trackmenu:lists_add": "trackmenu:lists", "trackmenu:alert_add": "trackmenu:alerts",
            }
            await state.set_state(st)
            await cb.message.edit_text(txt, parse_mode="HTML",
                                       reply_markup=_trackmenu_back_kb(back_map.get(data, "trackmenu:main")))
            await cb.answer()
            return

        # ── TikTok: DS tài khoản ──
        if data == "trackmenu:tiktok_list":
            tracks = db.user_tracks(tg_id)
            if not tracks:
                await cb.message.edit_text("📭 Chưa theo dõi tài khoản TikTok nào.",
                                           reply_markup=_trackmenu_sub_kb("tiktok"))
                await cb.answer()
                return
            lines = ["🎵 <b>TIKTOK ĐANG THEO DÕI</b>", ""]
            kb_rows = []
            for t in tracks[:10]:
                t = dict(t)
                un = t.get("tiktok_username") or ""
                fl = t.get("last_followers") or 0
                lines.append(f"• <b>@{html.escape(un)}</b> — 👥 {fmt_num(fl)}")
                kb_rows.append([InlineKeyboardButton(text=f"❌ @{un[:18]}", callback_data=f"trackmenu:tiktok_del:{un}")])
            kb_rows.append([InlineKeyboardButton(text="◀️ Quay lại", callback_data="trackmenu:tiktok")])
            await cb.message.edit_text("\n".join(lines), parse_mode="HTML",
                                       reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
            await cb.answer()
            return

        # ── TikTok: DS video ──
        if data == "trackmenu:tiktok_vlist":
            vtracks = db.user_video_tracks(tg_id)
            if not vtracks:
                await cb.message.edit_text("📭 Chưa theo dõi video TikTok nào.",
                                           reply_markup=_trackmenu_sub_kb("tiktok"))
                await cb.answer()
                return
            lines = ["🎬 <b>VIDEO TIKTOK ĐANG THEO DÕI</b>", ""]
            kb_rows = []
            for v in vtracks[:10]:
                v = dict(v)
                vid = str(v.get("video_id") or "")
                un = v.get("tiktok_username") or ""
                lines.append(f"• <a href=\"{html.escape(v.get('video_url') or '')}\">@{html.escape(un)}</a>")
                kb_rows.append([InlineKeyboardButton(text=f"❌ Video {vid[:14]}", callback_data=f"trackmenu:tiktokv_del:{vid}")])
            kb_rows.append([InlineKeyboardButton(text="◀️ Quay lại", callback_data="trackmenu:tiktok")])
            await cb.message.edit_text("\n".join(lines), parse_mode="HTML",
                                       reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
                                       disable_web_page_preview=True)
            await cb.answer()
            return

        # ── TikTok: xóa ──
        if action in ("tiktok_del", "tiktokv_del") and sub:
            if action == "tiktok_del":
                ok = db.remove_track(tg_id, sub)
            else:
                ok = db.remove_video_track(tg_id, sub)
            await cb.answer("✅ Đã hủy." if ok else "❌ Không tìm thấy.")
            cb2 = cb.model_copy(update={"data": "trackmenu:tiktok_list" if action == "tiktok_del" else "trackmenu:tiktok_vlist"})
            await on_trackmenu_cb(cb2, state)
            return

        # ── IG: DS tài khoản ──
        if data == "trackmenu:ig_list":
            tracks = db.user_ig_tracks(tg_id)
            if not tracks:
                await cb.message.edit_text("📭 Chưa theo dõi tài khoản IG nào.",
                                           reply_markup=_trackmenu_sub_kb("ig"))
                await cb.answer()
                return
            lines = ["📸 <b>IG ĐANG THEO DÕI</b>", ""]
            kb_rows = []
            for t in tracks[:10]:
                t = dict(t)
                un = t.get("ig_username") or ""
                fl = t.get("last_followers") or 0
                lines.append(f"• <b>@{html.escape(un)}</b> — 👥 {fmt_num(fl)}")
                kb_rows.append([InlineKeyboardButton(text=f"❌ @{un[:18]}", callback_data=f"trackmenu:ig_del:{un}")])
            kb_rows.append([InlineKeyboardButton(text="◀️ Quay lại", callback_data="trackmenu:ig")])
            await cb.message.edit_text("\n".join(lines), parse_mode="HTML",
                                       reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
            await cb.answer()
            return

        # ── IG: DS bài viết ──
        if data == "trackmenu:ig_plist":
            vtracks = db.user_ig_video_tracks(tg_id)
            if not vtracks:
                await cb.message.edit_text("📭 Chưa theo dõi bài viết IG nào.",
                                           reply_markup=_trackmenu_sub_kb("ig"))
                await cb.answer()
                return
            lines = ["🎞️ <b>BÀI VIẾT IG ĐANG THEO DÕI</b>", ""]
            kb_rows = []
            for v in vtracks[:10]:
                v = dict(v)
                pid = str(v.get("post_id") or "")
                un = v.get("ig_username") or ""
                lines.append(f"• <a href=\"{html.escape(v.get('post_url') or '')}\">@{html.escape(un)}</a>")
                kb_rows.append([InlineKeyboardButton(text=f"❌ Post {pid[:14]}", callback_data=f"trackmenu:igp_del:{pid}")])
            kb_rows.append([InlineKeyboardButton(text="◀️ Quay lại", callback_data="trackmenu:ig")])
            await cb.message.edit_text("\n".join(lines), parse_mode="HTML",
                                       reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
                                       disable_web_page_preview=True)
            await cb.answer()
            return

        # ── IG: xóa ──
        if action in ("ig_del", "igp_del") and sub:
            if action == "ig_del":
                ok = db.remove_ig_track(tg_id, sub)
            else:
                ok = db.remove_ig_video_track(tg_id, sub)
            await cb.answer("✅ Đã hủy." if ok else "❌ Không tìm thấy.")
            cb2 = cb.model_copy(update={"data": "trackmenu:ig_list" if action == "ig_del" else "trackmenu:ig_plist"})
            await on_trackmenu_cb(cb2, state)
            return

        # ── Zalo: DS ──
        if data == "trackmenu:zalo_list":
            tracks = db.user_zalo_tracks(tg_id)
            if not tracks:
                await cb.message.edit_text("📭 Chưa theo dõi SĐT Zalo nào.",
                                           reply_markup=_trackmenu_sub_kb("zalo"))
                await cb.answer()
                return
            lines = ["💬 <b>SĐT ZALO ĐANG THEO DÕI</b>", ""]
            kb_rows = []
            for t in tracks[:10]:
                t = dict(t)
                phone = str(t.get("phone") or "")
                nm = t.get("name") or ""
                st = t.get("status") or ""
                lines.append(f"• <code>{html.escape(phone)}</code> — {html.escape(nm)} ({st})")
                kb_rows.append([InlineKeyboardButton(text=f"❌ {phone}", callback_data=f"trackmenu:zalo_del:{phone}")])
            kb_rows.append([InlineKeyboardButton(text="◀️ Quay lại", callback_data="trackmenu:zalo")])
            await cb.message.edit_text("\n".join(lines), parse_mode="HTML",
                                       reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
            await cb.answer()
            return

        if action == "zalo_del" and sub:
            ok = db.remove_zalo_track(tg_id, sub)
            await cb.answer("✅ Đã hủy." if ok else "❌ Không tìm thấy.")
            cb2 = cb.model_copy(update={"data": "trackmenu:zalo_list"})
            await on_trackmenu_cb(cb2, state)
            return

        # ── Lists: xem ──
        if data == "trackmenu:lists_view":
            lists = db.get_user_lists(tg_id)
            if not lists:
                await cb.message.edit_text("📭 Chưa có danh sách nào.\nBấm ➕ Tạo danh sách để bắt đầu.",
                                           reply_markup=_trackmenu_sub_kb("lists"))
                await cb.answer()
                return
            lines = ["📁 <b>DANH SÁCH CỦA BẠN</b>", ""]
            kb_rows = []
            for l in lists[:10]:
                l = dict(l)
                name = l.get("name") or ""
                cnt = l.get("item_count", 0)
                lines.append(f"• <b>{html.escape(name)}</b> — {cnt} UID")
                kb_rows.append([
                    InlineKeyboardButton(text=f"🔍 {name[:14]}", callback_data=f"trackmenu:lists_scan:{name}"),
                    InlineKeyboardButton(text="🗑", callback_data=f"trackmenu:lists_del:{name}"),
                ])
            kb_rows.append([InlineKeyboardButton(text="◀️ Quay lại", callback_data="trackmenu:lists")])
            await cb.message.edit_text("\n".join(lines), parse_mode="HTML",
                                       reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
            await cb.answer()
            return

        # ── Lists: quét nhanh ──
        if action == "lists_scan" and sub:
            items = db.get_list_items(tg_id, sub)
            uids = [str(dict(i).get("value", "")) for i in items if dict(i).get("value")]
            if not uids:
                await cb.answer("Danh sách trống.", show_alert=True)
                return
            await cb.answer(f"⏳ Đang quét {len(uids)} UID...")
            # gọi logic scan dùng chung (không dùng FakeMsg nữa)
            await _run_scanlist(cb.message.chat.id, cb.from_user.id, sub,
                                answer=cb.message.answer, bot=cb.bot)
            return

        # ── Lists: xóa ──
        if action == "lists_del" and sub:
            ok = db.delete_user_list(tg_id, sub)
            await cb.answer("🗑 Đã xóa." if ok else "❌ Không tìm thấy.")
            cb2 = cb.model_copy(update={"data": "trackmenu:lists_view"})
            await on_trackmenu_cb(cb2, state)
            return

        # ── Alerts: DS ──
        if data == "trackmenu:alert_list":
            rules = db.get_alert_rules(tg_id=str(tg_id))
            if not rules:
                await cb.message.edit_text("📭 Chưa có cảnh báo nào.",
                                           reply_markup=_trackmenu_sub_kb("alerts"))
                await cb.answer()
                return
            lines = ["🔔 <b>DANH SÁCH CẢNH BÁO</b>", ""]
            kb_rows = []
            for r in rules[:10]:
                r = dict(r)
                lines.append(f"• ID {r['id']}: [{html.escape(str(r['platform']))}] {html.escape(str(r['target']))}")
                kb_rows.append([InlineKeyboardButton(text=f"❌ Xóa #{r['id']}", callback_data=f"trackmenu:alert_del:{r['id']}")])
            kb_rows.append([InlineKeyboardButton(text="◀️ Quay lại", callback_data="trackmenu:alerts")])
            await cb.message.edit_text("\n".join(lines), parse_mode="HTML",
                                       reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
            await cb.answer()
            return

        if action == "alert_del" and sub:
            try:
                db.delete_alert_rule(int(sub))
                await cb.answer("✅ Đã xóa.")
            except Exception:
                await cb.answer("❌ Lỗi.")
            cb2 = cb.model_copy(update={"data": "trackmenu:alert_list"})
            await on_trackmenu_cb(cb2, state)
            return

        await cb.answer("❌")
    except Exception as e:
        log.warning("trackmenu cb lỗi: %s", e)
        try:
            await cb.answer("❌ Có lỗi xảy ra.")
        except Exception:
            pass

def _trackmenu_back_kb(back_to: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Quay lại", callback_data=back_to)]
    ])

__all__ = [
    "on_track",
    "on_untrack",
    "on_tracklist",
    "on_trackvlist",
    "on_trackv",
    "on_untrackv",
    "on_trackig",
    "on_untrackig",
    "on_trackiglist",
    "on_trackvig",
    "on_untrackvig",
    "on_trackviglist",
    "on_list",
    "on_remove",
    "on_check",
    "on_fb_track_btn",
    "on_trackmenu_fb_input",
    "on_trackmenu_ig_input",
    "on_trackmenu_igp_input",
    "on_trackmenu_listname_input",
    "on_trackmenu_listadd_input",
    "on_trackmenu_alert_input",
    "on_alert_cmd",
    "on_newlist_cmd",
    "on_lists_cmd",
    "on_addtolist_cmd",
    "on_deletelist_cmd",
    "on_alertlist_cmd",
    "on_alertoff_cmd",
    "on_camp_giveaway",
    "on_camp_bounty",
    "on_fc_tracklive",
    "on_mywatches",
    "on_trackmode",
    "_trackmenu_text_main",
    "on_theodoi",
    "on_trackmenu_cb",
    "_trackmenu_back_kb",
]