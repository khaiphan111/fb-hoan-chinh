import asyncio
import logging
import time
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties

from . import config, db

log = logging.getLogger(__name__)

router = Router()

def get_admin_ids() -> list[int]:
    ids = []
    try:
        if db.get_setting("admin_tg_id"):
            ids.append(int(db.get_setting("admin_tg_id")))
    except: pass
    try:
        if db.get_setting("admin_tg_group_id"):
            ids.append(int(db.get_setting("admin_tg_group_id")))
    except: pass
    return ids

def is_admin(chat_id: int, user_id: int) -> bool:
    admins = get_admin_ids()
    return chat_id in admins or user_id in admins

def parse_time_str(time_str: str) -> int:
    """Parses time string like 2d, 12h, 1d12h to seconds"""
    total_seconds = 0
    import re
    
    days_match = re.search(r'(\d+)d', time_str)
    if days_match:
        total_seconds += int(days_match.group(1)) * 86400
        
    hours_match = re.search(r'(\d+)h', time_str)
    if hours_match:
        total_seconds += int(hours_match.group(1)) * 3600
        
    return total_seconds


@router.message(Command("phatcode"))
async def cmd_phatcode(msg: Message):
    if not is_admin(msg.chat.id, msg.from_user.id):
        return
        
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("❌ HDSD: /phatcode <số_tiền> [số_lượt_dùng=1] [hạn_sử_dụng=0]\nVí dụ: /phatcode 50000 10 12h (Mã 50k, 10 lượt, hạn 12 giờ)")
        return
        
    try:
        amount = int(parts[1])
        max_uses = 1
        expire_at = 0
        
        if len(parts) > 2:
            max_uses = int(parts[2])
            
        if len(parts) > 3:
            time_str = parts[3].lower()
            seconds = parse_time_str(time_str)
            if seconds > 0:
                expire_at = int(time.time()) + seconds
            elif time_str.isdigit():
                # fallback to days if just a number
                expire_at = int(time.time()) + int(time_str) * 86400
                
        code = db.generate_code(amount=amount, prefix="GLOBAL" if max_uses > 1 else "CODE", max_uses=max_uses, expire_at=expire_at)
        
        expire_text = "Vĩnh viễn"
        if expire_at > 0:
            from . import util
            expire_text = util.vn_time_str('%H:%M %d/%m/%Y', expire_at)
            
        await msg.answer(
            f"✅ <b>TẠO MÃ THÀNH CÔNG</b>\n\n"
            f"🎁 Mã code: <code>{code}</code>\n"
            f"💰 Giá trị: <b>{amount:,.0f} VNĐ</b>\n"
            f"👥 Số lượt dùng: <b>{max_uses}</b>\n"
            f"⏳ Hạn sử dụng: <b>{expire_text}</b>",
            parse_mode="HTML"
        )
    except Exception as e:
        await msg.answer(f"❌ Lỗi: {e}")


@router.message(Command("phatcodeall"))
async def cmd_phatcodeall(msg: Message):
    if not is_admin(msg.chat.id, msg.from_user.id):
        return
        
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("❌ HDSD: /phatcodeall <số_tiền> [hạn_sử_dụng=0]\nVí dụ: /phatcodeall 50000 1d12h (Mã 50k, hạn 1 ngày 12 giờ)")
        return
        
    try:
        amount = int(parts[1])
        expire_at = 0
        
        if len(parts) > 2:
            time_str = parts[2].lower()
            seconds = parse_time_str(time_str)
            if seconds > 0:
                expire_at = int(time.time()) + seconds
            elif time_str.isdigit():
                # fallback to days
                expire_at = int(time.time()) + int(time_str) * 86400
                
        # Get total users
        conn = db.get_conn()
        total_users = conn.execute("SELECT COUNT(tg_id) FROM tg_users").fetchone()[0]
        
        if total_users == 0:
            await msg.answer("❌ Hệ thống chưa có người dùng nào!")
            return
            
        code = db.generate_code(amount=amount, prefix="GIFT", max_uses=total_users, expire_at=expire_at)
        
        # Tell the main bot manager to broadcast
        from .bot import manager as main_bot_manager
        asyncio.create_task(broadcast_code_to_all(main_bot_manager, code, amount, expire_at))
        
        await msg.answer(f"✅ Đã tạo mã <code>{code}</code> và đang gửi thông báo tới {total_users} người dùng!", parse_mode="HTML")
    except Exception as e:
        await msg.answer(f"❌ Lỗi: {e}")

async def broadcast_code_to_all(main_bot_manager, code: str, amount: int, expire_at: int):
    # Fetch all users
    users = db.get_conn().execute("SELECT tg_id FROM tg_users").fetchall()
    
    expire_text = "Vĩnh viễn"
    if expire_at > 0:
        from . import util
        expire_text = util.vn_time_str('%H:%M %d/%m/%Y', expire_at)
        
    text = (
        f"🎉 <b>QUÀ TẶNG TỪ ADMIN</b> 🎉\n\n"
        f"🎁 Mã quà tặng: <code>{code}</code>\n"
        f"💰 Giá trị: <b>{amount:,.0f} VNĐ</b>\n"
        f"⏳ Hạn sử dụng: <b>{expire_text}</b>\n\n"
        f"<i>Nhanh tay sử dụng hoặc lưu trữ vào ví nhé!</i>"
    )
    
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Sử dụng ngay", callback_data=f"use_code_{code}")],
        [InlineKeyboardButton(text="📥 Lưu trữ", callback_data=f"save_code_{code}")]
    ])
    
    success = 0
    bot = main_bot_manager.bot
    if not bot:
        return
        
    for user in users:
        try:
            await bot.send_message(user["tg_id"], text, parse_mode="HTML", reply_markup=markup)
            success += 1
            await asyncio.sleep(0.05) # Prevent flood wait
        except:
            pass
            
    # Optionally notify admin bot back
    admin_bot = manager.bot
    if admin_bot:
        admins = get_admin_ids()
        for admin_id in admins:
            try:
                await admin_bot.send_message(admin_id, f"✅ Đã phát mã {code} tới {success} người dùng!")
            except: pass

@router.callback_query(F.data.startswith("tg_admin_confirm_"))
async def on_admin_confirm(cb: CallbackQuery):
    if not is_admin(cb.message.chat.id, cb.from_user.id):
        await cb.answer("❌ Bạn không có quyền duyệt!", show_alert=True)
        return
        
    parts = cb.data.split("_")
    user_id = int(parts[3])
    amount = int(parts[4])
    
    try:
        db.adjust_balance(user_id, amount, reason="bank_transfer")
        success = True
    except Exception as e:
        log.error(f"Lỗi cộng tiền: {e}")
        success = False
    if success:
        await cb.answer("✅ Đã cộng tiền thành công!", show_alert=True)
        try:
            await cb.message.edit_text(f"{cb.message.text}\n\n✅ <b>Đã duyệt {amount:,.0f} VNĐ bởi {cb.from_user.full_name}</b>", parse_mode="HTML")
        except: pass
        
        # Notify user via main bot
        from .bot import manager as main_bot_manager
        if main_bot_manager.bot:
            try:
                # Kiem tra VIP upgrade
                upgraded, new_vip, is_lifetime = db.check_vip_upgrade(user_id)
                msg_text = (
                    f"✅ <b>NẠP TIỀN THÀNH CÔNG</b>\n\n"
                    f"Bạn vừa được cộng <b>{amount:,.0f} VNĐ</b> vào tài khoản.\n"
                    f"Cảm ơn bạn đã sử dụng dịch vụ!"
                )
                await main_bot_manager.bot.send_message(user_id, msg_text, parse_mode="HTML")
                
                if upgraded or is_lifetime:
                    limit = db.get_setting(f"vip{new_vip}_limit", "10")
                    vip_msg = (
                        f"🎉 <b>CHÚC MỪNG BẠN ĐÃ LÊN VIP {new_vip}!</b> 🎉\n\n"
                        f"💎 <b>Quyền lợi mới:</b>\n"
                        f"- Theo dõi tối đa: <b>{limit} UID/Kênh</b>\n"
                    )
                    if is_lifetime:
                        vip_msg += "- Hạn sử dụng: <b>VĨNH VIỄN</b>\n\n"
                    else:
                        vip_msg += "\n"
                    vip_msg += "Cảm ơn bạn đã tin tưởng và sử dụng dịch vụ của chúng tôi! ❤️"
                    await main_bot_manager.bot.send_message(user_id, vip_msg, parse_mode="HTML")
            except: pass
    else:
        await cb.answer("❌ Lỗi khi cộng tiền!", show_alert=True)

