"""💰 Ứng tiền mua acc — khách xin ứng, admin duyệt tay.

Giai đoạn 1: DB + xin ứng/duyệt/từ chối + xem nợ + đánh dấu đã nhận tiền.
Luồng: khách thiếu tiền mua acc → xin ứng → bot riêng báo admin → duyệt/từ chối.
"""
import html
import logging
import time

from aiogram import F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from .. import db, perms as _perms
from ..util import vnd
from .common import _notify_admin_smart
from .core import router

log = logging.getLogger("loan")


class LoanState(StatesGroup):
    amount = State()        # chờ nhập số tiền xin ứng
    reject_reason = State()  # chờ nhập lý do từ chối
    repay_amount = State()   # chờ nhập số tiền đã nhận (tay)
    repay_note = State()     # chờ nhập ghi chú trả tay
    manual_tgid = State()    # tạo tay: chờ nhập ID khách
    manual_amount = State()  # tạo tay: chờ nhập số tiền
    manual_note = State()    # tạo tay: chờ nhập ghi chú
    adjust_dir = State()     # điều chỉnh: chờ chọn tăng/giảm
    adjust_amount = State()  # điều chỉnh: chờ nhập số tiền
    adjust_reason = State()  # điều chỉnh: chờ nhập lý do
    cfg_value = State()      # cài đặt: chờ nhập giá trị mới


# ---------- helpers ----------

def _loan_status_label(s: str) -> str:
    return {
        "pending": "⏳ Chờ duyệt",
        "active": "📋 Đang nợ",
        "overdue": "🔴 Quá hạn",
        "paid": "✅ Đã trả xong",
        "rejected": "❌ Bị từ chối",
    }.get(s, s)


def _fmt_date(ts: int) -> str:
    if not ts:
        return "—"
    return time.strftime("%d/%m/%Y", time.localtime(ts))


def _can_request(tg_id: int) -> tuple[bool, str]:
    """Kiểm tra điều kiện xin ứng. Trả về (ok, lý do nếu không)."""
    if not db.loan_enabled():
        return False, "Tính năng ứng tiền hiện đang tắt."
    min_acc = db.loan_setting_int("loan_min_acc")
    bought = db.loan_accs_bought(tg_id)
    if bought < min_acc:
        return False, f"Bạn cần mua ít nhất {min_acc} acc mới được ứng tiền (bạn đã mua {bought} acc)."
    if db.loan_has_pending(tg_id):
        return False, "Bạn đang có đơn xin ứng chờ duyệt, vui lòng chờ admin xử lý."
    debt = db.loan_total_debt(tg_id)
    max_total = db.loan_max_total_for(tg_id)
    if debt >= max_total:
        return False, f"Bạn đang nợ {vnd(debt)}đ (tối đa {vnd(max_total)}đ). Trả bớt nợ rồi xin ứng tiếp nhé."
    return True, ""


# ---------- KHÁCH: xin ứng ----------

@router.callback_query(F.data == "loan:req")
async def on_loan_req(cb: CallbackQuery, state: FSMContext):
    tg_id = cb.from_user.id
    ok, reason = _can_request(tg_id)
    if not ok:
        await cb.answer(reason, show_alert=True)
        return
    max_per = db.loan_setting_int("loan_max_per_request")
    debt = db.loan_total_debt(tg_id)
    max_total = db.loan_max_total_for(tg_id)
    can = min(max_per, max_total - debt)
    await state.set_state(LoanState.amount)
    await state.update_data(loan_max_can=can)
    await cb.message.answer(
        f"📝 <b>XIN ỨNG TIỀN MUA ACC</b>\n"
        f"Bạn đang nợ: <b>{vnd(debt)}đ</b>\n"
        f"Bạn có thể ứng tối đa: <b>{vnd(can)}đ</b>\n\n"
        f"Nhập số tiền muốn ứng (vd: <code>30000</code>):",
        parse_mode="HTML")
    await cb.answer()


