"""Test tự động phần TIỀN — chạy trên SQLite TẠM, tuyệt đối không đụng data.db thật.

Chạy:  ~/fbvenv/bin/python -m pytest tests/ -q
(cwd = ~/workspace/fb-hoan-chinh)
"""
import os
import sys

# Ép dùng SQLite local, không bao giờ chạm Postgres live
os.environ.pop("SUPABASE_DB_URL", None)

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "botcheckv2", "backend")
sys.path.insert(0, os.path.normpath(_BACKEND))

import pytest  # noqa: E402

from app import config as _config  # noqa: E402
from app import db as _db  # noqa: E402


@pytest.fixture()
def tdb(tmp_path, monkeypatch):
    """DB SQLite tạm riêng cho mỗi test."""
    test_db = str(tmp_path / "test_data.db")
    monkeypatch.setattr(_config, "DB_PATH", test_db)
    # reset singleton connection để lần get_conn() tới mở file tạm
    monkeypatch.setattr(_db, "_pg_conn", None)
    _db.init_db()
    _db.migrate_new_features()  # tạo bảng payos_orders...
    _db.migrate_db()            # ...rồi mới ALTER thêm cột target
    assert _config.DB_PATH.endswith("test_data.db"), "SAI DB PATH!"
    assert _db.SUPABASE_URL is None, "đang trỏ Postgres?!"
    return _db
