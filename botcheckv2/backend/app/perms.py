"""Phân quyền admin: 1 super admin (chủ shop) + các admin phụ được tick quyền.

- Super admin: id trong setting `admin_tg_id` — full quyền, duy nhất được quản lý admin phụ.
- Admin phụ: lưu trong bảng `extra_admins`, mỗi người có tập quyền tick chọn.
- Mọi menu (/adm, /shopadm) tự lọc theo quyền; lệnh gõ tay bị middleware chặn.
"""
from . import db

PERMS = [
    ("kho", "📦 Kho & loại acc"),
    ("price", "💲 Giá & khuyến mãi"),
    ("orders", "📋 Đơn hàng & bảo hành"),
    ("faq", "❓ FAQ tự động"),
    ("sup", "🏭 Nhà cung cấp"),
    ("tien", "💰 Tiền tệ"),
    ("user", "👤 Quản lý user"),
    ("promo", "🎟️ Mã giảm giá"),
    ("report", "📊 Báo cáo"),
    ("bcast", "📣 Broadcast & webhook"),
]
PERM_KEYS = [k for k, _ in PERMS]
PERM_LABEL = dict(PERMS)

# Lệnh gõ tay trong bot chính -> quyền yêu cầu
CMD_PERMS = {
    # kho
    "themacc": "kho", "themloai": "kho", "kho": "kho", "xuatkho": "kho",
    "xoakho": "kho", "xoaloai": "kho", "xoahan": "kho", "hienloai": "kho",
    "anhbia": "kho", "recheck": "kho", "xoadie": "kho",
    "nhapkhosheet": "kho", "setsheet": "kho", "chamdiem": "kho",
    # price
    "gia": "price", "suabh": "price", "creditbonus": "price",
    "giovang": "price", "hopmu": "price", "hopmugia": "price",
    "quadoi": "price", "loyaltyrandom": "price", "setmailapp": "price",
    # orders
    "donhang": "orders", "bhdon": "orders", "bhdone": "orders",
    "accinfo": "orders", "lo": "orders",
    # faq
    "faq": "faq", "themcauhoi": "faq", "xoacauhoi": "faq",
    # sup
    "ncc": "sup", "themncc": "sup", "nccauto": "sup", "danhgiancc": "sup",
    # promo
    "taopromo": "promo", "xoapromo": "promo", "dspromo": "promo",
    "flashsale": "promo", "code": "promo",
    # bcast
    "setwebhook": "bcast",
}

# Sub-command của /adm -> quyền yêu cầu
ADM_SUB_PERMS = {
    "topup": "tien", "setbal": "tien",
    "ban": "user", "unban": "user", "setvip": "user",
    "info": "user", "find": "user",
    "pending": "report", "revenue": "report", "stats": "report",
    "broadcast": "bcast", "webhook": "bcast",
    "promo": "promo", "taopromo": "promo", "dspromo": "promo",
    "xoapromo": "promo", "flashsale": "promo",
}

# Text nút bàn phím (không phải lệnh /) -> quyền yêu cầu
TEXT_PERMS = {
    "📊 Nhập kho Sheet": "kho",
}


def is_super(tg_id) -> bool:
    """Chủ shop — full quyền, duy nhất được quản lý admin phụ."""
    try:
        sid = db.get_setting("admin_tg_id")
        return bool(sid) and int(sid) == int(tg_id)
    except Exception:
        return False


def is_admin(tg_id) -> bool:
    """Super admin hoặc admin phụ (bất kỳ quyền nào)."""
    if is_super(tg_id):
        return True
    try:
        return db.extra_admin_get(tg_id) is not None
    except Exception:
        return False


def perms_of(tg_id) -> set:
    """Tập quyền của user. Super admin -> tất cả."""
    if is_super(tg_id):
        return set(PERM_KEYS)
    try:
        row = db.extra_admin_get(tg_id)
    except Exception:
        row = None
    if not row:
        return set()
    raw = (row.get("perms") or "").strip()
    if raw == "*":
        return set(PERM_KEYS)
    return {p for p in raw.split(",") if p in PERM_LABEL}


def has_perm(tg_id, perm: str) -> bool:
    return perm in perms_of(tg_id)


def perm_label(perm: str) -> str:
    return PERM_LABEL.get(perm, perm)


def cmd_perm_for_text(text: str):
    """Quyền yêu cầu cho 1 tin nhắn lệnh gõ tay. None = không thuộc diện phân quyền."""
    t = (text or "").strip()
    if not t:
        return None
    if t in TEXT_PERMS:
        return TEXT_PERMS[t]
    if not t.startswith("/"):
        return None
    parts = t[1:].split()
    if not parts:
        return None
    cmd = parts[0].split("@")[0].lower()
    if cmd == "adm":
        if len(parts) < 2:
            return None  # mở menu /adm — menu tự lọc
        return ADM_SUB_PERMS.get(parts[1].lower())
    return CMD_PERMS.get(cmd)


def notify_extra_ids(perm: str) -> list:
    """ID các admin phụ đang có quyền `perm` (để gửi thông báo theo quyền)."""
    out = []
    try:
        from . import db as _db
        for r in _db.extra_admin_list():
            ps = set((r["perms"] or "").split(",")) if r["perms"] else set()
            if perm in ps:
                try:
                    out.append(int(r["tg_id"]))
                except Exception:
                    pass
    except Exception:
        pass
    return out


def super_id() -> int:
    """Telegram ID của chủ shop (0 nếu chưa cài)."""
    try:
        from . import db as _db
        aid = (_db.get_setting("admin_tg_id", "") or "").strip()
        return int(aid) if aid.isdigit() else 0
    except Exception:
        return 0
