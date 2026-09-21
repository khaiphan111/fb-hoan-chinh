"""Validate URL chống SSRF: chỉ cho phép fetch các host hợp lệ, chặn IP nội bộ/metadata.

Dùng trước mọi httpx.get() với URL do user cung cấp.
"""
import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

# Host được phép theo từng loại link
FB_HOSTS = {
    "facebook.com", "www.facebook.com", "m.facebook.com", "mbasic.facebook.com",
    "fb.com", "www.fb.com", "fb.watch", "www.fb.watch",
}
TIKTOK_HOSTS = {
    "tiktok.com", "www.tiktok.com", "m.tiktok.com",
    "vm.tiktok.com", "vt.tiktok.com",
}


def _host_allowed(host: str, allowed: set) -> bool:
    host = host.lower()
    return host in allowed or any(host.endswith("." + h) for h in allowed)


def _ip_is_dangerous(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def is_safe_url(url: str, allowed_hosts: set) -> bool:
    """True nếu URL an toàn để server tự fetch: scheme http(s), host trong allowlist,
    và host không resolve ra IP nội bộ (chặn 169.254.169.254, localhost, LAN...)."""
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https"):
        return False
    host = (p.hostname or "").lower()
    if not host or not _host_allowed(host, allowed_hosts):
        return False
    # Chặn DNS resolve ra IP private (kể cả khi hostname trông "lành")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, None, type=socket.SOCK_STREAM
        )
    except (socket.gaierror, UnicodeError):
        return False
    if not infos:
        return False
    for info in infos:
        sockaddr = info[4]
        if not sockaddr or _ip_is_dangerous(str(sockaddr[0])):
            return False
    return True


async def fetch_with_safe_redirects(
    client, url: str, allowed_hosts: set, max_redirects: int = 5, **kwargs
) -> "object":
    """GET với follow redirect thủ công, mỗi hop đều validate chống SSRF.
    Trả về httpx.Response cuối cùng. Raise ValueError nếu URL không an toàn."""
    from httpx import Response  # noqa: F401  (giữ import local để module nhẹ)

    current = url
    for _ in range(max_redirects + 1):
        if not await is_safe_url(current, allowed_hosts):
            raise ValueError("URL không an toàn hoặc không thuộc nền tảng hỗ trợ")
        r = await client.get(current, follow_redirects=False, **kwargs)
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
            from urllib.parse import urljoin

            current = urljoin(current, r.headers["location"])
            continue
        return r
    raise ValueError("Quá nhiều redirect")
