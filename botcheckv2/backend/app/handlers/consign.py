"""Bot ký gửi acc: đối tác (/kygui) + chủ shop duyệt (/kyguiadm).

Quyết định đã chốt: đăng ký công khai, mọi gian hàng, phí kết hợp theo loại,
admin quyết giá bán, giữ tiền = thời gian BH, KM cần đối tác đồng ý,
hạn mức số acc + tổng giá trị, tranh chấp bắt buộc ảnh, rút tiền đủ 4 yếu tố,
quyền nhạy cảm chỉ chủ shop.
"""
import html
import json
import logging
import re
import time

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           Message)

from .. import consign as C
from .. import db
from .. import perms as _perms
from ..handlers.core import router

log = logging.getLogger("consign")


async def _send_notify_retry(bot, chat_id: int, text: str, tries: int = 3) -> tuple:
    """Gửi tin báo với retry. Trả về (ok, error)."""
    import asyncio as _aio
    last_err = ""
    for i in range(tries):
        try:
            await bot.send_message(int(chat_id), text, parse_mode="HTML")
            return True, ""
        except Exception as e:
            last_err = str(e)[:200]
            if i < tries - 1:
                await _aio.sleep(2)
    return False, last_err


async def _cb_answer(cb, *args, **kwargs):
    try:
        await cb.answer(*args, **kwargs)
    except Exception:
        pass


def _is_super(uid: int) -> bool:
    try:
        return _perms.is_super(uid)
    except Exception:
        return str(uid) == str(db.get_setting("admin_tg_id", ""))


# ================= FSM =================
class ConsignReg(StatesGroup):
    name = State()
    phone = State()
    group = State()
    email = State()
    confirm = State()


class ConsignBatch(StatesGroup):
    new_stall = State()
    new_category = State()
    floor_price = State()
    warranty = State()
    upload_choice = State()  # chọn cách đẩy hàng: file / text / sheet
    items = State()
    sheet_link = State()     # nhập link Google Sheet
    confirm = State()


class ConsignWithdraw(StatesGroup):
    amount = State()
    channel = State()
    account = State()
    confirm = State()


class ConsignAdminPrice(StatesGroup):
    sell_price = State()


class ConsignAdminPayout(StatesGroup):
    paid_ref = State()


class ConsignAdminSheet(StatesGroup):
    url = State()


class ConsignBotCfg(StatesGroup):
    token = State()


# ================= /kygui — menu đối tác =================
def _kg_menu_kb(c):
    rows = []
    if not c:
        rows.append([InlineKeyboardButton(text="📝 Đăng ký ký gửi", callback_data="kg:reg")])
    elif c["status"] == "pending":
        rows.append([InlineKeyboardButton(text="⏳ Hồ sơ đang chờ duyệt", callback_data="kg:noop")])
    elif c["status"] == "active":
        rows.extend([
            [InlineKeyboardButton(text="📦 Tạo lô mới", callback_data="kg:new")],
            [InlineKeyboardButton(text="📋 Lô của tôi", callback_data="kg:batches")],
            [InlineKeyboardButton(text="💰 Đơn đã bán", callback_data="kg:orders"),
             InlineKeyboardButton(text="⚠️ Tranh chấp", callback_data="kg:disputes")],
            [InlineKeyboardButton(text="👛 Ví ký gửi", callback_data="kg:wallet"),
             InlineKeyboardButton(text="💸 Rút tiền", callback_data="kg:withdraw")],
            [InlineKeyboardButton(text="📊 Thống kê", callback_data="kg:stats"),
             InlineKeyboardButton(text="👤 Hồ sơ", callback_data="kg:profile")],
            [InlineKeyboardButton(text="🎁 Khuyến mãi chờ duyệt", callback_data="kg:promos")],
            [InlineKeyboardButton(text="📧 Email kho Sheet", callback_data="kg:email")],
        ])
        # nút mở kho Sheet riêng — chỉ hiện sau khi chủ shop xác nhận đã mở quyền
        if (c.get("sheet_url") or "").strip() and c.get("sheet_access_granted"):
            rows.append([InlineKeyboardButton(text="📊 Mở kho Sheet riêng", callback_data="kg:sheet")])
    else:
        rows.append([InlineKeyboardButton(text="🚫 Tài khoản bị khóa", callback_data="kg:noop")])
    rows.append([InlineKeyboardButton(text="❓ Quy định ký gửi", callback_data="kg:rules")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text.startswith("/kygui"))
async def on_kygui(msg: Message, state: FSMContext):
    # /kyguiadm -> chuyển sang admin TRƯỚC khi check enabled
    # (để chủ shop bật/tắt được tính năng ngay cả khi đang tắt)
    if msg.text.strip().startswith("/kyguiadm"):
        await on_kyguiadm(msg, state)
        return
    if not C.enabled():
        await msg.answer("Tính năng ký gửi hiện đang tắt.")
        return
    await state.clear()
    c = db.consignor_get(msg.from_user.id)
    await msg.answer("🤝 <b>KÝ GỬI ACC</b>\n\nBán hàng qua shop, shop kiểm duyệt và giữ tiền bảo hành giúp bạn.",
                     parse_mode="HTML", reply_markup=_kg_menu_kb(c))


@router.callback_query(F.data == "kg:noop")
async def _kg_noop(cb: CallbackQuery):
    await _cb_answer(cb)


@router.callback_query(F.data == "kg:rules")
async def _kg_rules(cb: CallbackQuery):
    await _cb_answer(cb)
    await cb.message.edit_text(
        "📜 <b>QUY ĐỊNH KÝ GỬI</b>\n\n"
        "• Đăng ký công khai, chủ shop duyệt hồ sơ.\n"
        "• Bạn khai <b>giá sàn</b> (số tiền tối thiểu muốn nhận), <b>admin quyết định giá bán</b>.\n"
        "• Phí = phí cố định + % theo từng loại hàng (trừ vào giá bán).\n"
        "• Tiền bán vào <b>ví chờ</b>, hết thời gian bảo hành mới rút được.\n"
        "• Khuyến mãi giảm giá cần <b>bạn đồng ý</b> từng chiến dịch.\n"
        "• Hạn mức: số acc + tổng giá trị (xem hồ sơ).\n"
        "• Tranh chấp bắt buộc có <b>ảnh bằng chứng</b>.\n"
        "• Không bán trùng nơi khác khi hàng đang niêm yết.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Quay lại", callback_data="kg:menu")]]))


@router.callback_query(F.data == "kg:menu")
async def _kg_menu(cb: CallbackQuery, state: FSMContext):
    await _cb_answer(cb)
    await state.clear()
    c = db.consignor_get(cb.from_user.id)
    try:
        await cb.message.edit_text("🤝 <b>KÝ GỬI ACC</b>", parse_mode="HTML",
                                   reply_markup=_kg_menu_kb(c))
    except Exception:
        pass


# ================= Đăng ký =================
@router.callback_query(F.data == "kg:reg")
async def _kg_reg(cb: CallbackQuery, state: FSMContext):
    await _cb_answer(cb)
    if db.consignor_get(cb.from_user.id):
        await cb.message.answer("Bạn đã có hồ sơ rồi.")
        return
    await state.set_state(ConsignReg.name)
    await cb.message.answer("📝 <b>Đăng ký ký gửi</b>\n\nNhập <b>tên hiển thị</b> của bạn:",
                            parse_mode="HTML")


@router.message(ConsignReg.name)
async def _kg_reg_name(msg: Message, state: FSMContext):
    await state.update_data(name=msg.text.strip()[:60])
    await state.set_state(ConsignReg.phone)
    await msg.answer("Nhập <b>số điện thoại</b> liên hệ:", parse_mode="HTML")


@router.message(ConsignReg.phone)
async def _kg_reg_phone(msg: Message, state: FSMContext):
    await state.update_data(phone=msg.text.strip()[:30])
    await state.set_state(ConsignReg.group)
    await msg.answer("Bạn định ký gửi <b>nhóm hàng</b> nào? (vd: acc FB, Gmail...)\nGõ /boqua nếu chưa rõ.",
                     parse_mode="HTML")


@router.message(ConsignReg.group)
async def _kg_reg_group(msg: Message, state: FSMContext):
    d = await state.get_data()
    note = "" if msg.text.strip() == "/boqua" else msg.text.strip()[:200]
    await state.update_data(note=note)
    await state.set_state(ConsignReg.email)
    await msg.answer("📧 Nhập <b>email Google</b> của bạn để shop mở quyền xem <b>kho Sheet riêng</b>:\n"
                     "Gõ /boqua nếu chưa có.",
                     parse_mode="HTML")


@router.message(ConsignReg.email)
async def _kg_reg_email(msg: Message, state: FSMContext):
    d = await state.get_data()
    # cập nhật email kho Sheet (đối tác đã có hồ sơ)
    if d.get("_upd_email"):
        txt = msg.text.strip()
        if txt != "/boqua":
            if "@" not in txt or "." not in txt:
                await msg.answer("⚠️ Email chưa đúng định dạng. Nhập lại hoặc gõ /boqua:")
                return
            db.consignor_update(db.consignor_get(msg.from_user.id)["id"], sheet_email=txt[:120])
        await state.clear()
        c = db.consignor_get(msg.from_user.id)
        await msg.answer(f"✅ Đã cập nhật email: <b>{html.escape(c.get('sheet_email') or '—')}</b>\n"
                         f"Shop sẽ mở quyền xem kho Sheet cho email này.",
                         parse_mode="HTML", reply_markup=_kg_menu_kb(c))
        return
    # flow đăng ký mới
    email = "" if msg.text.strip() == "/boqua" else msg.text.strip()[:120]
    if email and ("@" not in email or "." not in email):
        await msg.answer("⚠️ Email chưa đúng định dạng. Nhập lại hoặc gõ /boqua:")
        return
    cid = db.consignor_create(msg.from_user.id, d["name"], d["phone"], d.get("note", ""), email)
    await state.clear()
    await msg.answer("✅ <b>Đã gửi hồ sơ!</b>\nChủ shop sẽ duyệt, bạn sẽ nhận tin báo.",
                     parse_mode="HTML", reply_markup=_kg_menu_kb(db.consignor_get(msg.from_user.id)))
    # báo chủ shop
    try:
        admin_id = int(db.get_setting("admin_tg_id", "0") or 0)
        if admin_id:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Duyệt", callback_data=f"kga:conok:{cid}"),
                 InlineKeyboardButton(text="❌ Từ chối", callback_data=f"kga:conno:{cid}")]])
            await msg.bot.send_message(
                admin_id,
                f"🤝 <b>Hồ sơ ký gửi mới</b>\n👤 {html.escape(d['name'])} (<code>{msg.from_user.id}</code>)\n"
                f"📞 {html.escape(d['phone'])}\n📧 {html.escape(email or '—')}\n"
                f"📦 {html.escape(d.get('note') or '—')}",
                parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass
    C.audit(msg.from_user.id, d["name"], "register", f"cid={cid}")


# ================= Tạo lô =================
def _parse_items(text: str) -> tuple:
    """Mỗi dòng (chuẩn 8 trường như admin nhập kho):
    uid|mk|ngày tạo|mail thay|ghi chú|2fa|cookie|token.
    Chỉ bắt buộc uid|mk. Trả (items, lỗi)."""
    items, errs = [], []
    for i, line in enumerate(text.strip().split("\n"), 1):
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2 or not parts[0]:
            errs.append(f"Dòng {i}: thiếu uid|mk")
            continue
        while len(parts) < 8:
            parts.append("")
        note = parts[4]
        if parts[2]:  # ngày tạo -> gộp vào ghi chú cho khỏi mất
            note = f"[NSX {parts[2]}] {note}".strip()
        items.append({"uid": parts[0], "password": parts[1],
                      "backup_mail": parts[3], "totp": parts[5],
                      "cookie": parts[6], "token": parts[7], "note": note})
    return items, errs


@router.callback_query(F.data == "kg:new")
async def _kg_new(cb: CallbackQuery, state: FSMContext):
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    if not c or c["status"] != "active":
        return
    stalls = [r["stall"] for r in db.get_conn().execute(
        "SELECT DISTINCT stall FROM acc_categories WHERE stall<>'' ORDER BY stall").fetchall()]
    if not stalls:
        stalls = ["Acc Facebook"]
    rows = [[InlineKeyboardButton(text=f"🏪 {s}", callback_data=f"kg:stall:{i}")]
            for i, s in enumerate(stalls[:10])]
    rows.append([InlineKeyboardButton(text="➕ Tạo gian hàng mới", callback_data="kg:newstall")])
    rows.append([InlineKeyboardButton(text="◀️ Quay lại", callback_data="kg:menu")])
    await state.update_data(_stalls=stalls)
    await cb.message.edit_text("<b>📦 Tạo lô mới — Bước 1/5</b>\nChọn gian hàng:",
                               parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


def _kg_step2_text_kb(stall, cats):
    """Màn hình bước 2/5: chọn loại acc trong gian hàng (dùng chung cho chọn sạp có sẵn và sạp mới tạo)."""
    rows = [[InlineKeyboardButton(text=x["name"], callback_data=f"kg:cat:{x['id']}")
             for x in cats[i:i + 2]] for i in range(0, len(cats), 2)]
    rows.append([InlineKeyboardButton(text="➕ Tạo loại mới", callback_data="kg:newcat")])
    rows.append([InlineKeyboardButton(text="◀️ Quay lại", callback_data="kg:menu")])
    return (f"<b>Bước 2/5</b>\n🏪 {html.escape(stall)}\nChọn loại acc:",
            InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "kg:newstall")
async def _kg_newstall(cb: CallbackQuery, state: FSMContext):
    """Đối tác tự tạo gian hàng mới (bước 1/5)."""
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    if not c or c["status"] != "active":
        return
    await state.set_state(ConsignBatch.new_stall)
    await cb.message.answer(
        "➕ <b>Tạo gian hàng mới</b>\n\n"
        "Nhập <b>tên gian hàng</b> của bạn (vd: Kho acc clone, Gmail trâu):\n"
        "<i>Hàng của bạn sẽ được bán dưới gian hàng này sau khi chủ shop duyệt.</i>",
        parse_mode="HTML")


@router.message(ConsignBatch.new_stall)
async def _kg_newstall_input(msg: Message, state: FSMContext):
    name = (msg.text or "").strip()[:40]
    if not name or name.startswith("/"):
        await msg.answer("⚠️ Tên gian hàng không hợp lệ, nhập lại:")
        return
    c = db.consignor_get(msg.from_user.id)
    if not c or c["status"] != "active":
        await state.clear()
        return
    stalls = [r["stall"] for r in db.get_conn().execute(
        "SELECT DISTINCT stall FROM acc_categories WHERE stall<>''").fetchall()]
    # trùng tên (không phân biệt hoa/thường) -> dùng luôn gian hàng đã có
    stall = next((s for s in stalls if s.lower() == name.lower()), name)
    cats = [dict(r) for r in db.get_conn().execute(
        "SELECT id, name FROM acc_categories WHERE stall=? ORDER BY name", (stall,)).fetchall()]
    await state.update_data(stall=stall, _stalls=stalls,
                            _cats=[(x["id"], x["name"]) for x in cats])
    C.audit(msg.from_user.id, msg.from_user.full_name, "kg_new_stall", f"stall={stall}")
    text, kb = _kg_step2_text_kb(stall, cats)
    await msg.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("kg:stall:"))
async def _kg_stall(cb: CallbackQuery, state: FSMContext):
    await _cb_answer(cb)
    d = await state.get_data()
    stalls = d.get("_stalls", [])
    try:
        stall = stalls[int(cb.data.split(":")[2])]
    except Exception:
        return
    cats = [dict(r) for r in db.get_conn().execute(
        "SELECT id, name FROM acc_categories WHERE stall=? ORDER BY name", (stall,)).fetchall()]
    if not cats:
        await cb.message.answer(f"Gian hàng {stall} chưa có loại acc nào, admin sẽ thêm sau.")
        return
    await state.update_data(stall=stall, _cats=[(x["id"], x["name"]) for x in cats])
    text, kb = _kg_step2_text_kb(stall, cats)
    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "kg:newcat")
