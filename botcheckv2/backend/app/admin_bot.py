import asyncio
import html
import logging
import os
import time
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession

from . import config, db
from . import perms as _perms

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
    try:
        if _perms.is_admin(user_id):
            return True
    except Exception:
        pass
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
    target = parts[5] if len(parts) > 5 and parts[5] in ("main", "shop") else "main"

    try:
        if target == "shop":
            db.credit_topup(user_id, amount, reason="bank_transfer", wallet="shop")
        else:
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
                wallet_txt = "🛒 Ví shop" if target == "shop" else "tài khoản"
                msg_text = (
                    f"✅ <b>NẠP TIỀN THÀNH CÔNG</b>\n\n"
                    f"Bạn vừa được cộng <b>{amount:,.0f} VNĐ</b> vào {wallet_txt}.\n"
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
        self.dp = Dispatcher(storage=MemoryStorage())
        self.dp.include_router(router)
        self.task = None
        self.running = False

    async def start(self):
        token = (db.get_setting("admin_bot_token") or "").strip()
        main_token = (db.get_setting("bot_token") or "").strip()
        if not token or token == main_token:
            log.info("Admin bot token not set or identical to main bot token. Admin bot polling disabled.")
            return
            
        proxy = (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("http_proxy")
        )
        session = AiohttpSession(proxy=proxy) if proxy else None
        self.bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"), session=session)
        self.running = True
        log.info("Admin Bot starting...")
        
        try:
            # Giữ tin nhắn đang chờ: restart giữa chừng không được nuốt tin user
            await self.bot.delete_webhook(drop_pending_updates=False)
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
        await _admm_show_main(msg)
        return

    subcmd = parts[1].lower().strip()
    rest = parts[2].strip() if len(parts) > 2 else ""

    # ── Phân quyền admin phụ theo từng sub-command ──
    need = _perms.ADM_SUB_PERMS.get(subcmd)
    if need and not _perms.has_perm(tg_id, need):
        await msg.answer(
            f"🚫 Bạn không có quyền <b>{_perms.perm_label(need)}</b>.\n"
            f"Liên hệ chủ shop để được cấp thêm quyền.",
            parse_mode="HTML")
        return

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
        q = rest.strip()
        user = db.get_user(int(q)) if q.isdigit() else None
        if not user:
            user = db.find_user_by_username(q)
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

    # ── /adm taopromo <CODE> <%> [lượt] [giờ] ─────────────────────────────
    elif subcmd == "taopromo":
        args = rest.split()
        if len(args) < 2:
            await msg.answer("❌ HDSD: <code>/adm taopromo &lt;CODE&gt; &lt;phần_trăm&gt; [số_lượt] [số_giờ]</code>\nVD: <code>/adm taopromo SALE20 20 100 24</code>", parse_mode="HTML"); return
        try:
            pct = int(args[1])
        except ValueError:
            await msg.answer("❌ Phần trăm phải là số (1-90)."); return
        max_uses = int(args[2]) if len(args) > 2 and args[2].isdigit() else 0
        hours = int(args[3]) if len(args) > 3 and args[3].isdigit() else 0
        ok, txt = db.create_promo(args[0], pct, max_uses, hours)
        extra = ""
        if ok:
            if max_uses: extra += f"\n🎫 Giới hạn: {max_uses} lượt"
            if hours: extra += f"\n⏳ Hiệu lực: {hours} giờ"
            extra += f"\n\n📢 Gõ <code>/adm flashsale {args[0].strip().upper()}</code> để thông báo cho toàn bộ user."
        await msg.answer(("✅ " if ok else "❌ ") + txt + extra, parse_mode="HTML")

    # ── /adm dspromo ─────────────────────────────────────────────────────
    elif subcmd == "dspromo":
        rows = db.list_promos()
        if not rows:
            await msg.answer("Chưa có mã giảm giá nào. Tạo bằng <code>/adm taopromo</code>", parse_mode="HTML"); return
        lines = ["🎟️ <b>DANH SÁCH MÃ GIẢM GIÁ</b>", "━━━━━━━━━━━━━━━"]
        for r in rows:
            d = dict(r)
            status = "✅" if db.promo_valid(d["code"])[0] else "⛔"
            lim = f"{int(d['used_count'])}/{int(d['max_uses'])}" if d["max_uses"] else f"{int(d['used_count'])}/∞"
            lines.append(f"{status} <code>{d['code']}</code> — giảm {int(d['pct'])}% — đã dùng {lim}")
        await msg.answer("\n".join(lines), parse_mode="HTML")

    # ── /adm xoapromo <CODE> ─────────────────────────────────────────────
    elif subcmd == "xoapromo":
        args = rest.split()
        if not args:
            await msg.answer("❌ HDSD: <code>/adm xoapromo &lt;CODE&gt;</code>", parse_mode="HTML"); return
        if db.delete_promo(args[0]):
            await msg.answer(f"✅ Đã xóa mã <b>{args[0].strip().upper()}</b>.", parse_mode="HTML")
        else:
            await msg.answer("❌ Mã không tồn tại.")

    # ── /adm flashsale <CODE> ────────────────────────────────────────────
    elif subcmd == "flashsale":
        args = rest.split()
        if not args:
            await msg.answer("❌ HDSD: <code>/adm flashsale &lt;CODE&gt;</code>", parse_mode="HTML"); return
        ok, why, row = db.promo_valid(args[0])
        if not ok:
            await msg.answer(f"❌ {why}"); return
        d = dict(row)
        exp_txt = f"\n⏰ Kết thúc: {util.vn_time_str('%d/%m %H:%M', d['expires_at'])}" if d["expires_at"] else ""
        lim_txt = f"\n🎫 Chỉ {int(d['max_uses']) - int(d['used_count'])} suất" if d["max_uses"] else ""
        text = (
            f"🔥 <b>FLASH SALE — GIẢM {int(d['pct'])}% GÓI CREDITS!</b> 🔥\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎟️ Mã: <code>{d['code']}</code>\n"
            f"💸 Giảm <b>{int(d['pct'])}%</b> khi mua gói credits{lim_txt}{exp_txt}\n\n"
            f"👉 Áp mã ngay: <code>/promo {d['code']}</code>\n"
            f"⚡ Mua gói: /muacredit"
        )
        import asyncio as _asyncio
        sent, failed = 0, 0
        sem = _asyncio.Semaphore(20)
        target_bot = bot_instance or msg.bot

        async def _send(uid):
            nonlocal sent, failed
            async with sem:
                try:
                    await target_bot.send_message(uid, text, parse_mode="HTML")
                    sent += 1
                except Exception:
                    failed += 1

        uids = [r["tg_id"] for r in db.get_all_users_for_broadcast()]
        await _asyncio.gather(*[_send(u) for u in uids], return_exceptions=True)
        await msg.answer(f"📢 Đã gửi flash sale <b>{d['code']}</b>: <b>{sent}</b> thành công, {failed} thất bại.", parse_mode="HTML")

    # ── /adm webhook <tên_key|id> <url|off> ──────────────────────────────
    elif subcmd == "webhook":
        args = rest.split(maxsplit=1)
        if not args:
            await msg.answer("❌ HDSD: <code>/adm webhook &lt;tên_key|id&gt; &lt;url|off&gt;</code>", parse_mode="HTML"); return
        key = db.get_reseller_by_name_or_id(args[0])
        if not key:
            await msg.answer(f"❌ Không tìm thấy key <b>{args[0]}</b>.", parse_mode="HTML"); return
        url = ""
        if len(args) > 1:
            u = args[1].strip()
            if u.lower() not in ("off", "xoa", "xóa", "-"):
                if not (u.startswith("http://") or u.startswith("https://")):
                    await msg.answer("❌ URL phải bắt đầu bằng http:// hoặc https://"); return
                url = u
        db.set_reseller_webhook(key["id"], url)
        if url:
            await msg.answer(f"✅ Đã gán webhook cho key <b>{key['name']}</b>:\n<code>{url}</code>", parse_mode="HTML")
        else:
            await msg.answer(f"✅ Đã xóa webhook của key <b>{key['name']}</b>.", parse_mode="HTML")

    else:
        await msg.answer(f"❓ Không hiểu sub-command: <code>{subcmd}</code>\nGõ /adm help để xem danh sách.", parse_mode="HTML")



async def _answer_long(msg, text: str, parse_mode: str = "HTML", limit: int = 4000):
    """Gửi text dài bằng cách chia thành nhiều tin nhắn, cắt ở ranh giới đoạn."""
    parts, cur = [], []
    cur_len = 0
    for para in text.split("\n\n"):
        chunk = para if not cur else "\n\n" + para
        if cur and cur_len + len(chunk) > limit:
            parts.append("\n\n".join(cur))
            cur, cur_len = [para], len(para)
        else:
            cur.append(para)
            cur_len += len(chunk)
    if cur:
        parts.append("\n\n".join(cur))
    for i, part in enumerate(parts):
        suffix = f" ({i+1}/{len(parts)})" if len(parts) > 1 else ""
        head = f"<b>📋 Bảng lệnh admin{suffix}</b>\n\n" if i > 0 else ""
        await msg.answer(head + part, parse_mode=parse_mode)

# ═══════════════════════════════════════════════════════════════════════════
# MENU NÚT /adm — bấm nút thay vì gõ lệnh tay (18 sub-command gộp theo nhóm)
# Đăng ký vào router bằng register_adm_menu(router) — dùng chung cho cả
# bot chính (bot.py) và admin bot (router nội bộ file này).
# ═══════════════════════════════════════════════════════════════════════════

class AdmMenuState(StatesGroup):
    """States nhập liệu từng bước cho menu /adm."""
    topup_uid = State()
    topup_amount = State()
    setbal_uid = State()
    setbal_amount = State()
    ban_uid = State()
    ban_reason = State()
    unban_uid = State()
    setvip_uid = State()
    setvip_days = State()
    info_uid = State()
    find_query = State()
    taopromo_code = State()
    taopromo_pct = State()
    taopromo_uses = State()
    taopromo_hours = State()
    promo_prefix = State()
    promo_amount = State()
    promo_uses = State()
    promo_expire = State()
    xoapromo_code = State()
    flashsale_code = State()
    broadcast_text = State()
    webhook_key = State()
    webhook_url = State()
    admadd_id = State()


def _admm_main_kb(tg_id=None):
    rows = []
    cats = [
        ("💰 Tiền tệ", "admm:cat_tien", "tien"),
        ("👤 Quản lý user", "admm:cat_user", "user"),
        ("📊 Báo cáo", "admm:cat_report", "report"),
        ("🎟️ Mã giảm giá", "admm:cat_promo", "promo"),
        ("📣 Broadcast & Webhook", "admm:cat_bcast", "bcast"),
    ]
    vis = [(t, c) for t, c, p in cats
           if tg_id is None or _perms.has_perm(tg_id, p)]
    for i in range(0, len(vis), 2):
        row = [InlineKeyboardButton(text=vis[i][0], callback_data=vis[i][1])]
        if i + 1 < len(vis):
            row.append(InlineKeyboardButton(text=vis[i + 1][0],
                                            callback_data=vis[i + 1][1]))
        rows.append(row)
    if tg_id is not None and _perms.is_super(tg_id):
        rows.append([InlineKeyboardButton(text="🛡️ Quản lý admin",
                                          callback_data="admx:list"),
                     InlineKeyboardButton(text="📜 Nhật ký hoạt động",
                                          callback_data="admx:audit")])
    if not rows:
        rows.append([InlineKeyboardButton(text="🚫 Không có quyền nào",
                                          callback_data="admm:noop")])
    rows.append([InlineKeyboardButton(text="📖 Hướng dẫn đầy đủ",
                                      callback_data="admm:help")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _admm_text_main() -> str:
    return (
        "🛠️ <b>QUẢN TRỊ</b>\n"
        "━━━━━━━━━━━━\n\n"
        "Chọn nhóm thao tác — bấm nút, khỏi gõ lệnh tay:\n\n"
        "💰 <b>Tiền tệ</b> — cộng tiền, set số dư\n"
        "👤 <b>Quản lý user</b> — xem info, tìm, khoá/mở khoá, set VIP\n"
        "📊 <b>Báo cáo</b> — tổng quan, doanh thu, đơn rút chờ duyệt\n"
        "🎟️ <b>Mã giảm giá</b> — tạo/xem/xoá mã, flash sale\n"
        "📣 <b>Broadcast</b> — gửi tin toàn bộ user, webhook reseller\n\n"
        "<i>Vẫn gõ tay được: /adm &lt;lệnh&gt; &lt;tham số&gt; — vd /adm topup 123 50k</i>"
    )


def _admm_back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Quay lại menu Admin", callback_data="admm:main")]
    ])


def _admm_confirm_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Xác nhận", callback_data="admm:confirm"),
        InlineKeyboardButton(text="❌ Huỷ", callback_data="admm:cancel"),
    ]])


