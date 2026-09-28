"""Bot ký gửi acc: đối tác (/kygui) + chủ shop duyệt (/kyguiadm).

Quyết định đã chốt: đăng ký công khai, mọi gian hàng, phí kết hợp theo loại,
admin quyết giá bán, giữ tiền = thời gian BH, KM cần đối tác đồng ý,
hạn mức số acc + tổng giá trị, tranh chấp bắt buộc ảnh, rút tiền đủ 4 yếu tố,
quyền nhạy cảm chỉ chủ shop.
"""
import html
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
    confirm = State()


class ConsignBatch(StatesGroup):
    floor_price = State()
    warranty = State()
    items = State()
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
            [InlineKeyboardButton(text="💰 Đơn đã bán", callback_data="kg:orders")],
            [InlineKeyboardButton(text="👛 Ví ký gửi", callback_data="kg:wallet"),
             InlineKeyboardButton(text="💸 Rút tiền", callback_data="kg:withdraw")],
            [InlineKeyboardButton(text="🎁 Khuyến mãi chờ duyệt", callback_data="kg:promos")],
        ])
    else:
        rows.append([InlineKeyboardButton(text="🚫 Tài khoản bị khóa", callback_data="kg:noop")])
    rows.append([InlineKeyboardButton(text="❓ Quy định ký gửi", callback_data="kg:rules")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text.startswith("/kygui"))
async def on_kygui(msg: Message, state: FSMContext):
    if not C.enabled():
        await msg.answer("Tính năng ký gửi hiện đang tắt.")
        return
    # /kyguiadm -> chuyển sang admin
    if msg.text.strip().startswith("/kyguiadm"):
        await on_kyguiadm(msg, state)
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
    cid = db.consignor_create(msg.from_user.id, d["name"], d["phone"], note)
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
                f"📞 {html.escape(d['phone'])}\n📦 {html.escape(note or '—')}",
                parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass
    C.audit(msg.from_user.id, d["name"], "register", f"cid={cid}")


# ================= Tạo lô =================
def _parse_items(text: str) -> tuple:
    """Mỗi dòng: uid|mk|mail thay|2fa|ghi chú. Trả (items, lỗi)."""
    items, errs = [], []
    for i, line in enumerate(text.strip().split("\n"), 1):
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2 or not parts[0]:
            errs.append(f"Dòng {i}: thiếu uid|mk")
            continue
        items.append({"uid": parts[0], "password": parts[1],
                      "backup_mail": parts[2] if len(parts) > 2 else "",
                      "totp": parts[3] if len(parts) > 3 else "",
                      "note": parts[4] if len(parts) > 4 else ""})
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
    rows.append([InlineKeyboardButton(text="◀️ Quay lại", callback_data="kg:menu")])
    await state.update_data(_stalls=stalls)
    await cb.message.edit_text("<b>📦 Tạo lô mới — Bước 1/5</b>\nChọn gian hàng:",
                               parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


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
    rows = [[InlineKeyboardButton(text=x["name"], callback_data=f"kg:cat:{x['id']}")
             for x in cats[i:i + 2]] for i in range(0, len(cats), 2)]
    rows.append([InlineKeyboardButton(text="◀️ Quay lại", callback_data="kg:menu")])
    await cb.message.edit_text(f"<b>Bước 2/5</b>\n🏪 {stall}\nChọn loại acc:", parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


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
    await state.set_state(ConsignBatch.items)
    await msg.answer("<b>Bước 5/5</b>\nDán danh sách acc, mỗi dòng:\n"
                     "<code>uid|mật khẩu|mail thay|2fa|ghi chú</code>\n"
                     "Tối thiểu uid|mật khẩu.", parse_mode="HTML")


@router.message(ConsignBatch.items)
async def _kg_items(msg: Message, state: FSMContext):
    text = msg.text or ""
    if msg.document:
        await msg.answer("Gửi file chưa hỗ trợ ở bản này, bạn dán nội dung giúp mik nhé.")
        return
    items, errs = _parse_items(text)
    if not items:
        await msg.answer("Không đọc được acc nào. Kiểm tra lại định dạng.")
        return
    d = await state.get_data()
    c = db.consignor_get(msg.from_user.id)
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
    items = [x for x in items if x["uid"] not in dup]
    if not items:
        await msg.answer("Tất cả UID đều trùng kho hiện tại.")
        return
    bid, code = db.consign_batch_create(c["id"], d["stall"], d["category_id"],
                                        d["floor_price"], d["warranty_days"])
    for it in items:
        db.consign_item_add(bid, c["id"], it)
    sug = C.suggest_price(d["category_id"], d["floor_price"])
    await state.update_data(batch_id=bid, code=code, n_items=len(items))
    await state.set_state(ConsignBatch.confirm)
    warn = f"\n⚠️ Trùng kho đã loại: {len(dup)}" if dup else ""
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
    txt = "📋 <b>Lô của bạn</b>\n\n" + "\n\n".join(C.batch_summary_text(b) for b in rows)
    await cb.message.edit_text(txt, parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                   [InlineKeyboardButton(text="◀️ Quay lại", callback_data="kg:menu")]]))


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
                                   [InlineKeyboardButton(text="💸 Rút tiền", callback_data="kg:withdraw")],
                                   [InlineKeyboardButton(text="◀️ Quay lại", callback_data="kg:menu")]]))


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
        [InlineKeyboardButton(text="📦 Lô chờ duyệt", callback_data="kga:batches")],
        [InlineKeyboardButton(text="💸 Rút tiền chờ duyệt", callback_data="kga:payouts")],
        [InlineKeyboardButton(text="⚠️ Tranh chấp mở", callback_data="kga:disputes")],
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