async def _kg_newcat(cb: CallbackQuery, state: FSMContext):
    """Đối tác tự tạo loại acc mới (chờ admin duyệt giá khi duyệt lô)."""
    await _cb_answer(cb)
    d = await state.get_data()
    stall = d.get("stall")
    if not stall:
        await cb.message.answer("Hết phiên, bấm /kygui làm lại.")
        await state.clear()
        return
    await state.set_state(ConsignBatch.new_category)
    await cb.message.answer(
        f"➕ <b>Tạo loại acc mới</b> trong gian hàng <b>{html.escape(stall)}</b>\n\n"
        f"Nhập <b>tên loại acc</b> (vd: Acc FB cổ 2019):\n"
        f"<i>Giá bán và phí do chủ shop quyết định khi duyệt lô của bạn.</i>",
        parse_mode="HTML")


@router.message(ConsignBatch.new_category)
async def _kg_newcat_input(msg: Message, state: FSMContext):
    d = await state.get_data()
    stall = d.get("stall")
    if not stall:
        await state.clear()
        return
    name = msg.text.strip()[:80]
    if not name or name.startswith("/"):
        await msg.answer("⚠️ Tên loại không hợp lệ, nhập lại:")
        return
    # chống trùng tên (name UNIQUE toàn shop)
    dup = db.get_conn().execute("SELECT id FROM acc_categories WHERE name=?", (name,)).fetchone()
    if dup:
        await msg.answer(f"⚠️ Loại <b>{html.escape(name)}</b> đã có rồi, nhập tên khác:",
                         parse_mode="HTML")
        return
    live_check = db.acc_stall_live_check(stall)
    with db._lock:
        c = db.get_conn()
        cur = c.execute(
            "INSERT INTO acc_categories (name, price, warranty_hours, description, active,"
            " created_at, stall, live_check, consign_pending)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (name, 0, 24, f"Do đối tác ký gửi tạo ({msg.from_user.id})", 1,
             int(time.time()), stall, live_check, 1))
        c.commit()
        cat_id = cur.lastrowid
    await state.update_data(category_id=cat_id, category_name=name)
    await state.set_state(ConsignBatch.floor_price)
    C.audit(msg.from_user.id, msg.from_user.full_name, "kg_new_category", f"cat={cat_id} {name}")
    await msg.answer(
        f"✅ Đã tạo loại <b>{html.escape(name)}</b>.\n\n"
        f"<b>Bước 3/5</b>\nNhập <b>giá sàn</b> bạn muốn (VNĐ, chỉ số):",
        parse_mode="HTML")


@router.callback_query(F.data.startswith("kg:cat:"))
async def _kg_cat(cb: CallbackQuery, state: FSMContext):
    await _cb_answer(cb)
    cat_id = int(cb.data.split(":")[2])
    cat = db.get_conn().execute("SELECT * FROM acc_categories WHERE id=?", (cat_id,)).fetchone()
    if not cat:
        return
    await state.update_data(category_id=cat_id, category_name=cat["name"])
    await state.set_state(ConsignBatch.floor_price)
    fee = db.consign_fee_get(cat_id)
    await cb.message.answer(
        f"<b>Bước 3/5</b>\nLoại: {cat['name']}\n"
        f"Phí loại này: {fee['fee_fixed']:,}đ + {fee['fee_pct']}%\n\n"
        f"Nhập <b>giá sàn</b> mỗi acc (số tiền tối thiểu bạn muốn nhận, VNĐ):",
        parse_mode="HTML")


@router.message(ConsignBatch.floor_price)
async def _kg_floor(msg: Message, state: FSMContext):
    try:
        v = int(msg.text.strip().replace(",", "").replace(".", "").replace("k", "000"))
        assert v > 0
    except Exception:
        await msg.answer("Nhập số tiền hợp lệ (vd 40000).")
        return
    await state.update_data(floor_price=v)
    await state.set_state(ConsignBatch.warranty)
    await msg.answer("<b>Bước 4/5</b>\nThời gian <b>bảo hành</b> (ngày, vd 7)? Tiền giữ đúng bằng thời gian này.",
                     parse_mode="HTML")


@router.message(ConsignBatch.warranty)
async def _kg_warranty(msg: Message, state: FSMContext):
    try:
        v = int(msg.text.strip())
        assert 0 <= v <= 365
    except Exception:
        await msg.answer("Nhập số ngày 0–365.")
        return
    await state.update_data(warranty_days=v)
    await state.set_state(ConsignBatch.upload_choice)
    await msg.answer(
        "<b>Bước 5/5 — Đẩy hàng lên</b>\nChọn cách gửi danh sách acc:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📤 Gửi file (.txt/.xlsx)", callback_data="kg:up:file")],
            [InlineKeyboardButton(text="✏️ Dán danh sách text", callback_data="kg:up:text")],
            [InlineKeyboardButton(text="📊 Sheet nhập hàng của bạn", callback_data="kg:up:mysheet")],
        ]))


def _kg_sync_kb(sheet_url: str):
    """Bàn phím sau khi ghi acc vào Sheet: Cập nhật kho + Mở Sheet."""
    rows = [[InlineKeyboardButton(text="🔄 Cập nhật kho", callback_data="kg:up:sync")]]
    if sheet_url:
        rows.append([InlineKeyboardButton(text="📊 Mở Sheet", url=sheet_url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _kg_ensure_sheet(msg_or_cb, state: FSMContext, c: dict):
    """Đảm bảo đối tác có Sheet riêng (tự tạo nếu chưa có). Trả (sid, url) hoặc (None, None)."""
    from .. import consign_sheet as _cs
    wait = await (msg_or_cb.message if hasattr(msg_or_cb, "message") else msg_or_cb).answer(
        "⏳ Đang chuẩn bị Sheet nhập hàng...")
    try:
        sid = await _cs.ensure_partner_sheet(c["id"], c.get("name") or "")
    except Exception:
        sid = ""
    try:
        await wait.delete()
    except Exception:
        pass
    if not sid:
        await (msg_or_cb.message if hasattr(msg_or_cb, "message") else msg_or_cb).answer(
            "❌ Không tạo được Sheet. Thử lại sau hoặc báo shop.")
        return None, None
    url = f"https://docs.google.com/spreadsheets/d/{sid}"
    await state.update_data(_import_sid=sid)
    return sid, url


@router.callback_query(F.data.startswith("kg:up:"))
async def _kg_upload_choice(cb: CallbackQuery, state: FSMContext):
    await _cb_answer(cb)
    mode = cb.data.split(":")[2]
    d = await state.get_data()
    c = db.consignor_get(cb.from_user.id)
    if not c or c["status"] != "active":
        return
    if mode == "file":
        await state.set_state(ConsignBatch.items)
        await cb.message.answer("📤 Gửi <b>file .txt hoặc .xlsx</b> chứa danh sách acc\n"
                                "(8 cột: uid|mk|ngày tạo|mail thay|ghi chú|2fa|cookie|token).\n"
                                "Bot sẽ tự ghi vào Sheet nhập hàng của bạn.",
                                parse_mode="HTML")
    elif mode == "text":
        await state.set_state(ConsignBatch.items)
        await cb.message.answer("✏️ Dán danh sách acc, mỗi dòng:\n"
                                "<code>uid|mk|ngày tạo|mail thay|ghi chú|2fa|cookie|token</code>\n"
                                "Chỉ bắt buộc <b>uid|mk</b>.\n"
                                "Bot sẽ tự ghi vào Sheet nhập hàng của bạn.", parse_mode="HTML")
    elif mode == "mysheet":
        sid, url = await _kg_ensure_sheet(cb, state, c)
        if not sid:
            return
        await cb.message.answer(
            "📊 <b>Sheet nhập hàng của bạn</b>\n"
            f'<a href="{url}">Mở Sheet</a>\n\n'
            "Bạn có thể:\n"
            "• Sửa trực tiếp trên Sheet (tab <b>Nhập hàng</b>), hoặc\n"
            "• Gửi file / dán text — bot tự ghi vào Sheet.\n\n"
            "Xong bấm <b>🔄 Cập nhật kho</b> để đưa acc vào lô.",
            parse_mode="HTML", disable_web_page_preview=True,
            reply_markup=_kg_sync_kb(url))


@router.callback_query(F.data == "kg:up:sheet_saved")
async def _kg_sheet_saved(cb: CallbackQuery, state: FSMContext):
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    url = (c["import_sheet_url"] or "") if c and "import_sheet_url" in c.keys() else ""
    if not url:
        await cb.message.answer("Chưa lưu Sheet nào. Gửi link Sheet mới giúp mik.")
        return
    await _kg_import_sheet(cb.message, state, url)


def _extract_sheet_id(url: str) -> str:
    m = re.search(r"/d/([a-zA-Z0-9-_]+)", url or "")
    if m:
        return m.group(1)
    s = (url or "").strip()
    if re.fullmatch(r"[a-zA-Z0-9-_]{20,}", s):
        return s
    return ""


async def _kg_import_sheet(msg: Message, state: FSMContext, url: str):
    """Đọc acc từ Google Sheet của đối tác -> tạo lô. Chỉ cần quyền xem."""
    from .. import sheet_import as _si
    sid = _extract_sheet_id(url)
    if not sid:
        await msg.answer("❌ Không nhận diện được link Sheet. Gửi lại link dạng\n"
                         "<code>https://docs.google.com/spreadsheets/d/...</code>", parse_mode="HTML")
        return
    wait = await msg.answer("⏳ Đang đọc Google Sheet...")
    try:
        meta = await _si._cli(["sheets", "spreadsheets", "get", "--params",
                               json.dumps({"spreadsheetId": sid,
                                           "fields": "sheets.properties.title"})])
        tabs = [s["properties"]["title"] for s in (meta.get("sheets") or [])]
        if not tabs:
            await wait.edit_text("❌ Không đọc được Sheet. Kiểm tra quyền chia sẻ link (bất kỳ ai có link đều xem được).")
            return
        tab = tabs[0]
        d = await _si._cli(["sheets", "spreadsheets", "values", "get", "--params",
                            json.dumps({"spreadsheetId": sid, "range": f"{tab}!A2:H"})])
        vals = d.get("values") or []
    except Exception as e:
        await wait.edit_text(f"❌ Không đọc được Sheet: {html.escape(str(e)[:200])}\n"
                             "Kiểm tra link và quyền chia sẻ.")
        return
    items = []
    for cells in vals:
        cells = [str(v or "").strip() for v in cells]
        while len(cells) < 8:
            cells.append("")
        if not cells[0] or not any(cells[:8]):
            continue
        note = cells[4]
        if cells[2]:
            note = f"[NSX {cells[2]}] {note}".strip()
        items.append({"uid": cells[0], "password": cells[1],
                      "backup_mail": cells[3], "totp": cells[5],
                      "cookie": cells[6], "token": cells[7], "note": note})
    if not items:
        await wait.edit_text("📭 Sheet không có dòng acc nào (tab <b>%s</b>)." % html.escape(tab),
                             parse_mode="HTML")
        return
    # lưu link để lần sau dùng lại
    try:
        c0 = db.consignor_get(msg.from_user.id)
        if c0:
            db.get_conn().execute("UPDATE consignors SET import_sheet_url=? WHERE id=?",
                                  (url.strip(), c0["id"]))
            db.get_conn().commit()
    except Exception:
        pass
    await wait.delete()
    await msg.answer(f"📊 Đọc được <b>{len(items)}</b> acc từ Sheet (tab {html.escape(tab)}).",
                     parse_mode="HTML")
    await _kg_finish_items(msg, state, items, [])


@router.message(ConsignBatch.sheet_link)
async def _kg_sheet_link(msg: Message, state: FSMContext):
    url = (msg.text or "").strip()
    if not url or not url.startswith("http"):
        await msg.answer("Gửi link Google Sheet (bắt đầu bằng https://).")
        return
    await _kg_import_sheet(msg, state, url)


async def _kg_finish_items(msg: Message, state: FSMContext, items: list, errs: list, uid: int = 0):
    """Chung: kiểm tra hạn mức + trùng kho -> tạo lô -> màn xác nhận."""
    if not items:
        await msg.answer("Không đọc được acc nào. Kiểm tra lại định dạng.")
        return
    d = await state.get_data()
    # FIX 2026-09-29 (bổ sung): state có thể mất sau restart backend ->
    # báo rõ thay vì KeyError crash ngầm
    if not d.get("floor_price") or not d.get("category_id"):
        await msg.answer(
            "⚠️ Phiên tạo lô đã hết hạn (do hệ thống khởi động lại).\n"
            "Bạn bấm <b>📦 Tạo lô mới</b> và làm lại 5 bước nhé — Sheet nhập hàng của bạn vẫn còn nguyên.",
            parse_mode="HTML")
        return
    # FIX 2026-09-29: khi gọi từ callback (vd _kg_sync), msg.from_user là BOT
    # (người gửi tin chứa nút), không phải user bấm nút -> phải truyền uid rõ ràng.
    uid = uid or msg.from_user.id
    c = db.consignor_get(uid)
    if not c:
        await msg.answer("Không tìm thấy hồ sơ đối tác. Gửi /kygui để đăng ký lại.")
        return
    ok, msg_lim = C.check_limits(c["id"], add_items=len(items),
                                 add_value=len(items) * d["floor_price"])
    if not ok:
        await msg.answer(f"🚫 {msg_lim}")
        return
    # chống trùng trong kho
    uids = [x["uid"] for x in items]
    q = ",".join("?" for _ in uids)
    dup = {r["uid"] for r in db.get_conn().execute(
        f"SELECT uid FROM acc_stock WHERE uid IN ({q}) AND status IN ('AVAILABLE','DIE')", uids).fetchall()}
    # chống trùng với lô đang chờ duyệt của chính đối tác
    dup2 = {r["uid"] for r in db.get_conn().execute(
        f"""SELECT ci.uid FROM consignment_items ci
            JOIN consignment_batches b ON b.id=ci.batch_id
            WHERE ci.uid IN ({q}) AND b.consignor_id=? AND b.status IN ('pending','approved')""",
        uids + [c["id"]]).fetchall()}
    skip = dup | dup2
    items = [x for x in items if x["uid"] not in skip]
    if not items:
        await msg.answer("Tất cả UID đều trùng kho hiện tại hoặc lô đang chờ duyệt.")
        return
    bid, code = db.consign_batch_create(c["id"], d["stall"], d["category_id"],
                                        d["floor_price"], d["warranty_days"])
    for it in items:
        db.consign_item_add(bid, c["id"], it)
    # đánh dấu các dòng Sheet đã lên lô (nếu đi từ nút 🔄 Cập nhật kho)
    try:
        await _kg_after_sync_mark(msg, state, {x["uid"] for x in items}, code)
    except Exception:
        pass
    sug = C.suggest_price(d["category_id"], d["floor_price"])
    await state.update_data(batch_id=bid, code=code, n_items=len(items))
    await state.set_state(ConsignBatch.confirm)
    warn = ""
    if dup:
        warn += f"\n⚠️ Trùng kho đã loại: {len(dup)}"
    if dup2:
        warn += f"\n⚠️ Đã có trong lô chờ duyệt: {len(dup2)}"
    if errs:
        warn += f"\n⚠️ Lỗi định dạng: {len(errs)} dòng"
    await msg.answer(
        f"<b>Xem lại lô {code}</b>\n"
        f"🏪 {d['stall']} — {d['category_name']}\n"
        f"📦 {len(items)} acc{warn}\n"
        f"💰 Giá sàn: {d['floor_price']:,}đ/acc\n"
        f"💡 Giá bán gợi ý: {sug:,}đ/acc (admin chốt)\n"
        f"🛡️ BH: {d['warranty_days']} ngày",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Gửi duyệt", callback_data="kg:submit"),
             InlineKeyboardButton(text="❌ Hủy lô", callback_data="kg:cancelbatch")]]))


