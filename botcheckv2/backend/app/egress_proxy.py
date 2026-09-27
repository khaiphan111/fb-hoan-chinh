"""Local forward proxy: giúp Chromium vượt proxy egress có auth.

Vấn đề: proxy egress (hatch-egress-proxy:3128) yêu cầu Proxy-Authorization
ngay từ request đầu tiên, KHÔNG trả 407 challenge. Trong khi đó:
- Chromium bỏ qua credentials nhúng trong --proxy-server
  (ERR_NO_SUPPORTED_PROXIES),
- Chromium trên Linux đọc proxy từ env nhưng bỏ qua auth (bug 16709),
- Playwright chỉ gắn auth khi nhận được 407 (không bao giờ xảy ra).

Giải pháp: proxy trung gian ở 127.0.0.1:3129. Nó nhận request từ Chromium
(không cần auth), tự gắn Proxy-Authorization rồi forward lên upstream.
Chromium chỉ cần trỏ vào đây, không cần biết auth.

Chạy như daemon thread trong tiến trình backend (ensure_running),
tự chết theo backend khi restart. Không lưu credential vào file.
"""
import asyncio
import base64
import logging
import os
import threading
from urllib.parse import urlparse

log = logging.getLogger("egress_proxy")

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 3129

_thread = None
_lock = threading.Lock()


def _upstream():
    """(host, port, auth_header_value) của proxy egress từ env."""
    p = (os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
         or os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY") or "")
    u = urlparse(p)
    if not u.hostname:
        return None, None, None
    auth = base64.b64encode(
        f"{u.username or ''}:{u.password or ''}".encode()).decode()
    return u.hostname, u.port or 3128, auth


async def _pipe(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _handle(client_reader, client_writer):
    up = _upstream()
    if not up[0]:
        client_writer.close()
        return
    up_host, up_port, auth = up
    try:
        try:
            data = await asyncio.wait_for(
                client_reader.readuntil(b"\r\n\r\n"), timeout=20)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError) as e:
            data = getattr(e, "partial", b"")
            if not data:
                client_writer.close()
                return
        sep = data.find(b"\r\n\r\n")
        head, rest = data[:sep], data[sep + 4:]
        lines = head.decode("latin1").split("\r\n")
        if not lines or " " not in lines[0]:
            client_writer.close()
            return
        method = lines[0].split(" ")[0].upper()
        # Gắn Proxy-Authorization ngay sau dòng request
        fwd = lines[0] + f"\r\nProxy-Authorization: Basic {auth}\r\n" \
            + "\r\n".join(lines[1:]) + "\r\n\r\n"
        fwd_b = fwd.encode("latin1") + rest
        up_reader, up_writer = await asyncio.open_connection(up_host, up_port)
        up_writer.write(fwd_b)
        await up_writer.drain()
        if method == "CONNECT":
            try:
                resp = await asyncio.wait_for(
                    up_reader.readuntil(b"\r\n\r\n"), timeout=20)
            except Exception:
                client_writer.close()
                up_writer.close()
                return
            client_writer.write(resp)
            await client_writer.drain()
            if b" 200 " not in resp.split(b"\r\n")[0]:
                client_writer.close()
                up_writer.close()
                return
        await asyncio.gather(
            _pipe(client_reader, up_writer),
            _pipe(up_reader, client_writer),
        )
    except Exception as e:
        log.debug("egress_proxy handle lỗi: %s", e)
        try:
            client_writer.close()
        except Exception:
            pass


async def _serve():
    srv = await asyncio.start_server(_handle, LISTEN_HOST, LISTEN_PORT)
    log.info("egress_proxy: lắng nghe %s:%d", LISTEN_HOST, LISTEN_PORT)
    async with srv:
        await srv.serve_forever()


def _run():
    try:
        asyncio.run(_serve())
    except Exception as e:
        log.warning("egress_proxy dừng: %s", e)


def ensure_running() -> bool:
    """Đảm bảo proxy local đang chạy. Idempotent, an toàn gọi nhiều lần."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return True
        host, _, _ = _upstream()
        if not host:
            log.warning("egress_proxy: không tìm thấy proxy egress trong env")
            return False
        _thread = threading.Thread(target=_run, daemon=True,
                                    name="egress-proxy")
        _thread.start()
        return True


def proxy_server() -> str:
    """Địa chỉ proxy local cho Chromium (không cần auth)."""
    return f"http://{LISTEN_HOST}:{LISTEN_PORT}"