@router.callback_query(F.data.startswith("tg_admin_withdraw_approve_"))
async def on_admin_withdraw_approve(cb: CallbackQuery):
    if not is_admin(cb.message.chat.id, cb.from_user.id):
        await cb.answer("❌ Bạn không có quyền duyệt!", show_alert=True)
        return
        
    parts = cb.data.split("_")
    # tg_admin_withdraw_approve_{req_id}_{tg_id}_{amount}
    req_id = int(parts[4])
    tg_id = int(parts[5])
    amount = int(parts[6])
    
    c = db.get_conn()
    if req_id == 0:
        req = c.execute("SELECT * FROM withdrawal_requests WHERE tg_id=? AND status='pending' ORDER BY id DESC LIMIT 1", (tg_id,)).fetchone()
    else:
        req = c.execute("SELECT * FROM withdrawal_requests WHERE id=?", (req_id,)).fetchone()
        
    if not req or req["status"] != "pending":
        await cb.answer("⚠️ Đơn này đã được xử lý từ trước!", show_alert=True)
        return
        
    actual_req_id = req["id"]
    with db._lock:
        c.execute("UPDATE withdrawal_requests SET status='approved', updated_at=? WHERE id=?", (int(time.time()), actual_req_id))
        c.execute("UPDATE tg_users SET ref_withdrawn = ref_withdrawn + ? WHERE tg_id=?", (amount, tg_id))
        c.commit()
        
    await cb.answer("✅ Đã duyệt đơn rút tiền thành công!", show_alert=True)
    try:
        await cb.message.edit_text(f"{cb.message.text}\n\n✅ <b>ĐÃ DUYỆT BỞI {cb.from_user.full_name} ({amount:,.0f} VNĐ)</b>", parse_mode="HTML")
    except: pass
    
    # Notify customer
    try:
        from .bot import manager as main_bot_manager
        cust_msg = (
            "🎉 <b>RÚT TIỀN HOA HỒNG THÀNH CÔNG!</b>\n\n"
            f"Yêu cầu rút tiền <b>#{actual_req_id}</b> của bạn đã được Admin duyệt và chuyển tiền.\n"
            f"💰 Số tiền: <b>{amount:,.0f} VNĐ</b>\n"
            f"🏦 Ngân hàng / STK: <b>{req.get('bank_info', '')}</b>\n\n"
            "Cảm ơn bạn đã đồng hành và phát triển cùng hệ thống! ❤️"
        )
        if main_bot_manager.bot:
            await main_bot_manager.bot.send_message(tg_id, cust_msg, parse_mode="HTML")
    except Exception as notify_err:
        log.error("Could not notify user of approved withdrawal: %s", notify_err)

@router.callback_query(F.data.startswith("tg_admin_withdraw_reject_"))
async def on_admin_withdraw_reject(cb: CallbackQuery):
    if not is_admin(cb.message.chat.id, cb.from_user.id):
        await cb.answer("❌ Bạn không có quyền từ chối!", show_alert=True)
        return
        
    parts = cb.data.split("_")
    req_id = int(parts[4])
    tg_id = int(parts[5])
    amount = int(parts[6])
    
    c = db.get_conn()
    if req_id == 0:
        req = c.execute("SELECT * FROM withdrawal_requests WHERE tg_id=? AND status='pending' ORDER BY id DESC LIMIT 1", (tg_id,)).fetchone()
    else:
        req = c.execute("SELECT * FROM withdrawal_requests WHERE id=?", (req_id,)).fetchone()
        
    if not req or req["status"] != "pending":
        await cb.answer("⚠️ Đơn này đã được xử lý từ trước!", show_alert=True)
        return
        
    actual_req_id = req["id"]
    with db._lock:
        c.execute("UPDATE withdrawal_requests SET status='rejected', updated_at=? WHERE id=?", (int(time.time()), actual_req_id))
        c.commit()
        
    await cb.answer("❌ Đã từ chối đơn rút tiền.", show_alert=True)
    try:
        await cb.message.edit_text(f"{cb.message.text}\n\n❌ <b>ĐÃ TỪ CHỐI BỞI {cb.from_user.full_name}</b>", parse_mode="HTML")
    except: pass
    
    # Notify customer
    try:
        from .bot import manager as main_bot_manager
        cust_msg = (
            "❌ <b>YÊU CẦU RÚT TIỀN BỊ TỪ CHỐI</b>\n\n"
            f"Yêu cầu rút tiền hoa hồng <b>#{actual_req_id}</b> ({amount:,.0f} VNĐ) của bạn đã bị Admin từ chối.\n"
            "Số dư hoa hồng của bạn vẫn được giữ nguyên.\n"
            "Vui lòng kiểm tra lại thông tin Ngân hàng / STK hoặc liên hệ Admin để được hỗ trợ."
        )
        if main_bot_manager.bot:
            await main_bot_manager.bot.send_message(tg_id, cust_msg, parse_mode="HTML")
    except Exception as notify_err:
        log.error("Could not notify user of rejected withdrawal: %s", notify_err)

class AdminBotManager:
    def __init__(self):
        self.bot = None
        self.dp = Dispatcher()
        self.dp.include_router(router)
        self.task = None
        self.running = False

    async def start(self):
        token = (db.get_setting("admin_bot_token") or "").strip()
        main_token = (db.get_setting("bot_token") or "").strip()
        if not token or token == main_token:
            log.info("Admin bot token not set or identical to main bot token. Admin bot polling disabled.")
            return
            
        self.bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"))
        self.running = True
        log.info("Admin Bot starting...")
        
        try:
            # Drop pending updates
            await self.bot.delete_webhook(drop_pending_updates=True)
            self.task = asyncio.create_task(self.dp.start_polling(self.bot))
        except Exception as e:
            log.error("Failed to start admin bot: %s", e)
            self.running = False

    async def stop(self):
        if self.running and self.bot:
            self.running = False
            log.info("Admin Bot stopping...")
            await self.bot.session.close()
            if self.task:
                self.task.cancel()
            self.bot = None

manager = AdminBotManager()