@router.message(ConsignBatch.items)
async def _kg_items(msg: Message, state: FSMContext):
    # Nhận file .txt/.xlsx (chuẩn như admin nhập kho) hoặc dán text
    # -> ghi vào Sheet nhập hàng riêng của đối tác (không tạo lô ngay).
    from .. import consign_sheet as _cs
    c = db.consignor_get(msg.from_user.id)
    if not c or c["status"] != "active":
        await state.clear()
        return
    if msg.document:
        from .common import _parse_stock_file
        doc = msg.document
        wait = await msg.answer("⏳ Đang đọc file...")
        try:
            file_info = await msg.bot.get_file(doc.file_id)
            raw = await msg.bot.download_file(file_info.file_path)
        except Exception as e:
            await wait.edit_text(f"❌ Không đọc được file: {e}")
            return
        rows, err = _parse_stock_file(raw.getvalue(), doc.file_name or "")
        if err:
            await wait.edit_text(err)
            return
        # rows: {uid,password,created_date,backup_mail,note,totp,cookie,token}
        items, errs = [], []
        for r in rows:
            if not r.get("uid"):
                continue
            note = r.get("note", "")
            if r.get("created_date"):
                note = f"[NSX {r['created_date']}] {note}".strip()
            items.append({"uid": r["uid"], "password": r.get("password", ""),
                          "backup_mail": r.get("backup_mail", ""), "totp": r.get("totp", ""),
                          "cookie": r.get("cookie", ""), "token": r.get("token", ""),
                          "note": note})
        await wait.delete()
    else:
        text = msg.text or ""
        items, errs = _parse_items(text)
    if not items:
        await msg.answer("Không đọc được acc nào. Kiểm tra lại định dạng.")
        return
    # ghi vào Sheet nhập hàng riêng
    d = await state.get_data()
    sid = d.get("_import_sid") or ""
    if not sid:
        sid, url = await _kg_ensure_sheet(msg, state, c)
        if not sid:
            return
    else:
        url = f"https://docs.google.com/spreadsheets/d/{sid}"
    wait = await msg.answer(f"⏳ Đang ghi {len(items)} acc vào Sheet...")
    n = await _cs.append_import_rows(sid, items)
    try:
        await wait.delete()
    except Exception:
        pass
    if n < 0:
        await msg.answer("❌ Không ghi được vào Sheet. Thử lại sau.")
        return
    warn = f"\n⚠️ Lỗi định dạng: {len(errs)} dòng" if errs else ""
    await msg.answer(
        f"✅ Đã ghi <b>{n}</b> acc vào <b>Sheet nhập hàng</b> của bạn.{warn}\n\n"
        "Gửi thêm file/text nếu cần, hoặc bấm <b>🔄 Cập nhật kho</b> để đưa acc vào lô.",
        parse_mode="HTML", reply_markup=_kg_sync_kb(url))


@router.callback_query(F.data == "kg:up:sync")
async def _kg_sync(cb: CallbackQuery, state: FSMContext):
    """🔄 Cập nhật kho: đọc tab 'Nhập hàng' -> nhập acc chưa lên lô vào batch."""
    from .. import consign_sheet as _cs
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    if not c or c["status"] != "active":
        return
    d = await state.get_data()
    # FIX 2026-09-29: kiểm tra state còn đủ thông tin tạo lô không
    # (backend restart làm mất FSM state -> báo rõ thay vì KeyError ngầm)
    if not d.get("floor_price") or not d.get("category_id"):
        await cb.message.answer(
            "⚠️ Phiên tạo lô đã hết hạn (do hệ thống khởi động lại).\n"
            "Bạn bấm <b>📦 Tạo lô mới</b> và làm lại 5 bước nhé — Sheet nhập hàng của bạn vẫn còn nguyên.",
            parse_mode="HTML", reply_markup=_kg_menu_kb(c))
        return
    sid = d.get("_import_sid") or ""
    if not sid:
        # thử lấy từ DB
        c2 = db.consignor_get_by_id(c["id"])
        sid = (c2.get("sheet_id") or "").strip() if c2 else ""
    if not sid:
        await cb.message.answer("Chưa có Sheet nhập hàng. Chọn lại cách gửi ở bước 5/5.")
        return
    wait = await cb.message.answer("⏳ Đang đọc Sheet nhập hàng...")
    items, rnums = await _cs.read_import_rows(sid)
    try:
        await wait.delete()
    except Exception:
        pass
    if not items:
        await cb.message.answer("📭 Sheet chưa có acc mới nào (các dòng đã lên lô được đánh dấu ở cột Trạng thái).")
        return
    # đánh dấu đã nhập TRƯỚC khi tạo lô để tránh nhập trùng nếu lỗi giữa chừng
    # (lưu ý: _kg_finish_items có thể loại bớt do trùng kho -> chỉ đánh dấu những dòng thực sự được thêm)
    await state.update_data(_sync_items=items, _sync_rows=rnums, _import_sid=sid)
    await _kg_finish_items(cb.message, state, items, [], uid=cb.from_user.id)


async def _kg_after_sync_mark(msg: Message, state: FSMContext, added_uids: set, code: str):
    """Đánh dấu các dòng Sheet đã được đưa vào lô."""
    from .. import consign_sheet as _cs
    d = await state.get_data()
    items = d.get("_sync_items") or []
    rnums = d.get("_sync_rows") or []
    sid = d.get("_import_sid") or ""
    if not sid or not items:
        return
    mark_rows = [r for it, r in zip(items, rnums) if it["uid"] in added_uids]
    if mark_rows:
        await _cs.mark_imported(sid, mark_rows, f"✅ {code}")


@router.callback_query(F.data == "kg:submit")
async def _kg_submit(cb: CallbackQuery, state: FSMContext):
    await _cb_answer(cb)
    d = await state.get_data()
    bid = d.get("batch_id")
    if not bid or not db.consign_batch_submit(bid):
        await cb.message.answer("Lô trống hoặc đã gửi rồi.")
        await state.clear()
        return
    await state.clear()
    b = db.consign_batch_get(bid)
    await cb.message.edit_text(f"✅ Đã gửi lô <b>{b['code']}</b> chờ duyệt!", parse_mode="HTML",
                               reply_markup=_kg_menu_kb(db.consignor_get(cb.from_user.id)))
    try:
        admin_id = int(db.get_setting("admin_tg_id", "0") or 0)
        if admin_id:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="👁 Xem & duyệt lô", callback_data=f"kga:batch:{bid}")]])
            await cb.bot.send_message(
                admin_id,
                f"📦 <b>Lô ký gửi mới {b['code']}</b>\n"
                f"👤 {html.escape((db.get_conn().execute('SELECT name FROM consignors WHERE id=?', (b['consignor_id'],)).fetchone() or {'name':''})['name'])} | "
                f"{b['total_items']} acc | Giá sàn {b['floor_price']:,}đ",
                parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass


@router.callback_query(F.data == "kg:cancelbatch")
async def _kg_cancelbatch(cb: CallbackQuery, state: FSMContext):
    await _cb_answer(cb)
    d = await state.get_data()
    bid = d.get("batch_id")
    if bid:
        with db._lock:
            c = db.get_conn()
            c.execute("DELETE FROM consignment_items WHERE batch_id=?", (bid,))
            c.execute("DELETE FROM consignment_batches WHERE id=? AND status='draft'", (bid,))
            c.commit()
    await state.clear()
    await cb.message.edit_text("Đã hủy lô.", reply_markup=_kg_menu_kb(db.consignor_get(cb.from_user.id)))


# ================= Lô / đơn / ví của đối tác =================
@router.callback_query(F.data == "kg:batches")
async def _kg_batches(cb: CallbackQuery):
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    rows = db.consign_batches_list(c["id"], limit=10)
    if not rows:
        await cb.message.answer("Chưa có lô nào.")
        return
    kb_rows = [[InlineKeyboardButton(text=f"📦 {b['code']} ({b['status']})",
                                    callback_data=f"kg:batch:{b['id']}")] for b in rows]
    kb_rows.append([InlineKeyboardButton(text="◀️ Quay lại", callback_data="kg:menu")])
    txt = "📋 <b>Lô của bạn</b>\n\n" + "\n\n".join(C.batch_summary_text(b) for b in rows)
    await cb.message.edit_text(txt, parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


@router.callback_query(F.data.startswith("kg:batch:"))
async def _kg_batch_detail(cb: CallbackQuery):
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    bid = int(cb.data.split(":")[2])
    b = db.consign_batch_get(bid)
    if not b or b["consignor_id"] != c["id"]:
        await cb.message.answer("Không tìm thấy lô.")
        return
    items = db.consign_items_of_batch(bid)
    st_icon = {"draft": "📝", "submitted": "⏳", "approved": "✅", "listed": "🟢",
               "rejected": "❌", "return_requested": "🔙", "closed": "🔒"}.get(b["status"], "•")
    lines = []
    for it in items[:30]:
        ist = {"pending": "⏳", "listed": "🟢", "sold": "✅", "returned": "🔙",
               "rejected": "❌"}.get(it["status"], "•")
        extra = ""
        if it["status"] == "sold":
            extra = f" {it['sold_price']:,}đ → nhận {it['net_amount']:,}đ"
        lines.append(f"{ist} <code>{it['uid']}</code>{extra}")
    if len(items) > 30:
        lines.append(f"<i>... và {len(items)-30} acc nữa (xem kho Sheet)</i>")
    kb = [[InlineKeyboardButton(text="◀️ Quay lại", callback_data="kg:batches")]]
    if b["status"] in ("submitted", "draft"):
        kb.insert(0, [
            InlineKeyboardButton(text="✏️ Sửa giá sàn", callback_data=f"kg:batchedit:{bid}"),
            InlineKeyboardButton(text="🗑 Hủy lô", callback_data=f"kg:batchcancel:{bid}"),
        ])
    if b["status"] in ("approved", "listed"):
        n_unsold = sum(1 for it in items if it["status"] == "listed")
        if n_unsold:
            kb.insert(0, [InlineKeyboardButton(
                text=f"🔙 Yêu cầu trả {n_unsold} acc chưa bán",
                callback_data=f"kg:retreq:{bid}")])
    await cb.message.edit_text(
        f"{st_icon} <b>Lô {b['code']}</b> — {b['status']}\n"
        f"Giá bán: {b['sell_price']:,}đ | BH: {b['warranty_days']} ngày\n"
        f"Tổng: {len(items)} acc\n\n" + "\n".join(lines),
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@router.callback_query(F.data.startswith("kg:retreq:"))
async def _kg_retreq(cb: CallbackQuery):
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    bid = int(cb.data.split(":")[2])
    if not db.consign_batch_request_return(bid, c["id"]):
        await cb.message.answer("Không yêu cầu được (lô không còn acc chưa bán).")
        return
    await cb.message.edit_text("✅ Đã gửi yêu cầu trả hàng. Chủ shop sẽ duyệt.")
    # báo chủ shop
    try:
        admin_id = int(db.get_setting("admin_tg_id", "0") or 0)
        b = db.consign_batch_get(bid)
        if admin_id:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Duyệt trả hàng", callback_data=f"kga:retok:{bid}"),
                 InlineKeyboardButton(text="❌ Từ chối", callback_data=f"kga:retno:{bid}")]])
            await cb.bot.send_message(
                admin_id,
                f"🔙 <b>Yêu cầu trả hàng ký gửi</b>\n👤 {html.escape(c['name'])} — lô <b>{b['code']}</b>",
                parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass
    C.audit(cb.from_user.id, c["name"], "return_request", f"bid={bid}")


@router.callback_query(F.data.startswith("kg:batchcancel:"))
async def _kg_batch_cancel(cb: CallbackQuery):
    """Đối tác hủy lô đang chờ duyệt."""
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    bid = int(cb.data.split(":")[2])
    b = db.consign_batch_get(bid)
    if not b or b["consignor_id"] != c["id"] or b["status"] not in ("submitted", "draft"):
        await cb.message.answer("❌ Lô không còn ở trạng thái chờ duyệt.")
        return
    db.get_conn().execute("UPDATE consignment_batches SET status='cancelled' WHERE id=?", (bid,))
    db.get_conn().commit()
    await cb.message.edit_text(f"🗑 Đã hủy lô <b>{b['code']}</b>.", parse_mode="HTML")
    C.audit(cb.from_user.id, c["name"], "batch_cancel", f"bid={bid}")


class BatchEditPriceState(StatesGroup):
    waiting_price = State()


@router.callback_query(F.data.startswith("kg:batchedit:"))
async def _kg_batch_edit(cb: CallbackQuery, state: FSMContext):
    """Đối tác sửa giá sàn lô đang chờ duyệt."""
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    bid = int(cb.data.split(":")[2])
    b = db.consign_batch_get(bid)
    if not b or b["consignor_id"] != c["id"] or b["status"] not in ("submitted", "draft"):
        await cb.message.answer("❌ Lô không còn ở trạng thái chờ duyệt.")
        return
    await state.update_data(_edit_bid=bid)
    await state.set_state(BatchEditPriceState.waiting_price)
    await cb.message.answer(
        f"✏️ <b>Sửa giá sàn lô {b['code']}</b>\n"
        f"Giá sàn hiện tại: <b>{b['floor_price']:,}đ</b>/acc\n\n"
        f"Nhập giá sàn mới (số tiền/acc):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Hủy", callback_data="kg:editcancel")],
        ]))


@router.callback_query(F.data == "kg:editcancel")
async def _kg_batch_edit_cancel(cb: CallbackQuery, state: FSMContext):
    """Hủy flow sửa giá sàn."""
    await _cb_answer(cb)
    await state.clear()
    await cb.message.answer("Đã hủy sửa giá sàn.")


@router.message(BatchEditPriceState.waiting_price)
async def _kg_batch_edit_price(msg: Message, state: FSMContext):
    if msg.text and msg.text.strip().lower() == "/huy":
        await state.clear()
        await msg.answer("Đã hủy.")
        return
    try:
        v = int(msg.text.strip().replace(",", "").replace(".", "").replace("k", "000"))
        assert v > 0
    except Exception:
        await msg.answer("Vui lòng nhập số tiền hợp lệ (vd: 50000).")
        return
    d = await state.get_data()
    bid = d.get("_edit_bid", 0)
    await state.clear()
    c = db.consignor_get(msg.from_user.id)
    b = db.consign_batch_get(bid)
    if not b or b["consignor_id"] != c["id"] or b["status"] not in ("submitted", "draft"):
        await msg.answer("❌ Lô không còn ở trạng thái chờ duyệt.")
        return
    old = b["floor_price"]
    db.get_conn().execute("UPDATE consignment_batches SET floor_price=? WHERE id=?", (v, bid))
    db.get_conn().commit()
    await msg.answer(f"✅ Đã sửa giá sàn lô <b>{b['code']}</b>: {old:,}đ → <b>{v:,}đ</b>/acc.",
                     parse_mode="HTML")
    C.audit(msg.from_user.id, c["name"], "batch_edit_price", f"bid={bid} {old}->{v}")


@router.callback_query(F.data == "kg:orders")
async def _kg_orders(cb: CallbackQuery):
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    rows = [dict(r) for r in db.get_conn().execute(
        "SELECT o.*, i.uid FROM consignment_orders o LEFT JOIN consignment_items i ON i.id=o.item_id"
        " WHERE o.consignor_id=? ORDER BY o.id DESC LIMIT 10", (c["id"],)).fetchall()]
    if not rows:
        await cb.message.answer("Chưa bán được acc nào.")
        return
    lines = []
    for o in rows:
        uid = (o["uid"] or "")[:4] + "••••"
        lines.append(f"• {uid} — bán {o['sell_price']:,}đ, thực nhận {o['net_amount']:,}đ")
    await cb.message.edit_text("💰 <b>Đơn đã bán</b>\n\n" + "\n".join(lines), parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                   [InlineKeyboardButton(text="◀️ Quay lại", callback_data="kg:menu")]]))