def _admm_skip_kb(skip_text="⏭ Bỏ qua"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=skip_text, callback_data="admm:skip")],
        [InlineKeyboardButton(text="◀️ Quay lại menu Admin", callback_data="admm:main")],
    ])


# ------------------------------------------------- chọn user theo số thứ tự
_USER_PICK_PAGE = 10


def _admm_user_pick_text(page: int):
    """Danh sách user đánh số thứ tự để admin bấm chọn thay vì nhập ID."""
    from . import util
    c = db.get_conn()
    try:
        total = c.execute("SELECT COUNT(*) n FROM tg_users").fetchone()["n"]
        rows = [dict(r) for r in c.execute(
            "SELECT tg_id, name, username, balance, is_blocked FROM tg_users "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (_USER_PICK_PAGE, page * _USER_PICK_PAGE)).fetchall()]
    except Exception:
        return "❌ Không đọc được danh sách user.", _admm_back_kb()
    if not rows:
        return "👥 Chưa có user nào.", _admm_back_kb()
    lines = ["🔍 <b>XEM THÔNG TIN USER</b>",
             f"👥 Tổng: <b>{total}</b> user — bấm <b>số thứ tự</b> để xem chi tiết,",
             "hoặc gửi <b>User ID</b> trực tiếp.",
             "━━━━━━━━━━━━"]
    base = page * _USER_PICK_PAGE
    btns, kb_rows = [], []
    for i, r in enumerate(rows):
        stt = base + i + 1
        nm = html.escape(str(r.get("name") or ""))
        un = f" (@{html.escape(str(r['username']))})" if r.get("username") else ""
        bal = util.vnd(r.get("balance") or 0)
        flag = " 🔴" if r.get("is_blocked") else ""
        lines.append(f"<b>{stt}.</b> {nm}{un} — {bal}{flag}")
        btns.append(InlineKeyboardButton(text=str(stt),
                                         callback_data=f"admm:picku_{r['tg_id']}"))
        if len(btns) == 5:
            kb_rows.append(btns)
            btns = []
    if btns:
        kb_rows.append(btns)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⏮ Trước", callback_data=f"admm:pickp_{page - 1}"))
    if (page + 1) * _USER_PICK_PAGE < total:
        nav.append(InlineKeyboardButton(text="⏭ Tiếp", callback_data=f"admm:pickp_{page + 1}"))
    if nav:
        kb_rows.append(nav)
    kb_rows.append([InlineKeyboardButton(text="◀️ Quay lại menu Admin", callback_data="admm:main")])
    lines.append("━━━━━━━━━━━━\nGõ /huy để huỷ.")
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb_rows)


class _AdmTextShim:
    """Giả lập Message với text tuỳ chỉnh để tái dùng _handle_adm_cmd cho flow nút bấm."""
    def __init__(self, msg: Message, text: str, edit_target=None):
        self._msg = msg
        self.text = text
        self._edit_target = edit_target
        self._edited = False

    @property
    def chat(self):
        return self._msg.chat

    @property
    def from_user(self):
        return self._msg.from_user

    @property
    def bot(self):
        return self._msg.bot

    async def answer(self, text, **kwargs):
        if self._edit_target is not None and not self._edited:
            self._edited = True
            kw = {k: v for k, v in kwargs.items()
                  if k in ("parse_mode", "reply_markup", "disable_web_page_preview")}
            await self._edit_target.edit_text(text, **kw)
        else:
            await self._msg.answer(text, **kwargs)


async def _admm_show_main(msg: Message):
    await msg.answer(_admm_text_main(), parse_mode="HTML",
                     reply_markup=_admm_main_kb(msg.from_user.id))


async def _admm_exec_via_cb(cb: CallbackQuery, state: FSMContext, cmd_text: str):
    """Chạy 1 sub-command /adm từ nút bấm, hiện kết quả ngay tại tin menu."""
    need = _perms.cmd_perm_for_text(cmd_text)
    if need and not _perms.has_perm(cb.from_user.id, need):
        await cb.answer(f"🚫 Bạn không có quyền {_perms.perm_label(need)}.",
                        show_alert=True)
        return
    await state.clear()
    try:
        db.admin_audit_add(cb.from_user.id, cb.from_user.full_name,
                           "menu_adm", (cmd_text or "")[:200])
    except Exception:
        pass
    shim = _AdmTextShim(cb.message, cmd_text, edit_target=cb.message)
    await _handle_adm_cmd(shim, bot_instance=cb.bot)
    await cb.message.edit_reply_markup(reply_markup=_admm_back_kb())
    await cb.answer()


async def _admm_exec_via_msg(msg: Message, state: FSMContext, cmd_text: str,
                            restore_state=None):
    """Chạy 1 sub-command /adm sau khi admin nhập liệu xong.
    restore_state: nếu cho, sau khi chạy xong sẽ đặt lại state này để admin
    nhập tiếp (dùng cho các flow chỉ-xem như tìm/xem user)."""
    need = _perms.cmd_perm_for_text(cmd_text)
    if need and not _perms.has_perm(msg.from_user.id, need):
        await msg.answer(f"🚫 Bạn không có quyền {_perms.perm_label(need)}.")
        return
    await state.clear()
    try:
        db.admin_audit_add(msg.from_user.id, msg.from_user.full_name,
                           "menu_adm", (cmd_text or "")[:200])
    except Exception:
        pass
    shim = _AdmTextShim(msg, cmd_text)
    await _handle_adm_cmd(shim, bot_instance=msg.bot)
    if restore_state is not None:
        await state.set_state(restore_state)


async def _admm_show_confirm_cb(cb: CallbackQuery, state: FSMContext, title: str,
                               lines: list, cmd_text: str):
    await state.update_data(cmd_text=cmd_text)
    txt = title + "\n━━━━━━━━━━━━\n" + "\n".join(lines) + "\n━━━━━━━━━━━━\n\nXác nhận thực hiện?"
    await cb.message.edit_text(txt, parse_mode="HTML", reply_markup=_admm_confirm_kb())
    await cb.answer()


async def _admm_show_confirm_msg(msg: Message, state: FSMContext, title: str,
                                lines: list, cmd_text: str):
    await state.update_data(cmd_text=cmd_text)
    txt = title + "\n━━━━━━━━━━━━\n" + "\n".join(lines) + "\n━━━━━━━━━━━━\n\nXác nhận thực hiện?"
    await msg.answer(txt, parse_mode="HTML", reply_markup=_admm_confirm_kb())


async def _admm_guard(msg: Message, state: FSMContext) -> bool:
    """Chặn input không phải admin hoặc lệnh /huy. Trả True nếu đã xử lý xong."""
    if not is_admin(msg.chat.id, msg.from_user.id):
        await state.clear()
        return True
    if (msg.text or "").strip() == "/huy":
        await state.clear()
        await msg.answer("Đã huỷ thao tác.", reply_markup=_admm_back_kb())
        return True
    return False


# ------------------------------------------------- quản lý admin phụ (chủ shop)
def _admx_name_of(tg_id: int) -> str:
    try:
        u = db.get_user(tg_id)
        if u:
            return (u.get("username") and "@" + u["username"]) or u.get("name") or str(tg_id)
    except Exception:
        pass
    return str(tg_id)


def _admx_list_text() -> str:
    rows = db.extra_admin_list()
    if not rows:
        return "🛡️ <b>QUẢN LÝ ADMIN</b>\n\nChưa có admin phụ nào.\nBấm <b>➕ Thêm admin</b> để cấp quyền."
    lines = ["🛡️ <b>QUẢN LÝ ADMIN</b>", ""]
    for r in rows:
        pl = [ _perms.perm_label(p) for p in (r.get("perms") or "").split(",") if p ]
        lines.append(f"• <code>{r['tg_id']}</code> {html.escape(r.get('name') or '?')}\n"
                     f"  └ {', '.join(pl) or '—'}")
    return "\n".join(lines)