# ═══════════════════════════════════════════════════════════════════════════
# ▼▼▼  ADMIN PANEL NÂNG CAO — /adm <subcmd>  ▼▼▼
# Tất cả lệnh admin được gộp vào 1 handler /adm để dễ quản lý
# Logic xử lý dùng chung hàm _handle_adm_cmd() với bot.py
# ═══════════════════════════════════════════════════════════════════════════

def _user_info_text(user) -> str:
    """Format thông tin user thành chuỗi HTML."""
    from . import util
    import time
    user_dict = dict(user) if user else {}
    created = time.strftime("%d/%m/%Y %H:%M", time.localtime(user_dict.get("created_at") or 0))
    sub_until = user_dict.get("sub_until") or 0
    sub_text = "Không có" if not sub_until else ("Vĩnh viễn" if sub_until > 9000000000 else time.strftime("%d/%m/%Y", time.localtime(sub_until)))
    vip_labels = {0: "Thường", 1: "VIP 1 🥉", 2: "VIP 2 🥈", 3: "VIP 3 🥇"}
    blocked = "🔴 BỊ KHÓA" if user_dict.get("is_blocked") else "🟢 Hoạt động"
    return (
        f"👤 <b>THÔNG TIN USER</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 ID: <code>{user_dict.get('tg_id')}</code>\n"
        f"📛 Tên: <b>{user_dict.get('name') or '?'}</b>\n"
        f"@️ Username: @{user_dict.get('username') or 'Không có'}\n"
        f"💳 Số dư: <b>{util.vnd(user_dict.get('balance') or 0)}</b>\n"
        f"💰 Tổng nạp: <b>{util.vnd(user_dict.get('total_topup') or 0)}</b>\n"
        f"🎁 Hoa hồng: <b>{util.vnd(user_dict.get('ref_earnings') or 0)}</b>\n"
        f"⭐ VIP: <b>{vip_labels.get(user_dict.get('vip_level') or 0, '?')}</b>\n"
        f"📅 Hạn dùng: <b>{sub_text}</b>\n"
        f"📅 Ngày tham gia: {created}\n"
        f"🔒 Trạng thái: {blocked}\n"
    )