@router.callback_query(F.data == "kg:wallet")
async def _kg_wallet(cb: CallbackQuery):
    await _cb_answer(cb)
    await cb.message.edit_text(C.wallet_text(cb.from_user.id), parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                   [InlineKeyboardButton(text="📜 Lịch sử ví", callback_data="kg:wallet_hist")],
                                   [InlineKeyboardButton(text="💸 Lịch sử rút", callback_data="kg:wd_hist")],
                                   [InlineKeyboardButton(text="💸 Rút tiền", callback_data="kg:withdraw")],
                                   [InlineKeyboardButton(text="◀️ Quay lại", callback_data="kg:menu")]]))


@router.callback_query(F.data == "kg:wallet_hist")
async def _kg_wallet_hist(cb: CallbackQuery):
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    if not c:
        await cb.message.edit_text("Bạn chưa đăng ký ký gửi.", parse_mode="HTML")
        return
    rows = db.consign_ledger_list(c["id"], limit=20)
    if not rows:
        txt = "📜 <b>Lịch sử ví</b>\n\nChưa có giao dịch nào."
    else:
        kind_map = {
            "pending_in": "💰 Tiền bán về (chờ BH)",
            "released": "✅ Hết BH → khả dụng",
            "payout_req": "💸 Yêu cầu rút",
            "payout_done": "✅ Rút thành công",
            "payout_cancel": "↩️ Hủy rút",
            "dispute_hold": "🔒 Giữ do tranh chấp",
            "dispute_release": "🔓 Giải phóng tranh chấp",
        }
        lines = []
        for r in rows:
            ts = time.strftime("%d/%m %H:%M", time.localtime(r["created_at"]))
            kind = kind_map.get(r["kind"], r["kind"])
            amt = f"{r['amount']:,}đ"
            note = f" — {r['note']}" if r.get("note") else ""
            lines.append(f"• {ts} | {kind}: <b>{amt}</b>{note}")
        txt = "📜 <b>Lịch sử ví</b> (20 gần nhất)\n\n" + "\n".join(lines)
    await cb.message.edit_text(txt, parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                   [InlineKeyboardButton(text="◀️ Quay lại", callback_data="kg:wallet")]]))


@router.callback_query(F.data == "kg:wd_hist")
async def _kg_wd_hist(cb: CallbackQuery):
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    if not c:
        await cb.message.edit_text("Bạn chưa đăng ký ký gửi.", parse_mode="HTML")
        return
    rows = db.get_conn().execute(
        "SELECT * FROM consignment_payouts WHERE consignor_id=? ORDER BY id DESC LIMIT 20",
        (c["id"],)).fetchall()
    if not rows:
        txt = "💸 <b>Lịch sử rút tiền</b>\n\nChưa có yêu cầu nào."
    else:
        status_map = {"pending": "⏳ Chờ duyệt", "paid": "✅ Đã chuyển", "rejected": "❌ Từ chối"}
        lines = []
        for r in rows:
            d = dict(r)
            ts = time.strftime("%d/%m %H:%M", time.localtime(d["created_at"]))
            st = status_map.get(d["status"], d["status"])
            lines.append(f"• {ts} | <b>{d['amount']:,}đ</b> → {d['channel']} | {st}")
        txt = "💸 <b>Lịch sử rút tiền</b> (20 gần nhất)\n\n" + "\n".join(lines)
    await cb.message.edit_text(txt, parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                   [InlineKeyboardButton(text="◀️ Quay lại", callback_data="kg:wallet")]]))


@router.callback_query(F.data == "kg:stats")
async def _kg_stats(cb: CallbackQuery):
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    if not c:
        await cb.message.edit_text("Bạn chưa đăng ký ký gửi.", parse_mode="HTML")
        return
    # Thống kê cá nhân
    conn = db.get_conn()
    sold = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(sell_price),0), COALESCE(SUM(net_amount),0), COALESCE(SUM(fee_amount),0)"
        " FROM consignment_orders WHERE consignor_id=? AND status='sold'",
        (c["id"],)).fetchone()
    batches = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(total_items),0) FROM consignment_batches WHERE consignor_id=?",
        (c["id"],)).fetchone()
    payouts = conn.execute(
        "SELECT COALESCE(SUM(net),0) FROM consignment_payouts WHERE consignor_id=? AND status='paid'",
        (c["id"],)).fetchone()
    w = db.consign_wallets(c["id"])
    txt = (
        f"📊 <b>Thống kê ký gửi</b>\n\n"
        f"📦 Tổng lô đã tạo: <b>{batches[0]}</b> ({batches[1]} acc)\n"
        f"💰 Đã bán: <b>{sold[0]}</b> acc\n"
        f"💵 Tổng doanh thu: <b>{sold[1]:,}đ</b>\n"
        f"💸 Phí shop đã thu: <b>{sold[3]:,}đ</b>\n"
        f"✅ Thực nhận: <b>{sold[2]:,}đ</b>\n"
        f"👛 Ví hiện tại: <b>{w['avail'] + w['pending']:,}đ</b> (khả dụng {w['avail']:,}đ)\n"
        f"💸 Đã rút thành công: <b>{payouts[0]:,}đ</b>"
    )
    await cb.message.edit_text(txt, parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                   [InlineKeyboardButton(text="◀️ Quay lại", callback_data="kg:menu")]]))


@router.callback_query(F.data == "kg:profile")
async def _kg_profile(cb: CallbackQuery):
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    if not c:
        await cb.message.edit_text("Bạn chưa đăng ký ký gửi.", parse_mode="HTML")
        return
    status_map = {"pending": "⏳ Chờ duyệt", "active": "✅ Đang hoạt động", "locked": "🔒 Bị khóa"}
    level_map = {"new": "🆕 Mới", "trusted": "⭐ Tin cậy", "vip": "💎 VIP"}
    payout = c.get("payout_info") or "(chưa có)"
    notify_on = (c.get("notify_sale", 1) or 0) == 1
    bot_un = (c.get("notify_bot_username") or "").strip()
    bot_cfg = f"✅ {html.escape(bot_un)}" if bot_un else "❌ chưa cấu hình"
    txt = (
        f"👤 <b>Hồ sơ đối tác</b>\n\n"
        f"📛 Tên: <b>{html.escape(c.get('name') or '')}</b>\n"
        f"📞 SĐT: {html.escape(c.get('phone') or '(chưa có)')}\n"
        f"📊 Trạng thái: {status_map.get(c.get('status'), c.get('status'))}\n"
        f"🏅 Cấp độ: {level_map.get(c.get('level'), c.get('level'))}\n"
        f"📦 Hạn mức: <b>{c.get('max_items') or 0}</b> acc / <b>{(c.get('max_value') or 0):,}đ</b>\n"
        f"💳 Thông tin nhận tiền:\n<code>{html.escape(payout)}</code>\n"
        f"🔔 Báo khi bán được acc: <b>{'BẬT' if notify_on else 'TẮT'}</b>\n"
        f"🤖 Bot báo riêng: <b>{bot_cfg}</b>\n\n"
        f"<i>Cập nhật thông tin nhận tiền khi tạo yêu cầu rút tiền.</i>"
    )
    kb_rows = [
        [InlineKeyboardButton(
            text="🔕 Tắt báo bán hàng" if notify_on else "🔔 Bật báo bán hàng",
            callback_data="kg:toggle_notify")],
        [InlineKeyboardButton(text="🤖 Cấu hình bot báo riêng", callback_data="kg:botcfg")],
    ]
    if bot_un:
        kb_rows.append([InlineKeyboardButton(text="🧪 Gửi tin test qua bot riêng", callback_data="kg:bottest")])
        kb_rows.append([InlineKeyboardButton(text="🗑 Xóa bot riêng", callback_data="kg:botdel")])
    kb_rows.append([InlineKeyboardButton(text="◀️ Quay lại", callback_data="kg:menu")])
    await cb.message.edit_text(txt, parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


@router.callback_query(F.data == "kg:toggle_notify")
async def _kg_toggle_notify(cb: CallbackQuery):
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    if not c:
        return
    new_val = 0 if (c.get("notify_sale", 1) or 0) == 1 else 1
    db.consignor_update(c["id"], notify_sale=new_val)
    await _kg_profile(cb)


@router.callback_query(F.data == "kg:botcfg")
async def _kg_botcfg(cb: CallbackQuery, state: FSMContext):
    """Bắt đầu cấu hình bot báo riêng: hướng dẫn + chờ nhập token."""
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    if not c:
        return
    await state.set_state(ConsignBotCfg.token)
    await cb.message.answer(
        "🤖 <b>Cấu hình bot báo riêng</b>\n\n"
        "Khi có acc bán được, tin báo sẽ gửi qua <b>bot riêng của bạn</b> thay vì bot chính.\n\n"
        "<b>Các bước:</b>\n"
        "1️⃣ Nhắn cho @BotFather → <code>/newbot</code> → đặt tên → lấy <b>token</b> (dạng <code>123456:ABC...</code>)\n"
        "2️⃣ Mở bot vừa tạo → bấm <b>Start</b> (bắt buộc, không thì bot không gửi được tin cho bạn)\n"
        "3️⃣ Gửi token vào đây\n\n"
        "<i>Gửi /huy để hủy.</i>",
        parse_mode="HTML")


@router.message(ConsignBotCfg.token)
async def _kg_botcfg_token(msg: Message, state: FSMContext):
    """Nhận token, validate qua getMe, lưu vào DB."""
    if (msg.text or "").strip() == "/huy":
        await state.clear()
        await msg.answer("Đã hủy cấu hình bot.")
        return
    c = db.consignor_get(msg.from_user.id)
    if not c:
        await state.clear()
        return
    token = (msg.text or "").strip()
    # Xóa tin nhắn chứa token để tránh lộ
    try:
        await msg.delete()
    except Exception:
        pass
    ok, res = await C.validate_bot_token(token)
    if not ok:
        await msg.answer(f"❌ {html.escape(res)}\n\nGửi lại token đúng, hoặc /huy để hủy.")
        return
    db.consignor_update(c["id"], notify_bot_token=token, notify_bot_username=res)
    try:
        C.audit(msg.from_user.id, msg.from_user.full_name, "botcfg_set", f"bot={res}")
    except Exception:
        pass
    await state.clear()
    await msg.answer(
        f"✅ <b>Đã lưu bot báo riêng: {html.escape(res)}</b>\n\n"
        f"Nhớ <b>mở {html.escape(res)} và bấm Start</b> trước nhé.\n"
        f"Bấm <b>🧪 Gửi tin test</b> trong Hồ sơ để kiểm tra.",
        parse_mode="HTML")


@router.callback_query(F.data == "kg:bottest")
async def _kg_bottest(cb: CallbackQuery):
    """Gửi tin test qua bot riêng của đối tác."""
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    if not c:
        return
    token = (c.get("notify_bot_token") or "").strip()
    if not token:
        await cb.message.answer("Bạn chưa cấu hình bot riêng.")
        return
    ok, err = await C.notify_via_partner_bot(
        cb.from_user.id,
        "🧪 <b>Tin test từ bot báo riêng ký gửi</b>\n\n"
        "Nếu bạn đọc được tin này thì cấu hình đã thành công! 🎉\n"
        "Từ nay tin bán được acc sẽ gửi qua bot này.",
        token)
    if ok:
        await cb.message.answer("✅ Đã gửi tin test qua bot riêng. Kiểm tra tin nhắn từ bot của bạn nhé!")
    else:
        await cb.message.answer(
            "❌ Không gửi được qua bot riêng.\n\n"
            "Kiểm tra: (1) bạn đã <b>Start</b> bot chưa, (2) token còn hiệu lực không.",
            parse_mode="HTML")


@router.callback_query(F.data == "kg:botdel")
async def _kg_botdel(cb: CallbackQuery):
    """Xóa cấu hình bot riêng → quay về dùng bot chính."""
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    if not c:
        return
    db.consignor_update(c["id"], notify_bot_token="", notify_bot_username="")
    try:
        C.audit(cb.from_user.id, cb.from_user.full_name, "botcfg_del", "")
    except Exception:
        pass
    await cb.message.answer("🗑 Đã xóa bot riêng. Tin báo sẽ gửi qua bot chính như trước.")
    await _kg_profile(cb)


@router.callback_query(F.data == "kg:promos")
async def _kg_promos(cb: CallbackQuery):
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    rows = db.consign_promos_pending(c["id"])
    if not rows:
        await cb.message.answer("Không có chiến dịch nào chờ bạn duyệt.")
        return
    for p in rows[:5]:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Đồng ý", callback_data=f"kg:promo:{p['id']}:1"),
             InlineKeyboardButton(text="❌ Từ chối", callback_data=f"kg:promo:{p['id']}:0")]])
        await cb.message.answer(f"🎁 <b>{html.escape(p['title'])}</b>\n{html.escape(p['detail'])}",
                                parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("kg:promo:"))
async def _kg_promo_decide(cb: CallbackQuery):
    await _cb_answer(cb)
    _, _, pid, v = cb.data.split(":")
    db.consign_promo_decide(int(pid), v == "1")
    await cb.message.edit_text("Đã ghi nhận: " + ("✅ Đồng ý" if v == "1" else "❌ Từ chối"))


# ================= Email kho Sheet =================
@router.callback_query(F.data == "kg:email")
async def _kg_email(cb: CallbackQuery, state: FSMContext):
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    cur = (c.get("sheet_email") or "").strip()
    await state.set_state(ConsignReg.email)
    await state.update_data(_upd_email=True)
    await cb.message.answer(
        f"📧 <b>Email kho Sheet</b>\nHiện tại: <b>{html.escape(cur or 'chưa có')}</b>\n\n"
        f"Nhập email Google để shop mở quyền xem <b>kho Sheet riêng</b> của bạn.\n"
        f"Gõ /boqua để giữ nguyên.",
        parse_mode="HTML")


@router.callback_query(F.data == "kg:sheet")
async def _kg_sheet(cb: CallbackQuery):
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    url = (c.get("sheet_url") or "").strip() if c else ""
    if not c or not url or not c.get("sheet_access_granted"):
        await cb.message.answer("📊 Kho Sheet riêng chưa sẵn sàng.\n"
                                "Shop sẽ mở quyền xem cho email Google của bạn, bạn sẽ nhận tin báo.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Mở kho Sheet riêng", url=url)]])
    await cb.message.answer("📊 <b>Kho Sheet riêng của bạn</b> — tự cập nhật mỗi 30 phút.\n"
                            "Bấm nút bên dưới để mở:",
                            parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "kg:disputes")