@router.message(StateFilter(LoanState.amount))
async def on_loan_amount(msg: Message, state: FSMContext):
    tg_id = msg.from_user.id
    try:
        amount = int(msg.text.replace(".", "").replace(",", "").replace("đ", "").strip())
    except (ValueError, AttributeError):
        await msg.answer("Số tiền không hợp lệ. Nhập số thôi nhé (vd: 30000).")
        return
    data = await state.get_data()
    can = data.get("loan_max_can", 0)
    if amount <= 0:
        await msg.answer("Số tiền phải lớn hơn 0.")
        return
    if amount > can:
        await msg.answer(f"Bạn chỉ được ứng tối đa {vnd(can)}đ. Nhập lại nhé.")
        return
    ok, reason = _can_request(tg_id)
    if not ok:
        await state.clear()
        await msg.answer(f"❌ {reason}")
        return
    lid = db.loan_create(tg_id, amount, created_by=tg_id)
    await state.clear()
    await msg.answer(
        f"✅ <b>Đã gửi đơn xin ứng {vnd(amount)}đ</b>\n"
        f"Chờ admin duyệt nhé, có kết quả mik báo ngay.",
        parse_mode="HTML")
    # Báo admin qua bot riêng (1 lần duy nhất, kèm nút Duyệt/Từ chối)
    try:
        user = db.get_user(tg_id) or {}
        name = user.get("full_name", "") or user.get("username", "") or str(tg_id)
        bought = db.loan_accs_bought(tg_id)
        debt = db.loan_total_debt(tg_id)
        text = (
            f"📝 <b>ĐƠN XIN ỨNG TIỀN #{lid}</b>\n"
            f"👤 Khách: {html.escape(str(name))} (<code>{tg_id}</code>)\n"
            f"🛒 Đã mua: <b>{bought}</b> acc\n"
            f"💰 Đang nợ: <b>{vnd(debt)}đ</b>\n"
            f"💵 Xin ứng: <b>{vnd(amount)}đ</b>\n"
            f"⏰ {time.strftime('%H:%M %d/%m', time.localtime())}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Duyệt", callback_data=f"loan:ap:{lid}"),
            InlineKeyboardButton(text="❌ Từ chối", callback_data=f"loan:rj:{lid}"),
        ]])
        # Ưu tiên gửi qua notify bot kèm nút; nếu không được mới fallback text thường
        sent = False
        try:
            from . import notify_bot as _nb
            if _nb.manager.running and _nb.manager.bot:
                for pid in _nb.privileged_ids():
                    try:
                        await _nb.manager.bot.send_message(
                            pid, text, parse_mode="HTML", reply_markup=kb)
                        sent = True
                    except Exception:
                        pass
        except Exception:
            pass
        if not sent:
            await _notify_admin_smart(msg.bot, text, perm=None)
    except Exception as e:
        log.warning("loan notify failed: %s", e)


# ---------- KHÁCH: xem nợ ----------

async def _congno_text(tg_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
    loans = db.loan_list_by_customer(tg_id, 10)
    if not loans:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📝 Xin ứng tiền", callback_data="loan:req")],
        ])
        return ("Bạn hiện không có khoản ứng nào. Mua acc thiếu tiền thì bấm "
                "\"📝 Xin ứng tiền\" nhé.", kb)
    debt = db.loan_total_debt(tg_id)
    lines = [f"💰 <b>NỢ CỦA BẠN</b> — còn nợ: <b>{vnd(debt)}đ</b>", "━━━━━━━━━━━━━━", ""]
    for ln in loans:
        rest = ln["amount"] - ln["paid_amount"]
        lines.append(
            f"#{ln['id']} — {_loan_status_label(ln['status'])}\n"
            f"Ứng {vnd(ln['amount'])}đ | Đã trả {vnd(ln['paid_amount'])}đ | Còn {vnd(rest)}đ\n"
            f"Hạn trả: {_fmt_date(ln['due_date'])}")
        lines.append("")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Xin ứng thêm", callback_data="loan:req")],
    ])
    return "\n".join(lines), kb


@router.message(Command("congno"))
async def on_congno(msg: Message):
    text, kb = _congno_text(msg.from_user.id)
    await msg.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "loan:my")
async def on_loan_my(cb: CallbackQuery):
    text, kb = _congno_text(cb.from_user.id)
    await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cb.answer()


# ---------- ADMIN: duyệt / từ chối ----------

def _is_loan_admin(tg_id: int) -> bool:
    return _perms.is_admin(tg_id)


@router.callback_query(F.data.startswith("loan:ap:"))
async def on_loan_approve(cb: CallbackQuery):
    admin_id = cb.from_user.id
    if not _is_loan_admin(admin_id):
        await cb.answer("Bạn không có quyền.", show_alert=True)
        return
    try:
        lid = int(cb.data.split(":")[2])
    except (ValueError, IndexError):
        await cb.answer("Dữ liệu không hợp lệ", show_alert=True)
        return
    loan = db.loan_approve(lid, admin_id)
    if not loan:
        await cb.answer("Đơn đã được xử lý rồi.", show_alert=True)
        return
    # Cộng tiền vào ví shop khách (bắt buộc thành công, nếu không báo lỗi)
    try:
        ok_credit = db.adjust_shop_balance(loan["tg_id"], loan["amount"], f"ung_tien:loan#{lid}")
    except Exception as e:
        ok_credit = False
        log.warning("adjust_shop_balance failed: %s", e)
    if not ok_credit:
        await cb.answer("⚠️ Duyệt xong nhưng cộng tiền thất bại, kiểm tra tay!", show_alert=True)
        log.error("loan #%s approved but credit failed for %s", lid, loan["tg_id"])
    try:
        db.admin_audit_log(admin_id, "loan_approve", f"duyệt ứng #{lid} {vnd(loan['amount'])}đ cho {loan['tg_id']}")
    except Exception:
        pass
    await cb.answer("Đã duyệt!")
    try:
        await cb.message.edit_text(
            cb.message.html_text + f"\n\n✅ <b>Đã duyệt</b> bởi {html.escape(cb.from_user.full_name)}",
            parse_mode="HTML")
    except Exception:
        pass
    # Báo khách
    try:
        await cb.bot.send_message(
            loan["tg_id"],
            f"✅ <b>Admin đã duyệt ứng {vnd(loan['amount'])}đ</b>\n"
            f"Tiền đã vào ví shop. Hạn trả: <b>{_fmt_date(loan['due_date'])}</b>\n"
            f"Mua acc tiếp nhé!",
            parse_mode="HTML")
    except Exception:
        pass


