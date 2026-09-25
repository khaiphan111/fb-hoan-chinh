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

from .core import MENU, router
from .common import process_tiktok_check

@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def on_other(msg: Message):
    text = msg.text or ""
    # 5.11 FAQ tu dong: truoc day handler rieng bi on_other che mat
    # (cung filter, dang ky sau) nen chua bao gio chay -> gop vao day.
    ans = db.shop_faq_match(text)
    if ans:
        await msg.answer(ans, parse_mode="HTML")
        return
    username = parse_username(text)
    if username:
        await process_tiktok_check(msg, username)
    else:
        await msg.answer(
            "💡 Gõ /tiktok &lt;username&gt; để check TikTok.\n"
            "Hoặc /help để xem hướng dẫn.",
            reply_markup=MENU,
        )

__all__ = [
    "on_other",
]