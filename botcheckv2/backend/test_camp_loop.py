import asyncio
from app import db
import time
import json
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile

async def main():
    bot = Bot(token=db.get_setting("bot_token"))
    
    text = "Test"
    ctype = "broadcast"
    config = {}
    
    kb = None
    buttons = []
    if buttons:
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        
    formatted_text = (
        f"📢 <b>THÔNG BÁO TỪ HỆ THỐNG</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{text}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Cảm ơn bạn đã đồng hành cùng chúng tôi!</i>"
    )
    
    users = db.list_users()
    success_count = 0
    
    for u in users:
        try:
            await bot.send_message(u["tg_id"], formatted_text, parse_mode="HTML", reply_markup=kb)
            success_count += 1
            print("Success for", u["tg_id"])
        except Exception as e:
            print(f"Error for {u['tg_id']}: {type(e).__name__} - {e}")
            
    print(f"Sent: {success_count}")
    await bot.session.close()

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(main())
