"""Shim tương thích ngược (refactor 2026-09-25).

Toàn bộ handlers đã tách vào package `app/handlers/` theo tính năng:
core (router/middleware/managers), common (helper dùng chung), wallet (tiền/PayOS),
check (kiểm tra live/die), tracking (theo dõi), shop (shop acc), promo (khuyến mãi/quay),
stock (kho/NCC), warranty (bảo hành), admin (quản trị), fallback (catch-all cuối).

File này chỉ import lại để mọi `from app.bot import ...` / `import app.bot` cũ
(main.py, admin_bot.py, tests) chạy y như trước — không chứa logic.
"""
from . import config as config  # noqa: F401  (giữ attribute cho test/code cũ)
from . import db as db  # noqa: F401
from . import fb as fb  # noqa: F401
from . import perms as _perms  # noqa: F401

from app.handlers.core import *  # noqa: F401,F403
from app.handlers.common import *  # noqa: F401,F403
from app.handlers.wallet import *  # noqa: F401,F403
from app.handlers.check import *  # noqa: F401,F403
from app.handlers.tracking import *  # noqa: F401,F403
from app.handlers.shop import *  # noqa: F401,F403
from app.handlers.promo import *  # noqa: F401,F403
from app.handlers.stock import *  # noqa: F401,F403
from app.handlers.warranty import *  # noqa: F401,F403
from app.handlers.admin import *  # noqa: F401,F403
from app.handlers.fallback import *  # noqa: F401,F403