async def _handle_adm_cmd(msg: Message, bot_instance=None):
    """
    Xử lý tất cả sub-commands của /adm.
    Dùng chung cho cả admin_bot.py và bot.py.
    """
    from . import util
    tg_id = msg.from_user.id

    if not is_admin(msg.chat.id, tg_id):
        await msg.answer("🚫 Bạn không có quyền sử dụng lệnh này.")
        return

    parts = msg.text.split(maxsplit=2)
    # parts[0] = /adm, parts[1] = subcmd, parts[2+] = args
    if len(parts) < 2:
        await _show_adm_help(msg)
        return

    subcmd = parts[1].lower().strip()
    rest = parts[2].strip() if len(parts) > 2 else ""

    # ── /adm help ────────────────────────────────────────────────────────────
    if subcmd in ("help", "?"):
        await _show_adm_help(msg)

    # ── /adm topup <id> <tiền> ────────────────────────────────────────────
    elif subcmd == "topup":
        args = rest.split()
        if len(args) < 2:
            await msg.answer("❌ HDSD: /adm topup &lt;user_id&gt; &lt;số_tiền&gt;", parse_mode="HTML")
            return
        try:
            uid = int(args[0]); amount = int(args[1].replace(",","").replace("k","000").replace("K","000"))
        except ValueError:
            await msg.answer("❌ ID hoặc số tiền không hợp lệ!"); return
        user = db.get_user(uid)
        if not user:
            await msg.answer(f"❌ Không tìm thấy user <code>{uid}</code>!", parse_mode="HTML"); return
        db.adjust_balance(uid, amount, f"Admin topup by {tg_id}")
        await msg.answer(
            f"✅ Cộng <b>{util.vnd(amount)}</b> cho user <code>{uid}</code> ({user['name'] or '?'})\n"
            f"💳 Số dư mới: <b>{util.vnd(db.get_user(uid)['balance'])}</b>",
            parse_mode="HTML"
        )
        try:
            target_bot = bot_instance or msg.bot
            await target_bot.send_message(uid,
                f"✅ <b>NẠP TIỀN THÀNH CÔNG</b>\nBạn vừa được cộng <b>{util.vnd(amount)}</b> vào tài khoản.",
                parse_mode="HTML")
        except Exception: pass

    # ── /adm setbal <id> <tiền> ───────────────────────────────────────────
    elif subcmd == "setbal":
        args = rest.split()
        if len(args) < 2:
            await msg.answer("❌ HDSD: /adm setbal &lt;user_id&gt; &lt;số_tiền&gt;", parse_mode="HTML"); return
        try:
            uid = int(args[0]); amount = int(args[1].replace(",","").replace("k","000").replace("K","000"))
        except ValueError:
            await msg.answer("❌ Thông số không hợp lệ!"); return
        if db.admin_set_balance(uid, amount, f"Admin setbal by {tg_id}"):
            await msg.answer(f"✅ Đã set số dư user <code>{uid}</code> thành <b>{util.vnd(amount)}</b>", parse_mode="HTML")
        else:
            await msg.answer(f"❌ Không tìm thấy user <code>{uid}</code>!", parse_mode="HTML")

    # ── /adm ban <id> [lý_do] ─────────────────────────────────────────────
    elif subcmd == "ban":
        args = rest.split(maxsplit=1)
        if not args:
            await msg.answer("❌ HDSD: /adm ban &lt;user_id&gt; [lý_do]", parse_mode="HTML"); return
        try:
            uid = int(args[0])
        except ValueError:
            await msg.answer("❌ User ID không hợp lệ!"); return
        reason = args[1].strip() if len(args) > 1 else "Vi phạm quy định"
        if db.ban_user(uid):
            await msg.answer(f"🔴 Đã khoá tài khoản <code>{uid}</code>\nLý do: {reason}", parse_mode="HTML")
            try:
                target_bot = bot_instance or msg.bot
                await target_bot.send_message(uid,
                    f"🚫 <b>Tài khoản của bạn đã bị khóa.</b>\nLý do: {reason}\nLiên hệ Admin để được hỗ trợ.",
                    parse_mode="HTML")
            except Exception: pass
        else:
            await msg.answer(f"❌ Không tìm thấy user <code>{uid}</code>!", parse_mode="HTML")

    # ── /adm unban <id> ────────────────────────────────────────────────────
    elif subcmd == "unban":
        try:
            uid = int(rest.split()[0])
        except (ValueError, IndexError):
            await msg.answer("❌ HDSD: /adm unban &lt;user_id&gt;", parse_mode="HTML"); return
        if db.unban_user(uid):
            await msg.answer(f"🟢 Đã mở khoá tài khoản <code>{uid}</code>", parse_mode="HTML")
            try:
                target_bot = bot_instance or msg.bot
                await target_bot.send_message(uid,
                    "✅ <b>Tài khoản của bạn đã được mở khoá!</b>", parse_mode="HTML")
            except Exception: pass
        else:
            await msg.answer(f"❌ Không tìm thấy user <code>{uid}</code>!", parse_mode="HTML")

    # ── /adm setvip <id> <cấp> [ngày] ────────────────────────────────────
    elif subcmd == "setvip":
        args = rest.split()
        if len(args) < 2:
            await msg.answer("❌ HDSD: /adm setvip &lt;id&gt; &lt;cấp&gt; [ngày]\nVD: /adm setvip 123 2 30", parse_mode="HTML"); return
        try:
            uid = int(args[0]); vip = int(args[1]); days = int(args[2]) if len(args) > 2 else 0
        except ValueError:
            await msg.answer("❌ Thông số không hợp lệ!"); return
        if db.admin_set_vip(uid, vip, days):
            txt = f"✅ Đã set VIP {vip} cho <code>{uid}</code>"
            if days: txt += f" — gia hạn thêm <b>{days} ngày</b>"
            await msg.answer(txt, parse_mode="HTML")
            try:
                target_bot = bot_instance or msg.bot
                notif = f"🎉 <b>Tài khoản của bạn đã được nâng lên VIP {vip}!</b>"
                if days: notif += f"\n⏳ Gia hạn thêm {days} ngày."
                await target_bot.send_message(uid, notif, parse_mode="HTML")
            except Exception: pass
        else:
            await msg.answer(f"❌ Không tìm thấy user <code>{uid}</code>!", parse_mode="HTML")

    # ── /adm info <id> ─────────────────────────────────────────────────────
    elif subcmd == "info":
        try:
            uid = int(rest.split()[0])
        except (ValueError, IndexError):
            await msg.answer("❌ HDSD: /adm info &lt;user_id&gt;", parse_mode="HTML"); return
        user = db.get_user(uid)
        if not user:
            await msg.answer(f"❌ Không tìm thấy user <code>{uid}</code>!", parse_mode="HTML"); return
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="💰 Cộng tiền", callback_data=f"adm_topup_{uid}"),
            InlineKeyboardButton(text="🔴 Khoá" if not user["is_blocked"] else "🟢 Mở khoá",
                                 callback_data=f"adm_ban_{uid}" if not user["is_blocked"] else f"adm_unban_{uid}"),
        ]])
        await msg.answer(_user_info_text(user), parse_mode="HTML", reply_markup=kb)

    # ── /adm find <@username> ─────────────────────────────────────────────
    elif subcmd == "find":
        if not rest:
            await msg.answer("❌ HDSD: /adm find &lt;@username&gt;", parse_mode="HTML"); return
        user = db.find_user_by_username(rest.strip())
        if user:
            await msg.answer(_user_info_text(user), parse_mode="HTML")
        else:
            await msg.answer(f"❌ Không tìm thấy user <b>{rest}</b>!", parse_mode="HTML")

    # ── /adm pending ────────────────────────────────────────────────────────
    elif subcmd == "pending":
        c = db.get_conn()
        rows = c.execute(
            "SELECT wr.*, u.username, u.name FROM withdrawal_requests wr "
            "LEFT JOIN tg_users u ON wr.tg_id = u.tg_id "
            "WHERE wr.status='pending' ORDER BY wr.created_at DESC LIMIT 20"
        ).fetchall()
        if not rows:
            await msg.answer("✅ Không có đơn rút tiền nào đang chờ xử lý."); return
        text = f"📋 <b>ĐƠN RÚT TIỀN CHỜ XỬ LÝ ({len(rows)} đơn)</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        for r in rows:
            name = r["name"] or f"ID {r['tg_id']}"
            text += f"🔸 <b>Đơn #{r['id']}</b> — {name}\n   💰 {util.vnd(r['amount'])} → <code>{r.get('bank_info','?')}</code>\n\n"
        await msg.answer(text, parse_mode="HTML")

    # ── /adm revenue ─────────────────────────────────────────────────────────
    elif subcmd == "revenue":
        stats = db.get_revenue_stats()
        await msg.answer(
            "📈 <b>DOANH THU & HỆ THỐNG</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 Tổng User: <b>{stats['total_users']:,}</b>\n"
            f"🆕 User mới hôm nay: <b>{stats['new_today']}</b>\n"
            f"🔥 Active hôm nay: <b>{stats['active_today']}</b>\n\n"
            f"💰 Hôm nay: <b>{util.vnd(stats['revenue_today'])}</b>\n"
            f"💰 Tháng này: <b>{util.vnd(stats['revenue_month'])}</b>\n"
            f"💰 Tổng: <b>{util.vnd(stats['revenue_total'])}</b>",
            parse_mode="HTML"
        )

    # ── /adm stats ──────────────────────────────────────────────────────────
    elif subcmd == "stats":
        stats = db.get_revenue_stats()
        c = db.get_conn()
        vip_counts = {i: c.execute("SELECT COUNT(*) as c FROM tg_users WHERE vip_level=?", (i,)).fetchone()["c"] for i in range(4)}
        alert_count = c.execute("SELECT COUNT(*) as c FROM alert_rules WHERE is_active=1").fetchone()["c"]
        await msg.answer(
            "🖥 <b>TỔNG QUAN HỆ THỐNG</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 Tổng User: <b>{stats['total_users']:,}</b>\n"
            f"• Mới hôm nay: {stats['new_today']} | Active: {stats['active_today']}\n"
            f"• Free: {vip_counts[0]} | VIP1: {vip_counts[1]} | VIP2: {vip_counts[2]} | VIP3: {vip_counts[3]}\n\n"
            f"💰 Hôm nay: <b>{util.vnd(stats['revenue_today'])}</b>\n"
            f"💰 Tháng: <b>{util.vnd(stats['revenue_month'])}</b>\n"
            f"💰 Tổng: <b>{util.vnd(stats['revenue_total'])}</b>\n\n"
            f"🔔 Alerts hoạt động: <b>{alert_count}</b>",
            parse_mode="HTML"
        )

    # ── /adm broadcast [vip|inactive] <tin> ──────────────────────────────────
    elif subcmd == "broadcast":
        bc_parts = rest.split(maxsplit=1)
        vip_only = False
        inactive_days = 0
        text_content = ""
        if bc_parts and bc_parts[0].lower() == "vip":
            vip_only = True
            text_content = bc_parts[1].strip() if len(bc_parts) > 1 else ""
        elif bc_parts and bc_parts[0].lower() == "inactive":
            inactive_days = 7
            text_content = bc_parts[1].strip() if len(bc_parts) > 1 else ""
        else:
            text_content = rest
        if not text_content:
            await msg.answer(
                "❌ HDSD:\n/adm broadcast &lt;tin&gt;\n/adm broadcast vip &lt;tin&gt;\n/adm broadcast inactive &lt;tin&gt;",
                parse_mode="HTML"); return
        users = db.get_all_users_for_broadcast(vip_only=vip_only, inactive_days=inactive_days)
        total = len(users)
        target_label = "VIP" if vip_only else ("inactive 7 ngày" if inactive_days else "tất cả")
        _pending_broadcasts[msg.chat.id] = {
            "text": text_content,
            "users": [u["tg_id"] for u in users],
            "bot": bot_instance,
        }
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"✅ Gửi ngay ({total} user)", callback_data="adm_bcast_confirm"),
            InlineKeyboardButton(text="❌ Hủy", callback_data="adm_bcast_cancel"),
        ]])
        await msg.answer(
            f"📢 Gửi tới <b>{total} user</b> ({target_label}):\n<i>{text_content[:300]}</i>\n\nXác nhận?",
            parse_mode="HTML", reply_markup=kb)

    # ── /adm promo <prefix> <tiền> [lượt] [hạn] ──────────────────────────────
    elif subcmd == "promo":
        args = rest.split()
        if len(args) < 2:
            await msg.answer("❌ HDSD: /adm promo &lt;prefix&gt; &lt;tiền&gt; [lượt] [hạn]\nVD: /adm promo SALE 50000 100 24h", parse_mode="HTML"); return
        try:
            prefix = args[0].upper()
            amount = int(args[1].replace(",","").replace("k","000").replace("K","000"))
            max_uses = int(args[2]) if len(args) > 2 else 1
            expire_at = 0
            if len(args) > 3:
                secs = parse_time_str(args[3].lower())
                if secs > 0:
                    expire_at = int(time.time()) + secs
        except (ValueError, IndexError):
            await msg.answer("❌ Thông số không hợp lệ!"); return
        code = db.generate_code(amount=amount, prefix=prefix, max_uses=max_uses, expire_at=expire_at)
        expire_text = "Vĩnh viễn" if not expire_at else util.vn_time_str('%H:%M %d/%m/%Y', expire_at)
        await msg.answer(
            f"✅ <b>TẠO MÃ KHUYẾN MÃI THÀNH CÔNG</b>\n\n"
            f"🎁 Mã: <code>{code}</code>\n"
            f"💰 Giá trị: <b>{util.vnd(amount)}</b>\n"
            f"👥 Số lượt dùng: <b>{max_uses}</b>\n"
            f"⏳ Hạn: <b>{expire_text}</b>",
            parse_mode="HTML")

    else:
        await msg.answer(f"❓ Không hiểu sub-command: <code>{subcmd}</code>\nGõ /adm help để xem danh sách.", parse_mode="HTML")


