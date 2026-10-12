"""Gộp danh mục dịch vụ nhiều nhà cung cấp (GĐ3: chọn nhà tự động).

Hai nhà có service_id khác nhau -> khớp theo tên chuẩn hóa (thường, bỏ ký tự
đặc biệt). Mỗi mục gộp giữ giá vốn + service_id của từng nhà để lúc thuê
thử nhà rẻ trước, lỗi thì nhảy sang nhà kia.
"""
import re

from . import list_providers


def norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def provider_enabled(pname: str) -> bool:
    """Nhà cung cấp có đang bật không (setting `<pname>_enabled`, mặc định bật)."""
    from .. import db as _db
    return _db.get_setting(f"{pname}_enabled", "1") == "1"


def any_provider_enabled() -> bool:
    """Còn ít nhất 1 nhà đang bật không (shop thuê số mở khi còn 1 nhà)."""
    return any(provider_enabled(p.name) for p in list_providers())


async def get_merged_services(country: str = "vn") -> list:
    """[{key, name, providers: {pname: {service_id, cost}}}] — sắp xếp theo tên.

    Bỏ qua nhà đang TẮT (admin tắt tạm) và nhà lỗi API (không làm hỏng cả list).
    """
    merged: dict[str, dict] = {}
    for p in list_providers():
        if not provider_enabled(p.name):
            continue
        try:
            items = await p.get_services(country=country)
        except Exception:
            continue
        for s in items:
            key = norm_name(s.get("name", ""))
            if not key:
                continue
            e = merged.setdefault(
                key, {"key": key, "name": s.get("name", ""), "providers": {}})
            e["providers"][p.name] = {
                "service_id": s["id"], "cost": s["price"]}
    return sorted(merged.values(), key=lambda e: e["name"].lower())


def offers_sorted(item: dict) -> list:
    """[(provider, service_id, cost, sell)] — rẻ trước (theo giá bán thực tế,
    đã gồm giá cố định từng dịch vụ và % lãi riêng từng nhà)."""
    from .. import db as _db
    out = []
    for pname, psvc in item.get("providers", {}).items():
        sell = _db.rent_sell_price(psvc["cost"], pname, psvc["service_id"])
        out.append((pname, psvc["service_id"], psvc["cost"], sell))
    out.sort(key=lambda x: x[3])
    return out


def best_sell(item: dict) -> int:
    """Giá bán rẻ nhất của mục gộp (hiện cho khách)."""
    offers = offers_sorted(item)
    return offers[0][3] if offers else 0