def _admx_list_kb():
    rows = [[InlineKeyboardButton(text="➕ Thêm admin", callback_data="admx:add")]]
    for r in db.extra_admin_list():
        nm = r.get("name") or str(r["tg_id"])
        rows.append([
            InlineKeyboardButton(text=f"🔑 {nm}", callback_data=f"admx:edit:{r['tg_id']}"),
            InlineKeyboardButton(text="🗑️", callback_data=f"admx:del:{r['tg_id']}"),
        ])
    rows.append([InlineKeyboardButton(text="◀️ Quay lại menu Admin",
                                      callback_data="admm:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


_AUDIT_PER_PAGE = 12


def _audit_text(page: int) -> str:
    rows = db.admin_audit_list(_AUDIT_PER_PAGE, page * _AUDIT_PER_PAGE)
    total = db.admin_audit_count()
    lines = ["📜 <b>NHẬT KÝ HOẠT ĐỘNG ADMIN</b>",
             f"<i>Tổng {total} dòng — trang {page + 1}</i>", ""]
    if not rows:
        lines.append("Chưa có hoạt động nào được ghi.")
        return "\n".join(lines)
    import datetime as _dt
    for r in rows:
        ts = _dt.datetime.fromtimestamp(r["created_at"]).strftime("%d/%m %H:%M")
        nm = html.escape((r.get("name") or "")[:30])
        act = html.escape(r.get("action") or "")
        det = html.escape((r.get("detail") or "")[:120])
        lines.append(f"• <code>{ts}</code> <b>{nm}</b> <code>{r['tg_id']}</code>\n"
                     f"  └ {act}" + (f": {det}" if det else ""))
    return "\n".join(lines)


def _audit_kb(page: int):
    total = db.admin_audit_count()
    pages = max(1, (total + _AUDIT_PER_PAGE - 1) // _AUDIT_PER_PAGE)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ Trước",
                                        callback_data=f"admx:audit:{page - 1}"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton(text="Sau ▶️",
                                        callback_data=f"admx:audit:{page + 1}"))
    rows = [nav] if nav else []
    rows.append([InlineKeyboardButton(text="◀️ Quản lý admin",
                                      callback_data="admx:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _admx_perm_kb(cur: set, save_cb: str):
    rows = []
    for k, label in _perms.PERMS:
        mark = "✅" if k in cur else "⬜"
        rows.append([InlineKeyboardButton(text=f"{mark} {label}",
                                          callback_data=f"admx:toggle:{k}")])
    rows.append([InlineKeyboardButton(text="✅ Lưu", callback_data=save_cb),
                 InlineKeyboardButton(text="❌ Huỷ", callback_data="admx:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _admm_parse_uid(text: str):
    try:
        return int((text or "").strip())
    except (ValueError, AttributeError):
        return None


def _admm_parse_amount(text: str):
    try:
        return int((text or "").strip().replace(",", "").replace("k", "000").replace("K", "000"))
    except (ValueError, AttributeError):
        return None


def register_adm_menu(target_router):
    """Gắn toàn bộ menu nút /adm (callback + nhập liệu FSM) vào router cho trước."""

    @target_router.callback_query(F.data.startswith("admm:"))
    async def _on_admm_cb(cb: CallbackQuery, state: FSMContext):
        if not is_admin(cb.message.chat.id, cb.from_user.id):
            await cb.answer("🚫 Không có quyền.", show_alert=True)
            return
        action = (cb.data or "")[5:]

        # ── Menu chính ──
        if action == "main":
            await state.clear()
            await cb.message.edit_text(_admm_text_main(), parse_mode="HTML",
                                      reply_markup=_admm_main_kb(cb.from_user.id))
            await cb.answer()
            return

        if action == "noop":
            await cb.answer()
            return

        if action == "help":
            await cb.answer()
            await _show_adm_help(cb.message)
            return

        # ── Nhóm Tiền tệ ──
        if action == "cat_tien":
            if not _perms.has_perm(cb.from_user.id, "tien"):
                await cb.answer("🚫 Bạn không có quyền 💰 Tiền tệ.", show_alert=True)
                return
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💰 Cộng tiền vào ví", callback_data="admm:go_topup")],
                [InlineKeyboardButton(text="✏️ Set lại số dư", callback_data="admm:go_setbal")],
                [InlineKeyboardButton(text="◀️ Quay lại menu Admin", callback_data="admm:main")],
            ])
            await cb.message.edit_text("💰 <b>TIỀN TỆ</b>\n\nChọn thao tác:",
                                      parse_mode="HTML", reply_markup=kb)
            await cb.answer()
            return

        # ── Nhóm Quản lý user ──
        if action == "cat_user":
            if not _perms.has_perm(cb.from_user.id, "user"):
                await cb.answer("🚫 Bạn không có quyền 👤 Quản lý user.", show_alert=True)
                return
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔍 Xem thông tin user", callback_data="admm:go_info")],
                [InlineKeyboardButton(text="🔎 Tìm user theo @username", callback_data="admm:go_find")],
                [InlineKeyboardButton(text="🔴 Khoá tài khoản", callback_data="admm:go_ban"),
                 InlineKeyboardButton(text="🟢 Mở khoá", callback_data="admm:go_unban")],
                [InlineKeyboardButton(text="⭐ Set VIP", callback_data="admm:go_setvip")],
                [InlineKeyboardButton(text="◀️ Quay lại menu Admin", callback_data="admm:main")],
            ])
            await cb.message.edit_text("👤 <b>QUẢN LÝ USER</b>\n\nChọn thao tác:",
                                      parse_mode="HTML", reply_markup=kb)
            await cb.answer()
            return

        # ── Nhóm Báo cáo ──
        if action == "cat_report":
            if not _perms.has_perm(cb.from_user.id, "report"):
                await cb.answer("🚫 Bạn không có quyền 📊 Báo cáo.", show_alert=True)
                return
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🖥 Tổng quan hệ thống", callback_data="admm:run_stats")],
                [InlineKeyboardButton(text="📈 Doanh thu", callback_data="admm:run_revenue")],
                [InlineKeyboardButton(text="📋 Đơn rút chờ duyệt", callback_data="admm:run_pending")],
                [InlineKeyboardButton(text="◀️ Quay lại menu Admin", callback_data="admm:main")],
            ])
            await cb.message.edit_text("📊 <b>BÁO CÁO</b>\n\nChọn báo cáo muốn xem:",
                                      parse_mode="HTML", reply_markup=kb)
            await cb.answer()
            return

        # ── Nhóm Mã giảm giá ──
        if action == "cat_promo":
            if not _perms.has_perm(cb.from_user.id, "promo"):
                await cb.answer("🚫 Bạn không có quyền 🎟️ Mã giảm giá.", show_alert=True)
                return
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎟️ Tạo mã giảm %", callback_data="admm:go_taopromo"),
                 InlineKeyboardButton(text="💵 Tạo mã tiền", callback_data="admm:go_promo")],
                [InlineKeyboardButton(text="📜 Danh sách mã", callback_data="admm:run_dspromo"),
                 InlineKeyboardButton(text="❌ Xoá mã", callback_data="admm:go_xoapromo")],
                [InlineKeyboardButton(text="🔥 Gửi Flash sale", callback_data="admm:go_flashsale")],
                [InlineKeyboardButton(text="◀️ Quay lại menu Admin", callback_data="admm:main")],
            ])
            await cb.message.edit_text("🎟️ <b>MÃ GIẢM GIÁ</b>\n\nChọn thao tác:",
                                      parse_mode="HTML", reply_markup=kb)
            await cb.answer()
            return

        # ── Nhóm Broadcast & Webhook ──
        if action == "cat_bcast":
            if not _perms.has_perm(cb.from_user.id, "bcast"):
                await cb.answer("🚫 Bạn không có quyền 📣 Broadcast & webhook.", show_alert=True)
                return
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📢 Gửi broadcast", callback_data="admm:go_broadcast")],
                [InlineKeyboardButton(text="🔔 Webhook reseller", callback_data="admm:go_webhook")],
                [InlineKeyboardButton(text="◀️ Quay lại menu Admin", callback_data="admm:main")],
            ])
            await cb.message.edit_text("📣 <b>BROADCAST & WEBHOOK</b>\n\nChọn thao tác:",
                                      parse_mode="HTML", reply_markup=kb)
            await cb.answer()
            return

        # ── Chạy ngay (không cần nhập liệu) ──
        if action == "run_stats":
            await _admm_exec_via_cb(cb, state, "/adm stats")
            return
        if action == "run_revenue":
            await _admm_exec_via_cb(cb, state, "/adm revenue")
            return
        if action == "run_pending":
            await _admm_exec_via_cb(cb, state, "/adm pending")
            return
        if action == "run_dspromo":
            await _admm_exec_via_cb(cb, state, "/adm dspromo")
            return

        # ── Chọn user theo số thứ tự (xem thông tin) ──
        if action.startswith("pickp_") or action.startswith("picku_"):
            need = _perms.cmd_perm_for_text("/adm info 0")
            if need and not _perms.has_perm(cb.from_user.id, need):
                await cb.answer(f"🚫 Bạn không có quyền {_perms.perm_label(need)}.",
                                show_alert=True)
                return
            if action.startswith("pickp_"):
                try:
                    page = int(action.split("_", 1)[1])
                except ValueError:
                    page = 0
                text, kb = _admm_user_pick_text(max(page, 0))
                await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
                await cb.answer()
                return
            try:
                uid = int(action.split("_", 1)[1])
            except ValueError:
                await cb.answer("ID không hợp lệ.", show_alert=True)
                return
            try:
                db.admin_audit_add(cb.from_user.id, cb.from_user.full_name,
                                   "menu_adm", f"/adm info {uid}")
            except Exception:
                pass
            shim = _AdmTextShim(cb.message, f"/adm info {uid}", edit_target=cb.message)
            await _handle_adm_cmd(shim, bot_instance=cb.bot)
            rm = cb.message.reply_markup
            rows = [list(r) for r in rm.inline_keyboard] if rm and rm.inline_keyboard else []
            rows.append([InlineKeyboardButton(text="📋 Danh sách user",
                                              callback_data="admm:pickp_0")])
            await cb.message.edit_reply_markup(
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
            await state.set_state(AdmMenuState.info_uid)
            await cb.answer()
            return

        # ── Bắt đầu các flow nhập liệu ──
        prompts = {
            "go_topup": (AdmMenuState.topup_uid, "💰 <b>CỘNG TIỀN</b> (bước 1/2)\n\nGửi <b>User ID</b> cần cộng tiền."),
            "go_setbal": (AdmMenuState.setbal_uid, "✏️ <b>SET SỐ DƯ</b> (bước 1/2)\n\nGửi <b>User ID</b> cần set lại số dư."),
            "go_ban": (AdmMenuState.ban_uid, "🔴 <b>KHOÁ TÀI KHOẢN</b> (bước 1/2)\n\nGửi <b>User ID</b> cần khoá."),
            "go_unban": (AdmMenuState.unban_uid, "🟢 <b>MỞ KHOÁ TÀI KHOẢN</b>\n\nGửi <b>User ID</b> cần mở khoá."),
            "go_setvip": (AdmMenuState.setvip_uid, "⭐ <b>SET VIP</b> (bước 1/3)\n\nGửi <b>User ID</b> cần set VIP."),
            "go_find": (AdmMenuState.find_query, "🔎 <b>TÌM USER</b>\n\nGửi <b>@username</b>, <b>User ID</b> hoặc tên cần tìm."),
            "go_taopromo": (AdmMenuState.taopromo_code, "🎟️ <b>TẠO MÃ GIẢM %</b> (bước 1/4)\n\nGửi <b>mã</b> (VD: SALE20)."),
            "go_promo": (AdmMenuState.promo_prefix, "💵 <b>TẠO MÃ TIỀN</b> (bước 1/4)\n\nGửi <b>prefix</b> (VD: SALE)."),
            "go_xoapromo": (AdmMenuState.xoapromo_code, "❌ <b>XOÁ MÃ GIẢM GIÁ</b>\n\nGửi <b>mã</b> cần xoá."),
            "go_flashsale": (AdmMenuState.flashsale_code, "🔥 <b>FLASH SALE</b>\n\nGửi <b>mã</b> muốn thông báo tới toàn bộ user."),
            "go_webhook": (AdmMenuState.webhook_key, "🔔 <b>WEBHOOK RESELLER</b> (bước 1/2)\n\nGửi <b>tên key hoặc ID</b> reseller."),
        }
        if action == "go_info":
            need = _perms.cmd_perm_for_text("/adm info 0")
            if need and not _perms.has_perm(cb.from_user.id, need):
                await cb.answer(f"🚫 Bạn không có quyền {_perms.perm_label(need)}.",
                                show_alert=True)
                return
            await state.set_state(AdmMenuState.info_uid)
            text, kb = _admm_user_pick_text(0)
            await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
            await cb.answer()
            return
        if action in prompts:
            st, prompt = prompts[action]
            await state.set_state(st)
            await cb.message.edit_text(prompt + "\n\nGõ /huy để huỷ.",
                                      parse_mode="HTML", reply_markup=_admm_back_kb())
            await cb.answer()
            return

        # ── Broadcast: chọn đối tượng ──
        if action == "go_broadcast":
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="👥 Tất cả user", callback_data="admm:bcast_tg_all"),
                 InlineKeyboardButton(text="⭐ Chỉ VIP", callback_data="admm:bcast_tg_vip")],
                [InlineKeyboardButton(text="😴 Inactive 7 ngày", callback_data="admm:bcast_tg_inactive")],
                [InlineKeyboardButton(text="◀️ Quay lại menu Admin", callback_data="admm:main")],
            ])
            await cb.message.edit_text("📢 <b>BROADCAST</b> (bước 1/2)\n\nChọn đối tượng nhận tin:",
                                      parse_mode="HTML", reply_markup=kb)
            await cb.answer()
            return

        if action in ("bcast_tg_all", "bcast_tg_vip", "bcast_tg_inactive"):
            target = {"bcast_tg_all": "all", "bcast_tg_vip": "vip",
                      "bcast_tg_inactive": "inactive"}[action]
            await state.update_data(bcast_target=target)
            await state.set_state(AdmMenuState.broadcast_text)
            await cb.message.edit_text("📢 <b>BROADCAST</b> (bước 2/2)\n\nGửi <b>nội dung</b> tin nhắn (hỗ trợ định dạng HTML).\n\nGõ /huy để huỷ.",
                                      parse_mode="HTML", reply_markup=_admm_back_kb())
            await cb.answer()
            return

        # ── Set VIP: chọn cấp ──
        if action.startswith("setvip_lv_"):
            data = await state.get_data()
            uid = data.get("uid")
            if not uid:
                await cb.answer("Hết hạn, làm lại từ đầu.", show_alert=True)
                return
            try:
                lv = int(action.rsplit("_", 1)[1])
            except ValueError:
                return
            await state.update_data(level=lv)
            await state.set_state(AdmMenuState.setvip_days)
            labels = {0: "Thường", 1: "VIP 1 🥉", 2: "VIP 2 🥈", 3: "VIP 3 🥇"}
            await cb.message.edit_text(
                f"⭐ <b>SET VIP</b> (bước 3/3)\n\nUser <code>{uid}</code> → <b>{labels.get(lv, lv)}</b>\n\n"
                f"Gửi <b>số ngày gia hạn</b> thêm, hoặc bấm Bỏ qua.",
                parse_mode="HTML", reply_markup=_admm_skip_kb("⏭ Không gia hạn"))
            await cb.answer()
            return

        # ── Webhook: gỡ ──
        if action == "webhook_off":
            data = await state.get_data()
            key = data.get("key")
            if not key:
                await cb.answer("Hết hạn, làm lại từ đầu.", show_alert=True)
                return
            await _admm_show_confirm_cb(cb, state, "🔔 <b>XÁC NHẬN GỠ WEBHOOK</b>",
                                       [f"🔑 Key: <b>{html.escape(str(key))}</b>",
                                        "🧹 Webhook sẽ bị xoá"],
                                       f"/adm webhook {key} off")
            return

        # ── Bỏ qua bước tuỳ chọn ──
        if action == "skip":
            st = await state.get_state()
            data = await state.get_data()
            from . import util as _util
            if st == AdmMenuState.ban_reason.state:
                await _admm_show_confirm_cb(cb, state, "🔴 <b>XÁC NHẬN KHOÁ</b>",
                                           [f"👤 User: <code>{data['uid']}</code>",
                                            "📝 Lý do: Vi phạm quy định"],
                                           f"/adm ban {data['uid']}")
                return
            if st == AdmMenuState.setvip_days.state:
                lv = data.get("level", 0)
                labels = {0: "Thường", 1: "VIP 1 🥉", 2: "VIP 2 🥈", 3: "VIP 3 🥇"}
                await _admm_show_confirm_cb(cb, state, "⭐ <b>XÁC NHẬN SET VIP</b>",
                                           [f"👤 User: <code>{data['uid']}</code>",
                                            f"⭐ Cấp: <b>{labels.get(lv, lv)}</b>"],
                                           f"/adm setvip {data['uid']} {lv}")
                return
            if st == AdmMenuState.taopromo_uses.state:
                await state.update_data(uses=0)
                await state.set_state(AdmMenuState.taopromo_hours)
                await cb.message.edit_text(
                    "🎟️ <b>TẠO MÃ GIẢM %</b> (bước 4/4)\n\nGửi <b>số giờ hiệu lực</b>, hoặc bấm Bỏ qua (vĩnh viễn).",
                    parse_mode="HTML", reply_markup=_admm_skip_kb("⏭ Vĩnh viễn"))
                await cb.answer()
                return
            if st == AdmMenuState.taopromo_hours.state:
                d = await state.get_data()
                await _admm_show_confirm_cb(cb, state, "🎟️ <b>XÁC NHẬN TẠO MÃ</b>",
                                           [f"🎫 Mã: <code>{html.escape(str(d['code']))}</code>",
                                            f"💸 Giảm: <b>{d['pct']}%</b>",
                                            f"👥 Lượt dùng: <b>{'không giới hạn' if not d.get('uses') else d['uses']}</b>",
                                            "⏳ Hiệu lực: <b>vĩnh viễn</b>"],
                                           f"/adm taopromo {d['code']} {d['pct']} {d.get('uses') or 0} 0")
                return
            if st == AdmMenuState.promo_uses.state:
                await state.update_data(uses=1)
                await state.set_state(AdmMenuState.promo_expire)
                await cb.message.edit_text(
                    "💵 <b>TẠO MÃ TIỀN</b> (bước 4/4)\n\nGửi <b>hạn dùng</b> (VD: 24h, 2d), hoặc bấm Bỏ qua (vĩnh viễn).",
                    parse_mode="HTML", reply_markup=_admm_skip_kb("⏭ Vĩnh viễn"))
                await cb.answer()
                return
            if st == AdmMenuState.promo_expire.state:
                d = await state.get_data()
                await _admm_show_confirm_cb(cb, state, "💵 <b>XÁC NHẬN TẠO MÃ TIỀN</b>",
                                           [f"🏷 Prefix: <b>{html.escape(str(d['prefix']))}</b>",
                                            f"💰 Giá trị: <b>{_util.vnd(d['amount'])}</b>",
                                            f"👥 Lượt dùng: <b>{d.get('uses') or 1}</b>",
                                            "⏳ Hạn: <b>vĩnh viễn</b>"],
                                           f"/adm promo {d['prefix']} {d['amount']} {d.get('uses') or 1} 0")
                return
            await cb.answer()
            return

        # ── Xác nhận / Huỷ ──
        if action == "confirm":
            data = await state.get_data()
            cmd_text = data.get("cmd_text")
            if not cmd_text:
                await cb.answer("Hết hạn, làm lại từ đầu.", show_alert=True)
                return
            await _admm_exec_via_cb(cb, state, cmd_text)
            return

        if action == "cancel":
            await state.clear()
            await cb.message.edit_text(_admm_text_main(), parse_mode="HTML",
                                      reply_markup=_admm_main_kb())
            await cb.answer("Đã huỷ.")
            return

        await cb.answer()

    # ── FSM: nhập liệu từng bước ──────────────────────────────────────────

    @target_router.message(AdmMenuState.topup_uid)
    async def _admm_topup_uid(msg: Message, state: FSMContext):
        if await _admm_guard(msg, state):
            return
        uid = _admm_parse_uid(msg.text)
        if not uid:
            await msg.answer("❌ User ID phải là số. Gửi lại hoặc /huy để huỷ.")
            return
        if not db.get_user(uid):
            await msg.answer(f"❌ Không tìm thấy user <code>{uid}</code>! Gửi lại ID khác hoặc /huy để huỷ.",
                             parse_mode="HTML")
            return
        await state.update_data(uid=uid)
        await state.set_state(AdmMenuState.topup_amount)
        await msg.answer("💰 <b>CỘNG TIỀN</b> (bước 2/2)\n\nGửi <b>số tiền</b> (VD: 50000 hoặc 50k).\nGõ /huy để huỷ.",
                         parse_mode="HTML")

    @target_router.message(AdmMenuState.topup_amount)
    async def _admm_topup_amount(msg: Message, state: FSMContext):
        if await _admm_guard(msg, state):
            return
        from . import util as _util
        amount = _admm_parse_amount(msg.text)
        if not amount or amount <= 0:
            await msg.answer("❌ Số tiền không hợp lệ. Gửi lại hoặc /huy để huỷ.")
            return
        data = await state.get_data()
        uid = data["uid"]
        user = db.get_user(uid)
        await _admm_show_confirm_msg(
            msg, state, "💰 <b>XÁC NHẬN CỘNG TIỀN</b>",
            [f"👤 User: <code>{uid}</code> ({html.escape(str((user or {}).get('name') or ''))})",
             f"💵 Số tiền: <b>{_util.vnd(amount)}</b>"],
            f"/adm topup {uid} {amount}")

    @target_router.message(AdmMenuState.setbal_uid)
    async def _admm_setbal_uid(msg: Message, state: FSMContext):
        if await _admm_guard(msg, state):
            return
        uid = _admm_parse_uid(msg.text)
        if not uid:
            await msg.answer("❌ User ID phải là số. Gửi lại hoặc /huy để huỷ.")
            return
        if not db.get_user(uid):
            await msg.answer(f"❌ Không tìm thấy user <code>{uid}</code>! Gửi lại ID khác hoặc /huy để huỷ.",
                             parse_mode="HTML")
            return
        await state.update_data(uid=uid)
        await state.set_state(AdmMenuState.setbal_amount)
        await msg.answer("✏️ <b>SET SỐ DƯ</b> (bước 2/2)\n\nGửi <b>số dư mới</b> (VD: 100000 hoặc 100k).\nGõ /huy để huỷ.",
                         parse_mode="HTML")

    @target_router.message(AdmMenuState.setbal_amount)
    async def _admm_setbal_amount(msg: Message, state: FSMContext):
        if await _admm_guard(msg, state):
            return
        from . import util as _util
        amount = _admm_parse_amount(msg.text)
        if amount is None or amount < 0:
            await msg.answer("❌ Số tiền không hợp lệ. Gửi lại hoặc /huy để huỷ.")
            return
        data = await state.get_data()
        uid = data["uid"]
        user = db.get_user(uid)
        cur = (user or {}).get("balance", 0)
        await _admm_show_confirm_msg(
            msg, state, "✏️ <b>XÁC NHẬN SET SỐ DƯ</b>",
            [f"👤 User: <code>{uid}</code>",
             f"💳 Số dư hiện tại: <b>{_util.vnd(cur)}</b>",
             f"💳 Số dư mới: <b>{_util.vnd(amount)}</b>"],
            f"/adm setbal {uid} {amount}")

    @target_router.message(AdmMenuState.ban_uid)
    async def _admm_ban_uid(msg: Message, state: FSMContext):
        if await _admm_guard(msg, state):
            return
        uid = _admm_parse_uid(msg.text)
        if not uid:
            await msg.answer("❌ User ID phải là số. Gửi lại hoặc /huy để huỷ.")
            return
        await state.update_data(uid=uid)
        await state.set_state(AdmMenuState.ban_reason)
        await msg.answer("🔴 <b>KHOÁ TÀI KHOẢN</b> (bước 2/2)\n\nGửi <b>lý do</b> khoá, hoặc bấm Bỏ qua.",
                         parse_mode="HTML", reply_markup=_admm_skip_kb())

    @target_router.message(AdmMenuState.ban_reason)
    async def _admm_ban_reason(msg: Message, state: FSMContext):
        if await _admm_guard(msg, state):
            return
        reason = (msg.text or "").strip()
        if not reason:
            await msg.answer("❌ Lý do trống. Gửi lại, bấm Bỏ qua, hoặc /huy để huỷ.",
                             reply_markup=_admm_skip_kb())
            return
        data = await state.get_data()
        await _admm_show_confirm_msg(
            msg, state, "🔴 <b>XÁC NHẬN KHOÁ</b>",
            [f"👤 User: <code>{data['uid']}</code>",
             f"📝 Lý do: {html.escape(reason)}"],
            f"/adm ban {data['uid']} {reason}")

    @target_router.message(AdmMenuState.unban_uid)
    async def _admm_unban_uid(msg: Message, state: FSMContext):
        if await _admm_guard(msg, state):
            return
        uid = _admm_parse_uid(msg.text)
        if not uid:
            await msg.answer("❌ User ID phải là số. Gửi lại hoặc /huy để huỷ.")
            return
        await _admm_show_confirm_msg(
            msg, state, "🟢 <b>XÁC NHẬN MỞ KHOÁ</b>",
            [f"👤 User: <code>{uid}</code>"],
            f"/adm unban {uid}")

    @target_router.message(AdmMenuState.setvip_uid)
    async def _admm_setvip_uid(msg: Message, state: FSMContext):
        if await _admm_guard(msg, state):
            return
        uid = _admm_parse_uid(msg.text)
        if not uid:
            await msg.answer("❌ User ID phải là số. Gửi lại hoặc /huy để huỷ.")
            return
        if not db.get_user(uid):
            await msg.answer(f"❌ Không tìm thấy user <code>{uid}</code>! Gửi lại ID khác hoặc /huy để huỷ.",
                             parse_mode="HTML")
            return
        await state.update_data(uid=uid)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Thường (0)", callback_data="admm:setvip_lv_0"),
             InlineKeyboardButton(text="VIP 1 🥉", callback_data="admm:setvip_lv_1")],
            [InlineKeyboardButton(text="VIP 2 🥈", callback_data="admm:setvip_lv_2"),
             InlineKeyboardButton(text="VIP 3 🥇", callback_data="admm:setvip_lv_3")],
            [InlineKeyboardButton(text="◀️ Quay lại menu Admin", callback_data="admm:main")],
        ])
        await msg.answer(f"⭐ <b>SET VIP</b> (bước 2/3)\n\nChọn cấp VIP cho <code>{uid}</code>:",
                         parse_mode="HTML", reply_markup=kb)

    @target_router.message(AdmMenuState.setvip_days)
    async def _admm_setvip_days(msg: Message, state: FSMContext):
        if await _admm_guard(msg, state):
            return
        try:
            days = int((msg.text or "").strip())
            if days < 0:
                raise ValueError
        except ValueError:
            await msg.answer("❌ Số ngày phải là số ≥ 0. Gửi lại, bấm Bỏ qua, hoặc /huy để huỷ.",
                             reply_markup=_admm_skip_kb("⏭ Không gia hạn"))
            return
        data = await state.get_data()
        labels = {0: "Thường", 1: "VIP 1 🥉", 2: "VIP 2 🥈", 3: "VIP 3 🥇"}
        lv = data.get("level", 0)
        await _admm_show_confirm_msg(
            msg, state, "⭐ <b>XÁC NHẬN SET VIP</b>",
            [f"👤 User: <code>{data['uid']}</code>",
             f"⭐ Cấp: <b>{labels.get(lv, lv)}</b>",
             f"⏳ Gia hạn thêm: <b>{days} ngày</b>"],
            f"/adm setvip {data['uid']} {lv} {days}")

    @target_router.message(AdmMenuState.info_uid)
    async def _admm_info_uid(msg: Message, state: FSMContext):
        if await _admm_guard(msg, state):
            return
        uid = _admm_parse_uid(msg.text)
        if not uid:
            await msg.answer("❌ User ID phải là số. Gửi lại hoặc /huy để huỷ.")
            return
        await _admm_exec_via_msg(msg, state, f"/adm info {uid}",
                                 restore_state=AdmMenuState.info_uid)
        await msg.answer("Gửi <b>User ID</b> khác để xem tiếp, hoặc /huy để huỷ.",
                         parse_mode="HTML")

    @target_router.message(AdmMenuState.find_query)
    async def _admm_find_query(msg: Message, state: FSMContext):
        if await _admm_guard(msg, state):
            return
        q = (msg.text or "").strip()
        if not q:
            await msg.answer("❌ Từ khoá trống. Gửi lại hoặc /huy để huỷ.")
            return
        await _admm_exec_via_msg(msg, state, f"/adm find {q}",
                                 restore_state=AdmMenuState.find_query)
        await msg.answer("Gửi <b>@username</b>/<b>ID</b> khác để tìm tiếp, hoặc /huy để huỷ.",
                         parse_mode="HTML")

    @target_router.message(AdmMenuState.taopromo_code)
    async def _admm_taopromo_code(msg: Message, state: FSMContext):
        if await _admm_guard(msg, state):
            return
        code = (msg.text or "").strip().upper()
        if not code or len(code) > 24:
            await msg.answer("❌ Mã không hợp lệ (tối đa 24 ký tự). Gửi lại hoặc /huy để huỷ.")
            return
        await state.update_data(code=code)
        await state.set_state(AdmMenuState.taopromo_pct)
        await msg.answer("🎟️ <b>TẠO MÃ GIẢM %</b> (bước 2/4)\n\nGửi <b>phần trăm giảm</b> (1-90).",
                         parse_mode="HTML")

    @target_router.message(AdmMenuState.taopromo_pct)
    async def _admm_taopromo_pct(msg: Message, state: FSMContext):
        if await _admm_guard(msg, state):
            return
        try:
            pct = int((msg.text or "").strip())
        except ValueError:
            pct = 0
        if not 1 <= pct <= 90:
            await msg.answer("❌ Phần trăm phải từ 1 đến 90. Gửi lại hoặc /huy để huỷ.")
            return
        await state.update_data(pct=pct)
        await state.set_state(AdmMenuState.taopromo_uses)
        await msg.answer("🎟️ <b>TẠO MÃ GIẢM %</b> (bước 3/4)\n\nGửi <b>giới hạn lượt dùng</b>, hoặc bấm Bỏ qua (không giới hạn).",
                         parse_mode="HTML", reply_markup=_admm_skip_kb("⏭ Không giới hạn"))

    @target_router.message(AdmMenuState.taopromo_uses)
    async def _admm_taopromo_uses(msg: Message, state: FSMContext):
        if await _admm_guard(msg, state):
            return
        try:
            uses = int((msg.text or "").strip())
            if uses < 0:
                raise ValueError
        except ValueError:
            await msg.answer("❌ Số lượt phải là số ≥ 0. Gửi lại, bấm Bỏ qua, hoặc /huy để huỷ.",
                             reply_markup=_admm_skip_kb("⏭ Không giới hạn"))
            return
        await state.update_data(uses=uses)
        await state.set_state(AdmMenuState.taopromo_hours)
        await msg.answer("🎟️ <b>TẠO MÃ GIẢM %</b> (bước 4/4)\n\nGửi <b>số giờ hiệu lực</b>, hoặc bấm Bỏ qua (vĩnh viễn).",
                         parse_mode="HTML", reply_markup=_admm_skip_kb("⏭ Vĩnh viễn"))

    @target_router.message(AdmMenuState.taopromo_hours)
    async def _admm_taopromo_hours(msg: Message, state: FSMContext):
        if await _admm_guard(msg, state):
            return
        try:
            hours = int((msg.text or "").strip())
            if hours < 0:
                raise ValueError
        except ValueError:
            await msg.answer("❌ Số giờ phải là số ≥ 0. Gửi lại, bấm Bỏ qua, hoặc /huy để huỷ.",
                             reply_markup=_admm_skip_kb("⏭ Vĩnh viễn"))
            return
        data = await state.get_data()
        await _admm_show_confirm_msg(
            msg, state, "🎟️ <b>XÁC NHẬN TẠO MÃ</b>",
            [f"🎫 Mã: <code>{html.escape(str(data['code']))}</code>",
             f"💸 Giảm: <b>{data['pct']}%</b>",
             f"👥 Lượt dùng: <b>{'không giới hạn' if not data.get('uses') else data['uses']}</b>",
             f"⏳ Hiệu lực: <b>{'vĩnh viễn' if not hours else f'{hours} giờ'}</b>"],
            f"/adm taopromo {data['code']} {data['pct']} {data.get('uses') or 0} {hours}")

    @target_router.message(AdmMenuState.promo_prefix)
    async def _admm_promo_prefix(msg: Message, state: FSMContext):
        if await _admm_guard(msg, state):
            return
        prefix = (msg.text or "").strip().upper()
        if not prefix or len(prefix) > 12:
            await msg.answer("❌ Prefix không hợp lệ (tối đa 12 ký tự). Gửi lại hoặc /huy để huỷ.")
            return
        await state.update_data(prefix=prefix)
        await state.set_state(AdmMenuState.promo_amount)
        await msg.answer("💵 <b>TẠO MÃ TIỀN</b> (bước 2/4)\n\nGửi <b>giá trị mã</b> (VD: 50000 hoặc 50k).",
                         parse_mode="HTML")

    @target_router.message(AdmMenuState.promo_amount)
    async def _admm_promo_amount(msg: Message, state: FSMContext):
        if await _admm_guard(msg, state):
            return
        amount = _admm_parse_amount(msg.text)
        if not amount or amount <= 0:
            await msg.answer("❌ Số tiền không hợp lệ. Gửi lại hoặc /huy để huỷ.")
            return
        await state.update_data(amount=amount)
        await state.set_state(AdmMenuState.promo_uses)
        await msg.answer("💵 <b>TẠO MÃ TIỀN</b> (bước 3/4)\n\nGửi <b>số lượt dùng</b>, hoặc bấm Bỏ qua (1 lượt).",
                         parse_mode="HTML", reply_markup=_admm_skip_kb("⏭ 1 lượt"))

    @target_router.message(AdmMenuState.promo_uses)
    async def _admm_promo_uses(msg: Message, state: FSMContext):
        if await _admm_guard(msg, state):
            return
        try:
            uses = int((msg.text or "").strip())
            if uses < 1:
                raise ValueError
        except ValueError:
            await msg.answer("❌ Số lượt phải là số ≥ 1. Gửi lại, bấm Bỏ qua, hoặc /huy để huỷ.",
                             reply_markup=_admm_skip_kb("⏭ 1 lượt"))
            return
        await state.update_data(uses=uses)
        await state.set_state(AdmMenuState.promo_expire)
        await msg.answer("💵 <b>TẠO MÃ TIỀN</b> (bước 4/4)\n\nGửi <b>hạn dùng</b> (VD: 24h, 2d), hoặc bấm Bỏ qua (vĩnh viễn).",
                         parse_mode="HTML", reply_markup=_admm_skip_kb("⏭ Vĩnh viễn"))

    @target_router.message(AdmMenuState.promo_expire)
    async def _admm_promo_expire(msg: Message, state: FSMContext):
        if await _admm_guard(msg, state):
            return
        from . import util as _util
        expire = (msg.text or "").strip().lower()
        if parse_time_str(expire) <= 0:
            await msg.answer("❌ Hạn không hợp lệ (VD: 24h, 2d). Gửi lại, bấm Bỏ qua, hoặc /huy để huỷ.",
                             reply_markup=_admm_skip_kb("⏭ Vĩnh viễn"))
            return
        data = await state.get_data()
        await _admm_show_confirm_msg(
            msg, state, "💵 <b>XÁC NHẬN TẠO MÃ TIỀN</b>",
            [f"🏷 Prefix: <b>{html.escape(str(data['prefix']))}</b>",
             f"💰 Giá trị: <b>{_util.vnd(data['amount'])}</b>",
             f"👥 Lượt dùng: <b>{data['uses']}</b>",
             f"⏳ Hạn: <b>{html.escape(expire)}</b>"],
            f"/adm promo {data['prefix']} {data['amount']} {data['uses']} {expire}")

    @target_router.message(AdmMenuState.xoapromo_code)
    async def _admm_xoapromo_code(msg: Message, state: FSMContext):
        if await _admm_guard(msg, state):
            return
        code = (msg.text or "").strip().upper()
        if not code:
            await msg.answer("❌ Mã trống. Gửi lại hoặc /huy để huỷ.")
            return
        await _admm_show_confirm_msg(
            msg, state, "❌ <b>XÁC NHẬN XOÁ MÃ</b>",
            [f"🎫 Mã: <code>{html.escape(code)}</code>"],
            f"/adm xoapromo {code}")

    @target_router.message(AdmMenuState.flashsale_code)
    async def _admm_flashsale_code(msg: Message, state: FSMContext):
        if await _admm_guard(msg, state):
            return
        from . import util as _util
        code = (msg.text or "").strip().upper()
        ok, why, row = db.promo_valid(code)
        if not ok:
            await msg.answer(f"❌ {html.escape(str(why))} Gửi mã khác hoặc /huy để huỷ.",
                             parse_mode="HTML")
            return
        d = dict(row)
        exp_txt = f"\n⏰ Kết thúc: {_util.vn_time_str('%d/%m %H:%M', d['expires_at'])}" if d["expires_at"] else ""
        lim_txt = f"\n🎫 Còn {int(d['max_uses']) - int(d['used_count'])} suất" if d["max_uses"] else ""
        total = len(db.get_all_users_for_broadcast())
        await _admm_show_confirm_msg(
            msg, state, "🔥 <b>XÁC NHẬN FLASH SALE</b>",
            [f"🎟️ Mã: <code>{d['code']}</code>",
             f"💸 Giảm <b>{int(d['pct'])}%</b> khi mua gói credits{lim_txt}{exp_txt}",
             f"👥 Sẽ gửi tới <b>{total}</b> user"],
            f"/adm flashsale {code}")

    @target_router.message(AdmMenuState.broadcast_text)
    async def _admm_broadcast_text(msg: Message, state: FSMContext):
        if await _admm_guard(msg, state):
            return
        text_content = (msg.text or "").strip()
        if not text_content:
            await msg.answer("❌ Nội dung trống. Gửi lại hoặc /huy để huỷ.")
            return
        data = await state.get_data()
        target = data.get("bcast_target", "all")
        vip_only = target == "vip"
        inactive_days = 7 if target == "inactive" else 0
        users = db.get_all_users_for_broadcast(vip_only=vip_only, inactive_days=inactive_days)
        total = len(users)
        target_label = "VIP" if vip_only else ("inactive 7 ngày" if inactive_days else "tất cả")
        _pending_broadcasts[msg.chat.id] = {
            "text": text_content,
            "users": [u["tg_id"] for u in users],
            "bot": msg.bot,
        }
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"✅ Gửi ngay ({total} user)", callback_data="adm_bcast_confirm"),
            InlineKeyboardButton(text="❌ Hủy", callback_data="adm_bcast_cancel"),
        ]])
        await state.clear()
        await msg.answer(
            f"📢 Gửi tới <b>{total} user</b> ({target_label}):\n<i>{html.escape(text_content[:300])}</i>\n\nXác nhận?",
            parse_mode="HTML", reply_markup=kb)

    @target_router.message(AdmMenuState.webhook_key)
    async def _admm_webhook_key(msg: Message, state: FSMContext):
        if await _admm_guard(msg, state):
            return
        key = (msg.text or "").strip()
        if not key:
            await msg.answer("❌ Tên key trống. Gửi lại hoặc /huy để huỷ.")
            return
        if not db.get_reseller_by_name_or_id(key):
            await msg.answer(f"❌ Không tìm thấy key <b>{html.escape(key)}</b>. Gửi lại hoặc /huy để huỷ.",
                             parse_mode="HTML")
            return
        await state.update_data(key=key)
        await state.set_state(AdmMenuState.webhook_url)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🧹 Gỡ webhook", callback_data="admm:webhook_off")],
            [InlineKeyboardButton(text="◀️ Quay lại menu Admin", callback_data="admm:main")],
        ])
        await msg.answer("🔔 <b>WEBHOOK RESELLER</b> (bước 2/2)\n\nGửi <b>URL webhook</b> (bắt đầu bằng http:// hoặc https://), hoặc bấm Gỡ webhook.",
                         parse_mode="HTML", reply_markup=kb)

    @target_router.message(AdmMenuState.webhook_url)
    async def _admm_webhook_url(msg: Message, state: FSMContext):
        if await _admm_guard(msg, state):
            return
        url = (msg.text or "").strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            await msg.answer("❌ URL phải bắt đầu bằng http:// hoặc https://. Gửi lại hoặc /huy để huỷ.")
            return
        data = await state.get_data()
        key = data["key"]
        await _admm_show_confirm_msg(
            msg, state, "🔔 <b>XÁC NHẬN WEBHOOK</b>",
            [f"🔑 Key: <b>{html.escape(str(key))}</b>",
             f"🔗 URL: <code>{html.escape(url)}</code>"],
            f"/adm webhook {key} {url}")

    # ── Callback dùng chung: sửa các nút admin đang "chết" ở bot chính ────
    # (adm_bcast_confirm/cancel, adm_ban_, adm_unban_ hiện chỉ có handler ở
    # router admin_bot; adm_topup_ thì chưa có handler ở đâu cả)

    @target_router.callback_query(F.data == "adm_bcast_confirm")
    async def _admm_bcast_confirm(cb: CallbackQuery):
        if not is_admin(cb.message.chat.id, cb.from_user.id):
            await cb.answer("🚫 Không có quyền.", show_alert=True)
            return
        bcast = _pending_broadcasts.pop(cb.message.chat.id, None)
        if not bcast:
            await cb.answer("Đã hết hạn, vui lòng thực hiện lại!", show_alert=True)
            return
        await cb.answer("Đang gửi...")
        await cb.message.edit_reply_markup(reply_markup=None)
        target_bot = bcast.get("bot") or cb.bot
        success = fail = 0
        for tg_id in bcast["users"]:
            try:
                await target_bot.send_message(tg_id, bcast["text"], parse_mode="HTML")
                success += 1
                await asyncio.sleep(0.05)
            except Exception:
                fail += 1
        await cb.message.answer(f"✅ <b>Broadcast xong!</b>\n✔ {success} thành công | ✘ {fail} lỗi",
                                parse_mode="HTML")

    @target_router.callback_query(F.data == "adm_bcast_cancel")
    async def _admm_bcast_cancel(cb: CallbackQuery):
        _pending_broadcasts.pop(cb.message.chat.id, None)
        await cb.answer("Đã hủy.", show_alert=True)
        await cb.message.edit_reply_markup(reply_markup=None)

    @target_router.callback_query(F.data.startswith("adm_ban_"))
    async def _admm_ban_quick(cb: CallbackQuery):
        if not is_admin(cb.message.chat.id, cb.from_user.id):
            return
        try:
            uid = int(cb.data.split("_")[-1])
        except ValueError:
            return
        db.ban_user(uid)
        await cb.answer(f"Đã khoá {uid}", show_alert=True)
        await cb.message.edit_reply_markup(reply_markup=None)

    @target_router.callback_query(F.data.startswith("adm_unban_"))
    async def _admm_unban_quick(cb: CallbackQuery):
        if not is_admin(cb.message.chat.id, cb.from_user.id):
            return
        try:
            uid = int(cb.data.split("_")[-1])
        except ValueError:
            return
        db.unban_user(uid)
        await cb.answer(f"Đã mở khoá {uid}", show_alert=True)
        await cb.message.edit_reply_markup(reply_markup=None)

    @target_router.callback_query(F.data.startswith("adm_topup_"))
    async def _admm_topup_quick(cb: CallbackQuery, state: FSMContext):
        if not is_admin(cb.message.chat.id, cb.from_user.id):
            return
        try:
            uid = int(cb.data.split("_")[-1])
        except ValueError:
            return
        await state.update_data(uid=uid)
        await state.set_state(AdmMenuState.topup_amount)
        await cb.message.answer(
            f"💰 <b>CỘNG TIỀN</b> cho <code>{uid}</code>\n\nGửi <b>số tiền</b> (VD: 50000 hoặc 50k).\nGõ /huy để huỷ.",
            parse_mode="HTML")
        await cb.answer()


    # ── Quản lý admin phụ (chỉ chủ shop) ──
    @target_router.callback_query(F.data.startswith("admx:"))
    async def _on_admx_cb(cb: CallbackQuery, state: FSMContext):
        print(f"DEBUG admx cb: data={cb.data!r} from={cb.from_user.id}", flush=True)
        if not _perms.is_super(cb.from_user.id):
            await cb.answer("🚫 Chỉ chủ shop mới quản lý được admin.",
                            show_alert=True)
            return
        action = (cb.data or "")[5:]

        if action == "list":
            await state.clear()
            await cb.message.edit_text(_admx_list_text(), parse_mode="HTML",
                                       reply_markup=_admx_list_kb())
            await cb.answer()
            return

        if action == "audit":
            await state.clear()
            await cb.message.edit_text(
                _audit_text(0), parse_mode="HTML",
                reply_markup=_audit_kb(0))
            await cb.answer()
            return

        if action.startswith("audit:"):
            try:
                page = max(0, int(action[6:]))
            except Exception:
                page = 0
            await cb.message.edit_text(
                _audit_text(page), parse_mode="HTML",
                reply_markup=_audit_kb(page))
            await cb.answer()
            return

        if action == "add":
            await state.clear()
            await state.set_state(AdmMenuState.admadd_id)
            await cb.message.edit_text(
                "➕ <b>THÊM ADMIN</b>\n\nGửi <b>Telegram ID</b> của người cần cấp quyền "
                "(lấy ID qua @userinfobot).\n\nGõ /huy để huỷ.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="❌ Huỷ",
                                          callback_data="admx:list")]]))
            await cb.answer()
            return

        if action.startswith("toggle:"):
            p = action[7:]
            data = await state.get_data()
            cur = set(data.get("admx_perms") or [])
            if p in _perms.PERM_LABEL:
                cur = (cur - {p}) if p in cur else (cur | {p})
                await state.update_data(admx_perms=sorted(cur))
            mode = data.get("admx_mode")
            aid = data.get("admx_id")
            if mode == "add":
                title = f"➕ <b>THÊM ADMIN</b> <code>{aid}</code>"
                save_cb = "admx:save"
            else:
                title = f"🔑 <b>SỬA QUYỀN</b> <code>{aid}</code>"
                save_cb = f"admx:saveedit:{aid}"
            await cb.message.edit_text(
                f"{title}\n\nTick chọn các quyền được phép "
                f"(<i>đang chọn {len(cur)}/{len(_perms.PERMS)}</i>):",
                parse_mode="HTML", reply_markup=_admx_perm_kb(cur, save_cb))
            await cb.answer()
            return

        if action == "save":
            data = await state.get_data()
            aid = data.get("admx_id")
            cur = set(data.get("admx_perms") or [])
            if not aid:
                await cb.answer("Thiếu ID, thử lại.", show_alert=True)
                return
            db.extra_admin_add(int(aid), _admx_name_of(int(aid)),
                               ",".join(sorted(cur)), cb.from_user.id)
            db.admin_audit_add(cb.from_user.id, cb.from_user.full_name,
                               "them_admin", f"{aid} quyen=[{','.join(sorted(cur))}]")
            try:
                await cb.bot.send_message(
                    int(aid),
                    "🎉 <b>Bạn đã được cấp quyền quản trị!</b>\n"
                    "Gõ /adm để mở menu quản trị.",
                    parse_mode="HTML")
            except Exception:
                pass
            await state.clear()
            await cb.message.edit_text(
                f"✅ Đã thêm admin <code>{aid}</code>.\n\n" + _admx_list_text(),
                parse_mode="HTML", reply_markup=_admx_list_kb())
            await cb.answer()
            return

        if action.startswith("edit:"):
            aid = action[5:]
            row = db.extra_admin_get(aid)
            if not row:
                await cb.answer("Admin không tồn tại.", show_alert=True)
                return
            cur = set((row.get("perms") or "").split(",")) & set(_perms.PERM_LABEL)
            await state.clear()
            await state.update_data(admx_mode="edit", admx_id=int(aid),
                                    admx_perms=sorted(cur))
            await cb.message.edit_text(
                f"🔑 <b>SỬA QUYỀN</b> <code>{aid}</code> "
                f"({html.escape(row.get('name') or '')})\n\n"
                f"Tick chọn các quyền được phép:",
                parse_mode="HTML",
                reply_markup=_admx_perm_kb(cur, f"admx:saveedit:{aid}"))
            await cb.answer()
            return

        if action.startswith("saveedit:"):
            aid = action[9:]
            if not (aid or "").isdigit():
                await cb.answer("Lỗi dữ liệu, thử lại.", show_alert=True)
                return
            data = await state.get_data()
            cur = set(data.get("admx_perms") or [])
            db.extra_admin_set_perms(int(aid), ",".join(sorted(cur)))
            db.admin_audit_add(cb.from_user.id, cb.from_user.full_name,
                               "sua_quyen_admin", f"{aid} quyen=[{','.join(sorted(cur))}]")
            await state.clear()
            await cb.message.edit_text(
                "✅ Đã cập nhật quyền.\n\n" + _admx_list_text(),
                parse_mode="HTML", reply_markup=_admx_list_kb())
            await cb.answer()
            return

        if action.startswith("del:"):
            aid = action[4:]
            row = db.extra_admin_get(aid)
            nm = (row or {}).get("name") or aid
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑️ Xoá luôn",
                                      callback_data=f"admx:delyes:{aid}"),
                 InlineKeyboardButton(text="❌ Huỷ",
                                      callback_data="admx:list")]])
            await cb.message.edit_text(
                f"Xoá quyền admin của <code>{aid}</code> "
                f"({html.escape(str(nm))})?",
                parse_mode="HTML", reply_markup=kb)
            await cb.answer()
            return

        if action.startswith("delyes:"):
            aid = action[7:]
            if (aid or "").isdigit():
                db.extra_admin_del(int(aid))
                db.admin_audit_add(cb.from_user.id, cb.from_user.full_name,
                                   "xoa_admin", f"{aid}")
            await state.clear()
            await cb.message.edit_text(
                "✅ Đã xoá.\n\n" + _admx_list_text(),
                parse_mode="HTML", reply_markup=_admx_list_kb())
            await cb.answer()
            return

        await cb.answer()

    @target_router.message(AdmMenuState.admadd_id)
    async def _on_admadd_id(msg: Message, state: FSMContext):
        if not _perms.is_super(msg.from_user.id):
            await state.clear()
            return
        if (msg.text or "").strip() == "/huy":
            await state.clear()
            await msg.answer("Đã huỷ.", reply_markup=_admm_back_kb())
            return
        try:
            aid = int((msg.text or "").strip())
        except Exception:
            await msg.answer("⚠️ ID phải là số. Gửi lại hoặc gõ /huy để huỷ.")
            return
        if _perms.is_super(aid):
            await msg.answer("⚠️ Đây là ID chủ shop rồi, không cần thêm. "
                             "Gửi ID khác hoặc /huy.")
            return
        if db.extra_admin_get(aid):
            await msg.answer("⚠️ ID này đã là admin. Gửi ID khác hoặc /huy.")
            return
        await state.update_data(admx_mode="add", admx_id=aid, admx_perms=[])
        await msg.answer(
            f"➕ <b>THÊM ADMIN</b> <code>{aid}</code> "
            f"({html.escape(_admx_name_of(aid))})\n\n"
            f"Tick chọn các quyền được phép:",
            parse_mode="HTML",
            reply_markup=_admx_perm_kb(set(), "admx:save"))

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

        "<b>🔑 API RESELLER</b>\n"
        "• <code>/taokey &lt;tên shop&gt; [credits]</code>\n"
        "  👉 <i>Tạo API key cho reseller. Key chỉ hiện 1 lần!</i>\n"
        "  💡 VD: <code>/taokey shopA 1000</code>\n\n"

        "• <code>/napkey &lt;key_id&gt; &lt;credits&gt;</code>\n"
        "  👉 <i>Nạp thêm credits cho key.</i>\n"
        "  💡 VD: <code>/napkey 3 500</code>\n\n"

        "• <code>/khoakey &lt;key_id&gt; [on|off]</code>\n"
        "  👉 <i>Khóa/mở API key (VD: <code>/khoakey 3 off</code>).</i>\n\n"

        "<b>🍪 COOKIE POOL FACEBOOK</b>\n"
        "• <code>/cookieadd</code>\n"
        "  👉 <i>Thêm cookie vào pool xoay vòng (gửi cookie ở tin nhắn tiếp theo, bot tự xóa).</i>\n\n"

        "• <code>/cookielist</code> — <i>Xem pool cookie (đã che).</i>\n"
        "• <code>/cookiedel &lt;stt&gt;</code> — <i>Xóa cookie khỏi pool.</i>\n\n"

        
        "<b>🛒 SHOP ACC FACEBOOK</b>\n"
        "💡 <i>Dùng /shopadm để thao tác bằng nút bấm.</i>\n"
        "• <code>/themloai &lt;tên&gt; | &lt;giá&gt; | &lt;giờ_BH&gt; | [mô_tả]</code> — Thêm loại acc mới\n"
        "• <code>/xoaloai &lt;id&gt;</code> — Ẩn loại acc khỏi shop (tên vẫn giữ)\n"
        "• <code>/hienloai &lt;id&gt;</code> — Hiện lại loại acc đã ẩn\n"
        "• <code>/xoahan &lt;id&gt; yes</code> — <i>XÓA HẲN loại acc (không khôi phục được).</i>\n"
        "• <code>/xoakho &lt;id&gt; yes</code> — <i>Xóa toàn bộ acc CHƯA BÁN trong kho của 1 loại.</i>\n"
        "• <code>/themacc &lt;id_loại&gt; [ncc_id] [giá_vốn]</code> — Nhập kho (gửi file ở tin tiếp theo)\n"
        "• <code>/setsheet &lt;link&gt; [tab]</code> — Cài đặt Google Sheet nhập kho\n"
        "• <code>/nhapkhosheet &lt;id_loại&gt; [ncc_id] [giá_vốn]</code> — Nhập kho từ Sheet (chỉ quét dòng chưa đánh dấu)\n"
        "• <code>/kho</code> — Xem tồn kho (kể cả loại đã tự ẩn)\n"
        "• <code>/xuatkho [id_loại]</code> — <i>Xuất toàn bộ acc ra file .xlsx (sao lưu dự phòng).</i>\n"
        "• <code>/gia &lt;id&gt; &lt;giá_mới&gt;</code> — Đổi giá bán\n"
        "• <code>/creditbonus &lt;id&gt; &lt;số&gt;</code> — Combo mua acc tặng credits\n"
        "• <code>/quadoi &lt;id_loại&gt;</code> — Chọn quà đổi điểm loyalty\n"
        "• <code>/giovang &lt;id|0&gt; [giờ] [%]</code> / <code>off</code> — Giờ vàng giảm giá\n"
        "• <code>/hopmugia &lt;giá&gt;</code> (0 = tắt) — Bật/tắt hộp mù\n"
        "• <code>/hopmu &lt;id&gt;</code> — Cho loại acc tham gia/rời hộp mù\n"
        "• <code>/accinfo &lt;uid&gt;</code> — Truy xuất hành trình 1 acc\n"
        "• <code>/donhang</code> — Đơn hàng gần đây\n"
        "• <code>/bhdon</code> — Đơn BH chờ duyệt | <code>/bhdone &lt;id&gt;</code> — Duyệt xong\n"
        "• <code>/suabh &lt;id_loại&gt; &lt;giờ&gt;</code> — Đổi thời gian bảo hành\n"
        "• <code>/lo &lt;id_loại&gt;</code> — Xem lãi từng lô nhập\n"
        "• <code>/anhbia &lt;id_loại&gt;</code> — Đặt ảnh bìa (gửi ảnh ở tin tiếp theo) | <code>xoa</code> để gỡ\n"
        "• <code>/setmailapp &lt;link&gt;</code> — Đặt link tải app mail ảo hiện cho khách sau khi mua\n"
        "• <code>/recheck [số_ngày]</code> — Quét LIVE toàn bộ kho ngay (acc DIE → cách ly) | đặt chu kỳ tự động (mặc định 3 ngày)\n"
        "• <code>/faq</code> — Xem FAQ | <code>/themcauhoi &lt;kw&gt; | &lt;trả_lời&gt;</code> — Thêm | <code>/xoacauhoi &lt;số&gt;</code> — Xóa\n\n"

        "<b>🏭 NHÀ CUNG CẤP</b>\n"
        "• <code>/themncc &lt;tên&gt; | &lt;liên_hệ&gt;</code> — Thêm NCC vào sổ\n"
        "• <code>/ncc</code> — Sổ NCC (⭐ tay + tỉ lệ sống tự động)\n"
        "• <code>/danhgiancc &lt;id&gt; &lt;sao 1-5&gt;</code> — Đánh giá tay\n"
        "• <code>/chamdiem &lt;id&gt; [số_ngày=7]</code> — Chấm tỉ lệ sống theo lô\n"
        "• <code>/nccauto &lt;url_file&gt; &lt;id_loại&gt; [ncc_id]</code> / <code>off</code> — Nhập kho tự động 6h sáng\n\n"