@router.callback_query(F.data.startswith("loan:rj:"))
async def on_loan_reject(cb: CallbackQuery, state: FSMContext):
    admin_id = cb.from_user.id
    if not _is_loan_admin(admin_id):
        await cb.answer("Bạn không có quyền.", show_alert=True)
        return
    try:
        lid = int(cb.data.split(":")[2])
    except (ValueError, IndexError):
        await cb.answer("Dữ liệu không hợp lệ", show_alert=True)
        return
    loan = db.loan_get(lid)
    if not loan or loan["status"] != "pending":
        await cb.answer("Đơn đã được xử lý rồi.", show_alert=True)
        return
    await state.set_state(LoanState.reject_reason)
    await state.update_data(reject_lid=lid)
    await cb.message.answer(f"Nhập lý do từ chối đơn #{lid} (vd: chưa đủ 5 acc, đang nợ quá hạn):")
    await cb.answer()


@router.message(StateFilter(LoanState.reject_reason))
async def on_loan_reject_reason(msg: Message, state: FSMContext):
    admin_id = msg.from_user.id
    data = await state.get_data()
    lid = data.get("reject_lid")
    reason = (msg.text or "").strip()[:200]
    if not lid:
        await state.clear()
        return
    if not reason:
        await msg.answer("Lý do không được để trống. Nhập lý do từ chối:")
        return
    await state.clear()
    if db.loan_reject(lid, admin_id, reason):
        try:
            db.admin_audit_log(admin_id, "loan_reject", f"từ chối ứng #{lid}: {reason}")
        except Exception:
            pass
        loan = db.loan_get(lid)
        await msg.answer(f"Đã từ chối đơn #{lid}.")
        if loan:
            try:
                await msg.bot.send_message(
                    loan["tg_id"],
                    f"❌ <b>Đơn xin ứng {vnd(loan['amount'])}đ chưa được duyệt.</b>\n"
                    f"Lý do: {html.escape(reason)}",
                    parse_mode="HTML")
            except Exception:
                pass
    else:
        await msg.answer("Đơn đã được xử lý rồi.")


# ---------- ADMIN: menu công nợ ----------