async def _kg_disputes(cb: CallbackQuery):
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    rows = db.consign_disputes_of_consignor(c["id"], 10)
    if not rows:
        await cb.message.answer("Không có tranh chấp nào liên quan đến acc của bạn. 🎉")
        return
    kb_rows = []
    for d in rows:
        st = {"open": "⚠️", "closed": "✅"}.get(d["status"], "•")
        kb_rows.append([InlineKeyboardButton(
            text=f"{st} Tranh chấp #{d['id']} — UID {d.get('uid') or ''}",
            callback_data=f"kg:disp:{d['id']}")])
    kb_rows.append([InlineKeyboardButton(text="◀️ Quay lại", callback_data="kg:menu")])
    await cb.message.edit_text("⚠️ <b>Tranh chấp liên quan đến bạn</b>\n\n"
                               "<i>Tiền của đơn tranh chấp bị tạm giữ đến khi shop xử lý xong.</i>",
                               parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


@router.callback_query(F.data.startswith("kg:disp:"))
async def _kg_disp_detail(cb: CallbackQuery):
    """Chi tiết tranh chấp cho đối tác: xem ảnh, deadline, phản hồi."""
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    did = int(cb.data.split(":")[2])
    d = db.get_conn().execute("SELECT * FROM consignment_disputes WHERE id=?", (did,)).fetchone()
    if not d or d["consignor_id"] != c["id"]:
        await cb.message.answer("Không tìm thấy.")
        return
    d = dict(d)
    # Lấy UID
    it = db.get_conn().execute("SELECT uid FROM consignment_items WHERE id=?", (d["item_id"],)).fetchone()
    uid = it["uid"] if it else ""
    # Thời gian còn lại
    now_ts = int(time.time())
    deadline_txt = ""
    if d["status"] == "open" and d["deadline_at"]:
        left = d["deadline_at"] - now_ts
        if left > 0:
            h = left // 3600
            deadline_txt = f"\n⏰ Còn <b>{h} giờ</b> để phản hồi (quá hạn → mặc định khách đúng)."
        else:
            deadline_txt = "\n⏰ <b>Đã quá hạn phản hồi.</b>"
    # Phản hồi của đối tác (nếu có)
    resp_txt = ""
    if d["partner_responded"]:
        resp_txt = f"\n\n📝 <b>Phản hồi của bạn:</b> {html.escape(d['partner_response'] or '')}"
    kb_rows = []
    if d["status"] == "open" and not d["partner_responded"]:
        kb_rows.append([
            InlineKeyboardButton(text="✅ Đồng ý đền", callback_data=f"kg:dispok:{did}"),
            InlineKeyboardButton(text="📝 Phản hồi lại", callback_data=f"kg:dispno:{did}"),
        ])
    kb_rows.append([InlineKeyboardButton(text="◀️ Quay lại", callback_data="kg:disputes")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    txt = (f"⚠️ <b>Tranh chấp #{did}</b>\n"
           f"UID <code>{html.escape(uid or '')}</code>\n"
           f"Lý do: {html.escape(d['reason'] or '')}"
           f"{deadline_txt}{resp_txt}")
    # Gửi ảnh bằng chứng nếu có
    try:
        if d["photo_file_id"]:
            await cb.message.answer_photo(d["photo_file_id"], caption=txt, parse_mode="HTML", reply_markup=kb)
        else:
            await cb.message.answer(txt, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer(txt, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("kg:dispok:"))
async def _kg_disp_agree(cb: CallbackQuery):
    """Đối tác đồng ý đền → đóng tranh chấp, trừ tiền, hoàn khách."""
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    did = int(cb.data.split(":")[2])
    d = db.get_conn().execute("SELECT * FROM consignment_disputes WHERE id=?", (did,)).fetchone()
    if not d or d["consignor_id"] != c["id"] or d["status"] != "open":
        await cb.message.answer("Tranh chấp không còn mở.")
        return
    o = db.get_conn().execute("SELECT sell_price FROM consignment_orders WHERE id=?", (d["order_id"],)).fetchone()
    amt = o["sell_price"] if o and o["sell_price"] else 0
    if db.consign_dispute_decide(did, "refund_buyer", amt, c["id"]):
        # Ghi nhận đối tác tự đồng ý
        db.get_conn().execute(
            "UPDATE consignment_disputes SET partner_responded=1, partner_response='Đồng ý đền bù' WHERE id=?",
            (did,))
        db.get_conn().commit()
        await cb.message.answer(f"✅ Đã ghi nhận. {amt:,}đ (đủ giá khách đã trả) sẽ trừ khỏi ví của bạn để hoàn cho khách.")
        # Báo admin
        try:
            admin_id = int(db.get_setting("admin_tg_id", "0") or 0)
            if admin_id:
                await cb.bot.send_message(admin_id,
                    f"✅ <b>Đối tác tự đồng ý đền</b>\nTranh chấp #{did} — {html.escape(c['name'])}\n"
                    f"Hoàn khách: <b>{amt:,}đ</b> (đối tác chịu 100%)",
                    parse_mode="HTML")
        except Exception as e:
            log.warning("dispute agree notify admin #%s: %s", did, e)
    else:
        await cb.message.answer("❌ Không xử lý được.")
    C.audit(cb.from_user.id, c["name"], "dispute_agree", f"did={did} amt={amt}")


class DisputeRespondState(StatesGroup):
    waiting_text = State()


@router.callback_query(F.data.startswith("kg:dispno:"))
async def _kg_disp_respond(cb: CallbackQuery, state: FSMContext):
    """Đối tác phản hồi lại tranh chấp."""
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    did = int(cb.data.split(":")[2])
    d = db.get_conn().execute("SELECT * FROM consignment_disputes WHERE id=?", (did,)).fetchone()
    if not d or d["consignor_id"] != c["id"] or d["status"] != "open" or d["partner_responded"]:
        await cb.message.answer("Không phản hồi được.")
        return
    await state.update_data(_disp_id=did)
    await state.set_state(DisputeRespondState.waiting_text)
    await cb.message.answer("📝 Nhập <b>lý do phản hồi</b> của bạn (gõ /huy để hủy):", parse_mode="HTML")


@router.message(DisputeRespondState.waiting_text)
async def _kg_disp_respond_text(msg: Message, state: FSMContext):
    if msg.text and msg.text.strip().lower() == "/huy":
        await state.clear()
        await msg.answer("Đã hủy.")
        return
    txt = (msg.text or "").strip()
    if not txt or len(txt) < 5:
        await msg.answer("Vui lòng nhập lý do rõ ràng (ít nhất 5 ký tự).")
        return
    d = await state.get_data()
    did = d.get("_disp_id", 0)
    await state.clear()
    c = db.consignor_get(msg.from_user.id)
    db.get_conn().execute(
        "UPDATE consignment_disputes SET partner_responded=1, partner_response=? WHERE id=?",
        (txt[:500], did))
    db.get_conn().commit()
    await msg.answer("✅ Đã gửi phản hồi. Shop sẽ xem xét cả 2 bên rồi quyết định.")
    # Báo admin
    try:
        admin_id = int(db.get_setting("admin_tg_id", "0") or 0)
        if admin_id:
            await msg.bot.send_message(admin_id,
                f"📝 <b>Đối tác phản hồi tranh chấp #{did}</b>\n"
                f"👤 {html.escape(c['name'])}\n{html.escape(txt[:300])}\n\n"
                f"Xem: /kyguiadm → Tranh chấp",
                parse_mode="HTML")
    except Exception as e:
        log.warning("dispute respond notify admin #%s: %s", did, e)
    C.audit(msg.from_user.id, c["name"], "dispute_respond", f"did={did}")


# ================= Rút tiền =================
@router.callback_query(F.data == "kg:withdraw")
async def _kg_withdraw(cb: CallbackQuery, state: FSMContext):
    await _cb_answer(cb)
    c = db.consignor_get(cb.from_user.id)
    w = db.consign_wallets(c["id"])
    min_w = int(db.get_setting("consign_min_withdraw", "50000") or 50000)
    fee = int(db.get_setting("consign_withdraw_fee", "0") or 0)
    sched = db.get_setting("consign_withdraw_schedule", "Chủ shop duyệt thủ công")
    await state.update_data(_w=w)
    await state.set_state(ConsignWithdraw.amount)
    await cb.message.answer(
        f"💸 <b>Rút tiền</b>\nKhả dụng: <b>{w['avail']:,}đ</b>\n"
        f"Tối thiểu: {min_w:,}đ | Phí rút: {fee:,}đ\nLịch: {sched}\n\nNhập số tiền muốn rút:",
        parse_mode="HTML")


@router.message(ConsignWithdraw.amount)
async def _kg_wd_amount(msg: Message, state: FSMContext):
    try:
        v = int(msg.text.strip().replace(",", "").replace(".", "").replace("k", "000"))
        assert v > 0
    except Exception:
        await msg.answer("Nhập số tiền hợp lệ.")
        return
    d = await state.get_data()
    if v > d["_w"]["avail"]:
        await msg.answer("Vượt số dư khả dụng.")
        return
    await state.update_data(amount=v)
    await state.set_state(ConsignWithdraw.channel)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏦 Ngân hàng", callback_data="kg:wdch:bank"),
         InlineKeyboardButton(text="📱 Momo", callback_data="kg:wdch:momo")]])
    await msg.answer("Chọn kênh nhận tiền:", reply_markup=kb)


@router.callback_query(F.data.startswith("kg:wdch:"))
async def _kg_wd_ch(cb: CallbackQuery, state: FSMContext):
    await _cb_answer(cb)
    ch = "Ngân hàng" if cb.data.endswith("bank") else "Momo"
    await state.update_data(channel=ch)
    await state.set_state(ConsignWithdraw.account)
    c = db.consignor_get(cb.from_user.id)
    hint = f"\n(Thông tin cũ: {c['payout_info']})" if c["payout_info"] else ""
    await cb.message.answer(f"Nhập thông tin nhận tiền ({ch}) — STK/số Momo + tên chủ:{hint}")


@router.message(ConsignWithdraw.account)
async def _kg_wd_account(msg: Message, state: FSMContext):
    await state.update_data(account=msg.text.strip()[:200])
    await state.set_state(ConsignWithdraw.confirm)
    d = await state.get_data()
    fee = int(db.get_setting("consign_withdraw_fee", "0") or 0)
    await msg.answer(
        f"Xác nhận rút:\n💰 {d['amount']:,}đ | Phí {fee:,}đ → thực nhận {d['amount'] - fee:,}đ\n"
        f"📬 {d['channel']}: {d['account']}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Xác nhận", callback_data="kg:wdok"),
             InlineKeyboardButton(text="❌ Hủy", callback_data="kg:menu")]]))


@router.callback_query(F.data == "kg:wdok")
async def _kg_wdok(cb: CallbackQuery, state: FSMContext):
    await _cb_answer(cb)
    d = await state.get_data()
    c = db.consignor_get(cb.from_user.id)
    ok, msg_t, pid = db.consign_payout_create(c["id"], d["amount"], d["channel"], d["account"])
    await state.clear()
    if not ok:
        await cb.message.answer(f"🚫 {msg_t}")
        return
    if d["account"] != c["payout_info"]:
        db.consignor_update(c["id"], payout_info=d["account"])
    await cb.message.answer(f"✅ {msg_t} (mã #{pid}). Chủ shop sẽ duyệt.")
    try:
        admin_id = int(db.get_setting("admin_tg_id", "0") or 0)
        if admin_id:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="👁 Duyệt rút tiền", callback_data=f"kga:payout:{pid}")]])
            await cb.bot.send_message(admin_id, f"💸 <b>Rút tiền ký gửi #{pid}</b>\n"
                                      f"👤 {html.escape(c['name'])} — {d['amount']:,}đ → {d['channel']}",
                                      parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass
    C.audit(cb.from_user.id, c["name"], "withdraw_req", f"pid={pid} amount={d['amount']}")


# ================= /kyguiadm — chỉ chủ shop =================
def _kga_menu():
    s = C.enabled()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Hồ sơ chờ duyệt", callback_data="kga:cons")],
        [InlineKeyboardButton(text="👤 Đối tác", callback_data="kga:conlist")],
        [InlineKeyboardButton(text="📦 Lô chờ duyệt", callback_data="kga:batches")],
        [InlineKeyboardButton(text="🟢 Lô đang bán", callback_data="kga:living")],
        [InlineKeyboardButton(text="🔙 Lô xin trả hàng", callback_data="kga:returns")],
        [InlineKeyboardButton(text="💸 Rút tiền chờ duyệt", callback_data="kga:payouts")],
        [InlineKeyboardButton(text="⚠️ Tranh chấp mở", callback_data="kga:disputes")],
        [InlineKeyboardButton(text="📊 Báo cáo tài chính", callback_data="kga:finance")],
        [InlineKeyboardButton(text="💲 Phí theo loại", callback_data="kga:fees")],
        [InlineKeyboardButton(text="⚙️ Cài đặt ký gửi", callback_data="kga:settings")],
        [InlineKeyboardButton(text=f"{'🟢' if s else '🔴'} Bật/tắt ký gửi", callback_data="kga:toggle")],
    ])


@router.message(F.text.startswith("/kyguiadm"))
async def on_kyguiadm(msg: Message, state: FSMContext):
    if not _is_super(msg.from_user.id):
        await msg.answer("🚫 Chỉ chủ shop mới dùng được.")
        return
    await state.clear()
    st = db.consign_stats()
    await msg.answer(
        f"🤝 <b>QUẢN LÝ KÝ GỬI</b>\n\n"
        f"👥 Đối tác: {st['active_consignors']} hoạt động, {st['pending_consignors']} chờ duyệt\n"
        f"📦 Lô chờ duyệt: {st['pending_batches']} | 📋 Đang bán: {st['listed_items']} acc\n"
        f"💰 GMV: {st['gmv']:,}đ\n"
        f"💸 Rút chờ: {st['pending_payouts']} | ⚠️ Tranh chấp: {st['open_disputes']}",
        parse_mode="HTML", reply_markup=_kga_menu())


@router.callback_query(F.data == "kga:menu")
async def _kga_menu_cb(cb: CallbackQuery):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    await cb.message.answer("🤝 <b>QUẢN LÝ KÝ GỬI</b>", parse_mode="HTML", reply_markup=_kga_menu())


@router.callback_query(F.data == "kga:toggle")
async def _kga_toggle(cb: CallbackQuery):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    v = "0" if C.enabled() else "1"
    db.set_setting("consign_enabled", v)
    await cb.message.edit_text(f"Ký gửi đã {'🟢 BẬT' if v == '1' else '🔴 TẮT'}.",
                               reply_markup=_kga_menu())
    C.audit(cb.from_user.id, cb.from_user.full_name, "toggle", v)


@router.callback_query(F.data == "kga:cons")
async def _kga_cons(cb: CallbackQuery):
    await _cb_answer(cb)
    rows = db.consignors_list("pending", 10)
    if not rows:
        await _cb_answer(cb, "Không có hồ sơ chờ.")
        return
    for r in rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Duyệt", callback_data=f"kga:conok:{r['id']}"),
             InlineKeyboardButton(text="❌ Từ chối", callback_data=f"kga:conno:{r['id']}")]])
        await cb.message.answer(
            f"👤 <b>{html.escape(r['name'])}</b> (<code>{r['tg_id']}</code>)\n"
            f"📞 {html.escape(r['phone'])} | 📦 {html.escape(r['note'] or '—')}\n"
            f"📅 {time.strftime('%d/%m %H:%M', time.localtime(r['created_at']))}",
            parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("kga:conok:"))
async def _kga_conok(cb: CallbackQuery):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    cid = int(cb.data.split(":")[2])
    db.consignor_set_status(cid, "active", cb.from_user.id)
    r = db.get_conn().execute("SELECT * FROM consignors WHERE id=?", (cid,)).fetchone()
    await cb.message.edit_text(f"✅ Đã duyệt {html.escape(r['name'])}.")
    await C.notify(r["tg_id"], "🎉 <b>Hồ sơ ký gửi của bạn đã được duyệt!</b>\nVào /kygui để tạo lô hàng.", cb.bot)
    C.audit(cb.from_user.id, cb.from_user.full_name, "approve_consignor", f"cid={cid}")


@router.callback_query(F.data.startswith("kga:conno:"))
async def _kga_conno(cb: CallbackQuery):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    cid = int(cb.data.split(":")[2])
    db.consignor_set_status(cid, "banned", cb.from_user.id)
    await cb.message.edit_text("❌ Đã từ chối hồ sơ.")
    C.audit(cb.from_user.id, cb.from_user.full_name, "reject_consignor", f"cid={cid}")


