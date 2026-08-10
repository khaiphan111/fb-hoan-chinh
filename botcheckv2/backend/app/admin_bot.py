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

@router.message(Command("help"))
async def cmd_help(msg: Message):
    if not is_admin(msg.chat.id, msg.from_user.id):
        return
    help_text = (
        "🛠 <b>DANH SÁCH LỆNH ADMIN</b>\n\n"
        "• /phatcode &lt;số_tiền&gt; [số_lượt] [hạn_sử_dụng]\n  👉 <i>Tạo mã quà tặng chung.</i>\n\n"
        "• /phatcodeall &lt;số_tiền&gt; [hạn_sử_dụng]\n  👉 <i>Phát mã và gửi thông báo cho <b>toàn bộ user</b> trong hệ thống.</i>\n\n"
        "• /help - Xem danh sách lệnh này."
    )
    await msg.answer(help_text, parse_mode="HTML")

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
            import datetime
            expire_text = datetime.datetime.fromtimestamp(expire_at).strftime('%H:%M %d/%m/%Y')
            
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
        import datetime
        expire_text = datetime.datetime.fromtimestamp(expire_at).strftime('%H:%M %d/%m/%Y')
        
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

class AdminBotManager:
    def __init__(self):
        self.bot = None
        self.dp = Dispatcher()
        self.dp.include_router(router)
        self.task = None
        self.running = False

    async def start(self):
        token = db.get_setting("admin_bot_token")
        if not token:
            log.info("Admin bot token not found. Admin bot disabled.")
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