def _loanadm_kb(is_super: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="⏳ Chờ duyệt", callback_data="loanadm:pending")],
        [InlineKeyboardButton(text="📋 Đang nợ", callback_data="loanadm:active")],
        [InlineKeyboardButton(text="✅ Đã trả xong", callback_data="loanadm:done")],
        [InlineKeyboardButton(text="➕ Tạo khoản ứng tay", callback_data="loanadm:manual")],
    ]
    if is_super:
        rows.append([InlineKeyboardButton(text="⚙️ Cài đặt", callback_data="loanadm:cfg")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("loanadm"))
async def on_loanadm(msg: Message):
    if not _is_loan_admin(msg.from_user.id):
        await msg.answer("Bạn không có quyền.")
        return
    is_super = _perms.is_super(msg.from_user.id)
    st = db.loan_stats()
    await msg.answer(
        f"💰 <b>CÔNG NỢ ỨNG TIỀN</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"⏳ Chờ duyệt: <b>{st['pending_count']}</b>\n"
        f"📋 Đang nợ: <b>{st['active_count']}</b> khoản — <b>{vnd(st['active_debt'])}đ</b>\n"
        f"🔴 Quá hạn: <b>{st['overdue_count']}</b> khoản — <b>{vnd(st['overdue_debt'])}đ</b>",
        parse_mode="HTML", reply_markup=_loanadm_kb(is_super))


@router.callback_query(F.data == "loanadm:menu")
async def on_loanadm_menu(cb: CallbackQuery):
    if not _is_loan_admin(cb.from_user.id):
        await cb.answer("Bạn không có quyền.", show_alert=True)
        return
    is_super = _perms.is_super(cb.from_user.id)
    st = db.loan_stats()
    await cb.message.edit_text(
        f"💰 <b>CÔNG NỢ ỨNG TIỀN</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"⏳ Chờ duyệt: <b>{st['pending_count']}</b>\n"
        f"📋 Đang nợ: <b>{st['active_count']}</b> khoản — <b>{vnd(st['active_debt'])}đ</b>\n"
        f"🔴 Quá hạn: <b>{st['overdue_count']}</b> khoản — <b>{vnd(st['overdue_debt'])}đ</b>",
        parse_mode="HTML", reply_markup=_loanadm_kb(is_super))
    await cb.answer()


def _loan_customer_label(tg_id: int) -> str:
    """Tên hiển thị của khách nợ (tên + username, fallback về ID)."""
    try:
        u = db.get_user(tg_id) or {}
        name = (u.get("name") or "").strip()
        username = (u.get("username") or "").strip()
        if name and username:
            return f"{name} (@{username})"
        if name:
            return name
        if username:
            return f"@{username}"
    except Exception:
        pass
    return str(tg_id)


@router.callback_query(F.data == "loanadm:pending")
async def on_loanadm_pending(cb: CallbackQuery):
    if not _is_loan_admin(cb.from_user.id):
        await cb.answer("Bạn không có quyền.", show_alert=True)
        return
    loans = db.loan_list_pending()
    if not loans:
        await cb.answer("Không có đơn chờ duyệt.", show_alert=True)
        return
    rows = []
    for ln in loans[:15]:
        rows.append([InlineKeyboardButton(
            text=f"#{ln['id']} — {vnd(ln['amount'])}đ — {_loan_customer_label(ln['tg_id'])}",
            callback_data=f"loanadm:view:{ln['id']}")])
    rows.append([InlineKeyboardButton(text="◀️ Quay lại", callback_data="loanadm:menu")])
    await cb.message.edit_text("⏳ <b>ĐƠN CHỜ DUYỆT</b>\nBấm vào từng đơn để duyệt/từ chối:",
                               parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()


@router.callback_query(F.data == "loanadm:active")
async def on_loanadm_active(cb: CallbackQuery):
    if not _is_loan_admin(cb.from_user.id):
        await cb.answer("Bạn không có quyền.", show_alert=True)
        return
    loans = db.loan_list_active()
    if not loans:
        await cb.answer("Không có khoản đang nợ.", show_alert=True)
        return
    rows = []
    for ln in loans[:15]:
        rest = ln["amount"] - ln["paid_amount"]
        flag = "🔴" if ln["status"] == "overdue" else ""
        rows.append([InlineKeyboardButton(
            text=f"{flag}#{ln['id']} — còn {vnd(rest)}đ — {_loan_customer_label(ln['tg_id'])}",
            callback_data=f"loanadm:view:{ln['id']}")])
    rows.append([InlineKeyboardButton(text="◀️ Quay lại", callback_data="loanadm:menu")])
    await cb.message.edit_text("📋 <b>KHOẢN ĐANG NỢ</b> (🔴 = quá hạn):",
                               parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()


@router.callback_query(F.data == "loanadm:done")
async def on_loanadm_done(cb: CallbackQuery):
    if not _is_loan_admin(cb.from_user.id):
        await cb.answer("Bạn không có quyền.", show_alert=True)
        return
    loans = db.loan_list_done(15)
    if not loans:
        await cb.answer("Chưa có khoản nào hoàn tất.", show_alert=True)
        return
    lines = ["✅ <b>ĐÃ TRẢ XONG / TỪ CHỐI</b>", ""]
    for ln in loans:
        lines.append(f"#{ln['id']} {_loan_status_label(ln['status'])} — {vnd(ln['amount'])}đ — {_loan_customer_label(ln['tg_id'])}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Quay lại", callback_data="loanadm:menu")]])
    await cb.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("loanadm:view:"))
