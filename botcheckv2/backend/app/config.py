# FB Live/Die Checker — Tác giả: @nhanxp | Hỗ trợ: Telegram/Facebook nhanxp
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

APP_NAME = "FB Live/Die Checker"
APP_VERSION = "1.0.0"
AUTHOR = "@khaikhai998"
SUPPORT_TELEGRAM = "nhanxp"
SUPPORT_FACEBOOK = "nhanxp"

PORT = int(os.environ.get("PORT", 8000))

# Token dùng cho avatar FB — cấu hình qua biến môi trường, không hardcode
DEFAULT_FB_AVATAR_TOKEN = os.environ.get("FB_AVATAR_TOKEN", "")

FB_GRAPH = "https://graph.facebook.com"


def base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_dir() -> str:
    if getattr(sys, "frozen", False):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


DB_PATH = os.path.join(base_dir(), "data.db")
STATIC_DIR = os.path.join(resource_dir(), "static")

# Mặc định an toàn: không chứa token/mật khẩu/domain thật.
# Các giá trị nhạy cảm lấy từ biến môi trường hoặc nhập qua trang setup.
DEFAULT_SETTINGS = {
    "bot_token": os.environ.get("BOT_TOKEN", ""),
    "admin_password": "",
    "price_1d": "5000",
    "price_7d": "20000",
    "price_1m": "50000",
    "poll_interval": "60",
    "fb_avatar_token": os.environ.get("FB_AVATAR_TOKEN", ""),
    "fb_cookie": "",
    "setup_done": "0",
    "enable_free_trial": "1",
    "free_trial_days": "3",
    "bank_name": "",
    "bank_account": "",
    "bank_owner": "",
    "admin_zalo_id": "",
    "admin_bot_token": os.environ.get("ADMIN_BOT_TOKEN", ""),
    "admin_tg_id": os.environ.get("ADMIN_TG_ID", ""),
    "admin_tg_group_id": "",
    "zalo_bot_token": os.environ.get("ZALO_BOT_TOKEN", ""),
    "web_domain": os.environ.get("WEB_DOMAIN", ""),
}
