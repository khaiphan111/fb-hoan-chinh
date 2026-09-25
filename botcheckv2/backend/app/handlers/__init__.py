"""Package handlers — mỗi module đăng ký handler lên router dùng chung (core.router)."""
from . import core  # noqa: F401  (đăng ký handlers)
from . import common  # noqa: F401  (đăng ký handlers)
from . import wallet  # noqa: F401  (đăng ký handlers)
from . import check  # noqa: F401  (đăng ký handlers)
from . import tracking  # noqa: F401  (đăng ký handlers)
from . import shop  # noqa: F401  (đăng ký handlers)
from . import promo  # noqa: F401  (đăng ký handlers)
from . import stock  # noqa: F401  (đăng ký handlers)
from . import warranty  # noqa: F401  (đăng ký handlers)
from . import admin  # noqa: F401  (đăng ký handlers)
from . import fallback  # noqa: F401  (đăng ký handlers)

# Khôi phục đúng thứ tự handler gốc (aiogram ưu tiên handler đăng ký trước).
# _order.py trích từ bot.py trước refactor; sắp xếp ổn định để các đăng ký
# trùng tên (vd on_checkfile_cmd ×3) giữ nguyên thứ tự tương đối.
from .core import router as _router
from ._order import MESSAGE_ORDER as _MSG_ORDER, CALLBACK_ORDER as _CB_ORDER


def _reorder(handlers, want):
    pos = {}
    for i, name in enumerate(want):
        pos.setdefault(name, i)
    handlers[:] = sorted(
        handlers, key=lambda h: pos.get(getattr(h.callback, "__name__", ""), 10**9))


_reorder(_router.message.handlers, _MSG_ORDER)
_reorder(_router.callback_query.handlers, _CB_ORDER)
del _router, _MSG_ORDER, _CB_ORDER, _reorder