async def on_loanadm_view(cb: CallbackQuery):
    if not _is_loan_admin(cb.from_user.id):
        await cb.answer("Bạn không có quyền.", show_alert=True)
        return
    try:
        lid = int(cb.data.split(":")[2])
    except (ValueError, IndexError):
        await cb.answer("Dữ liệu không hợp lệ", show_alert=True)
        return
    ln = db.loan_get(lid)
    if not ln:
        await cb.answer("Không tìm thấy.", show_alert=True)
        return
    rest = ln["amount"] - ln["paid_amount"]
    pays = db.loan_payments(lid)
    lines = [
        f"💰 <b>Khoản ứng #{lid}</b> — {_loan_status_label(ln['status'])}",
        f"👤 Khách: {html.escape(_loan_customer_label(ln['tg_id']))} (<code>{ln['tg_id']}</code>)",
        f"Ứng: <b>{vnd(ln['amount'])}đ</b> | Đã trả: <b>{vnd(ln['paid_amount'])}đ</b> | Còn nợ: <b>{vnd(rest)}đ</b>",
        f"Hạn trả: {_fmt_date(ln['due_date'])}",
    ]
    if ln["note"]:
        lines.append(f"Ghi chú: {html.escape(ln['note'])}")
    if pays:
        lines.append("")
        lines.append("📜 <b>Lịch sử:</b>")
        kind_label = {"repay_auto": "nạp ví tự trừ", "repay_manual": "trả tay",
                      "adjust_up": "tăng nợ", "adjust_down": "giảm nợ"}
        for p in pays[-8:]:
            kl = kind_label.get(p["kind"], p["kind"])
            amt_disp = vnd(abs(p["amount"]))
            lines.append(f"• {time.strftime('%d/%m %H:%M', time.localtime(p['created_at']))}: "
                         f"{kl} {amt_disp}đ" + (f" — {html.escape(p['note'])}" if p["note"] else ""))
    rows = []
    if ln["status"] == "pending":
        rows.append([
            InlineKeyboardButton(text="✅ Duyệt", callback_data=f"loan:ap:{lid}"),
            InlineKeyboardButton(text="❌ Từ chối", callback_data=f"loan:rj:{lid}"),
        ])
    if ln["status"] in ("active", "overdue"):
        rows.append([InlineKeyboardButton(text="✅ Đánh dấu đã nhận tiền",
                                          callback_data=f"loanadm:repay:{lid}")])
        # Điều chỉnh nợ: chỉ chủ shop được giảm, admin phụ được tăng
        rows.append([InlineKeyboardButton(text="✏️ Điều chỉnh nợ",
                                          callback_data=f"loanadm:adj:{lid}")])
    rows.append([InlineKeyboardButton(text="◀️ Quay lại", callback_data="loanadm:menu")])
    await cb.message.edit_text("\n".join(lines), parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()


# ---------- ADMIN: đánh dấu đã nhận tiền (trả tay) ----------

@router.callback_query(F.data.startswith("loanadm:repay:"))
async def on_loanadm_repay(cb: CallbackQuery, state: FSMContext):
    if not _is_loan_admin(cb.from_user.id):
        await cb.answer("Bạn không có quyền.", show_alert=True)
        return
    try:
        lid = int(cb.data.split(":")[2])
    except (ValueError, IndexError):
        await cb.answer("Dữ liệu không hợp lệ", show_alert=True)
        return
    ln = db.loan_get(lid)
    if not ln or ln["status"] not in ("active", "overdue"):
        await cb.answer("Khoản này không còn nợ.", show_alert=True)
        return
    rest = ln["amount"] - ln["paid_amount"]
    await state.set_state(LoanState.repay_amount)
    await state.update_data(repay_lid=lid)
    await cb.message.answer(
        f"Nhập số tiền đã nhận từ khách cho khoản #{lid} (còn nợ {vnd(rest)}đ):")
    await cb.answer()


@router.message(StateFilter(LoanState.repay_amount))
async def on_loanadm_repay_amount(msg: Message, state: FSMContext):
    try:
        amount = int(msg.text.replace(".", "").replace(",", "").replace("đ", "").strip())
    except (ValueError, AttributeError):
        await msg.answer("Số tiền không hợp lệ. Nhập số thôi nhé.")
        return
    if amount <= 0:
        await msg.answer("Số tiền phải lớn hơn 0.")
        return
    await state.update_data(repay_amount=amount)
    await state.set_state(LoanState.repay_note)
    await msg.answer("Nhập ghi chú (vd: nhận tiền mặt, khách CK VCB) — hoặc gõ <code>skip</code> để bỏ qua:",
                     parse_mode="HTML")


@router.message(StateFilter(LoanState.repay_note))
async def on_loanadm_repay_note(msg: Message, state: FSMContext):
    data = await state.get_data()
    lid = data.get("repay_lid")
    amount = data.get("repay_amount", 0)
    note = "" if (msg.text or "").strip().lower() == "skip" else (msg.text or "").strip()[:200]
    await state.clear()
    if not lid or amount <= 0:
        return
    ln = db.loan_add_payment(lid, amount, "repay_manual", note, msg.from_user.id)
    if not ln:
        await msg.answer("Không ghi nhận được (khoản đã đóng).")
        return
    try:
        db.admin_audit_log(msg.from_user.id, "loan_repay",
                           f"nhận {vnd(amount)}đ khoản #{lid}: {note}")
    except Exception:
        pass
    rest = ln["amount"] - ln["paid_amount"]
    done = " 🎉 <b>Khách đã trả hết nợ!</b>" if ln["status"] == "paid" else ""
    await msg.answer(
        f"✅ Đã ghi nhận {vnd(amount)}đ cho khoản #{lid}.{done}\n"
        f"Còn nợ: <b>{vnd(rest)}đ</b>", parse_mode="HTML")
    # Báo khách
    try:
        await msg.bot.send_message(
            ln["tg_id"],
            f"✅ <b>Đã ghi nhận bạn trả {vnd(amount)}đ</b> (khoản #{lid}).\n"
            f"Còn nợ: <b>{vnd(rest)}đ</b>" + (f"\n🎉 Bạn đã trả hết nợ!" if ln["status"] == "paid" else ""),
            parse_mode="HTML")
    except Exception:
        pass


# ---------- ADMIN: tạo khoản ứng tay ----------

@router.callback_query(F.data == "loanadm:manual")
async def on_loanadm_manual(cb: CallbackQuery, state: FSMContext):
    if not _is_loan_admin(cb.from_user.id):
        await cb.answer("Bạn không có quyền.", show_alert=True)
        return
    await state.set_state(LoanState.manual_tgid)
    await cb.message.answer("Nhập <b>ID Telegram</b> của khách:", parse_mode="HTML")
    await cb.answer()


@router.message(StateFilter(LoanState.manual_tgid))
async def on_loanadm_manual_tgid(msg: Message, state: FSMContext):
    try:
        tgid = int((msg.text or "").strip())
    except (ValueError, AttributeError):
        await msg.answer("ID không hợp lệ. Nhập số ID Telegram của khách.")
        return
    await state.update_data(manual_tgid=tgid)
    await state.set_state(LoanState.manual_amount)
    debt = db.loan_total_debt(tgid)
    await msg.answer(f"Khách <code>{tgid}</code> đang nợ {vnd(debt)}đ.\nNhập số tiền ứng:", parse_mode="HTML")


@router.message(StateFilter(LoanState.manual_amount))
async def on_loanadm_manual_amount(msg: Message, state: FSMContext):
    try:
        amount = int(msg.text.replace(".", "").replace(",", "").replace("đ", "").strip())
    except (ValueError, AttributeError):
        await msg.answer("Số tiền không hợp lệ.")
        return
    if amount <= 0:
        await msg.answer("Số tiền phải lớn hơn 0.")
        return
    await state.update_data(manual_amount=amount)
    await state.set_state(LoanState.manual_note)
    await msg.answer("Nhập ghi chú — hoặc gõ <code>skip</code>:", parse_mode="HTML")


@router.message(StateFilter(LoanState.manual_note))
async def on_loanadm_manual_note(msg: Message, state: FSMContext):
    data = await state.get_data()
    tgid = data.get("manual_tgid")
    amount = data.get("manual_amount", 0)
    note = "" if (msg.text or "").strip().lower() == "skip" else (msg.text or "").strip()[:200]
    await state.clear()
    if not tgid or amount <= 0:
        return
    lid = db.loan_create(tgid, amount, note, created_by=msg.from_user.id)
    loan = db.loan_approve(lid, msg.from_user.id)
    try:
        db.adjust_shop_balance(tgid, amount, f"ung_tien_tay:loan#{lid}")
    except Exception as e:
        log.warning("adjust_shop_balance failed: %s", e)
    try:
        db.admin_audit_log(msg.from_user.id, "loan_manual",
                           f"tạo tay ứng #{lid} {vnd(amount)}đ cho {tgid}: {note}")
    except Exception:
        pass
    await msg.answer(
        f"✅ Đã tạo khoản ứng tay <b>#{lid}</b>: {vnd(amount)}đ cho <code>{tgid}</code>\n"
        f"Tiền đã vào ví shop khách. Hạn trả: <b>{_fmt_date(loan['due_date'])}</b>",
        parse_mode="HTML")
    try:
        await msg.bot.send_message(
            tgid,
            f"💰 <b>Admin đã ứng cho bạn {vnd(amount)}đ</b>\n"
            f"Tiền đã vào ví shop. Hạn trả: <b>{_fmt_date(loan['due_date'])}</b>",
            parse_mode="HTML")
    except Exception:
        pass


# ---------- TỰ TRỪ NỢ KHI NẠP (GĐ2) ----------

async def apply_auto_deduct(bot, tg_id: int, credited_amount: int) -> int:
    """Tự trừ nợ khi khách nạp tiền vào ví shop.
    Trả về tổng số tiền đã trừ vào nợ."""
    if not db.loan_enabled() or db.loan_setting("loan_auto_deduct") != "1":
        return 0
    if credited_amount <= 0:
        return 0
    loans = db.loan_list_active()
    my_loans = [ln for ln in loans if ln["tg_id"] == tg_id]
    if not my_loans:
        return 0
    # Ưu tiên khoản sắp đến hạn nhất
    my_loans.sort(key=lambda x: x["due_date"] or 0)
    remaining = credited_amount
    total_deducted = 0
    for ln in my_loans:
        if remaining <= 0:
            break
        rest = ln["amount"] - ln["paid_amount"]
        if rest <= 0:
            continue
        take = min(remaining, rest)
        new_loan = db.loan_add_payment(ln["id"], take, "repay_auto",
                                       "nạp ví tự trừ", 0)
        if new_loan:
            total_deducted += take
            remaining -= take
    if total_deducted > 0:
        # Trừ tiền khỏi ví shop (tiền trả nợ không được tiêu tiếp)
        try:
            db.adjust_shop_balance(tg_id, -total_deducted, "tru_no_ung_tien")
        except Exception as e:
            log.warning("deduct wallet for loan failed: %s", e)
        debt_left = db.loan_total_debt(tg_id)
        try:
            await bot.send_message(
                tg_id,
                f"💰 <b>TỰ TRỪ NỢ</b>\n"
                f"Bạn nạp {vnd(credited_amount)}đ → đã trừ <b>{vnd(total_deducted)}đ</b> vào nợ.\n"
                f"Còn nợ: <b>{vnd(debt_left)}đ</b>",
                parse_mode="HTML")
        except Exception:
            pass
    return total_deducted


def check_overdue_block(tg_id: int) -> tuple[bool, str]:
    """Kiểm tra có bị chặn mua do quá hạn không. Trả về (bị_chặn, tin_nhắn)."""
    if not db.loan_enabled():
        return False, ""
    loans = db.loan_list_active()
    overdue = [ln for ln in loans if ln["tg_id"] == tg_id and ln["status"] == "overdue"]
    if overdue:
        rest = sum(ln["amount"] - ln["paid_amount"] for ln in overdue)
        return True, (
            f"🔴 <b>Bạn đang quá hạn {len(overdue)} khoản ứng ({vnd(rest)}đ).</b>\n"
            f"Trả hết nợ quá hạn mới mua tiếp được nhé.\n"
            f"Xem chi tiết: /congno")
    return False, ""


async def loan_reminder_tick(bot) -> dict:
    """Quét 1 lượt: đánh dấu quá hạn + nhắc nợ. Trả về thống kê."""
    import time as _time
    stats = {"overdue_marked": 0, "reminded": 0}
    if not db.loan_enabled() or db.loan_setting("loan_remind") != "1":
        return stats
    now = int(_time.time())
    # 1. Đánh dấu quá hạn
    stats["overdue_marked"] = db.loan_mark_overdue()
    # 2. Nhắc nợ: trước hạn 1 ngày + quá hạn mỗi ngày
    loans = db.loan_list_active()
    for ln in loans:
        due = ln["due_date"] or 0
        if not due:
            continue
        rest = ln["amount"] - ln["paid_amount"]
        if rest <= 0:
            continue
        days_left = (due - now) / 86400
        txt = ""
        if 0 < days_left <= 1:
            txt = (f"⏰ <b>NHẮC NỢ</b>\nKhoản ứng #{ln['id']} còn <b>{vnd(rest)}đ</b> "
                   f"sẽ đến hạn vào <b>{_fmt_date(due)}</b> (mai).\nTrả sớm nhé!")
        elif days_left <= 0:
            txt = (f"🔴 <b>QUÁ HẠN</b>\nKhoản ứng #{ln['id']} còn nợ <b>{vnd(rest)}đ</b> "
                   f"đã quá hạn từ <b>{_fmt_date(due)}</b>.\n"
                   f"Trả ngay để mua tiếp nhé! Xem: /congno")
        if txt:
            try:
                await bot.send_message(ln["tg_id"], txt, parse_mode="HTML")
                stats["reminded"] += 1
            except Exception:
                pass
    return stats


# ---------- ADMIN: điều chỉnh nợ (GĐ2) ----------

@router.callback_query(F.data.startswith("loanadm:adj:"))
async def on_loanadm_adj(cb: CallbackQuery, state: FSMContext):
    admin_id = cb.from_user.id
    if not _is_loan_admin(admin_id):
        await cb.answer("Bạn không có quyền.", show_alert=True)
        return
    try:
        lid = int(cb.data.split(":")[2])
    except (ValueError, IndexError):
        await cb.answer("Dữ liệu không hợp lệ", show_alert=True)
        return
    ln = db.loan_get(lid)
    if not ln or ln["status"] not in ("active", "overdue"):
        await cb.answer("Khoản này không còn nợ.", show_alert=True)
        return
    await state.set_state(LoanState.adjust_dir)
    await state.update_data(adj_lid=lid)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 Tăng nợ", callback_data="loanadj:up"),
         InlineKeyboardButton(text="📉 Giảm nợ", callback_data="loanadj:down")],
    ])
    rest = ln["amount"] - ln["paid_amount"]
    await cb.message.answer(
        f"✏️ <b>Điều chỉnh khoản #{lid}</b> (còn nợ {vnd(rest)}đ)\n"
        f"Chọn tăng hay giảm:",
        parse_mode="HTML", reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("loanadj:"))