async def _show_adm_help(msg: Message):
    """Hiển thị bảng hướng dẫn chi tiết các lệnh admin /adm."""
    help_text = (
        "🛠 <b>HƯỚNG DẪN CÁC LỆNH ADMIN — /adm &lt;subcmd&gt;</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"

        "<b>👤 QUẢN LÝ TÀI KHOẢN USER</b>\n"
        "• <code>/adm topup &lt;id&gt; &lt;tiền&gt;</code>\n"
        "  👉 <i>Cộng tiền nạp (tính vào tổng nạp &amp; tự động nâng VIP).</i>\n"
        "  💡 VD: <code>/adm topup 123456789 50000</code>\n\n"

        "• <code>/adm setbal &lt;id&gt; &lt;tiền&gt;</code>\n"
        "  👉 <i>Set số dư tài khoản trực tiếp.</i>\n"
        "  💡 VD: <code>/adm setbal 123456789 100000</code>\n\n"

        "• <code>/adm setvip &lt;id&gt; &lt;cấp&gt; [ngày]</code>\n"
        "  👉 <i>Nâng cấp VIP (1, 2, 3) và gia hạn thêm số ngày sử dụng.</i>\n"
        "  💡 VD: <code>/adm setvip 123456789 2 30</code>\n\n"

        "• <code>/adm ban &lt;id&gt; [lý_do]</code>\n"
        "  👉 <i>Khoá tài khoản user, chặn sử dụng bot.</i>\n"
        "  💡 VD: <code>/adm ban 123456789 Vi phạm quy định</code>\n\n"

        "• <code>/adm unban &lt;id&gt;</code>\n"
        "  👉 <i>Mở khoá tài khoản cho user.</i>\n"
        "  💡 VD: <code>/adm unban 123456789</code>\n\n"

        "• <code>/adm info &lt;id&gt;</code>\n"
        "  👉 <i>Xem toàn bộ thông tin chi tiết user theo Telegram ID.</i>\n"
        "  💡 VD: <code>/adm info 123456789</code>\n\n"

        "• <code>/adm find &lt;@username&gt;</code>\n"
        "  👉 <i>Tìm thông tin user theo username Telegram.</i>\n"
        "  💡 VD: <code>/adm find @khaitradecoin</code>\n\n"

        "<b>📊 BÁO CÁO &amp; THỐNG KÊ</b>\n"
        "• <code>/adm revenue</code> — <i>Báo cáo doanh thu nạp tiền (Hôm nay, Tháng, Tổng).</i>\n"
        "• <code>/adm stats</code> — <i>Thống kê tổng quan hệ thống (User, VIP, Active).</i>\n"
        "• <code>/adm pending</code> — <i>Danh sách đơn rút tiền hoa hồng chờ duyệt.</i>\n\n"

        "<b>📢 MARKETING &amp; TẠO MÃ</b>\n"
        "• <code>/adm broadcast &lt;tin_nhắn&gt;</code>\n"
        "  👉 <i>Gửi thông báo tới toàn bộ người dùng.</i>\n"
        "  💡 VD: <code>/adm broadcast Nâng cấp hệ thống 15p</code>\n\n"

        "• <code>/adm broadcast vip &lt;tin_nhắn&gt;</code>\n"
        "  👉 <i>Gửi thông báo riêng cho thành viên VIP (VIP > 0).</i>\n\n"

        "• <code>/adm broadcast inactive &lt;tin_nhắn&gt;</code>\n"
        "  👉 <i>Gửi thông báo cho user không hoạt động 7 ngày qua.</i>\n\n"

        "• <code>/adm promo &lt;prefix&gt; &lt;tiền&gt; [lượt] [hạn]</code>\n"
        "  👉 <i>Tạo mã quà tặng/Giftcode cho user nhập qua /code.</i>\n"
        "  💡 VD: <code>/adm promo SALE 50000 100 24h</code>\n\n"

        "<i>Chỉ Admin ID được cấp phép mới sử dụng được các lệnh này.</i>"
    )
    await msg.answer(help_text, parse_mode="HTML")


_pending_broadcasts = {}


@router.message(Command("adm"))
async def admin_bot_adm(msg: Message):
    """Handler /adm trong admin_bot — chuyển tới _handle_adm_cmd."""
    await _handle_adm_cmd(msg, bot_instance=manager.bot)


@router.callback_query(F.data == "adm_bcast_confirm")
async def on_bcast_confirm(cb: CallbackQuery):
    if not is_admin(cb.message.chat.id, cb.from_user.id):
        return
    bcast = _pending_broadcasts.pop(cb.message.chat.id, None)
    if not bcast:
        await cb.answer("Đã hết hạn, vui lòng thực hiện lại!", show_alert=True); return
    await cb.answer("Đang gửi...", show_alert=False)
    await cb.message.edit_reply_markup(reply_markup=None)
    target_bot = bcast.get("bot") or manager.bot
    if not target_bot:
        await cb.message.answer("❌ Bot chưa khởi động!"); return
    success = fail = 0
    for tg_id in bcast["users"]:
        try:
            await target_bot.send_message(tg_id, bcast["text"], parse_mode="HTML")
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            fail += 1
    await cb.message.answer(f"✅ <b>Broadcast xong!</b>\n✔ {success} thành công | ✘ {fail} lỗi", parse_mode="HTML")


@router.callback_query(F.data == "adm_bcast_cancel")
async def on_bcast_cancel(cb: CallbackQuery):
    _pending_broadcasts.pop(cb.message.chat.id, None)
    await cb.answer("Đã hủy.", show_alert=True)
    await cb.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("adm_ban_"))
async def on_adm_ban_cb(cb: CallbackQuery):
    if not is_admin(cb.message.chat.id, cb.from_user.id): return
    uid = int(cb.data.split("_")[-1])
    db.ban_user(uid)
    await cb.answer(f"Đã khoá {uid}", show_alert=True)
    await cb.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("adm_unban_"))
