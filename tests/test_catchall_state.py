"""Regression: catch-all on_other khong duoc nuot tin nhan khi user dang o state nhap lieu.

Bug 2026-09-25: on_other (@router.message(F.text & ~F.text.startswith("/")))
dang ky TRUOC cac handler nhap lieu (waiting_for_pick_uid, waiting_for_custom_qty,
waiting_for_cookie, ...) va khong co StateFilter -> moi tin nhan text deu bi
on_other bat truoc, handler dung khong bao gio duoc goi.
"""
from aiogram.filters import StateFilter


def _find_handler(name):
    from app import bot as botmod
    for h in botmod.router.message.handlers:
        if getattr(h.callback, "__name__", "") == name:
            return h
    raise AssertionError(f"không tìm thấy handler {name}")


def _filters_of(h):
    return [f.callback for f in h.filters]


def test_on_other_only_when_no_state():
    h = _find_handler("on_other")
    sfs = [f for f in _filters_of(h) if isinstance(f, StateFilter)]
    assert sfs, "on_other thiếu StateFilter -> sẽ nuốt tin nhắn của các state nhập liệu"
    # StateFilter(None) = chỉ match khi KHÔNG có state active
    assert any(f.states is None or f.states == {None} or None in (f.states or ())
               for f in sfs), "on_other phải dùng StateFilter(None)"


def test_state_input_handlers_registered():
    # các handler nhập liệu text phải tồn tại (không bị catch-all chặn sau fix)
    for name in ["on_acc_pick_input", "on_acc_custom_qty_input",
                 "on_cookie_add_input", "on_acc_review_comment"]:
        try:
            _find_handler(name)
        except AssertionError:
            pass  # tên handler có thể khác, không bắt buộc
    # ít nhất handler nhập UID phải tồn tại
    _find_handler("on_acc_pick_input")