async def on_loanadj_dir(cb: CallbackQuery, state: FSMContext):
    direction = cb.data.split(":")[1]  # up / down
    if direction == "down" and not _perms.is_super(cb.from_user.id):
        await cb.answer("Chỉ chủ shop được giảm nợ.", show_alert=True)
        return
    await state.update_data(adj_dir=direction)
    await state.set_state(LoanState.adjust_amount)
    label = "tăng" if direction == "up" else "giảm"
    await cb.message.answer(f"Nhập số tiền muốn {label} (vd: 20000):")
    await cb.answer()


@router.message(StateFilter(LoanState.adjust_amount))
async def on_loanadj_amount(msg: Message, state: FSMContext):
    try:
        amount = int(msg.text.replace(".", "").replace(",", "").replace("đ", "").strip())
    except (ValueError, AttributeError):
        await msg.answer("Số tiền không hợp lệ.")
        return
    if amount <= 0:
        await msg.answer("Số tiền phải lớn hơn 0.")
        return
    data = await state.get_data()
    lid = data.get("adj_lid")
    direction = data.get("adj_dir", "up")
    ln = db.loan_get(lid) if lid else None
    if not ln:
        await state.clear()
        return
    if direction == "down":
        rest = ln["amount"] - ln["paid_amount"]
        if amount > rest:
            await msg.answer(f"Chỉ được giảm tối đa {vnd(rest)}đ (số còn nợ). Nhập lại nhé.")
            return
    await state.update_data(adj_amount=amount)
    await state.set_state(LoanState.adjust_reason)
    await msg.answer("Nhập <b>lý do</b> điều chỉnh (bắt buộc, vd: bớt cho khách quen):",
                     parse_mode="HTML")


