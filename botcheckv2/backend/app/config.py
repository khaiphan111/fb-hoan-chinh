# FB Live/Die Checker — Tác giả: @nhanxp | Hỗ trợ: Telegram/Facebook nhanxp
import os
import sys

APP_NAME = "FB Live/Die Checker"
APP_VERSION = "1.0.0"
AUTHOR = "@khaikhai998"
SUPPORT_TELEGRAM = "nhanxp"
SUPPORT_FACEBOOK = "nhanxp"

PORT = int(os.environ.get("PORT", 8000))

DEFAULT_FB_AVATAR_TOKEN = "6628568379|c1e620fa708a1d5696fb991c1bde5662"

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

DEFAULT_SETTINGS = {
    "bot_token": "8706191019:AAEq39A1Th4rmJdtp1yxJCOj9rec8sC1o0g",
    "admin_password": "Khai16022006$",
    "price_1d": "5000",
    "price_7d": "20000",
    "price_1m": "50000",
    "poll_interval": "60",
    "fb_avatar_token": DEFAULT_FB_AVATAR_TOKEN,
    "fb_cookie": "",
    "setup_done": "0",
    "enable_free_trial": "1",
    "free_trial_days": "3",
    "bank_name": "",
    "bank_account": "",
    "bank_owner": "",
    "admin_zalo_id": "",
    "admin_bot_token": "7712225012:AAEBAFvRGPImyeI_vRK0nctaM44ADNDFrOo",
    "admin_tg_id": "5964340237",
    "admin_tg_group_id": "",
    "zalo_bot_token": "432129301271685100:ixeCEYzILfjbxmpSbxTHwGXxTnkzCPFfflvmQmFDiTKUbJWtnWsXjokv",
    "web_domain": "https://app.khaikhaizzy.indevs.in",
}