async def on_adm_unban_cb(cb: CallbackQuery):
    if not is_admin(cb.message.chat.id, cb.from_user.id): return
    uid = int(cb.data.split("_")[-1])
    db.unban_user(uid)
    await cb.answer(f"Đã mở khoá {uid}", show_alert=True)
    await cb.message.edit_reply_markup(reply_markup=None)

def _user_info_text(user) -> str:
    """Format thông tin user thành chuỗi HTML."""
    from . import util
    import time
    user_dict = dict(user) if user else {}
    created = time.strftime("%d/%m/%Y %H:%M", time.localtime(user_dict.get("created_at") or 0))
    sub_until = user_dict.get("sub_until") or 0
    sub_text = "Không có" if not sub_until else ("Vĩnh viễn" if sub_until > 9000000000 else time.strftime("%d/%m/%Y", time.localtime(sub_until)))
    vip_labels = {0: "Thường", 1: "VIP 1 🥉", 2: "VIP 2 🥈", 3: "VIP 3 🥇"}
    blocked = "🔴 BỊ KHÓA" if user_dict.get("is_blocked") else "🟢 Hoạt động"
    return (
        f"👤 <b>THÔNG TIN USER</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 ID: <code>{user_dict.get('tg_id')}</code>\n"
        f"📛 Tên: <b>{user_dict.get('name') or '?'}</b>\n"
        f"@️ Username: @{user_dict.get('username') or 'Không có'}\n"
        f"💳 Số dư: <b>{util.vnd(user_dict.get('balance') or 0)}</b>\n"
        f"💰 Tổng nạp: <b>{util.vnd(user_dict.get('total_topup') or 0)}</b>\n"
        f"🎁 Hoa hồng: <b>{util.vnd(user_dict.get('ref_earnings') or 0)}</b>\n"
        f"⭐ VIP: <b>{vip_labels.get(user_dict.get('vip_level') or 0, '?')}</b>\n"
        f"📅 Hạn dùng: <b>{sub_text}</b>\n"
        f"📅 Ngày tham gia: {created}\n"
        f"🔒 Trạng thái: {blocked}\n"
    )


@router.message(Command("topup"))
async def cmd_topup(msg: Message):
    """Cộng tiền thẳng cho user."""
    if not is_admin(msg.chat.id, msg.from_user.id):
        return
    parts = msg.text.split()
    if len(parts) < 3:
        await msg.answer("❌ HDSD: /topup &lt;user_id&gt; &lt;số_tiền&gt;\nVí dụ: /topup 123456789 100000", parse_mode="HTML")
        return
    try:
        user_id = int(parts[1])
        amount = int(parts[2].replace(",", "").replace("k", "000").replace("K", "000"))
    except ValueError:
        await msg.answer("❌ ID hoặc số tiền không hợp lệ!")
        return

    user = db.get_user(user_id)
    if not user:
        await msg.answer(f"❌ Không tìm thấy user ID <code>{user_id}</code>!", parse_mode="HTML")
        return

    from .util import vnd
    db.adjust_balance(user_id, amount, f"Admin topup by {msg.from_user.id}")
    await msg.answer(
        f"✅ Đã cộng <b>{vnd(amount)}</b> cho user <code>{user_id}</code> ({user['name'] or '?'})\n"
        f"💳 Số dư mới: <b>{vnd(db.get_user(user_id)['balance'])}</b>",
        parse_mode="HTML"
    )
    # Notify user
    try:
        from .bot import manager as main_bot
        if main_bot.bot:
            await main_bot.bot.send_message(
                user_id,
                f"✅ <b>NẠP TIỀN THÀNH CÔNG</b>\n\nBạn vừa được cộng <b>{vnd(amount)}</b> vào tài khoản.\nCảm ơn bạn đã sử dụng dịch vụ!",
                parse_mode="HTML"
            )
    except Exception:
        pass


@router.message(Command("setbal"))
async def cmd_setbal(msg: Message):
    """Set cứng số dư cho user."""
    if not is_admin(msg.chat.id, msg.from_user.id):
        return
    parts = msg.text.split()
    if len(parts) < 3:
        await msg.answer("❌ HDSD: /setbal &lt;user_id&gt; &lt;số_tiền&gt;\nVí dụ: /setbal 123456789 0", parse_mode="HTML")
        return
    try:
        user_id = int(parts[1])
        amount = int(parts[2].replace(",", "").replace("k", "000").replace("K", "000"))
    except ValueError:
        await msg.answer("❌ ID hoặc số tiền không hợp lệ!")
        return

    from .util import vnd
    if db.admin_set_balance(user_id, amount, f"Admin setbal by {msg.from_user.id}"):
        await msg.answer(f"✅ Đã set số dư user <code>{user_id}</code> thành <b>{vnd(amount)}</b>", parse_mode="HTML")
    else:
        await msg.answer(f"❌ Không tìm thấy user <code>{user_id}</code>!", parse_mode="HTML")


@router.message(Command("ban"))
async def cmd_ban(msg: Message):
    """Khoá tài khoản user."""
    if not is_admin(msg.chat.id, msg.from_user.id):
        return
    parts = msg.text.split(maxsplit=2)
    if len(parts) < 2:
        await msg.answer("❌ HDSD: /ban &lt;user_id&gt; [lý_do]", parse_mode="HTML")
        return
    try:
        user_id = int(parts[1])
    except ValueError:
        await msg.answer("❌ User ID không hợp lệ!")
        return

    reason = parts[2].strip() if len(parts) > 2 else "Vi phạm quy định"
    if db.ban_user(user_id):
        await msg.answer(f"🔴 Đã khoá tài khoản <code>{user_id}</code>\n📝 Lý do: {reason}", parse_mode="HTML")
        try:
            from .bot import manager as main_bot
            if main_bot.bot:
                await main_bot.bot.send_message(
                    user_id,
                    f"🚫 <b>Tài khoản của bạn đã bị khóa.</b>\nLý do: {reason}\nVui lòng liên hệ Admin để được hỗ trợ.",
                    parse_mode="HTML"
                )
        except Exception:
            pass
    else:
        await msg.answer(f"❌ Không tìm thấy user <code>{user_id}</code>!", parse_mode="HTML")


@router.message(Command("unban"))
async def cmd_unban(msg: Message):
    """Mở khoá tài khoản user."""
    if not is_admin(msg.chat.id, msg.from_user.id):
        return
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("❌ HDSD: /unban &lt;user_id&gt;", parse_mode="HTML")
        return
    try:
        user_id = int(parts[1])
    except ValueError:
        await msg.answer("❌ User ID không hợp lệ!")
        return

    if db.unban_user(user_id):
        await msg.answer(f"🟢 Đã mở khoá tài khoản <code>{user_id}</code>", parse_mode="HTML")
        try:
            from .bot import manager as main_bot
            if main_bot.bot:
                await main_bot.bot.send_message(user_id, "✅ <b>Tài khoản của bạn đã được mở khoá!</b> Bạn có thể tiếp tục sử dụng bot.", parse_mode="HTML")
        except Exception:
            pass
    else:
        await msg.answer(f"❌ Không tìm thấy user <code>{user_id}</code>!", parse_mode="HTML")


