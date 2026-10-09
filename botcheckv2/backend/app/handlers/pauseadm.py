"""Flow tạm dừng / mở lại bot cho chủ shop.

- /tamdung: chọn lý do -> chọn thời gian mở lại -> xác nhận (kèm toggle job nền)
- /molai: mở lại ngay + báo cho khách đã nhận tin tạm dừng
- Nút trong /adm (admm:pause / admm:resume) cũng đi qua đây.

Chỉ chủ shop (super admin) được dùng.
"""
import html
import re
import time

from aiogram import F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from .. import db
from .. import pause as _pause
from .. import perms as _perms
from .core import _is_admin, router, log


class PauseFlow(StatesGroup):
    waiting_reason = State()
    waiting_until = State()


REASONS = ["🔧 Bảo trì hệ thống", "⬆️ Nâng cấp tính năng", "🎉 Nghỉ lễ / Tết"]
DURATIONS = [
    ("30 phút", 30), ("1 giờ", 60), ("2 giờ", 120),
    ("6 giờ", 360), ("12 giờ", 720), ("24 giờ", 1440),
]
MAX_MINUTES = _pause.MAX_MINUTES


def _is_super(uid: int) -> bool:
    try:
        return bool(_perms.is_super(uid))
    except Exception:
        return False



async def _cb_answer(cb, *args, **kwargs):
    """Tra loi callback an toan: query het han/khong hop le thi bo qua,
    khong de lam chet ca update (nguoi dung thay bot im re)."""
    try:
        await cb.answer(*args, **kwargs)
    except Exception:
        pass


async def _deny_super(msg: Message) -> bool:
    if not _is_super(msg.from_user.id):
        await msg.answer("🚫 Chỉ chủ shop mới được tạm dừng / mở lại bot.")
        return True
    return False


def _reason_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=r, callback_data=f"pause:reason:{i}")]
             for i, r in enumerate(REASONS)]
    rows.append([InlineKeyboardButton(text="✏️ Lý do khác (tự nhập)",
                                      callback_data="pause:reason_custom")])
    rows.append([InlineKeyboardButton(text="❌ Hủy",
                                      callback_data="pause:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _dur_kb() -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(DURATIONS), 2):
        row = [InlineKeyboardButton(text=DURATIONS[i][0],
                                    callback_data=f"pause:dur:{DURATIONS[i][1]}")]
        if i + 1 < len(DURATIONS):
            row.append(InlineKeyboardButton(
                text=DURATIONS[i + 1][0],
                callback_data=f"pause:dur:{DURATIONS[i + 1][1]}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🕐 Tự nhập thời gian",
                                      callback_data="pause:dur_custom")])
    rows.append([InlineKeyboardButton(text="❌ Hủy", callback_data="pause:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _confirm_kb(stop_jobs: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"⚙️ Job nền: {'⏸️ TẠM DỪNG' if stop_jobs else '▶️ vẫn chạy'}",
            callback_data="pause:jobs")],
        [InlineKeyboardButton(text="✅ Xác nhận tạm dừng",
                              callback_data="pause:confirm"),
         InlineKeyboardButton(text="❌ Hủy", callback_data="pause:cancel")],
    ])


def _confirm_text(d: dict) -> str:
    return (
        "⏸️ <b>XÁC NHẬN TẠM DỪNG BOT</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"🔧 Lý do: <b>{html.escape(d.get('reason') or '')}</b>\n"
        f"🕐 Mở lại lúc: <b>{_pause.fmt_until(d.get('until') or 0)}</b>\n"
        f"⚙️ Job nền: <b>{'tạm dừng' if d.get('stop_jobs') else 'vẫn chạy'}</b>\n\n"
        "Khách nhắn đến sẽ nhận tin báo tạm dừng (tối đa 1 tin/giờ/người). "
        "Giao dịch đang dở bị hủy, khách làm lại sau khi mở."
    )


async def _show_reason(target, state: FSMContext):
    await state.clear()
    txt = ("⏸️ <b>TẠM DỪNG BOT</b>\n"
           "━━━━━━━━━━━━━━━\n"
           "Chọn lý do tạm dừng:")
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(txt, parse_mode="HTML",
                                       reply_markup=_reason_kb())
        await target.answer()
    else:
        await target.answer(txt, parse_mode="HTML", reply_markup=_reason_kb())


async def _show_dur(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    await cb.message.edit_text(
        f"⏸️ <b>TẠM DỪNG BOT</b>\n━━━━━━━━━━━━━━━\n"
        f"🔧 Lý do: <b>{html.escape(d.get('reason') or '')}</b>\n\n"
        "Mở lại sau bao lâu?",
        parse_mode="HTML", reply_markup=_dur_kb())
    await _cb_answer(cb, )


async def _show_confirm(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    await cb.message.edit_text(_confirm_text(d), parse_mode="HTML",
                               reply_markup=_confirm_kb(bool(d.get("stop_jobs"))))
    await _cb_answer(cb, )


def _parse_until(text: str):
    """Phân tích thời gian mở lại. Trả unix ts hoặc None.

    Nhận: số phút ("90"), "HH:MM", "HH:MM dd/mm".
    """
    text = (text or "").strip()
    now = int(time.time())
    if text.isdigit():
        mins = int(text)
        if 1 <= mins <= MAX_MINUTES:
            return now + mins * 60
        return None
    m = re.match(r"^(\d{1,2}):(\d{2})(?:\s+(\d{1,2})/(\d{1,2}))?$", text)
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    lt = time.localtime(now)
    if m.group(3):
        dd, mo = int(m.group(3)), int(m.group(4))
        try:
            ts = int(time.mktime((lt.tm_year, mo, dd, hh, mm, 0,
                                  0, 0, -1)))
        except (OverflowError, ValueError):
            return None
    else:
        ts = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                              hh, mm, 0, 0, 0, -1)))
        if ts <= now:
            ts += 86400  # giờ đã qua hôm nay -> mai
    if ts <= now or ts - now > MAX_MINUTES * 60:
        return None
    return ts


