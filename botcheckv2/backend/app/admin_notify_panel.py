"""Panel 🔔 BOT BÁO TIN — mỗi kênh thông báo đi về 1 bot riêng.

Chỉ chủ shop (is_super). Đăng ký bằng register_notify_panel(router),
gọi trong register_adm_menu() của admin_bot.py (cạnh _aset.register_settings).

- 📡 Định tuyến kênh: chọn kênh nào -> bot nào (lưu là có hiệu lực ngay).
- ➕ Thêm bot: nhập tên -> dán token (tin nhắn tự xóa ngay) -> tự validate.
- 🧪 Test từng bot, 🗑 xóa bot tự thêm, 📖 HD tạo bot qua @BotFather.
- Mọi thay đổi ghi nhật ký admin.
"""
import html
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from . import db
from . import perms as _perms
from . import notify_router as _nr

log = logging.getLogger("admin_notify_panel")


class NtfState(StatesGroup):
    name = State()
    token = State()


def _deny(obj) -> bool:
    return not _perms.is_super(obj.from_user.id)


def _audit(tg_id: int, name: str, detail: str):
    try:
        db.admin_audit_add(tg_id, name or "", "bot_bao_tin", detail)
    except Exception:
        pass


def _bot_status(bot_id: str) -> str:
    """🟢/🔴 trạng thái bot."""
    try:
        if bot_id == "notify":
            from .notify_bot import manager
            return "🟢" if getattr(manager, "running", False) else "🔴"
        if bot_id == "viotp":
            from .viotp_notify import manager
            return "🟢" if getattr(manager, "running", False) else "🔴"
        if bot_id == "admin":
            from .admin_bot import manager
            return "🟢" if getattr(manager, "running", False) else "🔴"
        if bot_id == "main":
            from .handlers.core import manager
            return "🟢" if getattr(manager, "running", False) else "🔴"
        if bot_id.startswith("custom:"):
            mgr = _nr._custom_managers.get(bot_id)
            if mgr and mgr.running and mgr.bot:
                return "🟢"
            cid = bot_id[7:]
            tok = (db.get_setting(f"notify_bot_token_{cid}", "") or "").strip()
            return "🟡" if tok else "🔴"
    except Exception:
        pass
    return "🔴"


def _channels_of(bot_id: str) -> list:
    return [c[1] for c in _nr.CHANNELS if _nr.get_route(c[0]) == bot_id]


# ─────────────────────────────────────────────────────────────
# View: tổng quan
# ─────────────────────────────────────────────────────────────
def main_text() -> str:
    lines = ["🔔 <b>BOT BÁO TIN</b>", "━━━━━━━━━━━━", "",
             "Mỗi kênh thông báo đi về <b>1 bot riêng</b>.",
             "📝 <i>Cách dùng: bấm 📡 để đổi kênh nào → bot nào.</i>", "",
             "🤖 <b>Bot đang có:</b>"]
    for b in _nr.list_bots():
        chs = _channels_of(b["id"])
        ch_txt = ", ".join(chs) if chs else "<i>chưa gán kênh nào</i>"
        un = f" ({b['username']})" if b.get("username") else ""
        lines.append(f"{_bot_status(b['id'])} <b>{html.escape(b['name'])}</b>"
                     f"{html.escape(un)}\n    └ {ch_txt}")
    lines += ["",
              "🟢 đang chạy • 🟡 có token nhưng chưa chạy • 🔴 chưa cấu hình"]
    return "\n".join(lines)