def _kga_con_kb(c):
    rows = []
    has_sheet = bool((c.get("sheet_id") or "").strip())
    granted = bool(c.get("sheet_access_granted"))
    if not has_sheet:
        rows.append([InlineKeyboardButton(text="🆕 Tạo Sheet kho tự động",
                                         callback_data=f"kga:consheetauto:{c['id']}")])
    rows.append([InlineKeyboardButton(text="🔗 Gắn link Sheet riêng",
                                     callback_data=f"kga:consheetlink:{c['id']}")])
    if has_sheet and not granted:
        rows.append([InlineKeyboardButton(text="✅ Đã mở quyền xem",
                                          callback_data=f"kga:congrant:{c['id']}")])
    if has_sheet:
        rows.append([InlineKeyboardButton(text="📊 Đồng bộ kho Sheet ngay",
                                          callback_data=f"kga:consheet:{c['id']}")])
    if c["status"] == "active":
        rows.append([InlineKeyboardButton(text="🔒 Khóa đối tác", callback_data=f"kga:conlock:{c['id']}")])
    elif c["status"] in ("banned", "locked"):
        rows.append([InlineKeyboardButton(text="🔓 Mở khóa", callback_data=f"kga:conunlock:{c['id']}")])
    rows.append([InlineKeyboardButton(text="📜 Lịch sử tin báo", callback_data=f"kga:connotif:{c['id']}")])
    rows.append([InlineKeyboardButton(text="◀️ Quay lại", callback_data="kga:conlist")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kga_con_text(c):
    w = db.consign_wallets(c["id"])
    n_batch = db.get_conn().execute("SELECT COUNT(*) v FROM consignment_batches WHERE consignor_id=?",
                                    (c["id"],)).fetchone()["v"]
    n_sold = db.get_conn().execute("SELECT COUNT(*) v FROM consignment_orders WHERE consignor_id=?",
                                   (c["id"],)).fetchone()["v"]
    email = (c.get("sheet_email") or "").strip() or "chưa có"
    if (c.get("sheet_id") or "").strip():
        sheet_st = "✅ đã mở quyền" if c.get("sheet_access_granted") else "⏳ chưa mở quyền"
    else:
        sheet_st = "chưa gắn link"
    bot_un = (c.get("notify_bot_username") or "").strip()
    bot_st = f"✅ {html.escape(bot_un)}" if bot_un else "❌ chưa cấu hình"
    return (f"👤 <b>{html.escape(c['name'])}</b> (<code>{c['tg_id']}</code>)\n"
            f"📞 {html.escape(c['phone'] or '—')} | 📧 {html.escape(email)}\n"
            f"📊 Kho Sheet riêng: {sheet_st}\n"
            f"🤖 Bot báo riêng: {bot_st}\n"
            f"Trạng thái: <b>{c['status']}</b> | Cấp: {c.get('level')}\n"
            f"📦 {n_batch} lô | ✅ {n_sold} acc đã bán\n"
            f"👛 Ví: chờ {w['pending']:,}đ | khả dụng {w['avail']:,}đ")


@router.callback_query(F.data == "kga:conlist")
async def _kga_conlist(cb: CallbackQuery):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    rows = db.consignors_list("", 15)
    if not rows:
        await cb.message.answer("Chưa có đối tác nào.")
        return
    kb = [[InlineKeyboardButton(text=f"{'🟢' if r['status']=='active' else '🔴'} {r['name']}",
                               callback_data=f"kga:con:{r['id']}")] for r in rows]
    kb.append([InlineKeyboardButton(text="◀️ Quay lại", callback_data="kga:menu")])
    await cb.message.answer("👥 <b>Đối tác ký gửi</b> — bấm để xem chi tiết:",
                            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@router.callback_query(F.data.startswith("kga:con:"))
async def _kga_con_detail(cb: CallbackQuery):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    cid = int(cb.data.split(":")[2])
    c = db.consignor_get_by_id(cid)
    if not c:
        await cb.message.answer("Không tìm thấy.")
        return
    await cb.message.answer(_kga_con_text(c), parse_mode="HTML", reply_markup=_kga_con_kb(c))


@router.callback_query(F.data.startswith("kga:connotif:"))
async def _kga_con_notif(cb: CallbackQuery):
    """Lịch sử tin báo cho đối tác."""
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    cid = int(cb.data.split(":")[2])
    rows = db.consign_notif_history(cid, 15)
    if not rows:
        await cb.message.answer("Chưa có tin báo nào được gửi cho đối tác này.")
        return
    lines = []
    for r in rows:
        icon = "✅" if r["ok"] else "❌"
        t = time.strftime("%d/%m %H:%M", time.localtime(r["created_at"]))
        via = {"main": "bot chính", "partner": "bot riêng"}.get(r["via_bot"], r["via_bot"])
        err = f" ({r['error'][:50]})" if r["error"] else ""
        lines.append(f"{icon} {t} — {r['kind']} #{r['ref_id']} qua {via}{err}")
    await cb.message.answer(
        f"📜 <b>Lịch sử tin báo</b>\n\n" + "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Quay lại", callback_data=f"kga:con:{cid}")]]))


@router.callback_query(F.data.startswith("kga:conlock:"))
async def _kga_conlock(cb: CallbackQuery):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    cid = int(cb.data.split(":")[2])
    db.consignor_set_status(cid, "locked", cb.from_user.id)
    c = db.consignor_get_by_id(cid)
    await cb.message.edit_text(_kga_con_text(c) + "\n\n🔒 <b>Đã khóa.</b>", parse_mode="HTML",
                               reply_markup=_kga_con_kb(c))
    try:
        await C.notify(c["tg_id"], "🔒 <b>Tài khoản ký gửi của bạn đã bị khóa.</b>\nLiên hệ chủ shop để biết thêm.", cb.bot)
    except Exception:
        pass
    C.audit(cb.from_user.id, cb.from_user.full_name, "lock_consignor", f"cid={cid}")


@router.callback_query(F.data.startswith("kga:conunlock:"))
async def _kga_conunlock(cb: CallbackQuery):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    cid = int(cb.data.split(":")[2])
    db.consignor_set_status(cid, "active", cb.from_user.id)
    c = db.consignor_get_by_id(cid)
    await cb.message.edit_text(_kga_con_text(c) + "\n\n🔓 <b>Đã mở khóa.</b>", parse_mode="HTML",
                               reply_markup=_kga_con_kb(c))
    C.audit(cb.from_user.id, cb.from_user.full_name, "unlock_consignor", f"cid={cid}")


@router.callback_query(F.data.startswith("kga:consheetauto:"))
async def _kga_consheetauto(cb: CallbackQuery):
    """Tạo spreadsheet kho riêng cho đối tác tự động (vẫn phải Share tay 1 lần)."""
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    cid = int(cb.data.split(":")[2])
    from .. import consign_sheet as _cs
    c = db.consignor_get_by_id(cid)
    if not c:
        await cb.message.answer("Không tìm thấy đối tác.")
        return
    if (c.get("sheet_id") or "").strip():
        await cb.message.answer("Đối tác đã có Sheet rồi.")
        return
    email = (c.get("sheet_email") or "").strip()
    if not email:
        await cb.message.answer("⚠️ Đối tác chưa có email Google. Bảo họ vào /kygui → 📧 Email kho Sheet để cập nhật trước.")
        return
    await cb.message.answer("⏳ Đang tạo Google Sheet kho riêng...")
    r = await _cs.create_partner_spreadsheet(c["name"])
    if not r:
        await cb.message.answer("❌ Tạo Sheet thất bại, thử lại sau hoặc dùng cách gắn link tay.")
        return
    db.consignor_update(cid, sheet_id=r["id"], sheet_url=r["url"], sheet_access_granted=0)
    c = db.consignor_get_by_id(cid)
    await cb.message.answer(
        f"✅ Đã tạo Sheet kho riêng cho <b>{html.escape(c['name'])}</b>:\n🔗 {r['url']}\n\n"
        f"👉 <b>Còn 1 bước tay:</b> mở Sheet → <b>Share</b> → nhập email "
        f"<b>{html.escape(email)}</b> → quyền <b>Viewer</b>.\n"
        f"Xong bấm nút <b>✅ Đã mở quyền xem</b> bên dưới.",
        parse_mode="HTML", reply_markup=_kga_con_kb(c))
    C.audit(cb.from_user.id, cb.from_user.full_name, "consheet_auto", f"cid={cid} sid={r['id']}")


@router.callback_query(F.data.startswith("kga:consheetlink:"))
async def _kga_consheetlink(cb: CallbackQuery, state: FSMContext):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    cid = int(cb.data.split(":")[2])
    c = db.consignor_get_by_id(cid)
    if not c:
        await cb.message.answer("Không tìm thấy đối tác.")
        return
    await state.update_data(_sheet_cid=cid)
    await state.set_state(ConsignAdminSheet.url)
    await cb.message.answer(
        f"🔗 <b>Gắn Sheet kho riêng</b> cho <b>{html.escape(c['name'])}</b>\n\n"
        f"1️⃣ Bạn tự tạo 1 Google Sheet mới (trống).\n"
        f"2️⃣ Dán link Sheet vào đây.\n"
        f"3️⃣ Mở quyền <b>Viewer</b> cho email: <b>{html.escape(c.get('sheet_email') or 'chưa có')}</b>\n"
        f"4️⃣ Bấm nút <b>✅ Đã mở quyền xem</b> để đối tác thấy nút mở kho.\n\n"
        f"<i>Gõ /huy để hủy.</i>",
        parse_mode="HTML")


@router.message(ConsignAdminSheet.url)
async def _kga_consheetlink_input(msg: Message, state: FSMContext):
    d = await state.get_data()
    cid = d.get("_sheet_cid")
    if not cid:
        await state.clear()
        return
    if msg.text.strip() == "/huy":
        await state.clear()
        await msg.answer("Đã hủy.")
        return
    from .. import consign_sheet as _cs
    sid = _cs.extract_sheet_id(msg.text)
    if not sid:
        await msg.answer("⚠️ Link không hợp lệ. Dán lại link Google Sheets (dạng .../spreadsheets/d/XXX/...):")
        return
    db.consignor_update(cid, sheet_id=sid, sheet_url=f"https://docs.google.com/spreadsheets/d/{sid}",
                        sheet_access_granted=0)
    await state.clear()
    c = db.consignor_get_by_id(cid)
    await msg.answer(
        f"✅ Đã gắn Sheet riêng cho <b>{html.escape(c['name'])}</b>.\n\n"
        f"👉 Giờ bạn mở Sheet → <b>Share</b> → nhập email "
        f"<b>{html.escape(c.get('sheet_email') or 'chưa có')}</b> → quyền <b>Viewer</b>.\n"
        f"Xong bấm nút <b>✅ Đã mở quyền xem</b> trong hồ sơ đối tác.",
        parse_mode="HTML", reply_markup=_kga_con_kb(c))
    C.audit(msg.from_user.id, msg.from_user.full_name, "consheet_link", f"cid={cid}")


@router.callback_query(F.data.startswith("kga:congrant:"))
async def _kga_congrant(cb: CallbackQuery):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    cid = int(cb.data.split(":")[2])
    c = db.consignor_get_by_id(cid)
    if not c or not (c.get("sheet_id") or "").strip():
        await cb.message.answer("Chưa gắn link Sheet.")
        return
    db.consignor_update(cid, sheet_access_granted=1)
    c = db.consignor_get_by_id(cid)
    await cb.message.edit_text(_kga_con_text(c) + "\n\n✅ <b>Đã xác nhận mở quyền xem.</b>\n"
                               "Đối tác giờ thấy nút mở kho Sheet riêng.",
                               parse_mode="HTML", reply_markup=_kga_con_kb(c))
    # báo đối tác: kho Sheet đã sẵn sàng
    try:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Mở kho Sheet riêng", url=c["sheet_url"])]])
        await cb.bot.send_message(
            int(c["tg_id"]),
            f"📊 <b>Kho Sheet riêng của bạn đã sẵn sàng!</b>\n"
            f"Bấm nút bên dưới để xem toàn bộ acc ký gửi (tự cập nhật).",
            parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass
    C.audit(cb.from_user.id, cb.from_user.full_name, "consheet_grant", f"cid={cid}")


@router.callback_query(F.data.startswith("kga:consheet:"))
async def _kga_consheet(cb: CallbackQuery):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    cid = int(cb.data.split(":")[2])
    from .. import consign_sheet as _cs
    c = db.consignor_get_by_id(cid)
    if not c or not (c.get("sheet_id") or "").strip():
        await cb.message.answer("⚠️ Đối tác chưa được gắn Sheet riêng. Bấm \"🔗 Gắn link Sheet riêng\" trước.")
        return
    await cb.message.answer("⏳ Đang đồng bộ kho Sheet...")
    n = await _cs.push_consign_warehouse(cid)
    if n >= 0:
        await cb.message.answer(
            f"✅ Đã đồng bộ <b>{n} acc</b> lên Sheet riêng của đối tác.\n"
            f"🔗 {c['sheet_url']}",
            parse_mode="HTML")
    elif n == -2:
        await cb.message.answer("⚠️ Đối tác chưa được gắn Sheet riêng.")
    else:
        await cb.message.answer("❌ Đồng bộ thất bại, thử lại sau.")
    C.audit(cb.from_user.id, cb.from_user.full_name, "consheet_sync", f"cid={cid} n={n}")


@router.callback_query(F.data == "kga:finance")
async def _kga_finance(cb: CallbackQuery):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    f = db.consign_finance_stats()
    await cb.message.answer(
        f"📊 <b>BÁO CÁO TÀI CHÍNH KÝ GỬI</b>\n\n"
        f"💰 Tổng bán (GMV): <b>{f['gmv']:,}đ</b>\n"
        f"💵 Phí shop thu: <b>{f['fee_earned']:,}đ</b>\n"
        f"⏳ Đang giữ (chờ BH): <b>{f['held']:,}đ</b>\n"
        f"⚠️ Giữ do tranh chấp: <b>{f['dispute_hold']:,}đ</b>\n"
        f"✅ Đã giải ngân: {f['released']:,}đ\n"
        f"💸 Đã rút thành công: {f['paid_out']:,}đ\n"
        f"⏳ Rút đang chờ: {f['pending_payout']:,}đ",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Quay lại", callback_data="kga:menu")]]))


@router.callback_query(F.data == "kga:batches")
async def _kga_batches(cb: CallbackQuery):
    await _cb_answer(cb)
    rows = db.consign_batches_list(status="submitted", limit=10)
    if not rows:
        await _cb_answer(cb, "Không có lô chờ duyệt.")
        return
    for b in rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👁 Xem & duyệt", callback_data=f"kga:batch:{b['id']}")]])
        await cb.message.answer(
            f"📦 <b>{b['code']}</b> — {html.escape(b['consignor_name'] or '')}\n"
            f"🏪 {b['stall']} | {b['total_items']} acc | Giá sàn {b['floor_price']:,}đ/acc",
            parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "kga:living")
async def _kga_living(cb: CallbackQuery):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    rows = db.consign_batches_list(status="approved", limit=10)
    if not rows:
        await cb.message.answer("Không có lô nào đang bán.")
        return
    for b in rows:
        n = db.get_conn().execute("SELECT COUNT(*) v FROM consignment_items"
                                  " WHERE batch_id=? AND status='listed'", (b["id"],)).fetchone()["v"]
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Sửa giá bán", callback_data=f"kga:reprice:{b['id']}")]])
        await cb.message.answer(
            f"🟢 <b>{b['code']}</b> — {html.escape(b['consignor_name'] or '')}\n"
            f"💲 {b['sell_price']:,}đ/acc | 📦 {n} acc chưa bán",
            parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("kga:batch:"))
async def _kga_batch(cb: CallbackQuery, state: FSMContext):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    bid = int(cb.data.split(":")[2])
    b = db.consign_batch_get(bid)
    if not b:
        await cb.message.answer("Lô không tồn tại (có thể đã bị xóa).")
        return
    items = db.consign_items_of_batch(bid)
    fee = db.consign_fee_get(b["category_id"])
    sug = C.suggest_price(b["category_id"], b["floor_price"])
    preview = "\n".join(f"• <code>{html.escape(i['uid'][:16])}</code>" for i in items[:8])
    if len(items) > 8:
        preview += f"\n… +{len(items) - 8} acc"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Duyệt giá {sug:,}đ", callback_data=f"kga:bok:{bid}:{sug}"),
         InlineKeyboardButton(text="✏️ Giá khác", callback_data=f"kga:bprice:{bid}")],
        [InlineKeyboardButton(text="❌ Từ chối lô", callback_data=f"kga:bno:{bid}")]])
    cat = db.get_conn().execute("SELECT name, consign_pending FROM acc_categories WHERE id=?",
                                (b["category_id"],)).fetchone()
    newcat_warn = ""
    if cat and (cat["consign_pending"] or 0):
        newcat_warn = (f"\n⚠️ <b>Loại mới do đối tác tạo</b> ({html.escape(cat['name'])}) — "
                       f"chưa có giá/phí. Khi bạn duyệt, giá bạn nhập sẽ thành "
                       f"<b>giá bán chính thức</b> của loại này.")
    await cb.message.answer(
        f"📦 <b>Lô {b['code']}</b>\n{preview}\n\n"
        f"💰 Giá sàn: {b['floor_price']:,}đ | Phí: {fee['fee_fixed']:,}đ + {fee['fee_pct']}%\n"
        f"💡 Gợi ý: {sug:,}đ | 🛡️ BH {b['warranty_days']} ngày{newcat_warn}",
        parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("kga:bok:"))
