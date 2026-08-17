import asyncio, logging, time
from typing import Optional
from . import config, db, fb
from . import bot as botmod
from .util import now, vn_time_str
from .event_bus import event_bus

async def _handle_alerts(platform: str, target: str, condition: str, message: str, bot=None):
    rules = db.get_alert_rules(target=target)
    for rule in rules:
        if rule["platform"] == platform and (rule["condition"] == "status_change" or rule["condition"] == condition):
            tg_id = rule["tg_id"]
            db.log_alert_history(tg_id, rule["id"], message)
            if bot:
                try:
                    await bot.send_message(int(tg_id), f"🚨 <b>Alert</b>:\n{message}", parse_mode="HTML")
                except:
                    pass

log = logging.getLogger(__name__)


class FollowerPoller:
    def __init__(self):
        self._account_task: Optional[asyncio.Task] = None
        self._video_task:   Optional[asyncio.Task] = None
        self._backup_task:  Optional[asyncio.Task] = None
        self.last_run: int = 0
        self._bot = None
        self._zalo_bot = None

    @property
    def running(self) -> bool:
        t1 = self._account_task and not self._account_task.done()
        t2 = self._video_task   and not self._video_task.done()
        t3 = self._backup_task  and not self._backup_task.done()
        return bool(t1 or t2 or t3)

    def set_bot(self, bot): self._bot = bot

    def set_zalo_bot(self, bot): self._zalo_bot = bot

    async def _alert_admin(self, msg: str):
        admin_tg_id = db.get_setting("admin_tg_id", "")
        if admin_tg_id and self._bot:
            try: await self._bot.send_message(int(admin_tg_id), f"⚠️ <b>SYSTEM ALERT</b>\n{msg}", parse_mode="HTML")
            except: pass

    def start(self):
        if not (self._account_task and not self._account_task.done()):
            self._account_task = asyncio.create_task(self._account_loop())
        if not (self._video_task and not self._video_task.done()):
            self._video_task = asyncio.create_task(self._video_loop())
        if not (self._backup_task and not self._backup_task.done()):
            self._backup_task = asyncio.create_task(self._backup_loop())
        if not hasattr(self, '_campaign_task') or not (self._campaign_task and not self._campaign_task.done()):
            self._campaign_task = asyncio.create_task(self._campaign_scheduler_loop())
        if not hasattr(self, '_proxy_task') or not (self._proxy_task and not self._proxy_task.done()):
            self._proxy_task = asyncio.create_task(self._proxy_loop())
        if not hasattr(self, '_daily_summary_task') or not (self._daily_summary_task and not self._daily_summary_task.done()):
            self._daily_summary_task = asyncio.create_task(self._daily_summary_loop())
        log.info("Poller khoi dong (account + video + backup + proxy + daily_summary + campaign).")

    async def _daily_summary_loop(self):
        while True:
            # Run at 20:00 every day
            now_t = time.localtime()
            if now_t.tm_hour == 20 and getattr(self, '_last_summary_day', -1) != now_t.tm_mday:
                self._last_summary_day = now_t.tm_mday
                try:
                    batches = db.get_and_clear_batch_notifications()
                    for tg_id, msgs in batches.items():
                        if not msgs: continue
                        summary = f"📋 <b>BÁO CÁO CUỐI NGÀY (20:00)</b>\n━━━━━━━━━━━━━━━━━\n"
                        # Group logic: Just list them compactly or count them if too many
                        if len(msgs) > 10:
                            summary += f"Bạn có <b>{len(msgs)}</b> biến động trong ngày. Dưới đây là 10 thông báo mới nhất:\n"
                            msgs = msgs[-10:]
                        for m in msgs:
                            summary += f"• {m}\n"
                        summary += "━━━━━━━━━━━━━━━━━\n<i>TikTok/FB Checker V2</i>"
                        if self._bot:
                            await self._bot.send_message(tg_id, summary, parse_mode="HTML")
                except Exception as e:
                    log.error("Lỗi gửi daily summary: %s", e)
            
            await asyncio.sleep(60) # Check every minute

    async def _campaign_scheduler_loop(self):
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        import json
        import os
        from aiogram.types import FSInputFile
        import time
        
        while True:
            try:
                now_ts = int(time.time())
                camps = db.get_campaigns("pending")
                for camp in camps:
                    if camp["scheduled_for"] > 0 and camp["scheduled_for"] > now_ts:
                        continue # Not time yet
                    
                    # Time to run campaign!
                    db.update_campaign_status(camp["id"], "running")
                    
                    text = camp.get("text_content", "") or ""
                    image = camp.get("image_url", "")
                    photo_path = None
                    if image:
                        photo_path = os.path.join(os.path.dirname(__file__), "..", "static", "images", image)
                        if not os.path.exists(photo_path): photo_path = None
                        
                    ctype = camp.get("type", "broadcast")
                    try: config = json.loads(camp.get("config", "{}"))
                    except: config = {}
                    
                    kb = None
                    buttons = []
                    
                    if ctype == "giveaway":
                        buttons.append([InlineKeyboardButton(text="🧧 Nhận Lì Xì", callback_data=f"camp_giveaway_{camp['id']}")])
                    elif ctype == "sale":
                        code = config.get("code", "")
                        if code:
                            buttons.append([InlineKeyboardButton(text="🎁 Áp Dụng Mã", callback_data=f"use_code_{code}")])
                    elif ctype == "cta":
                        btn_text = config.get("btn_text", "Truy Cập")
                        btn_url = config.get("btn_url", "")
                        if btn_url:
                            buttons.append([InlineKeyboardButton(text=btn_text, url=btn_url)])
                    elif ctype == "bounty":
                        buttons.append([InlineKeyboardButton(text="🎯 Nộp Bằng Chứng", callback_data=f"camp_bounty_{camp['id']}")])
                        
                    if buttons:
                        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
                        
                    formatted_text = (
                        f"📢 <b>THÔNG BÁO TỪ HỆ THỐNG</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"{text}\n\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"<i>Cảm ơn bạn đã đồng hành cùng chúng tôi!</i>"
                    )
                    
                    target_type = config.get("target_type", "all")
                    target_users_str = config.get("target_users", "")
                    
                    if target_type == "specific" and target_users_str.strip():
                        users = [{"tg_id": t.strip()} for t in target_users_str.split(",") if t.strip()]
                    else:
                        users = db.list_users()
                        
                    success_count = 0
                    
                    last_error = ""
                    for u in users:
                        try:
                            if photo_path:
                                await self._bot.send_photo(u["tg_id"], photo=FSInputFile(photo_path), caption=formatted_text, parse_mode="HTML", reply_markup=kb)
                            else:
                                await self._bot.send_message(u["tg_id"], formatted_text, parse_mode="HTML", reply_markup=kb)
                            success_count += 1
                            await asyncio.sleep(0.05)
                        except Exception as e:
                            last_error = str(e)
                            log.error(f"Send campaign err for {u['tg_id']}: {e}")
                    
                    stats = {"sent": success_count}
                    if last_error:
                        stats["error"] = last_error
                        
                    db.update_campaign_stats(camp["id"], json.dumps(stats))
                    db.update_campaign_status(camp["id"], "finished")
                    
            except Exception as e:
                log.error(f"Campaign scheduler error: {e}")
                
            await asyncio.sleep(10)

    async def stop(self):
        tasks = [self._account_task, self._video_task, self._backup_task]
        if hasattr(self, '_proxy_task'):
            tasks.append(self._proxy_task)
        for t in tasks:
            if t:
                t.cancel()
                try: await t
                except: pass
        self._account_task = self._video_task = self._backup_task = None
        self._proxy_task = None

    # ══ BACKUP LOOP ═══════════════════════════════════════════
    async def _backup_loop(self):
        await asyncio.sleep(60)
        while True:
            try:
                now_t = time.localtime()
                last_backup_str = db.get_setting("last_backup_date", "")
                today_str = vn_time_str("%Y-%m-%d")
                
                if now_t.tm_hour == 0 and last_backup_str != today_str:
                    admin_tg_id = db.get_setting("admin_tg_id", "")
                    admin_tg_group_id = db.get_setting("admin_tg_group_id", "")
                    
                    admins = []
                    if admin_tg_id: admins.append(int(admin_tg_id))
                    if admin_tg_group_id: admins.append(int(admin_tg_group_id))
                    
                    if admins and self._bot:
                        from aiogram.types import FSInputFile
                        file_path = config.DB_PATH
                        for aid in admins:
                            try:
                                await self._bot.send_document(
                                    aid,
                                    document=FSInputFile(file_path),
                                    caption=f"📦 Backup Auto - {today_str}"
                                )
                            except Exception as e:
                                log.error("Loi gui backup DB toi %s: %s", aid, e)
                        db.set_setting("last_backup_date", today_str)
            except Exception as e:
                log.error("Loi backup DB: %s", e)
            await asyncio.sleep(300)

    # ══ ACCOUNT LOOP ═══════════════════════════════════════════
    async def _account_loop(self):
        await asyncio.sleep(30)
        while True:
            try:
                await self._check_accounts()
                await self._check_ig_accounts()
                await self._check_fb_accounts()
                await self._check_fb_watches()
            except Exception as e:
                log.exception("Loi account poller: %s", e)
            interval = max(60, int(db.get_setting("poll_interval", str(config.POLL_INTERVAL))))
            await asyncio.sleep(interval)

    async def _filter_expired_tracks(self, tracks):
        valid = []
        for t in tracks:
            tg_id = t.get("tg_user_id")
            if not tg_id:
                valid.append(t)
                continue
            u = db.get_user(tg_id)
            if not u:
                valid.append(t)
                continue
            now_ts = int(time.time())
            if u["sub_until"] > now_ts:
                valid.append(t)
            else:
                price = int(db.get_setting("price_per_month", "50000"))
                if u.get("auto_renew", 0) == 1 and u.get("balance", 0) >= price:
                    with db._lock:
                        c = db.get_conn()
                        c.execute("UPDATE tg_users SET balance = balance - ?, sub_until = ? WHERE tg_id=?", (price, now_ts + 30*86400, tg_id))
                        c.execute("INSERT INTO txns(ts, tg_id, amount, reason) VALUES(?,?,?,?)", (now_ts, tg_id, -price, "auto_renew_sub"))
                        c.commit()
                    valid.append(t)
                    if self._bot:
                        try: await self._bot.send_message(tg_id, f"🔄 <b>Gia hạn tự động thành công!</b>\nHệ thống đã trừ <b>{price:,}đ</b> và gia hạn thêm 30 ngày sử dụng.", parse_mode="HTML")
                        except: pass
                    continue
                
                if u["expired_notified"] == 0:
                    with db._lock:
                        db.get_conn().execute("UPDATE tg_users SET expired_notified=1 WHERE tg_id=?", (tg_id,))
                        db.get_conn().commit()
                    if self._bot:
                        try:
                            await self._bot.send_message(tg_id, "⚠️ <b>Gói VIP của bạn đã hết hạn.</b>\nHệ thống tạm dừng tất cả các tác vụ theo dõi. Vui lòng nạp thêm (hoặc gia hạn) để tiếp tục sử dụng!", parse_mode="HTML")
                        except: pass
        return valid

    async def _check_accounts(self):
        from . import tiktok as tk
        tracks = await self._filter_expired_tracks(db.all_active_tracks())
        if not tracks: return
        self.last_run = int(time.time())
        log.info("Account poller: check %d tai khoan.", len(tracks))

        for track in tracks:
            try:
                info = await tk.fetch_tiktok_info(track["tiktok_username"])
                new_fl, old_fl = info["followers"], track["last_followers"]
                new_vid, old_vid = info["videos"], track["last_videos"]
                latest  = info.get("latest_video")
                new_vid_id  = (latest or {}).get("id", "") or ""
                last_vid_id = track.get("last_video_id", "") or ""

                db.update_track_stats(track["id"], new_fl, info["following"], new_vid, new_vid_id)
                db.record_track_history(track["id"], "tiktok_account", "followers", new_fl)

                fl_diff = new_fl - old_fl
                if fl_diff != 0:
                    asyncio.create_task(event_bus.emit("tiktok_follower_change", {
                        "username": info["username"],
                        "old_fl": old_fl,
                        "new_fl": new_fl,
                        "tg_user_id": track.get("tg_user_id"),
                        "zalo_user_id": track.get("zalo_user_id")
                    }))
                    sign = "+" if fl_diff > 0 else ""
                    dir_ = "tăng 📈" if fl_diff > 0 else "giảm 📉"
                    now_str = vn_time_str("%d/%m/%Y %H:%M:%S")
                    msg = (f"🔔 <b>Follower {dir_}</b>\n\n"
                           f"📱 <b>@{info['username']}</b>\n"
                           f"👥 Thay đổi: {sign}{fl_diff:,} → Tổng: <b>{tk.fmt_num(new_fl)}</b>\n"
                           f"➡️ Đang follow: <b>{tk.fmt_num(info['following'])}</b>\n"
                           f"❤️ Tổng likes: <b>{tk.fmt_num(info['hearts'])}</b>\n"
                           f"🎬 Tổng videos: <b>{tk.fmt_num(new_vid)}</b>\n\n"
                           f"⏰ Thời gian: <b>{now_str}</b>\n\n"
                           f"🤖 <i>TikTok Checker V2 by @khaikhai998</i>")
                    
                    zalo_id = track.get("zalo_user_id")
                    tg_id = track.get("tg_user_id")
                    
                    if zalo_id and self._zalo_bot:
                        try: await self._zalo_bot.send_message(zalo_id, msg)
                        except Exception as e: log.warning("Notify Zalo: %s", e)
                    elif tg_id and self._bot:
                        if fl_diff <= -1000:
                            smart_msg = f"⚠️ <b>CẢNH BÁO BẤT THƯỜNG:</b> Tụt Follow nhanh!\n\n{msg}"
                            try: await self._bot.send_message(tg_id, smart_msg)
                            except Exception as e: log.warning("Notify Telegram: %s", e)
                        else:
                            db.add_batch_notification(tg_id, f"TikTok @{info['username']} {dir_} {abs(fl_diff):,} followers (Tổng: {tk.fmt_num(new_fl)})")
                        
                    db.add_log("follower_change", f"@{info['username']}: {old_fl:,}→{new_fl:,} ({sign}{fl_diff:,})",
                               tg_id or zalo_id, info["username"])

                # New video notify
                is_new = ((new_vid_id and last_vid_id and new_vid_id != last_vid_id)
                          or (not last_vid_id and new_vid > old_vid > 0))
                if is_new:
                    now_str = vn_time_str("%d/%m/%Y %H:%M:%S")
                    caption = tk.build_video_caption(latest) if latest and latest.get("id") else (
                        f"🎬 <b>@{info['username']}</b> vừa đăng video mới!\n"
                        f"🎬 Tổng: <b>{tk.fmt_num(new_vid)}</b> videos\n\n"
                        f"⏰ Thời gian: <b>{now_str}</b>\n\n"
                        f"🔗 <a href='https://www.tiktok.com/@{info['username']}'>Xem trang TikTok</a>\n\n"
                        f"🤖 <i>TikTok Checker V2 by @khaikhai998</i>")
                    
                    zalo_id = track.get("zalo_user_id")
                    tg_id = track.get("tg_user_id")
                    
                    try:
                        if zalo_id and self._zalo_bot:
                            await self._zalo_bot.send_message(zalo_id, caption)
                        elif tg_id and self._bot:
                            if latest and latest.get("cover"):
                                from aiogram.types import URLInputFile
                                await self._bot.send_photo(tg_id, photo=URLInputFile(latest["cover"], filename="thumb.jpg"), caption=caption)
                            else:
                                await self._bot.send_message(tg_id, caption)
                    except Exception as e: log.warning("Notify video: %s", e)
                    db.add_log("video_new", f"@{info['username']}: video moi", tg_id or zalo_id, info["username"])

            except Exception as e:
                log.warning("Loi check @%s: %s", track["tiktok_username"], e)
                if "proxy" in str(e).lower() or "429" in str(e):
                    await self._alert_admin(f"Lỗi API/Proxy khi check TikTok @{track['tiktok_username']}: {e}")
            await asyncio.sleep(3)

    # ══ VIDEO LOOP ═════════════════════════════════════════════
    async def _video_loop(self):
        await asyncio.sleep(45)
        while True:
            try:
                await self._check_videos()
                await self._check_ig_videos()
                await self._check_fb_posts()
            except Exception as e:
                log.exception("Loi video poller: %s", e)
            await asyncio.sleep(60)

    async def _check_videos(self):
        from . import tiktok as tk
        now = int(time.time())
        vtracks = await self._filter_expired_tracks(db.all_active_video_tracks())
        if not vtracks: return

        for vt in vtracks:
            if vt["last_checked"] + vt["check_interval"] > now:
                continue
            try:
                info = await tk.fetch_video_info(vt["video_url"])
                old = {
                    "plays":    vt["last_plays"],
                    "likes":    vt["last_likes"],
                    "comments": vt["last_comments"],
                    "shares":   vt["last_shares"],
                    "favorites": vt.get("last_favorites", 0),
                }
                new_p, new_l, new_c, new_s, new_f = info["plays"], info["likes"], info["comments"], info["shares"], info.get("favorites", 0)

                db.update_video_track_stats(vt["id"], new_p, new_l, new_c, new_s, new_f)
                db.record_track_history(vt["id"], "tiktok_video", "views", new_p)

                dp = new_p - old["plays"]
                now_ts = int(time.time())
                spike_threshold = int(db.get_setting("spike_threshold", "10000"))
                if dp >= spike_threshold and (now_ts - vt.get("last_spike_alert_at", 0)) > 86400:
                    with db._lock:
                        db.get_conn().execute("UPDATE video_tracks SET last_spike_alert_at=? WHERE id=?", (now_ts, vt["id"]))
                        db.get_conn().commit()
                    spike_msg = f"🔥 <b>CẢNH BÁO: VIDEO LÊN XU HƯỚNG!</b>\n\nVideo <a href='{info['url']}'>TikTok</a> của bạn vừa tăng đột biến <b>+{dp:,} views</b>!"
                    zalo_id = vt.get("zalo_user_id")
                    tg_id = vt.get("tg_user_id")
                    try:
                        if zalo_id and self._zalo_bot: await self._zalo_bot.send_message(zalo_id, spike_msg)
                        elif tg_id and self._bot: await self._bot.send_message(tg_id, spike_msg, parse_mode="HTML")
                    except: pass

                changed = (new_p != old["plays"] or new_l != old["likes"]
                           or new_c != old["comments"] or new_s != old["shares"]
                           or new_f != old["favorites"])

                if changed:
                    caption = tk.build_video_caption(info, old)
                    zalo_id = vt.get("zalo_user_id")
                    tg_id = vt.get("tg_user_id")
                    
                    try:
                        if zalo_id and self._zalo_bot:
                            await self._zalo_bot.send_message(zalo_id, caption)
                        elif tg_id and self._bot:
                            if info.get("cover"):
                                from aiogram.types import URLInputFile
                                await self._bot.send_photo(tg_id, photo=URLInputFile(info["cover"], filename="thumb.jpg"), caption=caption)
                            else:
                                await self._bot.send_message(tg_id, caption)
                    except Exception as e:
                        log.warning("Notify video track: %s", e)

                    dp = new_p - old["plays"]
                    dl = new_l - old["likes"]
                    db.add_log("video_stats",
                               f"Video @{info['username']}: +{dp:,} views, +{dl:,} likes",
                               vt["tg_user_id"], info.get("username",""))
            except Exception as e:
                log.warning("Loi check video %s: %s", vt["video_url"], e)
            await asyncio.sleep(2)

    async def _check_fb_posts(self):
        from . import fb
        now = int(time.time())
        vtracks = await self._filter_expired_tracks(db.all_active_fb_post_tracks())
        if not vtracks: return

        for vt in vtracks:
            if vt["last_checked"] + vt["check_interval"] > now:
                continue
            try:
                info = await fb.fetch_fb_post_info(vt["post_url"])
                old = {
                    "likes":    vt["last_likes"],
                    "comments": vt["last_comments"],
                    "shares":   vt["last_shares"],
                }
                new_l, new_c, new_s = info["likes"], info["comments"], info["shares"]

                db.update_fb_post_track_stats(vt["id"], new_l, new_c, new_s)

                changed = (new_l != old["likes"] or new_c != old["comments"] or new_s != old["shares"])

                if changed:
                    caption = fb.build_fb_post_caption(info)
                    caption += f"\n\n📈 Tăng: +{new_l - old['likes']:,} Thích, +{new_c - old['comments']:,} Bình luận, +{new_s - old['shares']:,} Chia sẻ."
                    zalo_id = vt.get("zalo_user_id")
                    tg_id = vt.get("tg_user_id")
                    
                    try:
                        if zalo_id and self._zalo_bot:
                            await self._zalo_bot.send_message(zalo_id, caption)
                        elif tg_id and self._bot:
                            if info.get("cover"):
                                from aiogram.types import URLInputFile
                                await self._bot.send_photo(tg_id, photo=URLInputFile(info["cover"], filename="thumb.jpg"), caption=caption)
                            else:
                                await self._bot.send_message(tg_id, caption, disable_web_page_preview=True)
                    except Exception as e:
                        log.warning("Notify FB post track: %s", e)

                    dl = new_l - old["likes"]
                    db.add_log("fb_post_stats",
                               f"FB Post {info['post_id']}: +{dl:,} likes",
                               vt["tg_user_id"], info.get("post_id",""))
            except Exception as e:
                log.warning("Loi check fb post %s: %s", vt["post_url"], e)
            await asyncio.sleep(2)

    # ══ IG CHECKERS ════════════════════════════════════════════
    async def _check_ig_accounts(self):
        from . import ig
        tracks = await self._filter_expired_tracks(db.all_active_ig_tracks())
        if not tracks: return
        log.info("IG Account poller: check %d tai khoan.", len(tracks))

        for track in tracks:
            try:
                info = await ig.fetch_ig_info(track["ig_username"])
                new_fl, old_fl = info["followers"], track["last_followers"]
                db.update_ig_track_stats(track["id"], new_fl, info["following"], info["posts"])
                db.record_track_history(track["id"], "ig_account", "followers", new_fl)

                fl_diff = new_fl - old_fl
                if fl_diff != 0 and old_fl > 0:
                    sign = "+" if fl_diff > 0 else ""
                    dir_ = "tăng 📈" if fl_diff > 0 else "giảm 📉"
                    now_str = vn_time_str("%d/%m/%Y %H:%M:%S")
                    msg = (f"🔔 <b>IG Follower {dir_}</b>\n\n"
                           f"📸 <b>@{info['username']}</b>\n"
                           f"👥 Thay đổi: {sign}{fl_diff:,} → Tổng: <b>{ig.fmt_num(new_fl)}</b>\n"
                           f"➡️ Đang follow: <b>{ig.fmt_num(info['following'])}</b>\n"
                           f"🖼️ Bài viết: <b>{ig.fmt_num(info['posts'])}</b>\n\n"
                           f"⏰ Thời gian: <b>{now_str}</b>\n\n"
                           f"🤖 <i>Instagram Checker V2 by @khaikhai998</i>")
                    
                    zalo_id = track.get("zalo_user_id")
                    tg_id = track.get("tg_user_id")
                    if zalo_id and self._zalo_bot:
                        try: await self._zalo_bot.send_message(zalo_id, msg)
                        except Exception as e: log.warning("Notify Zalo IG: %s", e)
                    elif tg_id and self._bot:
                        try: await self._bot.send_message(tg_id, msg)
                        except Exception as e: log.warning("Notify Telegram IG: %s", e)
                        
                    db.add_log("follower_change", f"IG @{info['username']}: {old_fl:,}→{new_fl:,} ({sign}{fl_diff:,})",
                               tg_id or zalo_id, info["username"])
            except Exception as e:
                log.warning("Loi check IG @%s: %s", track["ig_username"], e)
            await asyncio.sleep(4)

    async def _check_fb_watches(self):
        for w in db.active_watches():
            if w["expire_at"] and now() > w["expire_at"]:
                db.deactivate_watch(w["id"])
                db.add_log("system", f"Hết hạn theo dõi UID {w['uid']}", w["tg_id"], w["uid"])
                continue

            res = await fb.check_uid(w["uid"])
            if not res["ok"]:
                continue
            new_status = "live" if res["alive"] else "die"
            avatar = res["avatar_url"] or w["avatar_url"] or fb.avatar_url(w["uid"])
            old = w["last_status"]
            db.update_watch_status(w["id"], new_status, avatar)

            if old and old != new_status:
                db.add_log(
                    "change",
                    f"UID {w['uid']}: {old} → {new_status}",
                    w["tg_id"],
                    w["uid"],
                )
                asyncio.create_task(event_bus.emit("fb_watch_status_change", {
                    "uid": w['uid'],
                    "old_status": old,
                    "new_status": new_status,
                    "tg_id": w['tg_id']
                }))
                bot = botmod.manager.bot
                if bot:
                    try:
                        db.add_batch_notification(w["tg_id"], f"FB UID {w['uid']}: {old} ➡️ {new_status}")
                    except Exception as e:
                        db.add_log("system", f"Lỗi lưu batch_notification {w['tg_id']}: {e}")
                
                # Check alerts
                await _handle_alerts("fb_watch", w["uid"], new_status, f"UID {w['uid']} status changed to {new_status}", botmod.manager.bot)
            await asyncio.sleep(0.3)

    async def _check_fb_accounts(self):
        from . import fb
        tracks = await self._filter_expired_tracks(db.all_active_fb_tracks())
        if not tracks: return
        log.info("FB Account poller: check %d tai khoan.", len(tracks))

        for track in tracks:
            try:
                res = await fb.check_uid(track["fb_uid"])
                new_status = "live" if res["alive"] else "die"
                old_status = track["last_status"]

                db.update_fb_track_status(track["id"], new_status, res.get("avatar_url", ""))

                if old_status and new_status != old_status:
                    icon = "🟢 MỞ KHOÁ (LIVE)" if res["alive"] else "🔴 BỊ KHOÁ (DIE)"
                    now_str = vn_time_str("%d/%m/%Y %H:%M:%S")
                    msg = (f"🔔 <b>Cảnh Báo Facebook {icon}</b>\n\n"
                           f"👤 <b>UID:</b> <code>{res['uid']}</code>\n"
                           f"🔄 <b>Thay đổi:</b> {old_status.upper()} ➡️ {new_status.upper()}\n"
                           f"⏰ Thời gian: <b>{now_str}</b>\n\n"
                           f"🤖 <i>Facebook Checker by @khaikhai998</i>")
                    
                    zalo_id = track.get("zalo_user_id")
                    tg_id = track.get("tg_user_id")
                    
                    if zalo_id and self._zalo_bot:
                        try: await self._zalo_bot.send_message(zalo_id, msg)
                        except Exception as e: log.warning("Notify Zalo FB: %s", e)
                    elif tg_id and self._bot:
                        db.add_batch_notification(tg_id, f"Tài khoản FB {res['uid']}: {old_status.upper()} ➡️ {new_status.upper()}")
                        
                    db.add_log("fb_status_change", f"FB {res['uid']}: {old_status.upper()} -> {new_status.upper()}",
                               tg_id or zalo_id, res["uid"])
                               
                    await _handle_alerts("fb_track", res["uid"], new_status, msg, self._bot)
            except Exception as e:
                log.warning("Loi check FB %s: %s", track["fb_uid"], e)
            await asyncio.sleep(3)

    async def _check_ig_videos(self):
        from . import ig
        now = int(time.time())
        vtracks = await self._filter_expired_tracks(db.all_active_ig_video_tracks())
        if not vtracks: return

        for vt in vtracks:
            if vt["last_checked"] + vt["check_interval"] > now:
                continue
            try:
                info = await ig.fetch_ig_post_info(vt["post_url"])
                old = {
                    "likes":    vt["last_likes"],
                    "comments": vt["last_comments"],
                    "views":    vt["last_views"],
                }
                new_l, new_c, new_v = info["likes"], info["comments"], info.get("views", 0)

                db.update_ig_video_track_stats(vt["id"], new_l, new_c, new_v)
                db.record_track_history(vt["id"], "ig_video", "views", new_v)

                changed = (new_l != old["likes"] or new_c != old["comments"] or new_v != old["views"])

                if changed and old["likes"] > 0:
                    caption = ig.build_ig_video_caption(info, old)
                    zalo_id = vt.get("zalo_user_id")
                    tg_id = vt.get("tg_user_id")
                    try:
                        if zalo_id and self._zalo_bot:
                            await self._zalo_bot.send_message(zalo_id, caption)
                        elif tg_id and self._bot:
                            if info.get("cover"):
                                from aiogram.types import URLInputFile
                                await self._bot.send_photo(tg_id, photo=URLInputFile(info["cover"], filename="thumb.jpg"), caption=caption)
                            else:
                                await self._bot.send_message(tg_id, caption)
                    except Exception as e:
                        log.warning("Notify IG video track: %s", e)

                    dl = new_l - old["likes"]
                    dc = new_c - old["comments"]
                    db.add_log("video_stats",
                               f"IG Post @{info['username']}: +{dl:,} likes, +{dc:,} cmt",
                               vt["tg_user_id"], info.get("username",""))
            except Exception as e:
                log.warning("Loi check IG post %s: %s", vt["post_url"], e)
            await asyncio.sleep(4)

    async def _proxy_loop(self):
        import httpx
        while True:
            await asyncio.sleep(600)  # run every 10 mins
            try:
                proxies = db.get_proxies()
                active_proxies = [p for p in proxies if p["is_active"] == 1]
                
                # Check proxy health
                for p in active_proxies:
                    try:
                        async with httpx.AsyncClient(proxy=p["proxy_url"], timeout=10) as client:
                            resp = await client.get("https://www.google.com/")
                            if resp.status_code == 200:
                                with db._lock:
                                    c = db.get_conn()
                                    c.execute("UPDATE proxies SET fail_count=0 WHERE id=?", (p["id"],))
                                    c.commit()
                            else:
                                db.mark_proxy_failed(p["proxy_url"])
                    except Exception:
                        db.mark_proxy_failed(p["proxy_url"])
                        
                # Auto fetch new proxy if below min
                min_active = int(db.get_setting("min_active_proxies", "0") or "0")
                active_proxies = [p for p in db.get_proxies() if p["is_active"] == 1]
                if min_active > 0 and len(active_proxies) < min_active:
                    api_url = db.get_setting("proxy_api_url", "")
                    api_key = db.get_setting("proxy_api_key", "")
                    if api_url and api_key:
                        try:
                            # Generic POST call
                            async with httpx.AsyncClient() as client:
                                r = await client.post(api_url, json={"api_key": api_key})
                                if r.status_code == 200:
                                    data = r.json()
                                    proxy_str = ""
                                    if "data" in data and isinstance(data["data"], dict):
                                        proxy_str = data["data"].get("https", "") or data["data"].get("http", "")
                                    elif "proxy" in data:
                                        proxy_str = data["proxy"]
                                        
                                    if proxy_str:
                                        if not proxy_str.startswith("http"):
                                            proxy_str = "http://" + proxy_str
                                        db.add_proxy(proxy_str)
                                        log.info(f"Auto fetched new proxy: {proxy_str}")
                        except Exception as e:
                            log.error(f"Error fetching proxy: {e}")
            except Exception as e:
                log.error(f"Proxy loop error: {e}")


