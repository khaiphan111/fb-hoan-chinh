import asyncio, html, logging, time
from typing import Optional
from . import config, db, fb
from . import bot as botmod
from .util import now, vn_time_str
from .event_bus import event_bus
from . import ops

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

async def _notify_admin_watch_change(uid: str, old: str, new: str, tg_id: int):
    """Báo cho admin qua mọi kênh đang chạy khi UID được theo dõi đổi trạng thái."""
    icon = "🔴" if new == "die" else "🟢"
    msg = (
        f"{icon} <b>WATCH ĐỔI TRẠNG THÁI</b>\n"
        f"🆔 UID: <code>{uid}</code>\n"
        f"👤 User: <code>{tg_id}</code>\n"
        f"📊 {old} ➡️ <b>{new.upper()}</b>"
    )
    # 1. Admin Telegram bot
    try:
        from .admin_bot import manager as admin_manager
        admin_bot = admin_manager.bot
        admin_id = db.get_setting("admin_tg_id")
        if admin_bot and admin_id:
            await admin_bot.send_message(int(admin_id), msg, parse_mode="HTML")
    except Exception as e:
        log.warning("Admin TG notify failed: %s", e)
    # 2. Zalo admin
    try:
        zalo_manager = getattr(botmod, "zalo_manager", None)
        admin_zalo = db.get_setting("admin_zalo_chat_id") or db.get_setting("admin_zalo")
        if zalo_manager and getattr(zalo_manager, "running", False) and admin_zalo:
            import asyncio as _asyncio
            _asyncio.create_task(zalo_manager.send_message(admin_zalo, msg))
    except Exception as e:
        log.warning("Admin Zalo notify failed: %s", e)


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
        if not hasattr(self, '_maint_task') or not (self._maint_task and not self._maint_task.done()):
            self._maint_task = asyncio.create_task(self._maintenance_loop())
        if not hasattr(self, '_ops_task') or not (self._ops_task and not self._ops_task.done()):
            self._ops_task = asyncio.create_task(self._ops_loop())
        log.info("Poller khoi dong (account + video + backup + proxy + daily_summary + campaign + maintenance).")

    async def _daily_summary_loop(self):
        while True:
            try:
                now_t = time.localtime()

                # 1. Gửi báo cáo định kỳ theo giờ tự chọn cho từng user (khi đúng phút 00)
                if now_t.tm_min == 0:
                    current_hour = now_t.tm_hour
                    users_due = db.get_users_for_daily_report(current_hour)
                    for user in users_due:
                        tg_id = user["tg_id"]
                        try:
                            # Lấy danh sách alert_rules / tracks của user
                            rules = db.get_alert_rules(tg_id=str(tg_id))
                            if not rules:
                                # Fallback: lấy từ user lists nếu không có alert rules
                                lists = db.get_user_lists(int(tg_id))
                                items = []
                                for l in lists:
                                    items.extend(db.get_list_items(int(tg_id), l["list_name"]))
                                targets = list(set(i["value"] for i in items))
                            else:
                                targets = list(set(r["target"] for r in rules if r["platform"] == "fb"))

                            if targets:
                                from .fb import check_uid
                                live_cnt = 0
                                die_cnt = 0
                                die_uids = []
                                for t in targets:
                                    try:
                                        res = await check_uid(t)
                                        if res.get("alive") or res.get("status") == "live":
                                            live_cnt += 1
                                        else:
                                            die_cnt += 1
                                            die_uids.append(t)
                                    except Exception:
                                        die_cnt += 1

                                hour_str = f"{current_hour:02d}:00"
                                report_text = (
                                    f"☀️ <b>BÁO CÁO TỰ ĐỘNG ({hour_str})</b>\n"
                                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                                    f"📊 Dàn <b>{len(targets)}</b> nick FB của bạn đang có:\n"
                                    f"• 🟢 Live: <b>{live_cnt}</b> tài khoản\n"
                                    f"• 🔴 Die: <b>{die_cnt}</b> tài khoản\n\n"
                                )
                                if die_uids:
                                    report_text += "🔴 <b>Chi tiết nick DIE hôm nay:</b>\n" + "\n".join(f"• <code>{u}</code>" for u in die_uids[:10])
                                    if len(die_uids) > 10:
                                        report_text += f"\n<i>...và {len(die_uids)-10} UID khác</i>"
                                    report_text += "\n\n"
                                report_text += "🤖 <i>Tự động theo dõi bởi FB Checker V2</i>"

                                if self._bot:
                                    await self._bot.send_message(int(tg_id), report_text, parse_mode="HTML")
                        except Exception as ex:
                            log.error("Lỗi gửi daily report cho user %s: %s", tg_id, ex)

                # 2. Gửi batch notifications vào 20:00 hàng ngày (nếu có)
                if now_t.tm_hour == 20 and getattr(self, '_last_summary_day', -1) != now_t.tm_mday:
                    self._last_summary_day = now_t.tm_mday
                    batches = db.get_and_clear_batch_notifications()
                    for tg_id, msgs in batches.items():
                        if not msgs: continue
                        summary = f"📋 <b>BÁO CÁO CUỐI NGÀY (20:00)</b>\n━━━━━━━━━━━━━━━━━\n"
                        if len(msgs) > 10:
                            summary += f"Bạn có <b>{len(msgs)}</b> biến động trong ngày. Dưới đây là 10 thông báo mới nhất:\n"
                            msgs = msgs[-10:]
                        for m in msgs:
                            summary += f"• {m}\n"
                        summary += "━━━━━━━━━━━━━━━━━\n<i>TikTok/FB Checker V2</i>"
                        if self._bot:
                            await self._bot.send_message(tg_id, summary, parse_mode="HTML")
            except Exception as e:
                log.error("Lỗi trong _daily_summary_loop: %s", e)

            await asyncio.sleep(60) # Check every minute

    async def _remind_overdue_warranty(self):
        """5.6 Claim PENDING quá warranty_remind_hours (mặc định 12h) chưa xử lý
        → nhắc admin 1 lần qua bot admin."""
        import html as _html
        try:
            hours = int(db.get_setting("warranty_remind_hours", "12") or 12)
        except Exception:
            hours = 12
        claims = db.acc_claims_overdue(hours)
        if not claims:
            return
        lines = [f"⏰ <b>NHẮC: {len(claims)} BẢO HÀNH QUÁ {hours}H CHƯA XỬ LÝ</b>",
                 "━━━━━━━━━━━━", ""]
        for w in claims:
            w = dict(w)
            lines.append(
                f"#{w['id']} — đơn <b>#{w['order_id']}</b> — {_html.escape(w['cat_name'] or '')}\n"
                f"👤 UID <code>{_html.escape(w['uid'] or '')}</code> | "
                f"khách <code>{w['tg_id']}</code>\n"
                f"📅 Gửi lúc: {vn_time_str(ts=w['created_at'])}\n"
                f"Xử lý xong: <code>/bhdone {w['id']}</code>")
        db.acc_claim_mark_reminded([int(w["id"]) for w in claims])
        await self._send_admin_report("\n\n".join(lines))

    async def _send_admin_report(self, text: str) -> None:
        """Gửi báo cáo cho admin: ưu tiên admin bot, fallback bot chính."""
        sent = False
        try:
            from .admin_bot import manager as admin_manager
            admin_bot = admin_manager.bot
            admin_id = db.get_setting("admin_tg_id")
            if admin_bot and admin_id:
                await admin_bot.send_message(int(admin_id), text, parse_mode="HTML")
                sent = True
        except Exception as e:
            log.warning("Admin report via admin_bot failed: %s", e)
        if not sent:
            try:
                bot_inst = self._bot or getattr(botmod.manager, "bot", None)
                admin_id = db.get_setting("admin_tg_id")
                if bot_inst and admin_id:
                    await bot_inst.send_message(int(admin_id), text, parse_mode="HTML")
            except Exception as e:
                log.warning("Admin report via main bot failed: %s", e)

    async def _ops_loop(self):
        """Viec van hanh tu dong: bao cao sang 7h, qua sinh nhat 8h, quet gian lan moi gio."""
        while True:
            try:
                now_t = time.localtime()
                if now_t.tm_min < 5:
                    if now_t.tm_hour == 7:
                        await ops.morning_report()
                    elif now_t.tm_hour == 8:
                        await ops.birthday_job(getattr(self, '_bot', None))
                    await ops.fraud_scan()
            except Exception as e:
                log.exception("ops_loop error: %s", e)
            await asyncio.sleep(60)

    async def _maintenance_loop(self):
        """Job bảo trì: 3h sáng dọn cookie pool, 8h sáng báo cáo doanh thu."""
        while True:
            try:
                now_t = time.localtime()
                today = time.strftime("%Y-%m-%d", now_t)
                if now_t.tm_hour == 3 and db.get_setting("maint_cookie_clean") != today:
                    try:
                        await self._clean_cookie_pool()
                    finally:
                        db.set_setting("maint_cookie_clean", today)
                if now_t.tm_hour == 8 and db.get_setting("maint_revenue_report") != today:
                    try:
                        await self._send_revenue_report()
                    finally:
                        db.set_setting("maint_revenue_report", today)
                # 5.6 Nhắc claim bảo hành quá hạn chưa xử lý
                try:
                    await self._remind_overdue_warranty()
                except Exception as e:
                    log.warning("warranty remind: %s", e)
                # 5.13 Hỏi thăm sau 24h mua acc
                try:
                    await self._followup_orders()
                except Exception as e:
                    log.warning("followup: %s", e)
                # Cảm ơn + xin đánh giá sau khi mua ~90 phút
                try:
                    await self._nudge_reviews()
                except Exception as e:
                    log.warning("review nudge: %s", e)
                # 5.10 Cảnh báo acc nằm kho lâu
                try:
                    await self._warn_stale_stock()
                except Exception as e:
                    log.warning("stale stock: %s", e)
                # 5.5 Dọn kho định kỳ 2h sáng
                if now_t.tm_hour == 2 and db.get_setting("maint_clean_stock") != today:
                    try:
                        await self._clean_old_stock()
                    finally:
                        db.set_setting("maint_clean_stock", today)
                # 5.8 Backup kho sau khi dọn cookie (3h sáng)
                if now_t.tm_hour == 3 and db.get_setting("maint_stock_backup") != today:
                    try:
                        await self._backup_stock()
                    except Exception as e:
                        log.warning("backup stock: %s", e)
                    finally:
                        db.set_setting("maint_stock_backup", today)
                # Backup database mỗi đêm (4h sáng, giữ 7 bản gần nhất)
                if now_t.tm_hour == 4 and db.get_setting("maint_db_backup") != today:
                    try:
                        path = db.db_backup(keep=7)
                        if path:
                            log.info("db backup ok: %s", path)
                    except Exception as e:
                        log.warning("backup db: %s", e)
                    finally:
                        db.set_setting("maint_db_backup", today)
                # 5.4 Nhập kho tự động từ NCC (6h sáng)
                if now_t.tm_hour == 6 and db.get_setting("maint_supplier_import") != today:
                    try:
                        await self._auto_import_supplier()
                    except Exception as e:
                        log.warning("supplier import: %s", e)
                    finally:
                        db.set_setting("maint_supplier_import", today)
                # Re-check LIVE toàn bộ kho định kỳ (mặc định 3 ngày/lần, 3h sáng)
                try:
                    await self._maybe_stock_recheck(now_t, today)
                except Exception as e:
                    log.warning("stock recheck schedule: %s", e)
                # Thu hồi quyền admin phụ hết hạn tạm thời
                try:
                    await self._sweep_expired_admins()
                except Exception as e:
                    log.warning("admin expiry sweep: %s", e)
            except Exception as e:
                log.warning("maintenance loop: %s", e)
            await asyncio.sleep(300)

    async def _sweep_expired_admins(self):
        """Thu hồi quyền admin phụ đã hết hạn tạm thời, báo cả 2 bên."""
        rows = db.extra_admin_expired()
        if not rows:
            return
        from . import perms as _perms
        owner_id = _perms.super_id()
        for r in rows:
            aid = int(r["tg_id"])
            db.extra_admin_del(aid)
            try:
                db.admin_audit_add(owner_id, "Hệ thống", "thuhoi_admin_hethan",
                                   f"{aid} ({r.get('name') or ''})")
            except Exception:
                pass
            if self._bot:
                try:
                    await self._bot.send_message(
                        aid,
                        "⏳ <b>Thông báo từ shop</b>\n\n"
                        "Quyền quản trị tạm thời của bạn đã hết hạn.",
                        parse_mode="HTML")
                except Exception:
                    pass
                if owner_id and owner_id != aid:
                    try:
                        await self._bot.send_message(
                            owner_id,
                            f"⏳ Quyền admin tạm thời của <code>{aid}</code> "
                            f"({html.escape(r.get('name') or '')}) đã hết hạn, "
                            f"bot đã tự thu hồi.",
                            parse_mode="HTML")
                    except Exception:
                        pass
            log.info("admin expiry sweep: revoked %s", aid)

    async def _maybe_stock_recheck(self, now_t, today):
        """Lịch re-check LIVE toàn bộ kho: mỗi stock_recheck_days ngày
        (mặc định 3), chạy lúc stock_recheck_hour giờ (mặc định 3h sáng)."""
        try:
            days = int(db.get_setting("stock_recheck_days", "3") or 3)
            hour = int(db.get_setting("stock_recheck_hour", "3") or 3)
        except Exception:
            days, hour = 3, 3
        days = max(1, days)
        if now_t.tm_hour != hour % 24:
            return
        last = db.get_setting("stock_recheck_last", "") or ""
        if last:
            try:
                d0 = time.mktime(time.strptime(last, "%Y-%m-%d"))
                d1 = time.mktime(time.strptime(today, "%Y-%m-%d"))
                if (d1 - d0) < days * 86400 - 60:
                    return
            except Exception:
                pass
        db.set_setting("stock_recheck_last", today)
        await self._run_stock_recheck()

    async def _run_stock_recheck(self, manual=False):
        """Quét LIVE toàn bộ acc AVAILABLE trong kho.
        Acc DIE → cách ly khỏi kho bán + báo admin. Lỗi hạ tầng → bỏ qua."""
        try:
            rows = [dict(r) for r in db.get_conn().execute(
                "SELECT id, uid, cat_id FROM acc_stock WHERE status='AVAILABLE' "
                "ORDER BY id").fetchall()]
        except Exception as e:
            log.warning("stock recheck: không đọc được kho: %s", e)
            return
        total = len(rows)
        if not total:
            log.info("stock recheck: kho trống, bỏ qua")
            return
        log.info("stock recheck: bắt đầu quét %d acc (manual=%s)...", total, manual)
        die_statuses = {"dead", "disabled", "checkpoint", "checkpoint_282",
                        "checkpoint_956"}
        sem = asyncio.Semaphore(10)
        live_n, err_n = 0, 0
        die_ids, die_rows = [], []

        async def _one(r):
            async with sem:
                try:
                    res = await fb.check_uid(str(r["uid"]))
                    st = str(res.get("status") or "").lower()
                except Exception:
                    st = "error"
                return r, st

        try:
            batch_n = 100
            for i in range(0, total, batch_n):
                batch = rows[i:i + batch_n]
                for r, st in await asyncio.gather(*[_one(x) for x in batch]):
                    if st == "live":
                        live_n += 1
                    elif st in die_statuses:
                        die_ids.append(r["id"])
                        die_rows.append(r)
                    else:
                        err_n += 1
                await asyncio.sleep(2)
        except Exception as e:
            log.warning("stock recheck: lỗi khi quét: %s", e)
        if die_ids:
            try:
                db.acc_stock_quarantine(die_ids)
            except Exception as e:
                log.warning("stock recheck: cách ly lỗi: %s", e)
        try:
            by_cat = {}
            for r in die_rows:
                by_cat.setdefault(r["cat_id"], []).append(r["uid"])
            lines = ["🔄 <b>RE-CHECK KHO ĐỊNH KỲ</b>",
                     f"Đã quét: <b>{total}</b> acc | 🟢 Live: <b>{live_n}</b> | "
                     f"🗑 Die (cách ly): <b>{len(die_ids)}</b> | "
                     f"❓ Lỗi check: <b>{err_n}</b>"]
            for cid, uids in by_cat.items():
                try:
                    c = db.acc_category_get(cid)
                    name = c["name"] if c else f"#{cid}"
                except Exception:
                    name = f"#{cid}"
                show = ", ".join(f"<code>{html.escape(str(u))}</code>"
                                 for u in uids[:10])
                more = f" <i>(+{len(uids) - 10})</i>" if len(uids) > 10 else ""
                lines.append(f"📦 <b>{html.escape(name)}</b>: {show}{more}")
            if die_ids:
                lines.append("\n<i>Dọn hẳn: /xoadie [id_loại]</i>")
            await self._alert_admin("\n".join(lines))
        except Exception as e:
            log.warning("stock recheck: báo admin lỗi: %s", e)
        log.info("stock recheck: xong — live=%d die=%d err=%d",
                 live_n, len(die_ids), err_n)


    async def _followup_orders(self):
        """5.13 Hỏi thăm sau 24h mua acc: acc ổn không, cần BH không."""
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        import html as _html
        orders = db.acc_orders_need_followup()
        if not orders:
            return
        try:
            import app.bot as botmod
            bot = self._bot or getattr(botmod.manager, "bot", None)
        except Exception:
            bot = None
        if not bot:
            return
        for o in orders:
            o = dict(o)
            try:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👍 Acc ổn, cảm ơn!",
                                          callback_data=f"accok:{o['id']}")],
                    [InlineKeyboardButton(text="🛡 Acc có vấn đề — bảo hành",
                                          callback_data=f"accwarranty:{o['id']}")],
                ])
                await bot.send_message(
                    int(o["tg_id"]),
                    f"💬 <b>Chào bạn!</b>\n\n"
                    f"Acc <b>{_html.escape(o['cat_name'] or '')}</b> (đơn #{o['id']}) "
                    f"bạn mua hôm qua dùng ổn không?\n\n"
                    f"Nếu acc lỗi trong thời gian bảo hành, bấm nút bên dưới để được xử lý ngay nhé!",
                    parse_mode="HTML", reply_markup=kb)
                db.acc_order_mark_followup(o["id"])
            except Exception:
                continue

    async def _nudge_reviews(self):
        """Cảm ơn + xin đánh giá sau review_nudge_minutes (mặc định 90 phút) mua acc."""
        try:
            minutes = int(db.get_setting("review_nudge_minutes", "90") or 90)
        except Exception:
            minutes = 90
        orders = db.acc_orders_need_review_nudge(minutes)
        if not orders:
            return
        try:
            import app.bot as botmod
            bot = self._bot or getattr(botmod.manager, "bot", None)
        except Exception:
            bot = None
        if not bot:
            return
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        import html as _html
        for o in orders:
            o = dict(o)
            try:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⭐ Đánh giá ngay",
                                          callback_data=f"accreview:{o['id']}")],
                ])
                await bot.send_message(
                    int(o["tg_id"]),
                    f"🙏 <b>Cảm ơn bạn đã mua acc!</b>\n\n"
                    f"Acc <b>{_html.escape(o['cat_name'] or '')}</b> (đơn #{o['id']}) "
                    f"dùng có ổn không bạn?\n"
                    f"Cho shop xin 1 đánh giá nhé — đánh giá xong được tặng credits ngay!",
                    parse_mode="HTML", reply_markup=kb)
                db.acc_order_mark_review_nudged(o["id"])
            except Exception:
                continue

    async def _warn_stale_stock(self):
        """5.10 Cảnh báo acc nằm kho lâu chưa bán (mặc định 30 ngày)."""
        try:
            days = int(db.get_setting("stale_days", "30") or 30)
        except Exception:
            days = 30
        rows = db.acc_stale_stock(days, 20)
        if not rows:
            return
        import html as _html
        lines = [f"⏳ <b>ACC NẰM KHO LÂU (>{days} ngày chưa bán)</b>",
                 "━━━━━━━━━━━━━━━", ""]
        ids = []
        for r in rows:
            r = dict(r)
            ids.append(r["id"])
            d = time.strftime("%d/%m/%Y", time.localtime(r["added_at"] or 0))
            lines.append(
                f"• <code>{_html.escape(r['uid'] or '')}</code> — "
                f"{_html.escape(r['cat_name'] or '')} (nhập {d})")
        lines += ["", f"<i>Gợi ý: giảm giá xả kho bằng /gia, "
                     f"hoặc đưa vào hộp mù (/hopmu).</i>"]
        db.acc_mark_stale_warned(ids)
        await self._send_admin_report("\n".join(lines))

    async def _clean_old_stock(self):
        """5.5 Dọn kho định kỳ 2h sáng: acc tồn quá clean_stock_days mà die -> loại."""
        from . import fb as fb_mod
        try:
            days = int(db.get_setting("clean_stock_days", "60") or 60)
        except Exception:
            days = 60
        rows = db.acc_old_stock(days, 50)
        if not rows:
            return
        sem = asyncio.Semaphore(5)
        dead_ids = []

        async def _one(sid, uid):
            async with sem:
                try:
                    r = await fb_mod.check_uid(str(uid))
                    if str(r.get("status", "")).lower() in ("die", "dead"):
                        dead_ids.append(sid)
                except Exception:
                    pass

        await asyncio.gather(*[_one(r["id"], r["uid"]) for r in rows])
        for sid in dead_ids:
            db.acc_mark_status(sid, "DEAD")
        await self._send_admin_report(
            f"🧹 <b>DỌN KHO ĐỊNH KỲ (2h sáng)</b>\n"
            f"🔢 Đã quét: <b>{len(rows)}</b> acc tồn trên {days} ngày\n"
            f"🔴 Loại khỏi kho: <b>{len(dead_ids)}</b> acc die")

    async def _backup_stock(self):
        """5.8 Backup kho tự động: xuất xlsx toàn bộ acc AVAILABLE, giữ 7 bản."""
        import os
        from openpyxl import Workbook
        rows = db.get_conn().execute(
            "SELECT s.*, c.name cat_name FROM acc_stock s "
            "LEFT JOIN acc_categories c ON c.id=s.cat_id "
            "WHERE s.status='AVAILABLE' ORDER BY s.cat_id, s.id").fetchall()
        if not rows:
            return
        d = os.path.expanduser("~/workspace/fb-hoan-chinh/backups")
        os.makedirs(d, exist_ok=True)
        fn = time.strftime("stock_%Y%m%d_%H%M.xlsx", time.localtime())
        path = os.path.join(d, fn)
        wb = Workbook()
        ws = wb.active
        ws.title = "kho"
        ws.append(["loai", "uid", "mk", "ngay_tao", "mail_thay", "ghi_chu",
                   "2fa", "cookie", "token", "batch", "ngay_nhap"])
        for r in rows:
            r = dict(r)
            ws.append([r.get("cat_name"), r.get("uid"), r.get("password"),
                       r.get("created_date"), r.get("backup_mail"), r.get("note"),
                       r.get("totp"), r.get("cookie"), r.get("token"),
                       r.get("batch"),
                       time.strftime("%d/%m/%Y", time.localtime(r.get("added_at") or 0))])
        wb.save(path)
        files = sorted(f for f in os.listdir(d)
                       if f.startswith("stock_") and f.endswith(".xlsx"))
        for old in files[:-7]:
            try:
                os.remove(os.path.join(d, old))
            except Exception:
                pass
        await self._send_admin_report(
            f"💾 <b>BACKUP KHO (3h sáng)</b>\n"
            f"📦 Đã lưu <b>{len(rows)}</b> acc chưa bán vào file:\n<code>{fn}</code>")

    async def _auto_import_supplier(self):
        """5.4 Nhập kho tự động từ NCC mỗi sáng 6h."""
        url = db.get_setting("supplier_auto_url", "")
        if not url:
            return
        try:
            cat_id = int(db.get_setting("supplier_auto_cat", "0") or 0)
            sup_id = int(db.get_setting("supplier_auto_supplier", "0") or 0)
        except Exception:
            return
        cat = db.acc_category_get(cat_id) if cat_id else None
        if not cat:
            return
        import urllib.request
        import socket
        from urllib.parse import urlparse
        try:
            host = urlparse(url).hostname or ""
            ip = socket.gethostbyname(host)
            oc = [int(x) for x in ip.split(".")]
            if oc[0] in (10, 127) or (oc[0] == 172 and 16 <= oc[1] <= 31) \
                    or (oc[0] == 192 and oc[1] == 168) or ip.startswith("0."):
                raise ValueError("blocked private ip")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read(5 * 1024 * 1024)
            text = raw.decode("utf-8", errors="ignore")
        except Exception as e:
            await self._send_admin_report(
                f"⚠️ <b>Nhập kho NCC tự động thất bại:</b> không tải được file: {e}")
            return
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = [x.strip() for x in line.split("|")]
            while len(p) < 8:
                p.append("")
            rows.append({"uid": p[0], "password": p[1], "created_date": p[2],
                         "backup_mail": p[3], "note": p[4], "totp": p[5],
                         "cookie": p[6], "token": p[7]})
        if not rows:
            return
        batch = "Lô NCC " + time.strftime("%d/%m %H:%M", time.localtime())
        added, skipped = db.acc_stock_add_batch(cat_id, rows, batch=batch,
                                                supplier_id=sup_id)
        await self._send_admin_report(
            f"📥 <b>NHẬP KHO TỰ ĐỘNG TỪ NCC (6h sáng)</b>\n"
            f"📦 Loại: <b>{cat['name']}</b>\n"
            f"➕ Thêm: <b>{added}</b> acc | ⏭ Bỏ qua: {skipped}\n"
            f"📊 Tồn kho: <b>{db.acc_stock_count(cat_id)}</b> acc")

    async def _clean_cookie_pool(self):
        """Kiểm tra từng cookie trong pool bằng chính acc của nó; loại cookie chết."""
        from .fb import get_fb_cookie_pool, set_fb_cookie_pool, _check_with_cookie
        import re
        pool = get_fb_cookie_pool()
        if not pool:
            return
        log.info("Cookie maintenance: kiem tra %d cookie...", len(pool))
        sem = asyncio.Semaphore(5)

        async def _one(ck: str):
            async with sem:
                try:
                    m = re.search(r"c_user=(\d+)", ck)
                    uid = m.group(1) if m else "me"
                    res = await _check_with_cookie(uid, ck)
                    return (ck, res.get("status"))
                except Exception:
                    return (ck, "error")

        results = await asyncio.gather(*[_one(ck) for ck in pool])
        kept, removed = [], []
        for ck, status in results:
            if status == "live":
                kept.append(ck)
            else:
                removed.append(status)
        if len(kept) != len(pool):
            set_fb_cookie_pool(kept)
        # Thống kê chi tiết
        from collections import Counter
        cnt = Counter(removed)
        detail = ", ".join(f"{k}: {v}" for k, v in cnt.items()) or "—"
        await self._send_admin_report(
            f"🍪 <b>DỌN COOKIE POOL (3h sáng)</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🔢 Đã kiểm tra: <b>{len(pool)}</b>\n"
            f"🟢 Giữ lại: <b>{len(kept)}</b>\n"
            f"🔴 Đã loại: <b>{len(removed)}</b> ({detail})\n\n"
            f"<i>Chỉ giữ cookie còn LIVE để pool xoay vòng ổn định.</i>"
        )
        log.info("Cookie maintenance xong: giu %d, loai %d.", len(kept), len(removed))

    async def _send_revenue_report(self):
        """Báo cáo doanh thu hôm qua cho admin lúc 8h sáng."""
        y = time.localtime(time.time() - 86400)
        day_str = time.strftime("%d/%m/%Y", y)
        day_key = time.strftime("%Y-%m-%d", y)
        start = int(time.mktime(time.strptime(day_key + " 00:00", "%Y-%m-%d %H:%M")))
        end = start + 86400
        c = db.get_conn()
        revenue = c.execute(
            "SELECT COALESCE(SUM(amount),0) FROM txns WHERE ts>=? AND ts<? AND amount>0 "
            "AND (reason='bank_transfer' OR reason LIKE 'Admin topup%')",
            (start, end),
        ).fetchone()[0]
        new_users = c.execute(
            "SELECT COUNT(*) FROM tg_users WHERE created_at>=? AND created_at<?", (start, end)
        ).fetchone()[0]
        checks = c.execute(
            "SELECT COUNT(*) FROM check_stats WHERE checked_at>=? AND checked_at<?", (start, end)
        ).fetchone()[0]
        bulk_checks = c.execute(
            "SELECT COUNT(*) FROM check_history WHERE checked_at>=? AND checked_at<?", (start, end)
        ).fetchone()[0]
        top = c.execute(
            "SELECT tg_id, COALESCE(SUM(amount),0) s FROM txns WHERE ts>=? AND ts<? AND amount>0 "
            "AND (reason='bank_transfer' OR reason LIKE 'Admin topup%') "
            "GROUP BY tg_id ORDER BY s DESC LIMIT 5",
            (start, end),
        ).fetchall()
        lines = [
            f"📊 <b>BÁO CÁO NGÀY {day_str}</b>",
            "━━━━━━━━━━━━━━━",
            f"💰 Doanh thu nạp: <b>{int(revenue):,}đ</b>",
            f"👥 User mới: <b>{new_users}</b>",
            f"⚡ Lượt check đơn: <b>{checks}</b>",
            f"📦 Lượt check bulk: <b>{bulk_checks}</b>",
        ]
        if top:
            lines.append("")
            lines.append("🏆 <b>Top nạp tiền:</b>")
            for i, r in enumerate(top, 1):
                lines.append(f"{i}. <code>{r['tg_id']}</code> — {int(r['s']):,}đ")
        await self._send_admin_report("\n".join(lines))
        log.info("Revenue report sent for %s: %sđ", day_str, int(revenue))

    async def _campaign_scheduler_loop(self):
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        import json
        import os
        from aiogram.types import FSInputFile
        import time
        
        while True:
            try:
                now_ts = int(time.time())
                camps = [dict(c) for c in db.get_campaigns(status="pending")]
                
                # Lấy bot instance từ self._bot hoặc fallback sang manager.bot
                bot_inst = self._bot
                if not bot_inst:
                    try:
                        from .bot import manager
                        if manager and manager.bot:
                            bot_inst = manager.bot
                    except Exception:
                        pass
                
                if camps and not bot_inst:
                    log.warning("Campaign loop: Bot instance chưa sẵn sàng, chờ 5s...")
                    await asyncio.sleep(5)
                    continue

                for camp in camps:
                    if camp.get("scheduled_for", 0) > 0 and camp["scheduled_for"] > now_ts:
                        continue # Chưa tới giờ gửi

                    # Cập nhật trạng thái đang chạy
                    db.update_campaign_status(camp["id"], "running")

                    text = camp.get("text_content", "") or ""
                    image = camp.get("image_url", "")
                    photo_path = None
                    if image:
                        # Kiểm tra nhiều đường dẫn khả thi của ảnh
                        candidates = [
                            os.path.join(os.path.dirname(__file__), "..", "static", "images", image),
                            os.path.join(os.path.dirname(__file__), "static", "images", image),
                            os.path.join(os.getcwd(), "static", "images", image),
                            os.path.join(os.getcwd(), "app", "static", "images", image),
                        ]
                        for cand in candidates:
                            if os.path.exists(cand):
                                photo_path = cand
                                break

                    ctype = camp.get("type", "broadcast")
                    try:
                        config = json.loads(camp.get("config", "{}"))
                    except Exception:
                        config = {}

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
                            tg_id = int(u["tg_id"])
                            if photo_path:
                                await bot_inst.send_photo(
                                    tg_id,
                                    photo=FSInputFile(photo_path),
                                    caption=text if text else None,
                                    parse_mode="HTML" if text else None,
                                    reply_markup=kb
                                )
                            else:
                                await bot_inst.send_message(
                                    tg_id,
                                    text,
                                    parse_mode="HTML",
                                    reply_markup=kb
                                )
                            success_count += 1
                            await asyncio.sleep(0.05)
                        except Exception as e:
                            last_error = str(e)
                            log.error(f"Send campaign #{camp['id']} err for {u['tg_id']}: {e}")

                    stats = {"sent": success_count}
                    if last_error:
                        stats["error"] = last_error

                    db.update_campaign_stats(camp["id"], json.dumps(stats))
                    db.update_campaign_status(camp["id"], "finished")
                    log.info(f"Campaign #{camp['id']} finished! Sent to {success_count} users.")

            except Exception as e:
                log.error(f"Campaign scheduler error: {e}")

            await asyncio.sleep(5)


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
                    # Tặng credits kèm gia hạn tháng: 90% so với mua gói credit thẳng
                    bonus = botmod._sub_credit_bonus(price)
                    bonus_txt = ""
                    if bonus > 0:
                        try:
                            total_cr = db.add_credits(tg_id, bonus, "Tặng kèm gia hạn tháng (auto-renew)")
                            bonus_txt = f"\n🎁 Tặng kèm: <b>{bonus}</b> credits (tổng: {total_cr})"
                        except Exception:
                            pass
                    valid.append(t)
                    if self._bot:
                        try: await self._bot.send_message(tg_id, f"🔄 <b>Gia hạn tự động thành công!</b>\nHệ thống đã trừ <b>{price:,}đ</b> và gia hạn thêm 30 ngày sử dụng.{bonus_txt}", parse_mode="HTML")
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
                # Chế độ báo của watch: 'die_only' chỉ báo user khi chuyển sang DIE
                try:
                    alert_mode = w["alert_mode"] or "all"
                except Exception:
                    alert_mode = "all"
                notify_user = (alert_mode == "all") or (new_status == "die")
                bot = botmod.manager.bot
                if bot and notify_user:
                    try:
                        db.add_batch_notification(w["tg_id"], f"FB UID {w['uid']}: {old} ➡️ {new_status}")
                    except Exception as e:
                        db.add_log("system", f"Lỗi lưu batch_notification {w['tg_id']}: {e}")

                # Đa kênh cho admin: báo mọi lần đổi trạng thái qua admin bot + Zalo
                try:
                    await _notify_admin_watch_change(w["uid"], old, new_status, w["tg_id"])
                except Exception as e:
                    log.warning("Notify admin watch change: %s", e)

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
