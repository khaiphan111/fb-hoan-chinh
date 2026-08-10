# FB Live/Die Checker — Tác giả: @nhanxp | Hỗ trợ: Telegram/Facebook nhanxp
import time


def vnd(n) -> str:
    try:
        return f"{int(n):,}".replace(",", ".")
    except (ValueError, TypeError):
        return str(n)


def now() -> int:
    return int(time.time())


def parse_check_args(text: str):
    parts = (text or "").split()
    if parts and parts[0].startswith("/"):
        parts = parts[1:]
    if not parts:
        return None, None, None, None
    uid = parts[0]
    rest = parts[1:]

    trailing_nums = []
    while rest and rest[-1].isdigit() and len(trailing_nums) < 2:
        trailing_nums.insert(0, int(rest[-1]))
        rest = rest[:-1]

    price = days = None
    if len(trailing_nums) == 2:
        price, days = trailing_nums[0], trailing_nums[1]
    elif len(trailing_nums) == 1:
        days = trailing_nums[0]

    note = " ".join(rest).strip() or None
    return uid, note, price, days