async def _kga_bok(cb: CallbackQuery):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    _, _, bid, price = cb.data.split(":")
    await _approve_batch(cb, int(bid), int(price))


async def _approve_batch(cb: CallbackQuery, bid: int, sell_price: int):
    """Duyệt lô: nhập acc vào kho (source=consign)."""
    b = db.consign_batch_get(bid)
    if not b or b["status"] not in ("submitted", "draft"):
        await cb.message.answer("Lô không còn ở trạng thái chờ duyệt.")
        return
    if not db.consign_batch_decide(bid, True, sell_price, cb.from_user.id):
        await cb.message.answer("Lô đã được xử lý rồi.")
        return
    items = db.consign_items_of_batch(bid)
    n = 0
    for it in items:
        # chống trùng lần cuối
        dup = db.get_conn().execute(
            "SELECT 1 FROM acc_stock WHERE uid=? AND status IN ('AVAILABLE','DIE') LIMIT 1",
            (it["uid"],)).fetchone()
        if dup:
            db.consign_item_set(bid, it["id"], "rejected", "trùng kho")
            continue
        with db._lock:
            c = db.get_conn()
            cur = c.execute(
                "INSERT INTO acc_stock (cat_id, uid, password, backup_mail, totp, cookie, token,"
                " note, status, source, consign_item_id, price_override, added_at)"
                " VALUES (?,?,?,?,?,?,?,?, 'AVAILABLE','consign',?,?,?) RETURNING id",
                (b["category_id"], it["uid"], it["password"], it["backup_mail"], it["totp"],
                 it["cookie"], it["token"], f"[KG-{b['code']}] {it['note']}", it["id"],
                 sell_price, int(time.time())))
            c.commit()
            sid = cur.lastrowid
        db.consign_item_link_stock(it["id"], sid)
        n += 1
    db.get_conn().execute("UPDATE consignment_batches SET status='listed', ok_items=? WHERE id=?", (n, bid))
    db.get_conn().commit()
    # Loại do đối tác tạo (consign_pending=1): chốt giá/BH theo lô vừa duyệt
    cat = db.get_conn().execute("SELECT consign_pending FROM acc_categories WHERE id=?",
                                (b["category_id"],)).fetchone()
    cat_msg = ""
    if cat and (cat["consign_pending"] or 0):
        db.get_conn().execute(
            "UPDATE acc_categories SET price=?, warranty_hours=?, consign_pending=0 WHERE id=?",
            (sell_price, int(b["warranty_days"] or 0) * 24, b["category_id"]))
        db.get_conn().commit()
        cat_msg = f"\n🏷️ Loại mới đã được chốt giá {sell_price:,}đ (nhớ set phí tại 💲 Phí theo loại)."
    await cb.message.answer(f"✅ Đã duyệt lô <b>{b['code']}</b>: {n} acc lên kệ giá {sell_price:,}đ."
                            f"\n💰 Phí shop ăn: <b>{sell_price - b['floor_price']:,}đ</b>/acc (giá sàn {b['floor_price']:,}đ).{cat_msg}",
                            parse_mode="HTML")
    # báo đối tác
    r = db.get_conn().execute("SELECT tg_id FROM consignors WHERE id=?", (b["consignor_id"],)).fetchone()
    if r:
        floor = b["floor_price"] or 0
        if sell_price < floor:
            # Giá chốt thấp hơn giá sàn đối tác đề xuất → báo rõ
            await C.notify(r["tg_id"],
                f"📦 <b>Lô {b['code']} đã được duyệt!</b>\n"
                f"{n} acc đã lên kệ, giá bán {sell_price:,}đ/acc.\n"
                f"⚠️ Giá sàn bạn đề xuất: {floor:,}đ → shop chốt: <b>{sell_price:,}đ</b>.",
                cb.bot)
        else:
            await C.notify(r["tg_id"], f"📦 <b>Lô {b['code']} đã được duyệt!</b>\n{n} acc đã lên kệ, giá bán {sell_price:,}đ/acc.", cb.bot)
    C.audit(cb.from_user.id, cb.from_user.full_name, "approve_batch", f"bid={bid} price={sell_price} fee={sell_price - b['floor_price']} n={n}")


@router.callback_query(F.data.startswith("kga:bprice:"))
async def _kga_bprice(cb: CallbackQuery, state: FSMContext):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    bid = int(cb.data.split(":")[2])
    await state.update_data(_kga_bid=bid)
    await state.set_state(ConsignAdminPrice.sell_price)
    b = db.consign_batch_get(bid)
    floor = b["floor_price"] if b else 0
    await cb.message.answer(
        f"Nhập <b>giá bán</b> mỗi acc (VNĐ) — giá sàn: <b>{floor:,}đ</b>\n"
        f"Hoặc nhập <code>+số tiền phí</code> shop ăn mỗi acc (vd <code>+5000</code> → giá bán = {floor:,} + 5,000 = {floor + 5000:,}đ).",
        parse_mode="HTML")


def _parse_price_or_fee(text: str, floor_price: int) -> tuple:
    """Parse nhập giá: '20000' = giá bán, '+5000' = phí shop ăn → giá bán = sàn + phí.
    Trả (ok, sell_price, fee_amount, err_msg)."""
    t = (text or "").strip()
    is_fee = t.startswith("+")
    num = t[1:] if is_fee else t
    try:
        v = int(num.replace(",", "").replace(".", "").replace("k", "000").replace("K", "000"))
        assert v > 0
    except Exception:
        return False, 0, 0, "Nhập số tiền hợp lệ (vd 20000 hoặc +5000)."
    if is_fee:
        return True, floor_price + v, v, ""
    return True, v, v - floor_price, ""


@router.message(ConsignAdminPrice.sell_price)
async def _kga_price_input(msg: Message, state: FSMContext):
    d = await state.get_data()
    # chế độ sửa giá sau duyệt
    if d.get("_reprice_mode"):
        bid = d["_kga_bid"]
        b0 = db.consign_batch_get(bid)
        floor0 = b0["floor_price"] if b0 else 0
        ok, v, fee_amt, err = _parse_price_or_fee(msg.text, floor0)
        if not ok:
            await msg.answer(err)
            return
        await state.clear()
        if db.consign_batch_update_price(bid, v, msg.from_user.id):
            b = db.consign_batch_get(bid)
            if not b:
                await msg.answer("✅ Đã đổi giá.")
                return
            fee_line = f"\n💰 Phí shop ăn: <b>{fee_amt:,}đ</b>/acc" if fee_amt >= 0 else ""
            await msg.answer(f"✅ Đã đổi giá lô <b>{b['code']}</b> → <b>{v:,}đ</b>/acc{fee_line}\n"
                             f"<i>Các acc chưa bán đã cập nhật giá mới.</i>", parse_mode="HTML")
            # báo đối tác
            try:
                r = db.get_conn().execute("SELECT tg_id FROM consignors WHERE id=?",
                                          (b["consignor_id"],)).fetchone()
                if r and r["tg_id"]:
                    ok, err = await _send_notify_retry(
                        msg.bot, r["tg_id"],
                        f"💲 <b>Giá bán lô {b['code']} đã đổi</b>\nGiá mới: <b>{v:,}đ</b>/acc.")
                    db.consign_notif_log(b["consignor_id"], "price_change", bid, "main", ok, err)
                    if not ok:
                        log.warning("price_change notify bid=%s: %s", bid, err)
            except Exception as e:
                log.warning("price_change notify bid=%s: %s", bid, e)
                db.consign_notif_log(b["consignor_id"], "price_change", bid, "main", False, str(e)[:200])
        else:
            await msg.answer("❌ Không đổi được giá (lô không ở trạng thái đang bán).")
        return
    # chế độ nhập phí theo loại
    if d.get("_fee_mode"):
        parts = msg.text.strip().split()
        try:
            fixed = int(parts[0].replace(",", "").replace(".", "").replace("k", "000"))
            pct = float(parts[1]) if len(parts) > 1 else 0
            assert fixed >= 0 and 0 <= pct < 100
        except Exception:
            await msg.answer("Nhập dạng: <code>cố_định %</code> — vd <code>5000 10</code>", parse_mode="HTML")
            return
        old_fee = db.consign_fee_get(d["_fee_cat"])
        db.consign_fee_set(d["_fee_cat"], fixed, pct, msg.from_user.id)
        await state.clear()
        await msg.answer(f"✅ Đã lưu phí: {fixed:,}đ + {pct}%")
        C.audit(msg.from_user.id, msg.from_user.full_name, "fee_set",
                f"cat={d['_fee_cat']} fixed={fixed} pct={pct}")
        # Báo cho các đối tác có lô đang bán thuộc loại này — chỉ khi phí thật sự đổi
        if old_fee.get("fee_fixed") == fixed and float(old_fee.get("fee_pct") or 0) == float(pct):
            await msg.answer("(Phí không đổi nên không gửi thông báo cho đối tác.)")
            return
        try:
            cat = db.get_conn().execute("SELECT name FROM acc_categories WHERE id=?",
                                        (d["_fee_cat"],)).fetchone()
            cat_name = cat["name"] if cat else f"#{d['_fee_cat']}"
            partners = db.get_conn().execute(
                "SELECT DISTINCT c.id, c.tg_id, c.name FROM consignors c "
                "JOIN consignment_batches b ON b.consignor_id=c.id "
                "WHERE b.category_id=? AND b.status='listed' AND c.status='active'",
                (d["_fee_cat"],)).fetchall()
            sent = 0
            for p in partners:
                ok, err = await _send_notify_retry(
                    msg.bot, p["tg_id"],
                    f"💰 <b>Phí ký gửi thay đổi</b>\n"
                    f"Loại: {html.escape(cat_name)}\n"
                    f"Phí mới: <b>{fixed:,}đ + {pct}%</b>/acc bán được.\n"
                    f"<i>Áp dụng cho các acc bán từ bây giờ.</i>")
                db.consign_notif_log(p["id"], "fee_change", d["_fee_cat"], "main", ok, err)
                if ok:
                    sent += 1
                else:
                    log.warning("fee_change notify partner=%s: %s", p["id"], err)
            await msg.answer(f"📣 Đã báo phí mới cho {sent} đối tác.")
        except Exception as e:
            log.warning("fee_change notify cat=%s: %s", d["_fee_cat"], e)
            await msg.answer("⚠️ Đã lưu phí nhưng gửi thông báo lỗi, kiểm tra log.")
        return
    bid = d["_kga_bid"]
    b = db.consign_batch_get(bid)
    if not b:
        await state.clear()
        await msg.answer("Lô không tồn tại (có thể đã bị xóa).")
        return
    ok, v, fee_amt, err = _parse_price_or_fee(msg.text, b["floor_price"] if b else 0)
    if not ok:
        await msg.answer(err)
        return
    await state.clear()
    if v < b["floor_price"]:
        # cần đối tác đồng ý nếu thấp hơn giá sàn
        await msg.answer(f"⚠️ Giá {v:,}đ thấp hơn giá sàn {b['floor_price']:,}đ.\n"
                         f"Theo quy định cần đối tác đồng ý — đã gửi yêu cầu.")
        pid = db.consign_promo_create(f"Giá bán lô {b['code']}: {v:,}đ",
                                      "Admin đề xuất giá thấp hơn giá sàn, bạn có đồng ý?",
                                      b["consignor_id"])
        r = db.get_conn().execute("SELECT tg_id FROM consignors WHERE id=?", (b["consignor_id"],)).fetchone()
        if r:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Đồng ý", callback_data=f"kg:promo:{pid}:1"),
                 InlineKeyboardButton(text="❌ Từ chối", callback_data=f"kg:promo:{pid}:0")]])
            try:
                await msg.bot.send_message(r["tg_id"], f"💲 <b>Đề xuất giá lô {b['code']}</b>\n"
                                           f"Giá sàn: {b['floor_price']:,}đ → đề xuất: {v:,}đ\nBạn có đồng ý?",
                                           parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass
        # lưu giá đề xuất vào note để xử lý khi đối tác đồng ý — đơn giản: duyệt luôn khi đồng ý
        db.get_conn().execute("UPDATE consignment_promos SET detail=? WHERE id=?",
                              (f"PRICE:{bid}:{v}", pid))
        db.get_conn().commit()
        return
    # fake cb để tái dùng _approve_batch
    class _FakeCb:
        pass
    fc = _FakeCb()
    fc.message = msg
    fc.bot = msg.bot
    fc.from_user = msg.from_user
    await _approve_batch(fc, bid, v)


@router.callback_query(F.data.startswith("kga:bno:"))
async def _kga_bno(cb: CallbackQuery):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    bid = int(cb.data.split(":")[2])
    b = db.consign_batch_get(bid)
    db.consign_batch_decide(bid, False, 0, cb.from_user.id, "từ chối")
    await cb.message.answer(f"❌ Đã từ chối lô {b['code'] if b else bid}.")
    # báo đối tác
    if b:
        r = db.get_conn().execute("SELECT tg_id FROM consignors WHERE id=?", (b["consignor_id"],)).fetchone()
        if r:
            await C.notify(r["tg_id"],
                f"❌ <b>Lô {b['code']} đã bị từ chối.</b>\n"
                f"Liên hệ shop để biết lý do và gửi lại lô mới.",
                cb.bot)
    C.audit(cb.from_user.id, cb.from_user.full_name, "reject_batch", f"bid={bid}")


# ================= Duyệt trả hàng =================
@router.callback_query(F.data == "kga:returns")
async def _kga_returns(cb: CallbackQuery):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    rows = db.consign_batches_list(status="return_requested", limit=10)
    if not rows:
        await cb.message.answer("Không có lô nào xin trả hàng.")
        return
    for b in rows:
        c = db.consignor_get_by_id(b["consignor_id"])
        n = db.get_conn().execute("SELECT COUNT(*) v FROM consignment_items"
                                  " WHERE batch_id=? AND status='listed'", (b["id"],)).fetchone()["v"]
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Duyệt trả hàng", callback_data=f"kga:retok:{b['id']}"),
             InlineKeyboardButton(text="❌ Từ chối", callback_data=f"kga:retno:{b['id']}")]])
        await cb.message.answer(
            f"🔙 <b>Lô {b['code']}</b> xin trả hàng\n"
            f"👤 {html.escape(c['name'] if c else '')} — {n} acc chưa bán",
            parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("kga:retok:"))
async def _kga_retok(cb: CallbackQuery):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    bid = int(cb.data.split(":")[2])
    if db.consign_batch_do_return(bid, True, cb.from_user.id):
        b = db.consign_batch_get(bid)
        await cb.message.edit_text(f"✅ Đã duyệt trả hàng lô <b>{b['code']}</b>.\n"
                                    f"Acc chưa bán đã rời kệ.", parse_mode="HTML")
        try:
            r = db.get_conn().execute("SELECT tg_id FROM consignors WHERE id=?",
                                      (b["consignor_id"],)).fetchone()
            if r:
                await cb.bot.send_message(r["tg_id"],
                    f"🔙 <b>Yêu cầu trả hàng lô {b['code']} đã được duyệt.</b>",
                    parse_mode="HTML")
        except Exception:
            pass
    else:
        await cb.message.edit_text("❌ Không xử lý được.")