def main_kb() -> InlineKeyboardMarkup:
    rows = []
    for b in _nr.list_bots():
        label = b["name"]
        if len(label) > 18:
            label = label[:17] + "…"
        rows.append([InlineKeyboardButton(
            text=f"🧪 Test {label}",
            callback_data=f"ntf:test:{b['id']}")])
    rows.append([InlineKeyboardButton(text="📡 Định tuyến kênh",
                                      callback_data="ntf:routes")])
    rows.append([
        InlineKeyboardButton(text="➕ Thêm bot", callback_data="ntf:add"),
        InlineKeyboardButton(text="📖 HD tạo bot", callback_data="ntf:guide"),
    ])
    if any(b["kind"] == "custom" for b in _nr.list_bots()):
        rows.append([InlineKeyboardButton(text="🗑 Xóa bot tự thêm",
                                          callback_data="ntf:dellist")])
    rows.append([InlineKeyboardButton(text="◀️ Cài đặt",
                                      callback_data="admset:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ─────────────────────────────────────────────────────────────
# View: định tuyến kênh
# ─────────────────────────────────────────────────────────────
def routes_text() -> str:
    return ("📡 <b>ĐỊNH TUYẾN KÊNH</b>\n"
            "━━━━━━━━━━━━\n\n"
            "Bấm vào từng kênh để chọn bot nhận tin.\n"
            "📝 <i>Cách dùng: đổi xong là có hiệu lực ngay, không cần restart.</i>")


def routes_kb() -> InlineKeyboardMarkup:
    rows = []
    for cid, label, _hint, _dflt in _nr.CHANNELS:
        bot_name = _nr.bot_display(_nr.get_route(cid))
        if len(bot_name) > 20:
            bot_name = bot_name[:19] + "…"
        rows.append([InlineKeyboardButton(
            text=f"{label} → {bot_name}",
            callback_data=f"ntf:route:{cid}")])
    rows.append([InlineKeyboardButton(text="◀️ Bot báo tin",
                                      callback_data="ntf:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def route_pick_text(cid: str) -> str:
    _id, label, hint, _d = _nr.CHANNEL_BY_ID[cid]
    return (f"📡 <b>{html.escape(label)}</b>\n"
            f"━━━━━━━━━━━━\n\n"
            f"📝 {html.escape(hint)}\n\n"
            f"Chọn bot nhận tin kênh này:")


def route_pick_kb(cid: str) -> InlineKeyboardMarkup:
    cur = _nr.get_route(cid)
    rows = []
    for b in _nr.list_bots():
        mark = "✅ " if b["id"] == cur else ""
        label = f"{mark}{_bot_status(b['id'])} {b['name']}"
        if len(label) > 30:
            label = label[:29] + "…"
        rows.append([InlineKeyboardButton(
            text=label,
            callback_data=f"ntf:set:{cid}:{b['id']}")])
    rows.append([InlineKeyboardButton(text="◀️ Định tuyến kênh",
                                      callback_data="ntf:routes")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


GUIDE_TEXT = (
    "📖 <b>TẠO BOT BÁO TIN MỚI</b>\n"
    "━━━━━━━━━━━━\n\n"
    "1️⃣ Mở <b>@BotFather</b> → gửi <code>/newbot</code>\n"
    "2️⃣ Đặt <b>tên hiển thị</b> (VD: Báo Tiền Shop)\n"
    "3️⃣ Đặt <b>username</b> — phải kết thúc bằng <code>bot</code>\n"
    "    (VD: <code>shop_baotien_bot</code>)\n"
    "4️⃣ BotFather trả về <b>token</b> (dạng <code>123456:ABC...</code>) — copy lại\n"
    "5️⃣ Bấm <b>➕ Thêm bot</b> ở màn hình trước, nhập tên → dán token\n"
    "    📝 <i>Tin nhắn chứa token sẽ tự xóa ngay để khỏi lộ.</i>\n"
    "6️⃣ Mở bot mới → bấm <b>Start</b> (bắt buộc, không là bot không gửi được tin!)\n"
    "7️⃣ Bấm <b>🧪 Test</b> để kiểm tra, rồi vào <b>📡 Định tuyến kênh</b>\n"
    "    để gán kênh cho bot mới.\n\n"
    "📝 <i>Cách dùng: mỗi bot nên nhận 1 nhóm tin riêng cho dễ theo dõi.</i>"
)


def guide_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Thêm bot", callback_data="ntf:add")],
        [InlineKeyboardButton(text="◀️ Bot báo tin",
                              callback_data="ntf:main")],
    ])


def dellist_kb() -> InlineKeyboardMarkup:
    rows = []
    for b in _nr.list_bots():
        if b["kind"] != "custom":
            continue
        rows.append([InlineKeyboardButton(
            text=f"🗑 {b['name']}",
            callback_data=f"ntf:del:{b['id']}")])
    rows.append([InlineKeyboardButton(text="◀️ Bot báo tin",
                                      callback_data="ntf:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ─────────────────────────────────────────────────────────────
# Handlers
# ─────────────────────────────────────────────────────────────
def register_notify_panel(target_router: Router):
    @target_router.callback_query(F.data.startswith("ntf:"))
    async def _on_ntf_cb(cb: CallbackQuery, state: FSMContext):
        if _deny(cb):
            await cb.answer("🚫 Chỉ chủ shop mới dùng được.",
                            show_alert=True)
            return
        action = (cb.data or "")[4:]

        async def _ans(text=""):
            try:
                await cb.answer(text)
            except Exception:
                pass

        async def _edit(text, kb):
            await cb.message.edit_text(text, parse_mode="HTML",
                                       reply_markup=kb)

        # ── Tổng quan ──
        if action == "main":
            await state.clear()
            await _ans()
            await _edit(main_text(), main_kb())
            return

        # ── Danh sách kênh ──
        if action == "routes":
            await state.clear()
            await _ans()
            await _edit(routes_text(), routes_kb())
            return

        # ── Chọn bot cho kênh ──
        if action.startswith("route:"):
            cid = action[6:]
            if cid not in _nr.CHANNEL_BY_ID:
                await _ans("❌ Kênh không tồn tại.")
                return
            await state.clear()
            await _ans()
            await _edit(route_pick_text(cid), route_pick_kb(cid))
            return

        # ── Lưu định tuyến ──
        if action.startswith("set:"):
            parts = action[4:].split(":", 1)
            if len(parts) != 2:
                await _ans("❌ Dữ liệu không hợp lệ.")
                return
            cid, bot_id = parts
            if cid not in _nr.CHANNEL_BY_ID:
                await _ans("❌ Kênh không tồn tại.")
                return
            if bot_id not in {b["id"] for b in _nr.list_bots()}:
                await _ans("❌ Bot không tồn tại.")
                return
            old = _nr.bot_display(_nr.get_route(cid))
            _nr.set_route(cid, bot_id)
            new = _nr.bot_display(bot_id)
            _audit(cb.from_user.id, cb.from_user.full_name or "",
                   f"Định tuyến {_nr.CHANNEL_BY_ID[cid][1]}: {old} → {new}")
            await _ans("✅ Đã lưu!")
            await _edit(routes_text(), routes_kb())
            return

        # ── Test bot ──
        if action.startswith("test:"):
            bot_id = action[5:]
            if bot_id not in {b["id"] for b in _nr.list_bots()}:
                await _ans("❌ Bot không tồn tại.")
                return
            ok, msg = await _nr.test_bot(bot_id)
            await cb.answer(msg, show_alert=True)
            await _edit(main_text(), main_kb())
            return

        # ── Thêm bot: bước 1 nhập tên ──
        if action == "add":
            await state.set_state(NtfState.name)
            await _ans()
            await _edit(
                "➕ <b>THÊM BOT BÁO TIN</b>\n"
                "━━━━━━━━━━━━\n\n"
                "Gửi <b>tên bot</b> vào đây (VD: Báo Tiền).\n"
                "Gõ /huy để huỷ.\n\n"
                "📝 <i>Chưa có bot? Bấm 📖 HD tạo bot để xem cách tạo.</i>",
                InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ Bot báo tin",
                                          callback_data="ntf:main")]]))
            return

        # ── Xóa bot: chọn ──
        if action == "dellist":
            await state.clear()
            await _ans()
            await _edit(
                "🗑 <b>XÓA BOT TỰ THÊM</b>\n"
                "━━━━━━━━━━━━\n\n"
                "Chọn bot muốn xóa:\n"
                "📝 <i>Các kênh đang gán về bot này sẽ tự về Bot báo đơn.</i>",
                dellist_kb())
            return

        # ── Xóa bot: xác nhận ──
        if action.startswith("del:"):
            bot_id = action[4:]
            b = next((x for x in _nr.list_bots()
                      if x["id"] == bot_id and x["kind"] == "custom"), None)
            if not b:
                await _ans("❌ Bot không tồn tại.")
                return
            await state.clear()
            await _ans()
            await _edit(
                f"⚠️ <b>Xóa bot {html.escape(b['name'])}?</b>\n\n"
                "Bot sẽ dừng hẳn, token bị xóa.\n"
                "Các kênh đang gán về nó sẽ tự về Bot báo đơn.",
                InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="✅ Xóa luôn", callback_data=f"ntf:delok:{bot_id}")],
                    [InlineKeyboardButton(text="◀️ Quay lại",
                                          callback_data="ntf:dellist")],
                ]))
            return

        # ── Xóa bot: thực hiện ──
        if action.startswith("delok:"):
            bot_id = action[6:]
            b = next((x for x in _nr.list_bots()
                      if x["id"] == bot_id and x["kind"] == "custom"), None)
            if not b:
                await _ans("❌ Bot không tồn tại.")
                return
            cid = bot_id[7:]
            await _nr.stop_one_custom_bot(cid)
            _nr.delete_custom_bot(cid)
            _audit(cb.from_user.id, cb.from_user.full_name or "",
                   f"Xóa bot báo tin: {b['name']}")
            await _ans("🗑 Đã xóa!")
            await _edit(main_text(), main_kb())
            return

        # ── Hướng dẫn ──
        if action == "guide":
            await state.clear()
            await _ans()
            await _edit(GUIDE_TEXT, guide_kb())
            return

        await _ans()

    @target_router.message(NtfState.name)
    async def _on_ntf_name(msg: Message, state: FSMContext):
        if not _perms.is_super(msg.from_user.id):
            await state.clear()
            return
        name = (msg.text or "").strip()
        if not name or len(name) > 32:
            await msg.answer("❌ Tên không hợp lệ (1–32 ký tự). Gửi lại nhé.")
            return
        cid = _nr.add_custom_bot(name)
        await state.update_data(ntf_cid=cid, ntf_name=name)
        await state.set_state(NtfState.token)
        try:
            await msg.delete()
        except Exception:
            pass
        await msg.answer(
            f"✅ Đã tạo bot <b>{html.escape(name)}</b>.\n\n"
            "Giờ <b>dán token</b> của bot vào đây "
            "(lấy từ @BotFather).\n"
            "📝 <i>Tin nhắn chứa token sẽ tự xóa ngay.</i>",
            parse_mode="HTML")

    @target_router.message(NtfState.token)
    async def _on_ntf_token(msg: Message, state: FSMContext):
        if not _perms.is_super(msg.from_user.id):
            await state.clear()
            return
        data = await state.get_data()
        cid = data.get("ntf_cid")
        name = data.get("ntf_name", "")
        token = (msg.text or "").strip()
        try:
            await msg.delete()
        except Exception:
            pass
        if not cid:
            await state.clear()
            await msg.answer("❌ Phiên hết hạn, làm lại từ ➕ Thêm bot nhé.")
            return
        ok, res = await _nr.validate_token(token)
        if not ok:
            await msg.answer(f"❌ {html.escape(res)}\n\nDán lại token đúng, hoặc /huy để huỷ.")
            return
        db.set_setting(f"notify_bot_token_{cid}", token)
        db.set_setting(f"notify_bot_username_{cid}", res)
        started = await _nr.start_one_custom_bot(cid)
        await state.clear()
        _audit(msg.from_user.id, msg.from_user.full_name or "",
               f"Thêm bot báo tin: {name} ({res})")
        await msg.answer(
            f"✅ <b>Đã thêm bot {html.escape(name)} ({html.escape(res)})</b>\n\n"
            f"{'🟢 Bot đã chạy.' if started else '🟡 Đã lưu token nhưng bot chưa chạy — kiểm tra lại sau.'}\n\n"
            "⚠️ Nhớ <b>mở bot mới và bấm Start</b> rồi mới nhận được tin!\n"
            "Tiếp theo: vào <b>📡 Định tuyến kênh</b> để gán kênh cho bot này.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📡 Định tuyến kênh",
                                      callback_data="ntf:routes")],
                [InlineKeyboardButton(text="🧪 Test ngay",
                                      callback_data=f"ntf:test:custom:{cid}")],
                [InlineKeyboardButton(text="◀️ Bot báo tin",
                                      callback_data="ntf:main")],
            ]))