@router.callback_query(F.data.startswith("kga:batch:"))
async def _kga_batch(cb: CallbackQuery, state: FSMContext):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    bid = int(cb.data.split(":")[2])
    b = db.consign_batch_get(bid)
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
    await cb.message.answer(
        f"📦 <b>Lô {b['code']}</b>\n{preview}\n\n"
        f"💰 Giá sàn: {b['floor_price']:,}đ | Phí: {fee['fee_fixed']:,}đ + {fee['fee_pct']}%\n"
        f"💡 Gợi ý: {sug:,}đ | 🛡️ BH {b['warranty_days']} ngày",
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
    await cb.message.answer(f"✅ Đã duyệt lô <b>{b['code']}</b>: {n} acc lên kệ giá {sell_price:,}đ.",
                            parse_mode="HTML")
    # báo đối tác
    r = db.get_conn().execute("SELECT tg_id FROM consignors WHERE id=?", (b["consignor_id"],)).fetchone()
    if r:
        await C.notify(r["tg_id"], f"📦 <b>Lô {b['code']} đã được duyệt!</b>\n{n} acc đã lên kệ, giá bán {sell_price:,}đ/acc.", cb.bot)
    C.audit(cb.from_user.id, cb.from_user.full_name, "approve_batch", f"bid={bid} price={sell_price} n={n}")


@router.callback_query(F.data.startswith("kga:bprice:"))
async def _kga_bprice(cb: CallbackQuery, state: FSMContext):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    bid = int(cb.data.split(":")[2])
    await state.update_data(_kga_bid=bid)
    await state.set_state(ConsignAdminPrice.sell_price)
    await cb.message.answer("Nhập <b>giá bán</b> mỗi acc (VNĐ):", parse_mode="HTML")


@router.message(ConsignAdminPrice.sell_price)
async def _kga_price_input(msg: Message, state: FSMContext):
    d = await state.get_data()
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
        db.consign_fee_set(d["_fee_cat"], fixed, pct, msg.from_user.id)
        await state.clear()
        await msg.answer(f"✅ Đã lưu phí: {fixed:,}đ + {pct}%")
        C.audit(msg.from_user.id, msg.from_user.full_name, "fee_set",
                f"cat={d['_fee_cat']} fixed={fixed} pct={pct}")
        return
    try:
        v = int(msg.text.strip().replace(",", "").replace(".", "").replace("k", "000"))
        assert v > 0
    except Exception:
        await msg.answer("Nhập số tiền hợp lệ.")
        return
    bid = d["_kga_bid"]
    b = db.consign_batch_get(bid)
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
    db.consign_batch_decide(bid, False, 0, cb.from_user.id, "từ chối")
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
    try:
        if d["photo_file_id"]:
            await cb.message.answer_photo(d["photo_file_id"],
                                          caption=f"⚠️ <b>Tranh chấp #{did}</b>\nLý do: {html.escape(d['reason'])}\n"
                                          f"Thực nhận đối tác: {d['net_amount']:,}đ",
                                          parse_mode="HTML", reply_markup=kb)
        else:
            await cb.message.answer(f"⚠️ <b>Tranh chấp #{did}</b> (chưa có ảnh!)\nLý do: {html.escape(d['reason'])}",
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
    o = db.get_conn().execute("SELECT net_amount FROM consignment_orders WHERE id=?", (d["order_id"],)).fetchone()
    if db.consign_dispute_decide(did, "refund_buyer", o["net_amount"] if o else 0, cb.from_user.id):
        await cb.message.answer(f"✅ Đã hoàn tiền khách, khấu trừ {o['net_amount']:,}đ từ ví đối tác.")
        C.audit(cb.from_user.id, cb.from_user.full_name, "dispute_refund", f"did={did}")
    else:
        await cb.message.answer("Đã xử lý trước đó.")


@router.callback_query(F.data.startswith("kga:drep:"))
async def _kga_drep(cb: CallbackQuery):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    did = int(cb.data.split(":")[2])
    if db.consign_dispute_decide(did, "replace", 0, cb.from_user.id):
        await cb.message.answer("✅ Ghi nhận đổi acc — bạn tự giao acc thay cho khách, đối tác không bị trừ tiền.")
        C.audit(cb.from_user.id, cb.from_user.full_name, "dispute_replace", f"did={did}")


@router.callback_query(F.data.startswith("kga:dno:"))
async def _kga_dno(cb: CallbackQuery):
    await _cb_answer(cb)
    if not _is_super(cb.from_user.id):
        return
    did = int(cb.data.split(":")[2])
    if db.consign_dispute_decide(did, "reject", 0, cb.from_user.id):
        await cb.message.answer("❌ Đã từ chối khiếu nại, mở giữ tiền đối tác.")
        C.audit(cb.from_user.id, cb.from_user.full_name, "dispute_reject", f"did={did}")


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