poller = FollowerPoller()


# --- YOUTUBE POLLER ---
async def _poll_yt():
    from app.yt import fetch_yt_info, fetch_yt_video_info
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    while True:
        try:
            tracks = db.all_active_yt_tracks()
            for t in tracks:
                try:
                    if not _is_user_active(t["tg_user_id"]): continue
                    
                    res = await fetch_yt_info(t["yt_username"])
                    old_subs = t["last_subscribers"]
                    old_videos = t["last_videos"]
                    new_subs = res["subscribers"]
                    new_videos = res["videos"]
                    
                    if new_videos > old_videos and old_videos > 0:
                        db.add_log("video_new", f"YT @{res['username']}: video mới", t["tg_user_id"], res["username"])
                        await _bot.send_message(t["tg_user_id"], f"🎥 Kênh YouTube <b>{res['username']}</b> vừa đăng video mới!\nHiện có: {new_videos} video.", parse_mode="HTML")
                        
                    if new_subs != old_subs and old_subs > 0:
                        db.record_track_history(t["id"], "yt_account", "subscribers", new_subs)
                        
                        if abs(new_subs - old_subs) >= 1000:
                            icon = "📈" if new_subs > old_subs else "📉"
                            sign = "+" if new_subs > old_subs else ""
                            diff = new_subs - old_subs
                            db.add_log("follower_change", f"YT @{res['username']}: {old_subs:,}→{new_subs:,} ({sign}{diff:,})", t["tg_user_id"], res["username"])
                            
                            kb = InlineKeyboardMarkup(inline_keyboard=[[
                                InlineKeyboardButton(text="📊 Xem Biểu Đồ Sub", callback_data=f"chart_yt_account_{t['id']}")
                            ]])
                            await _bot.send_message(t["tg_user_id"], f"{icon} Kênh YouTube <b>{res['username']}</b> biến động sub!\nSub hiện tại: {new_subs:,}", parse_mode="HTML", reply_markup=kb)
                        
                    db.update_yt_track_status(t["id"], new_subs, new_videos)
                except Exception as e:
                    pass
                await asyncio.sleep(2)
                
            v_tracks = db.all_active_yt_video_tracks()
            for t in v_tracks:
                try:
                    if not _is_user_active(t["tg_user_id"]): continue
                    # interval check
                    if now() - t["last_checked"] < t["check_interval"]: continue
                    
                    res = await fetch_yt_video_info(t["video_id"])
                    
                    old_views = t["last_views"]
                    new_views = res["views"]
                    if new_views != old_views and old_views > 0:
                        db.record_track_history(t["id"], "yt_video", "views", new_views)
                        
                        diff = new_views - old_views
                        if diff >= 5000:
                            db.add_log("video_stats", f"YT Video {res['id']}: +{diff:,} views", t["tg_user_id"], res["id"])
                            kb = InlineKeyboardMarkup(inline_keyboard=[[
                                InlineKeyboardButton(text="📊 Xem Biểu Đồ Views", callback_data=f"chart_yt_video_{t['id']}")
                            ]])
                            await _bot.send_message(t["tg_user_id"], f"👁️ Video YouTube <b>{res['username']}</b> tăng +{diff:,} views!\nHiện tại: {new_views:,} views.", parse_mode="HTML", reply_markup=kb)
                            
                    db.update_yt_video_track(t["id"], res["views"], res["likes"], res["comments"])
                except Exception as e:
                    pass
                await asyncio.sleep(2)
                
        except Exception as e:
            pass
        await asyncio.sleep(60)