"<b>🎟️ FLASH SALE (mã giảm giá)</b>\n"
        "• <code>/adm taopromo &lt;CODE&gt; &lt;%&gt; [lượt] [giờ]</code>\n"
        "  💡 VD: <code>/adm taopromo SALE20 20 100 24</code>\n\n"
        "• <code>/adm dspromo</code> — <i>Xem các mã đang có.</i>\n"
        "• <code>/adm xoapromo &lt;CODE&gt;</code> — <i>Xóa mã.</i>\n"
        "• <code>/adm flashsale &lt;CODE&gt;</code> — <i>Gửi thông báo sale cho toàn bộ user.</i>\n\n"

        "<b>🔔 WEBHOOK RESELLER</b>\n"
        "• <code>/adm webhook &lt;tên_key|id&gt; &lt;url|off&gt;</code>\n"
        "  👉 <i>Mỗi lượt API sẽ POST kết quả về URL.</i>\n"
        "  💡 VD: <code>/adm webhook shopA https://site.com/hook</code>\n\n"

        "<i>Chỉ Admin ID được cấp phép mới sử dụng được các lệnh này.</i>"
    )
    await _answer_long(msg, help_text)



_pending_broadcasts = {}


@router.message(Command("adm"))
async def admin_bot_adm(msg: Message, state: FSMContext):
    """Handler /adm trong admin_bot — chuyển tới _handle_adm_cmd."""
    await state.clear()
    await _handle_adm_cmd(msg, bot_instance=manager.bot)