@router.message(Command("setvip"))
async def cmd_setvip(msg: Message):
    """Tặng/set VIP cho user."""
    if not is_admin(msg.chat.id, msg.from_user.id):
        return
    parts = msg.text.split()
    if len(parts) < 3:
        await msg.answer(
            "❌ HDSD: /setvip &lt;user_id&gt; &lt;cấp_vip&gt; [số_ngày]\n"
            "Ví dụ: /setvip 123456789 2 30 — Set VIP 2, gia hạn thêm 30 ngày",
            parse_mode="HTML"
        )
        return
    try:
        user_id = int(parts[1])
        vip_level = int(parts[2])
        days = int(parts[3]) if len(parts) > 3 else 0
    except ValueError:
        await msg.answer("❌ Thông số không hợp lệ!")
        return

    if db.admin_set_vip(user_id, vip_level, days):
        user = db.get_user(user_id)
        msg_text = f"✅ Đã set VIP {vip_level} cho user <code>{user_id}</code>"
        if days > 0:
            msg_text += f" — gia hạn thêm <b>{days} ngày</b>"
        await msg.answer(msg_text, parse_mode="HTML")
        try:
            from .bot import manager as main_bot
            if main_bot.bot:
                notif = f"🎉 <b>Tài khoản của bạn đã được nâng lên VIP {vip_level}!</b>"
                if days > 0:
                    notif += f"\n⏳ Gia hạn thêm {days} ngày."
                await main_bot.bot.send_message(user_id, notif, parse_mode="HTML")
        except Exception:
            pass
    else:
        await msg.answer(f"❌ Không tìm thấy user <code>{user_id}</code>!", parse_mode="HTML")


@router.message(Command("info"))
async def cmd_info(msg: Message):
    """Xem thông tin đầy đủ của một user."""
    if not is_admin(msg.chat.id, msg.from_user.id):
        return
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("❌ HDSD: /info &lt;user_id&gt;", parse_mode="HTML")
        return
    try:
        user_id = int(parts[1])
    except ValueError:
        await msg.answer("❌ User ID không hợp lệ!")
        return

    user = db.get_user(user_id)
    if not user:
        await msg.answer(f"❌ Không tìm thấy user <code>{user_id}</code>!", parse_mode="HTML")
        return

    text = _user_info_text(user)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💰 Cộng tiền", callback_data=f"adm_topup_{user_id}"),
            InlineKeyboardButton(text="🔴 Khoá" if not user["is_blocked"] else "🟢 Mở khoá",
                                 callback_data=f"adm_ban_{user_id}" if not user["is_blocked"] else f"adm_unban_{user_id}"),
        ]
    ])
    await msg.answer(text, parse_mode="HTML", reply_markup=kb)


@router.message(Command("find"))
async def cmd_find(msg: Message):
    """Tìm kiếm user theo username."""
    if not is_admin(msg.chat.id, msg.from_user.id):
        return
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer("❌ HDSD: /find &lt;@username hoặc tên&gt;", parse_mode="HTML")
        return

    query = parts[1].strip()
    user = db.find_user_by_username(query)
    if user:
        await msg.answer(_user_info_text(user), parse_mode="HTML")
    else:
        await msg.answer(f"❌ Không tìm thấy user với username <b>{query}</b>!", parse_mode="HTML")


@router.message(Command("pending"))
async def cmd_pending(msg: Message):
    """Xem danh sách các giao dịch nạp tiền đang chờ xử lý."""
    if not is_admin(msg.chat.id, msg.from_user.id):
        return

    c = db.get_conn()
    rows = c.execute(
        "SELECT wr.*, u.username, u.name FROM withdrawal_requests wr "
        "LEFT JOIN tg_users u ON wr.tg_id = u.tg_id "
        "WHERE wr.status='pending' ORDER BY wr.created_at DESC LIMIT 20"
    ).fetchall()

    if not rows:
        await msg.answer("✅ Không có đơn rút tiền nào đang chờ xử lý.")
        return

    from . import util
    text = f"📋 <b>CÁC ĐƠN RÚT TIỀN ĐANG CHỜ ({len(rows)} đơn)</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for r in rows:
        name = r["name"] or f"ID {r['tg_id']}"
        text += (
            f"🔸 <b>Đơn #{r['id']}</b> — {name}\n"
            f"   💰 {util.vnd(r['amount'])} → <code>{r.get('bank_info', '?')}</code>\n\n"
        )
    await msg.answer(text, parse_mode="HTML")


@router.message(Command("revenue"))
async def cmd_revenue(msg: Message):
    """Xem doanh thu theo ngày/tháng."""
    if not is_admin(msg.chat.id, msg.from_user.id):
        return

    from . import util
    stats = db.get_revenue_stats()
    text = (
        "📈 <b>THỐNG KÊ DOANH THU & HỆ THỐNG</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>Tổng User:</b> {stats['total_users']:,}\n"
        f"🆕 <b>User mới hôm nay:</b> {stats['new_today']}\n"
        f"🔥 <b>User active hôm nay:</b> {stats['active_today']}\n\n"
        f"💰 <b>Doanh thu hôm nay:</b> {util.vnd(stats['revenue_today'])}\n"
        f"💰 <b>Doanh thu tháng này:</b> {util.vnd(stats['revenue_month'])}\n"
        f"💰 <b>Tổng doanh thu:</b> {util.vnd(stats['revenue_total'])}\n"
    )
    await msg.answer(text, parse_mode="HTML")


@router.message(Command("broadcast"))
async def cmd_broadcast(msg: Message):
    """Gửi thông báo hàng loạt tới user."""
    if not is_admin(msg.chat.id, msg.from_user.id):
        return

    parts = msg.text.split(maxsplit=2)
    if len(parts) < 2:
        await msg.answer(
            "❌ HDSD:\n"
            "/broadcast &lt;tin nhắn&gt; — Gửi tất cả user\n"
            "/broadcast vip &lt;tin nhắn&gt; — Gửi user VIP\n"
            "/broadcast inactive &lt;tin nhắn&gt; — Gửi user không dùng 7+ ngày",
            parse_mode="HTML"
        )
        return

    target_type = parts[1].strip().lower()
    vip_only = False
    inactive_days = 0
    text_content = ""

    if target_type == "vip" and len(parts) > 2:
        vip_only = True
        text_content = parts[2].strip()
    elif target_type == "inactive" and len(parts) > 2:
        inactive_days = 7
        text_content = parts[2].strip()
    else:
        # No modifier — use full text from part[1] onwards
        text_content = msg.text.split(maxsplit=1)[1].strip()

    if not text_content:
        await msg.answer("❌ Nội dung tin nhắn không được để trống!")
        return

    users = db.get_all_users_for_broadcast(vip_only=vip_only, inactive_days=inactive_days)
    total = len(users)

    target_label = "VIP" if vip_only else ("không active 7 ngày" if inactive_days else "tất cả")
    confirm_text = (
        f"📢 Chuẩn bị gửi broadcast tới <b>{total} user</b> ({target_label}):\n\n"
        f"<i>{text_content[:300]}</i>\n\n"
        "Xác nhận gửi?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"✅ Gửi ngay ({total} user)", callback_data=f"adm_bcast_confirm"),
        InlineKeyboardButton(text="❌ Hủy", callback_data="adm_bcast_cancel"),
    ]])

    # Store pending broadcast in memory (simple approach)
    _pending_broadcasts[msg.chat.id] = {
        "text": text_content,
        "users": [u["tg_id"] for u in users],
    }
    await msg.answer(confirm_text, parse_mode="HTML", reply_markup=kb)


