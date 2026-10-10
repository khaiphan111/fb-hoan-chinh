"""Test fix E1/E2: phân quyền hành động tiền trên bot admin.

Chạy: ~/fbvenv/bin/python -m pytest tests/test_admin_perm.py -q
"""
import asyncio

from app import admin_bot
from app import perms


def test_e2_can_money_super_admin(tdb):
    """Super admin luôn có quyền tiền."""
    assert admin_bot._can_money(5964340237) is True


def test_e2_can_money_tien_perm(tdb):
    """Admin phụ có tick quyền 'tien' -> được."""
    tdb.extra_admin_add(9210051, "Test Tien", "tien", added_by=5964340237)
    assert admin_bot._can_money(9210051) is True


def test_e2_can_money_khong_quyen(tdb):
    """Admin phụ chỉ có quyền faq -> KHÔNG được đụng tiền."""
    tdb.extra_admin_add(9210052, "Test Faq", "faq", added_by=5964340237)
    assert admin_bot._can_money(9210052) is False


def test_e2_can_money_nguoi_la(tdb):
    """Người lạ hoàn toàn -> KHÔNG."""
    assert admin_bot._can_money(999888777) is False


def test_e1_need_perm_chan(tdb):
    """_need_perm: không có quyền -> trả False và báo lỗi."""
    tdb.extra_admin_add(9210053, "Test Faq2", "faq", added_by=5964340237)

    class FakeUser:
        id = 9210053

    class FakeMsg:
        from_user = FakeUser()
        answered = None

        async def answer(self, text):
            self.answered = text

    msg = FakeMsg()
    ok = asyncio.get_event_loop().run_until_complete(
        admin_bot._need_perm(msg, "tien"))
    assert ok is False
    assert msg.answered and "Tiền tệ" in msg.answered


def test_e1_need_perm_cho(tdb):
    """_need_perm: có quyền -> trả True, không báo gì."""
    tdb.extra_admin_add(9210054, "Test Tien2", "tien", added_by=5964340237)

    class FakeUser:
        id = 9210054

    class FakeMsg:
        from_user = FakeUser()
        answered = None

        async def answer(self, text):
            self.answered = text

    msg = FakeMsg()
    ok = asyncio.get_event_loop().run_until_complete(
        admin_bot._need_perm(msg, "tien"))
    assert ok is True
    assert msg.answered is None