@router.message(StateFilter(LoanState.adjust_reason))
async def on_loanadj_reason(msg: Message, state: FSMContext):
    data = await state.get_data()
    lid = data.get("adj_lid")
    amount = data.get("adj_amount", 0)
    direction = data.get("adj_dir", "up")
    reason = (msg.text or "").strip()[:200]
    await state.clear()
    if not lid or amount <= 0 or not reason:
        await msg.answer("Lý do không được để trống. Làm lại nhé.")
        return
    kind = "adjust_up" if direction == "up" else "adjust_down"
    ln = db.loan_add_payment(lid, amount, kind, reason, msg.from_user.id)
    if not ln:
        await msg.answer("Không điều chỉnh được.")
        return
    try:
        db.admin_audit_log(msg.from_user.id, "loan_adjust",
                           f"{kind} #{lid} {vnd(amount)}đ: {reason}")
    except Exception:
        pass
    rest = ln["amount"] - ln["paid_amount"]
    label = "tăng" if direction == "up" else "giảm"
    await msg.answer(
        f"✅ Đã {label} nợ khoản #{lid} thêm {vnd(amount)}đ.\n"
        f"Lý do: {html.escape(reason)}\n"
        f"Còn nợ: <b>{vnd(rest)}đ</b>",
        parse_mode="HTML")