@router.callback_query(F.data.startswith("kga:retno:"))
async def _kga_retno(cb: CallbackQuery):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    bid = int(cb.data.split(":")[2])
    if db.consign_batch_do_return(bid, False, cb.from_user.id):
        b = db.consign_batch_get(bid)
        await cb.message.edit_text(f"❌ Đã từ chối trả hàng lô <b>{b['code']}</b>.", parse_mode="HTML")
        try:
            r = db.get_conn().execute("SELECT tg_id FROM consignors WHERE id=?",
                                      (b["consignor_id"],)).fetchone()
            if r:
                await cb.bot.send_message(r["tg_id"],
                    f"❌ <b>Yêu cầu trả hàng lô {b['code']} bị từ chối.</b>\n"
                    f"Acc tiếp tục được bán.",
                    parse_mode="HTML")
        except Exception:
            pass
    else:
        await cb.message.edit_text("❌ Không xử lý được.")


# ================= Sửa giá sau duyệt =================
@router.callback_query(F.data.startswith("kga:reprice:"))
async def _kga_reprice(cb: CallbackQuery, state: FSMContext):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    bid = int(cb.data.split(":")[2])
    b = db.consign_batch_get(bid)
    if not b or b["status"] not in ("approved", "listed"):
        await cb.message.answer("Lô không ở trạng thái đang bán.")
        return
    await state.update_data(_kga_bid=bid, _reprice_mode=1, _fee_mode=0)
    await state.set_state(ConsignAdminPrice.sell_price)
    await cb.message.answer(f"💲 Lô <b>{b['code']}</b> — giá hiện tại {b['sell_price']:,}đ.\n"
                            f"Nhập <b>giá bán mới</b> mỗi acc (VNĐ):", parse_mode="HTML")
    b = db.consign_batch_get(bid)
    await cb.message.answer(f"❌ Đã từ chối lô {b['code']}.")
    C.audit(cb.from_user.id, cb.from_user.full_name, "reject_batch", f"bid={bid}")


# ---- Rút tiền (chủ shop) ----
@router.callback_query(F.data == "kga:payouts")
async def _kga_payouts(cb: CallbackQuery):
    await _cb_answer(cb)
    rows = db.consign_payouts_list("pending", 10)
    if not rows:
        await _cb_answer(cb, "Không có yêu cầu rút.")
        return
    for p in rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👁 Chi tiết", callback_data=f"kga:payout:{p['id']}")]])
        await cb.message.answer(f"💸 <b>#{p['id']}</b> {html.escape(p['consignor_name'] or '')} — "
                                f"{p['amount']:,}đ → {p['channel']}", parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("kga:payout:"))
async def _kga_payout(cb: CallbackQuery, state: FSMContext):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    pid = int(cb.data.split(":")[2])
    p = db.get_conn().execute("SELECT p.*, c.name n FROM consignment_payouts p"
                              " LEFT JOIN consignors c ON c.id=p.consignor_id WHERE p.id=?",
                              (pid,)).fetchone()
    if not p or p["status"] != "pending":
        await _cb_answer(cb, "Đã xử lý rồi.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Đã chuyển tiền", callback_data=f"kga:payok:{pid}"),
         InlineKeyboardButton(text="❌ Từ chối", callback_data=f"kga:payno:{pid}")]])
    await cb.message.answer(
        f"💸 <b>Rút tiền #{pid}</b>\n👤 {html.escape(p['n'] or '')}\n"
        f"💰 {p['amount']:,}đ | Phí {p['fee']:,}đ → thực chuyển {p['net']:,}đ\n"
        f"📬 {p['channel']}: <code>{html.escape(p['account_info'])}</code>\n\n"
        f"Chuyển tiền xong bấm “Đã chuyển tiền” rồi nhập mã giao dịch.",
        parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("kga:payok:"))
async def _kga_payok(cb: CallbackQuery, state: FSMContext):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    pid = int(cb.data.split(":")[2])
    await state.update_data(_kga_pid=pid)
    await state.set_state(ConsignAdminPayout.paid_ref)
    await cb.message.answer("Nhập <b>mã giao dịch</b> chuyển tiền (gõ /boqua nếu không có):", parse_mode="HTML")


@router.message(ConsignAdminPayout.paid_ref)
async def _kga_paid_ref(msg: Message, state: FSMContext):
    d = await state.get_data()
    pid = d["_kga_pid"]
    ref = "" if msg.text.strip() == "/boqua" else msg.text.strip()[:100]
    await state.clear()
    p = db.get_conn().execute("SELECT * FROM consignment_payouts WHERE id=?", (pid,)).fetchone()
    if db.consign_payout_decide(pid, True, msg.from_user.id, ref):
        await msg.answer(f"✅ Đã duyệt rút #{pid} ({p['net']:,}đ).")
        r = db.get_conn().execute("SELECT tg_id FROM consignors WHERE id=?", (p["consignor_id"],)).fetchone()
        if r:
            await C.notify(r["tg_id"], f"💸 <b>Tiền rút #{pid} đã được chuyển!</b>\n{p['net']:,}đ → {p['channel']}"
                           + (f"\nMã GD: {ref}" if ref else ""), msg.bot)
        C.audit(msg.from_user.id, msg.from_user.full_name, "approve_payout", f"pid={pid}")
    else:
        await msg.answer("Yêu cầu đã được xử lý trước đó.")


@router.callback_query(F.data.startswith("kga:payno:"))
async def _kga_payno(cb: CallbackQuery):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    pid = int(cb.data.split(":")[2])
    if db.consign_payout_decide(pid, False, cb.from_user.id):
        await cb.message.answer(f"❌ Đã từ chối rút #{pid}, tiền trả về ví khả dụng.")
        C.audit(cb.from_user.id, cb.from_user.full_name, "reject_payout", f"pid={pid}")


# ---- Tranh chấp ----
@router.callback_query(F.data == "kga:disputes")
async def _kga_disputes(cb: CallbackQuery):
    await _cb_answer(cb)
    rows = db.consign_disputes_list("open", 10)
    if not rows:
        await _cb_answer(cb, "Không có tranh chấp mở.")
        return
    for d in rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👁 Xử lý", callback_data=f"kga:disp:{d['id']}")]])
        await cb.message.answer(f"⚠️ <b>Tranh chấp #{d['id']}</b>\n{html.escape(d['reason'][:120])}",
                                parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("kga:disp:"))
async def _kga_disp(cb: CallbackQuery):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    did = int(cb.data.split(":")[2])
    d = db.get_conn().execute("SELECT d.*, o.sell_price, o.net_amount FROM consignment_disputes d"
                              " LEFT JOIN consignment_orders o ON o.id=d.order_id WHERE d.id=?",
                              (did,)).fetchone()
    if not d or d["status"] != "open":
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Hoàn tiền khách", callback_data=f"kga:dref:{did}"),
         InlineKeyboardButton(text="🔄 Đổi acc", callback_data=f"kga:drep:{did}")],
        [InlineKeyboardButton(text="❌ Từ chối KN", callback_data=f"kga:dno:{did}")]])
    # Phản hồi của đối tác (nếu có)
    resp = ""
    if d["partner_responded"]:
        resp = f"\n📝 <b>Đối tác phản hồi:</b> {html.escape(d['partner_response'] or '')}"
    try:
        if d["photo_file_id"]:
            await cb.message.answer_photo(d["photo_file_id"],
                                          caption=f"⚠️ <b>Tranh chấp #{did}</b>\nLý do: {html.escape(d['reason'])}\n"
                                          f"Thực nhận đối tác: {d['net_amount']:,}đ{resp}",
                                          parse_mode="HTML", reply_markup=kb)
        else:
            await cb.message.answer(f"⚠️ <b>Tranh chấp #{did}</b> (chưa có ảnh!)\nLý do: {html.escape(d['reason'])}{resp}",
                                    parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer("Không tải được ảnh.", reply_markup=kb)


@router.callback_query(F.data.startswith("kga:dref:"))
async def _kga_dref(cb: CallbackQuery):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    did = int(cb.data.split(":")[2])
    d = db.get_conn().execute("SELECT * FROM consignment_disputes WHERE id=?", (did,)).fetchone()
    o = db.get_conn().execute("SELECT sell_price FROM consignment_orders WHERE id=?", (d["order_id"],)).fetchone()
    amt = o["sell_price"] if o and o["sell_price"] else 0
    if db.consign_dispute_decide(did, "refund_buyer", amt, cb.from_user.id):
        await cb.message.answer(f"✅ Đã hoàn đủ <b>{amt:,}đ</b> cho khách, khấu trừ từ ví đối tác (chịu 100%).",
                                parse_mode="HTML")
        C.audit(cb.from_user.id, cb.from_user.full_name, "dispute_refund", f"did={did} amt={amt}")
        await _notify_dispute_done(cb, did, f"❌ Tranh chấp #{did}: shop đã <b>hoàn đủ tiền cho khách</b>.\n"
                                            f"Bạn bị khấu trừ <b>{amt:,}đ</b> (chịu 100%).")
    else:
        await cb.message.answer("Đã xử lý trước đó.")


async def _notify_dispute_done(cb, did: int, text: str):
    """Báo đối tác khi tranh chấp được xử lý xong."""
    try:
        r = db.get_conn().execute(
            "SELECT cr.tg_id FROM consignment_disputes d"
            " JOIN consignment_orders o ON o.id=d.order_id"
            " JOIN consignors cr ON cr.id=o.consignor_id"
            " WHERE d.id=?", (did,)).fetchone()
        if r and r["tg_id"]:
            await cb.bot.send_message(int(r["tg_id"]), text, parse_mode="HTML")
    except Exception:
        pass


@router.callback_query(F.data.startswith("kga:drep:"))
async def _kga_drep(cb: CallbackQuery):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    did = int(cb.data.split(":")[2])
    if db.consign_dispute_decide(did, "replace", 0, cb.from_user.id):
        await cb.message.answer("✅ Ghi nhận đổi acc — bạn tự giao acc thay cho khách, đối tác không bị trừ tiền.")
        C.audit(cb.from_user.id, cb.from_user.full_name, "dispute_replace", f"did={did}")
        await _notify_dispute_done(cb, did, f"✅ Tranh chấp #{did}: shop đã <b>đổi acc khác</b> cho khách.\n"
                                            f"Bạn không bị trừ tiền.")


@router.callback_query(F.data.startswith("kga:dno:"))
async def _kga_dno(cb: CallbackQuery):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    did = int(cb.data.split(":")[2])
    if db.consign_dispute_decide(did, "reject", 0, cb.from_user.id):
        await cb.message.answer("❌ Đã từ chối khiếu nại, mở giữ tiền đối tác.")
        C.audit(cb.from_user.id, cb.from_user.full_name, "dispute_reject", f"did={did}")
        await _notify_dispute_done(cb, did, f"✅ Tranh chấp #{did}: shop đã <b>từ chối khiếu nại</b>.\n"
                                            f"Tiền của bạn được mở giữ.")


# ---- Phí theo loại ----
@router.callback_query(F.data == "kga:fees")
async def _kga_fees(cb: CallbackQuery):
    await _cb_answer(cb)
    cats = [dict(r) for r in db.get_conn().execute(
        "SELECT id, name, stall FROM acc_categories ORDER BY stall, name LIMIT 20").fetchall()]
    rows = []
    for x in cats:
        f = db.consign_fee_get(x["id"])
        rows.append([InlineKeyboardButton(
            text=f"{x['name'][:20]}: {f['fee_fixed']:,}đ+{f['fee_pct']}%",
            callback_data=f"kga:fee:{x['id']}")])
    await cb.message.answer("💲 <b>Phí ký gửi theo loại</b> — bấm để sửa:", parse_mode="HTML",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("kga:fee:"))
async def _kga_fee(cb: CallbackQuery, state: FSMContext):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    cid = int(cb.data.split(":")[2])
    await state.update_data(_fee_cat=cid)
    await state.set_state(ConsignAdminPrice.sell_price)  # tái dùng state
    await state.update_data(_fee_mode=1)
    f = db.consign_fee_get(cid)
    await cb.message.answer(f"Nhập phí mới cho loại này, dạng <code>cố_định %</code> (vd <code>5000 10</code>).\n"
                            f"Hiện tại: {f['fee_fixed']:,}đ + {f['fee_pct']}%", parse_mode="HTML")


# ---- Cài đặt ----
class ConsignSetting(StatesGroup):
    key = State()
    value = State()

_SETTING_DEFS = [
    ("max_items", "consign_default_max_items", "Hạn mức acc mặc định"),
    ("max_value", "consign_default_max_value", "Hạn mức giá trị mặc định (đ)"),
    ("min_withdraw", "consign_min_withdraw", "Rút tối thiểu (đ)"),
    ("withdraw_fee", "consign_withdraw_fee", "Phí rút (đ)"),
    ("withdraw_schedule", "consign_withdraw_schedule", "Lịch xử lý rút"),
]

@router.callback_query(F.data == "kga:settings")
async def _kga_settings(cb: CallbackQuery):
    await _cb_answer(cb)
    g = db.get_setting
    rows = []
    for short, key, label in _SETTING_DEFS:
        v = g(key, "") or "chưa cấu hình"
        rows.append([InlineKeyboardButton(
            text=f"{label}: {v}", callback_data=f"kga:set:{short}")])
    rows.append([InlineKeyboardButton(text="🔙 Quay lại", callback_data="kga:menu")])
    await cb.message.answer("⚙️ <b>Cài đặt ký gửi</b> — bấm để đổi:", parse_mode="HTML",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("kga:set:"))
async def _kga_set_pick(cb: CallbackQuery, state: FSMContext):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    short = cb.data.split(":")[2]
    d = next((x for x in _SETTING_DEFS if x[0] == short), None)
    if not d:
        return
    await state.update_data(_set_key=d[1], _set_label=d[2])
    await state.set_state(ConsignSetting.value)
    await cb.message.answer(f"Nhập giá trị mới cho <b>{d[2]}</b> (hiện tại: {db.get_setting(d[1], '') or 'chưa cấu hình'}).\nGõ /huy để hủy.",
                            parse_mode="HTML")


@router.message(ConsignSetting.value)
async def _kga_set_value(msg: Message, state: FSMContext):
    if (msg.text or "").strip().lower() == "/huy":
        await state.clear()
        await msg.answer("Đã hủy.")
        return
    if not _is_super(msg.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    key, label = data.get("_set_key"), data.get("_set_label")
    val = (msg.text or "").strip()
    if key != "consign_withdraw_schedule" and not val.replace(".", "").replace(",", "").isdigit():
        await msg.answer("Vui lòng nhập số. Gõ /huy để hủy.")
        return
    db.set_setting(key, val.replace(".", "").replace(",", "") if key != "consign_withdraw_schedule" else val)
    await state.clear()
    C.audit(msg.from_user.id, msg.from_user.full_name, "setting", f"{key}={val}")
    await msg.answer(f"✅ Đã lưu {label} = {val}")


# hook: khi đối tác đồng ý giá thấp hơn sàn (promo PRICE:bid:price),
# chuyển lô về submitted để admin duyệt nốt với giá đã thỏa thuận
_orig_promo_decide = db.consign_promo_decide


def _promo_decide_hook(pid: int, accept: bool) -> bool:
    ok = _orig_promo_decide(pid, accept)
    if ok and accept:
        r = db.get_conn().execute("SELECT detail FROM consignment_promos WHERE id=?", (pid,)).fetchone()
        if r and (r["detail"] or "").startswith("PRICE:"):
            _, bid, price = r["detail"].split(":")
            b = db.consign_batch_get(int(bid))
            if b and b["status"] in ("submitted", "draft"):
                db.get_conn().execute(
                    "UPDATE consignment_batches SET status='submitted', sell_price=?, decide_note=? WHERE id=?",
                    (int(price), "đối tác đã đồng ý giá", int(bid)))
                db.get_conn().commit()
    return ok


db.consign_promo_decide = _promo_decide_hook