# --- ZALO POLLER ---
async def _poll_zalo():
    from app.zalo_checker import check_zalo_phone
    while True:
        try:
            tracks = db.all_active_zalo_tracks()
            cookie = db.get_setting("zalo_cookie", "")
            imei = db.get_setting("zalo_imei", "")
            
            for t in tracks:
                try:
                    if not _is_user_active(t["tg_user_id"]): continue
                    
                    res = await check_zalo_phone(t["phone"], cookie, imei)
                    
                    new_status = "LIVE" if res.get("live") else "DIE"
                    old_status = t["status"]
                    
                    if old_status != new_status:
                        db.add_log("zalo_status_change", f"Zalo {t['phone']}: {old_status} -> {new_status}", t["tg_user_id"], t["phone"])
                        icon = "✅" if new_status == "LIVE" else "❌"
                        name_str = f"\nTên Zalo: <b>{res.get('name', '')}</b>" if res.get('name') else ""
                        msg = f"{icon} SĐT Zalo <b>{t['phone']}</b> đã chuyển sang trạng thái <b>{new_status}</b>!{name_str}"
                        await _bot.send_message(t["tg_user_id"], msg, parse_mode="HTML")
                        
                        await _handle_alerts("zalo", t["phone"], new_status, msg, _bot)
                        
                    db.update_zalo_track_status(t["id"], new_status, res.get("name", t["name"]), res.get("avatar", t["avatar"]))
                except Exception as e:
                    pass
                await asyncio.sleep(5) # Delay 5 seconds between each check
        except Exception as e:
            pass
        await asyncio.sleep(60)