# ── Entry points ──────────────────────────────────────────────────────────

@router.message(Command("tamdung"))
async def on_tamdung(msg: Message, state: FSMContext):
    """Chủ shop: bắt đầu flow tạm dừng bot."""
    if not _is_admin(msg.from_user.id):
        return
    if await _deny_super(msg):
        return
    if _pause.is_paused():
        info = _pause.get_info()
        await msg.answer(
            "⏸️ Bot <b>đang tạm dừng</b>.\n"
            "━━━━━━━━━━━━━━━\n"
            f"🔧 Lý do: <b>{html.escape(info.get('reason') or '')}</b>\n"
            f"🕐 Mở lại lúc: <b>{_pause.fmt_until(info.get('until') or 0)}</b>\n\n"
            "Muốn đổi thời gian/lý do thì mở lại rồi tạm dừng lại, "
            "hoặc gõ /molai để mở ngay.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="▶️ Mở lại ngay",
                                     callback_data="pause:resume_now")]]))
        return
    await _show_reason(msg, state)


@router.message(Command("molai"))
async def on_molai(msg: Message, state: FSMContext):
    """Chủ shop: mở lại bot ngay."""
    if not _is_admin(msg.from_user.id):
        return
    if await _deny_super(msg):
        return
    await state.clear()
    txt = await do_resume(msg.bot, msg.from_user.id,
                          msg.from_user.full_name or "")
    await msg.answer(txt, parse_mode="HTML")


async def do_resume(bot, by_id: int, by_name: str) -> str:
    """Mở lại bot + báo khách. Dùng chung cho /molai, nút bấm, web (qua API)."""
    if not _pause.is_paused():
        return "ℹ️ Bot đang hoạt động bình thường, không có gì để mở."
    if _pause.try_deactivate(by_id=by_id, by_name=by_name):
        n = await _pause.notify_reopened(bot, auto=False)
        return (f"✅ <b>Đã mở lại bot.</b>\n"
                f"Đã gửi tin báo cho <b>{n}</b> khách.")
    return "ℹ️ Bot đã được mở lại trước đó."


async def start_pause_flow_cb(cb: CallbackQuery, state: FSMContext):
    """Entry từ nút admm:pause trong /adm."""
    if _pause.is_paused():
        info = _pause.get_info()
        await cb.message.edit_text(
            "⏸️ Bot <b>đang tạm dừng</b>.\n"
            "━━━━━━━━━━━━━━━\n"
            f"🔧 Lý do: <b>{html.escape(info.get('reason') or '')}</b>\n"
            f"🕐 Mở lại lúc: <b>{_pause.fmt_until(info.get('until') or 0)}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="▶️ Mở lại ngay",
                                     callback_data="pause:resume_now")]]))
        await _cb_answer(cb, )
        return
    await _show_reason(cb, state)