# ---------- ADMIN: cài đặt (GĐ2, chỉ chủ shop) ----------

_LOAN_CFG = [
    ("loan_enabled", "Bật/tắt ứng tiền", "bool"),
    ("loan_min_acc", "Số acc tối thiểu để được ứng", "int"),
    ("loan_max_per_request", "Mỗi lần ứng tối đa (đ)", "int"),
    ("loan_max_total", "Tổng nợ tối đa/khách (đ)", "int"),
    ("loan_due_days", "Hạn trả (ngày)", "int"),
    ("loan_auto_deduct", "Nạp ví tự trừ nợ", "bool"),
    ("loan_remind", "Nhắc nợ tự động", "bool"),
]


def _cfg_text() -> str:
    lines = ["⚙️ <b>CÀI ĐẶT ỨNG TIỀN</b>", ""]
    for key, label, typ in _LOAN_CFG:
        val = db.loan_setting(key)
        if typ == "bool":
            disp = "🟢 Bật" if val == "1" else "🔴 Tắt"
        elif "max" in key or "per_request" in key:
            disp = f"{vnd(int(val or 0))}đ"
        else:
            disp = val
        lines.append(f"• {label}: <b>{disp}</b>")
    lines.append("")
    lines.append("Bấm vào từng dòng để đổi.")
    return "\n".join(lines)


def _cfg_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, label, _typ in _LOAN_CFG:
        short = label.split("(")[0].strip()[:22]
        rows.append([InlineKeyboardButton(text=f"✏️ {short}",
                                          callback_data=f"loancfg:{key}")])
    rows.append([InlineKeyboardButton(text="◀️ Quay lại", callback_data="loanadm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "loanadm:cfg")
async def on_loanadm_cfg(cb: CallbackQuery):
    if not _perms.is_super(cb.from_user.id):
        await cb.answer("Chỉ chủ shop đổi được cài đặt.", show_alert=True)
        return
    await cb.message.edit_text(_cfg_text(), parse_mode="HTML", reply_markup=_cfg_kb())
    await cb.answer()


@router.callback_query(F.data.startswith("loancfg:"))
async def on_loancfg_edit(cb: CallbackQuery, state: FSMContext):
    if not _perms.is_super(cb.from_user.id):
        await cb.answer("Chỉ chủ shop đổi được cài đặt.", show_alert=True)
        return
    key = cb.data.split(":")[1]
    cfg = next((c for c in _LOAN_CFG if c[0] == key), None)
    if not cfg:
        await cb.answer("Không hợp lệ", show_alert=True)
        return
    _, label, typ = cfg
    if typ == "bool":
        cur = db.loan_setting(key)
        new = "0" if cur == "1" else "1"
        db.set_setting(key, new)
        try:
            db.admin_audit_log(cb.from_user.id, "loan_cfg", f"{key} -> {new}")
        except Exception:
            pass
        await cb.message.edit_text(_cfg_text(), parse_mode="HTML", reply_markup=_cfg_kb())
        await cb.answer("Đã đổi!")
        return
    await state.set_state(LoanState.cfg_value)  # state riêng cho cài đặt
    await state.update_data(cfg_key=key, cfg_label=label)
    await cb.message.answer(f"Nhập giá trị mới cho <b>{label}</b> (hiện tại: {db.loan_setting(key)}):",
                            parse_mode="HTML")
    await cb.answer()


@router.message(StateFilter(LoanState.cfg_value))
async def on_loancfg_value(msg: Message, state: FSMContext):
    data = await state.get_data()
    key = data.get("cfg_key")
    if not key:
        return
    try:
        val = int(msg.text.replace(".", "").replace(",", "").strip())
    except (ValueError, AttributeError):
        await msg.answer("Nhập số thôi nhé.")
        return
    if val < 0:
        await msg.answer("Giá trị phải >= 0.")
        return
    if key == "loan_min_acc" and val < 1:
        await msg.answer("Số acc tối thiểu phải >= 1.")
        return
    await state.clear()
    db.set_setting(key, str(val))
    try:
        db.admin_audit_log(msg.from_user.id, "loan_cfg", f"{key} -> {val}")
    except Exception:
        pass
    await msg.answer(f"✅ Đã đổi <b>{key}</b> = <b>{val}</b>.", parse_mode="HTML",
                     reply_markup=_cfg_kb())