class CookieAddState(StatesGroup):
    waiting_for_cookie = State()


def _mask_cookie(ck: str) -> str:
    ck = ck or ""
    return (ck[:10] + "..." + ck[-6:]) if len(ck) > 20 else "***"


@router.message(Command("taokey"))
async def adm_taokey(msg: Message):
    """Admin bot: tạo API key cho reseller. /taokey <tên shop> [số credits]"""
    if not is_admin(msg.chat.id, msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("⚠️ Cú pháp: <code>/taokey &lt;tên shop&gt; [số credits]</code>\nVí dụ: <code>/taokey shopA 1000</code>")
        return
    name = parts[1]
    credits = 0
    if len(parts) > 2:
        try:
            credits = max(0, int(parts[2]))
        except ValueError:
            await msg.answer("❌ Số credits phải là số nguyên!")
            return
    k = db.create_reseller_key(name, credits)
    await msg.answer(
        "🔑 <b>TẠO API KEY THÀNH CÔNG</b>\n\n"
        f"🏪 Shop: <b>{html.escape(name)}</b>\n"
        f"🆔 Key ID: <code>{k['id']}</code>\n"
        f"⚡ Credits: <b>{k['credits']}</b>\n\n"
        f"🔐 API Key:\n<code>{k['api_key']}</code>\n\n"
        "⚠️ <i>Gửi key này cho reseller qua kênh riêng. Key chỉ hiện 1 lần!</i>\n\n"
        "📖 Tài liệu API:\n"
        "• <code>POST /api/v1/fb/check</code> — header <code>X-API-Key</code>, body <code>{\"uid\": \"...\"}</code>\n"
        "• <code>POST /api/v1/fb/getuid</code> — body <code>{\"link\": \"...\"}</code>\n"
        "• <code>GET /api/v1/balance</code> — xem credits\n"
        "• <code>GET /api/v1/usage</code> — lịch sử dùng",
        parse_mode="HTML",
    )


@router.message(Command("napkey"))
async def adm_napkey(msg: Message):
    """Admin bot: nạp credits cho reseller key. /napkey <key_id> <số credits>"""
    if not is_admin(msg.chat.id, msg.from_user.id):
        return
    parts = (msg.text or "").split()
    if len(parts) < 3:
        await msg.answer("⚠️ Cú pháp: <code>/napkey &lt;key_id&gt; &lt;số credits&gt;</code>")
        return
    try:
        key_id, n = int(parts[1]), int(parts[2])
    except ValueError:
        await msg.answer("❌ key_id và số credits phải là số!")
        return
    db.add_reseller_credits(key_id, n)
    await msg.answer(f"✅ Đã nạp <b>{n}</b> credits cho key <code>{key_id}</code>.", parse_mode="HTML")


@router.message(Command("khoakey"))
async def adm_khoakey(msg: Message):
    """Admin bot: khóa/mở API key reseller. /khoakey <key_id> [on|off]"""
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


@router.message(Command("cookieadd"))
async def adm_cookieadd(msg: Message, state: FSMContext):
    """Admin bot: thêm cookie vào pool xoay vòng (1 bước hoặc 2 bước)."""
    if not is_admin(msg.chat.id, msg.from_user.id):
        return
    inline_ck = (msg.text or "").partition(" ")[2].strip()
    if inline_ck:
        await _adm_save_pool_cookie(msg, state, inline_ck)
        return
    await state.set_state(CookieAddState.waiting_for_cookie)
    await msg.answer(
        "🍪 <b>THÊM COOKIE VÀO POOL</b>\n\n"
        "Hãy gửi <b>chuỗi cookie Facebook</b> (1 tin nhắn).\n"
        "Sau khi lưu, tin nhắn chứa cookie sẽ được <b>xóa ngay</b> để bảo mật.\n\n"
        "Gõ /cancel để hủy.",
        parse_mode="HTML",
    )


async def _adm_save_pool_cookie(msg: Message, state: FSMContext, ck_text: str):
    if "c_user" not in ck_text and "xs" not in ck_text:
        await msg.answer("❌ Chuỗi này không giống cookie Facebook (thiếu c_user/xs).")
        return
    from .fb import get_fb_cookie_pool, set_fb_cookie_pool
    pool = get_fb_cookie_pool()
    if ck_text in pool:
        await msg.answer("⚠️ Cookie này đã có trong pool rồi.")
    else:
        pool.append(ck_text)
        set_fb_cookie_pool(pool)
        await msg.answer(f"✅ Đã thêm cookie vào pool. Pool hiện có <b>{len(pool)}</b> cookie.", parse_mode="HTML")
    try:
        await msg.delete()
    except Exception:
        pass
    await state.clear()


@router.message(CookieAddState.waiting_for_cookie)
async def adm_cookieadd_received(msg: Message, state: FSMContext):
    if not is_admin(msg.chat.id, msg.from_user.id):
        await state.clear()
        return
    if (msg.text or "").strip() == "/cancel":
        await state.clear()
        await msg.answer("Đã hủy.")
        return
    ck_text = (msg.text or "").strip()
    if ck_text.startswith("/cookieadd"):
        ck_text = ck_text.partition(" ")[2].strip()
    await _adm_save_pool_cookie(msg, state, ck_text)


@router.message(Command("cookielist"))
async def adm_cookielist(msg: Message):
    """Admin bot: xem danh sách cookie trong pool (đã che)."""
    if not is_admin(msg.chat.id, msg.from_user.id):
        return
    from .fb import get_fb_cookie_pool
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
async def adm_cookiedel(msg: Message):
    """Admin bot: xóa cookie khỏi pool. /cookiedel <số thứ tự>"""
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
    from .fb import get_fb_cookie_pool, set_fb_cookie_pool
    pool = get_fb_cookie_pool()
    if idx < 0 or idx >= len(pool):
        await msg.answer("❌ Số thứ tự không đúng!")
        return
    pool.pop(idx)
    set_fb_cookie_pool(pool)
    await msg.answer(f"✅ Đã xóa. Pool còn <b>{len(pool)}</b> cookie.", parse_mode="HTML")


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


register_adm_menu(router)