# ── Callback dispatcher ───────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pause:"))
async def _on_pause_cb(cb: CallbackQuery, state: FSMContext):
    if not _is_admin(cb.from_user.id) or not _is_super(cb.from_user.id):
        await _cb_answer(cb, "🚫 Chỉ chủ shop mới dùng được.", show_alert=True)
        return
    action = (cb.data or "")[6:]

    if action == "cancel":
        await state.clear()
        await cb.message.edit_text("Đã hủy thao tác tạm dừng bot.")
        await _cb_answer(cb, )
        return

    if action == "resume_now":
        await state.clear()
        txt = await do_resume(cb.bot, cb.from_user.id,
                              cb.from_user.full_name or "")
        await cb.message.edit_text(txt, parse_mode="HTML")
        await _cb_answer(cb, )
        return

    if action == "reason_custom":
        await state.set_state(PauseFlow.waiting_reason)
        await cb.message.edit_text(
            "✏️ Nhập <b>lý do tạm dừng</b> (vd: Nâng cấp server):\n"
            "Gõ /huy để hủy.",
            parse_mode="HTML")
        await _cb_answer(cb, )
        return

    if action.startswith("reason:"):
        try:
            idx = int(action.split(":")[1])
            reason = REASONS[idx]
        except (IndexError, ValueError):
            await _cb_answer(cb, "Lựa chọn không hợp lệ.", show_alert=True)
            return
        await state.update_data(reason=reason)
        await _show_dur(cb, state)
        return

    if action == "dur_custom":
        await state.set_state(PauseFlow.waiting_until)
        await cb.message.edit_text(
            "🕐 Nhập <b>thời gian mở lại</b>:\n"
            "• Số phút: <code>90</code>\n"
            "• Giờ cụ thể: <code>18:30</code> (qua giờ thì tính ngày mai)\n"
            "• Ngày giờ: <code>18:30 29/09</code>\n"
            "Gõ /huy để hủy.",
            parse_mode="HTML")
        await _cb_answer(cb, )
        return

    if action.startswith("dur:"):
        try:
            mins = int(action.split(":")[1])
        except ValueError:
            await _cb_answer(cb, "Lựa chọn không hợp lệ.", show_alert=True)
            return
        if not (1 <= mins <= MAX_MINUTES):
            await _cb_answer(cb, "Lựa chọn không hợp lệ.", show_alert=True)
            return
        await state.update_data(until=int(time.time()) + mins * 60,
                                stop_jobs=False)
        await _show_confirm(cb, state)
        return

    if action == "jobs":
        d = await state.get_data()
        await state.update_data(stop_jobs=not d.get("stop_jobs"))
        await _show_confirm(cb, state)
        return

    if action == "confirm":
        d = await state.get_data()
        reason = d.get("reason") or "Bảo trì hệ thống"
        until = int(d.get("until") or 0)
        if until <= int(time.time()):
            await _cb_answer(cb, "Thời gian không hợp lệ, làm lại nhé.",
                            show_alert=True)
            await state.clear()
            return
        stop_jobs = bool(d.get("stop_jobs"))
        _pause.activate(reason, until, by_id=cb.from_user.id,
                        by_name=cb.from_user.full_name or "",
                        stop_jobs=stop_jobs)
        await state.clear()
        await cb.message.edit_text(
            "⏸️ <b>ĐÃ TẠM DỪNG BOT</b>\n"
            "━━━━━━━━━━━━━━━\n"
            f"🔧 Lý do: <b>{html.escape(reason)}</b>\n"
            f"🕐 Mở lại lúc: <b>{_pause.fmt_until(until)}</b>\n"
            f"⚙️ Job nền: <b>{'tạm dừng' if stop_jobs else 'vẫn chạy'}</b>\n\n"
            "Khách nhắn đến sẽ nhận tin báo tạm dừng. "
            "Bot tự mở lại đúng giờ.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="▶️ Mở lại ngay",
                                     callback_data="pause:resume_now")]]))
        await _cb_answer(cb, )
        return

    await _cb_answer(cb, )


# ── Nhập liệu tay (FSM) ───────────────────────────────────────────────────

@router.message(StateFilter(PauseFlow.waiting_reason))
async def _pause_reason_input(msg: Message, state: FSMContext):
    if not _is_admin(msg.from_user.id) or not _is_super(msg.from_user.id):
        await state.clear()
        return
    if (msg.text or "").strip() == "/huy":
        await state.clear()
        await msg.answer("Đã hủy thao tác tạm dừng bot.")
        return
    reason = (msg.text or "").strip()
    if not reason:
        await msg.answer("⚠️ Lý do không được để trống. Nhập lại hoặc /huy.")
        return
    await state.update_data(reason=reason[:200])
    await state.set_state(None)
    await msg.answer(
        f"🔧 Lý do: <b>{html.escape(reason[:200])}</b>\n\nMở lại sau bao lâu?",
        parse_mode="HTML", reply_markup=_dur_kb())


@router.message(StateFilter(PauseFlow.waiting_until))
async def _pause_until_input(msg: Message, state: FSMContext):
    if not _is_admin(msg.from_user.id) or not _is_super(msg.from_user.id):
        await state.clear()
        return
    if (msg.text or "").strip() == "/huy":
        await state.clear()
        await msg.answer("Đã hủy thao tác tạm dừng bot.")
        return
    until = _parse_until(msg.text or "")
    if not until:
        await msg.answer(
            "⚠️ Không hiểu. Nhập số phút (<code>90</code>), giờ "
            "(<code>18:30</code>) hoặc ngày giờ (<code>18:30 29/09</code>).",
            parse_mode="HTML")
        return
    await state.update_data(until=until, stop_jobs=False)
    await state.set_state(None)
    d = await state.get_data()
    await msg.answer(_confirm_text(d), parse_mode="HTML",
                     reply_markup=_confirm_kb(False))


__all__ = [
    "on_tamdung", "on_molai", "do_resume", "start_pause_flow_cb",
    "_on_pause_cb", "_pause_reason_input", "_pause_until_input",
]
