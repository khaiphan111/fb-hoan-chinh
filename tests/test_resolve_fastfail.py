"""Test resolve_fb_uid fail-nhanh: khi API traodoisub đã trả lời dứt khoát
"không resolve được" thì KHÔNG đốt thêm 25s vào Graph API + cào HTML
(cả hai luôn fail từ IP máy chủ: chưa có fb_avatar_token, FB chặn page load).

Chạy:  ~/fbvenv/bin/python -m pytest tests/ -q
(cwd = ~/workspace/fb-hoan-chinh)
"""
import asyncio
import os
import sys
import time

os.environ.pop("SUPABASE_DB_URL", None)

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "botcheckv2", "backend")
sys.path.insert(0, os.path.normpath(_BACKEND))

import app.fb as _fb  # noqa: E402


class _FakeResp:
    status_code = 200
    text = "{}"

    def json(self):
        return {}


class _SpyClient:
    """Fake httpx.AsyncClient: get() trả rỗng ngay, post() không được gọi."""
    gets = 0

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        raise AssertionError("post() không được gọi khi đã mock _resolve_via_traodoisub")

    async def get(self, *a, **k):
        _SpyClient.gets += 1
        return _FakeResp()


def _patch_net(monkeypatch):
    monkeypatch.setattr(_fb.httpx, "AsyncClient", _SpyClient)
    _SpyClient.gets = 0
    calls = {"urlopen": 0}

    def _boom(*a, **k):
        calls["urlopen"] += 1
        raise AssertionError("urlopen không được gọi")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    return calls


def test_definitive_fail_skips_fallbacks(monkeypatch):
    """API trả lời dứt khoát ('','') -> dừng ngay, không chạm Graph/HTML."""
    async def _tds(link, client):
        return "", ""

    monkeypatch.setattr(_fb, "_resolve_via_traodoisub", _tds)
    calls = _patch_net(monkeypatch)
    t0 = time.monotonic()
    uid, name, method = asyncio.run(
        _fb.resolve_fb_uid("https://www.facebook.com/some.username.xyz"))
    dt = time.monotonic() - t0
    assert (uid, name, method) == ("", "", "")
    assert _SpyClient.gets == 0, "đã gọi Graph API dù API trả lời dứt khoát"
    assert calls["urlopen"] == 0, "đã cào HTML dù API trả lời dứt khoát"
    # trước fix: Graph 15s + HTML 10s = >= 25s; sau fix phải xong trong vài giây
    assert dt < 10, f"quá chậm ({dt:.1f}s), fallback vẫn chạy?"


def test_busy_still_tries_fallbacks(monkeypatch):
    """API bận (chưa có câu trả lời dứt khoát) -> vẫn thử fallback như cũ."""
    async def _tds(link, client):
        raise _fb.TraodoisubBusy("busy")

    monkeypatch.setattr(_fb, "_resolve_via_traodoisub", _tds)
    calls = _patch_net(monkeypatch)
    # B3a bị bỏ qua vì chưa cấu hình fb_avatar_token; B3b cào HTML phải được thử
    # (urlopen bị chặn bởi assert ở trên; resolve_fb_uid bắt Exception nên vẫn trả ("","","")).
    uid, name, method = asyncio.run(
        _fb.resolve_fb_uid("https://www.facebook.com/some.username.xyz"))
    assert (uid, name, method) == ("", "", "")
    assert calls["urlopen"] >= 1, "API bận mà không thử fallback cào HTML"


def test_success_still_works(monkeypatch):
    async def _tds(link, client):
        return "123456789", "Test User"

    monkeypatch.setattr(_fb, "_resolve_via_traodoisub", _tds)
    _patch_net(monkeypatch)
    uid, name, method = asyncio.run(
        _fb.resolve_fb_uid("https://www.facebook.com/some.username.xyz"))
    assert (uid, name, method) == ("123456789", "Test User", "traodoisub.com")
