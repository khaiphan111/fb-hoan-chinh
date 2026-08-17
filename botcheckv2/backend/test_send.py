import asyncio
from app import db
from aiogram import Bot

async def main():
    bot_token = db.get_setting("bot_token")
    print("Token:", bot_token)
    bot = Bot(token=bot_token)
    users = db.list_users()
    for u in users:
        print("Sending to", u['tg_id'])
        try:
            msg = await bot.send_message(u['tg_id'], "Test message", parse_mode="HTML")
            print("Success!", msg.message_id)
        except Exception as e:
            print(f"Exception for {u['tg_id']}: {type(e).__name__} - {e}")
    await bot.session.close()

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(main())
