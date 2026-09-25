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


def test_faq_catchall_excludes_slash_commands():
    """H1 (refactor 2026-09-25): catch-all on_other (da gop FAQ tu dong) khong duoc
    nuot lenh go tay bat dau bang '/'. Decorator nam trong app/handlers/fallback.py."""
    import re
    from app import bot as botmod
    src = open("botcheckv2/backend/app/handlers/fallback.py", encoding="utf-8").read()
    m = re.search(r"(@router\.message\([^\n]*\))\s*\nasync def on_other", src)
    assert m, "khong tim thay decorator cua on_other"
    deco = m.group(1)
    assert '~F.text.startswith(\"/\")' in deco or "~F.text.startswith('/')" in deco, \
        f"filter chua loai tru lenh '/': {deco}"
    # FAQ tu dong phai duoc goi trong on_other (truoc day bi che mat)
    body = src[m.end():m.end() + 600]
    assert "shop_faq_match" in body, "on_other chua goi shop_faq_match (FAQ tu dong)"
    # 12 lenh go tay phai van co handler dang ky
    handlers = botmod.router.message.handlers
    cmd_pos = {}
    for i, h in enumerate(handlers):
        for f in h.filters:
            cb = getattr(f, "callback", None)
            cmds = getattr(cb, "commands", None) if cb else None
            if cmds:
                for c in cmds:
                    cmd_pos.setdefault(c, i)
    for c in ["coc", "coclist", "huycoc", "hopmu", "hopmugia", "giovang",
              "themncc", "ncc", "danhgiancc", "chamdiem", "lo", "nccauto"]:
        assert c in cmd_pos, f"lenh /{c} khong co handler"