_pending_broadcasts = {}


@router.callback_query(F.data == "adm_bcast_confirm")
async def on_bcast_confirm(cb: CallbackQuery):
    if not is_admin(cb.message.chat.id, cb.from_user.id):
        return
    bcast = _pending_broadcasts.pop(cb.message.chat.id, None)
    if not bcast:
        await cb.answer("Đã hết hạn, vui lòng thực hiện lại!", show_alert=True)
        return

    await cb.answer("Đang gửi...", show_alert=False)
    await cb.message.edit_reply_markup(reply_markup=None)

    from .bot import manager as main_bot
    if not main_bot.bot:
        await cb.message.answer("❌ Main bot chưa khởi động!")
        return

    success = 0
    fail = 0
    for tg_id in bcast["users"]:
        try:
            await main_bot.bot.send_message(tg_id, bcast["text"], parse_mode="HTML")
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            fail += 1

    await cb.message.answer(f"✅ <b>Broadcast hoàn tất!</b>\n✔ Thành công: {success}\n✘ Lỗi: {fail}", parse_mode="HTML")


@router.callback_query(F.data == "adm_bcast_cancel")
async def on_bcast_cancel(cb: CallbackQuery):
    _pending_broadcasts.pop(cb.message.chat.id, None)
    await cb.answer("Đã hủy broadcast.", show_alert=True)
    await cb.message.edit_reply_markup(reply_markup=None)


@router.message(Command("promo"))
async def cmd_promo(msg: Message):
    """Tạo mã khuyến mãi với nhiều tuỳ chọn. Alias /phatcode nâng cao."""
    if not is_admin(msg.chat.id, msg.from_user.id):
        return
    parts = msg.text.split()
    if len(parts) < 3:
        await msg.answer(
            "❌ HDSD: /promo &lt;tên_prefix&gt; &lt;giá_trị&gt; [số_lần_dùng=1] [hạn=0]\n"
            "Ví dụ: /promo SALE 50000 100 24h — Mã SALE-XXXXX, 50k, 100 lượt, hạn 24 giờ",
            parse_mode="HTML"
        )
        return
    try:
        prefix = parts[1].upper()
        amount = int(parts[2].replace(",", "").replace("k", "000").replace("K", "000"))
        max_uses = int(parts[3]) if len(parts) > 3 else 1
        expire_at = 0
        if len(parts) > 4:
            seconds = parse_time_str(parts[4].lower())
            if seconds > 0:
                expire_at = int(time.time()) + seconds
    except (ValueError, IndexError):
        await msg.answer("❌ Thông số không hợp lệ!")
        return

    from . import util
    code = db.generate_code(amount=amount, prefix=prefix, max_uses=max_uses, expire_at=expire_at)
    expire_text = "Vĩnh viễn" if not expire_at else util.vn_time_str('%H:%M %d/%m/%Y', expire_at)
    await msg.answer(
        f"✅ <b>TẠO MÃ KHUYẾN MÃI THÀNH CÔNG</b>\n\n"
        f"🎁 Mã: <code>{code}</code>\n"
        f"💰 Giá trị: <b>{util.vnd(amount)}</b>\n"
        f"👥 Số lượt dùng: <b>{max_uses}</b>\n"
        f"⏳ Hạn sử dụng: <b>{expire_text}</b>",
        parse_mode="HTML"
    )


@router.message(Command("adminstats"))
async def cmd_adminstats(msg: Message):
    """Tổng quan toàn bộ hệ thống."""
    if not is_admin(msg.chat.id, msg.from_user.id):
        return

    from . import util
    stats = db.get_revenue_stats()
    c = db.get_conn()
    vip_counts = {i: c.execute("SELECT COUNT(*) as c FROM tg_users WHERE vip_level=?", (i,)).fetchone()["c"] for i in range(4)}
    alert_count = c.execute("SELECT COUNT(*) as c FROM alert_rules WHERE is_active=1").fetchone()["c"]

    try:
        from .bot import manager as main_bot
        from .admin_bot import manager as admin_bot_manager
        bot_status = "🟢 Online" if main_bot.running else "🔴 Offline"
        admin_bot_status = "🟢 Online" if admin_bot_manager.running else "⚫ Không cấu hình"
    except Exception:
        bot_status = "?"
        admin_bot_status = "?"

    text = (
        "🖥 <b>TỔNG QUAN HỆ THỐNG</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🤖 Main Bot: {bot_status}\n"
        f"🛠 Admin Bot: {admin_bot_status}\n\n"
        f"👥 <b>Người Dùng:</b>\n"
        f"• Tổng: <b>{stats['total_users']:,}</b>\n"
        f"• Mới hôm nay: <b>{stats['new_today']}</b>\n"
        f"• Active hôm nay: <b>{stats['active_today']}</b>\n"
        f"• VIP 1: {vip_counts[1]} | VIP 2: {vip_counts[2]} | VIP 3: {vip_counts[3]}\n\n"
        f"💰 <b>Doanh Thu:</b>\n"
        f"• Hôm nay: <b>{util.vnd(stats['revenue_today'])}</b>\n"
        f"• Tháng này: <b>{util.vnd(stats['revenue_month'])}</b>\n"
        f"• Tổng tất cả: <b>{util.vnd(stats['revenue_total'])}</b>\n\n"
        f"🔔 <b>Alerts đang hoạt động:</b> {alert_count}\n"
    )
    await msg.answer(text, parse_mode="HTML")


# Cập nhật /help cho Admin Bot
@router.message(Command("help"))
async def cmd_help_v2(msg: Message):
    if not is_admin(msg.chat.id, msg.from_user.id):
        return
    help_text = (
        "🛠 <b>DANH SÁCH LỆNH ADMIN V2</b>\n\n"
        "<b>💰 Quản lý User</b>\n"
        "• /topup &lt;id&gt; &lt;tiền&gt; — Cộng tiền thẳng\n"
        "• /setbal &lt;id&gt; &lt;tiền&gt; — Set cứng số dư\n"
        "• /ban &lt;id&gt; [lý_do] — Khoá tài khoản\n"
        "• /unban &lt;id&gt; — Mở khoá\n"
        "• /setvip &lt;id&gt; &lt;cấp&gt; [ngày] — Set VIP\n"
        "• /info &lt;id&gt; — Xem thông tin user\n"
        "• /find &lt;@username&gt; — Tìm user\n\n"
        "<b>📊 Thống Kê & Vận Hành</b>\n"
        "• /revenue — Doanh thu hôm nay/tháng\n"
        "• /adminstats — Tổng quan hệ thống\n"
        "• /pending — Các đơn rút tiền đang chờ\n\n"
        "<b>📢 Marketing</b>\n"
        "• /broadcast &lt;tin&gt; — Gửi tất cả user\n"
        "• /broadcast vip &lt;tin&gt; — Gửi user VIP\n"
        "• /broadcast inactive &lt;tin&gt; — Gửi user không hoạt động 7 ngày\n"
        "• /phatcode &lt;tiền&gt; [lượt] [hạn] — Tạo giftcode\n"
        "• /phatcodeall &lt;tiền&gt; [hạn] — Phát code cho tất cả\n"
        "• /promo &lt;prefix&gt; &lt;tiền&gt; [lượt] [hạn] — Tạo mã khuyến mãi\n"
    )
    await msg.answer(help_text, parse_mode="HTML")

