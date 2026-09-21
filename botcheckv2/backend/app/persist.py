"""Luu tru ben vung cho FSM aiogram + cache ket qua check file/cookie.

Van de goc: Dispatcher dung MemoryStorage va _file_check_cache /
_cookie_check_cache chi nam trong RAM -> moi lan restart backend la mat
het: user dang di flow nhieu buoc thi bot quen mat buoc hien tai, nut
"tai ket qua" sau khi check file khong con tac dung, phai gui lai file.

Giai phap: luu xuong SQLite (DB chinh cua bot, song sot qua restart).
- SQLiteStorage: cai dat BaseStorage cua aiogram, hanh vi giong het
  MemoryStorage (state luu dang string "Group:state"). TTL mac dinh 24h
  de state cu tu nhieu ngay truoc khong lam phien user.
- check_cache_get/set: write-through 2 tang (dict RAM + DB) cho ket qua
  check file ("file") va check cookie ("cookie"). Truong bytes
  (xlsx_out) luu cot blob rieng, phan con lai JSON.
"""

import asyncio
import json
import sqlite3
import time
from typing import Any, Dict, Optional, Tuple

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey

FSM_TTL = 24 * 3600
CACHE_TTL = 24 * 3600

_MISSING = object()


def _open(db_path: str) -> sqlite3.Connection:
    c = sqlite3.connect(db_path, timeout=15)
    c.execute("PRAGMA journal_mode=WAL")
    return c


class SQLiteStorage(BaseStorage):
    """BaseStorage luu FSM xuong SQLite. An toan thread nho mo connection
    rieng cho moi thao tac (chay trong to_thread de khong chan event loop)."""

    def __init__(self, db_path: str, ttl: int = FSM_TTL):
        self._db_path = db_path
        self._ttl = ttl

    def _read_row(self, key: StorageKey) -> Optional[Tuple]:
        now = int(time.time())
        c = _open(self._db_path)
        try:
            r = c.execute(
                "SELECT state, data, updated_at FROM fsm_storage"
                " WHERE bot_id=? AND chat_id=? AND user_id=?",
                (key.bot_id, key.chat_id, key.user_id),
            ).fetchone()
            if not r:
                return None
            if now - (r[2] or 0) > self._ttl:
                c.execute(
                    "DELETE FROM fsm_storage WHERE bot_id=? AND chat_id=? AND user_id=?",
                    (key.bot_id, key.chat_id, key.user_id),
                )
                c.commit()
                return None
            return r
        finally:
            c.close()

    def _write(self, key: StorageKey, state: Any = _MISSING, data: Any = _MISSING) -> None:
        now = int(time.time())
        c = _open(self._db_path)
        try:
            r = c.execute(
                "SELECT state, data FROM fsm_storage"
                " WHERE bot_id=? AND chat_id=? AND user_id=?",
                (key.bot_id, key.chat_id, key.user_id),
            ).fetchone()
            cur_state, cur_data = (r[0], r[1]) if r else (None, "{}")
            new_state = cur_state if state is _MISSING else state
            new_data = cur_data if data is _MISSING else data
            c.execute(
                """INSERT INTO fsm_storage (bot_id, chat_id, user_id, state, data, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (bot_id, chat_id, user_id)
                   DO UPDATE SET state=excluded.state, data=excluded.data,
                                 updated_at=excluded.updated_at""",
                (key.bot_id, key.chat_id, key.user_id, new_state, new_data, now),
            )
            c.commit()
        finally:
            c.close()

    async def set_state(self, key: StorageKey, state: Any = None) -> None:
        # Giong MemoryStorage: luu dang string "Group:state"
        s = state.state if isinstance(state, State) else state
        await asyncio.to_thread(self._write, key, s)

    async def get_state(self, key: StorageKey) -> Optional[str]:
        row = await asyncio.to_thread(self._read_row, key)
        return row[0] if row else None

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        if not isinstance(data, dict):
            raise TypeError(f"Data must be a dict, got {type(data).__name__}")
        await asyncio.to_thread(self._write, key, _MISSING, json.dumps(data, default=str))

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        row = await asyncio.to_thread(self._read_row, key)
        if not row or not row[1]:
            return {}
        try:
            return json.loads(row[1])
        except Exception:
            return {}

    async def update_data(self, key: StorageKey, data: Dict[str, Any]) -> Dict[str, Any]:
        current = await self.get_data(key)
        current.update(data)
        await self.set_data(key, current)
        return current

    async def close(self) -> None:
        return None


# ─── Cache ket qua check file / cookie (2 tang: RAM + DB) ──────────────

_mem: Dict[str, Dict[Tuple[int, int], Tuple[Dict, float]]] = {"file": {}, "cookie": {}}
_DB_PATH: Optional[str] = None


def init_cache_db(db_path: str) -> None:
    """Goi 1 lan khi khoi dong (sau db.init_db)."""
    global _DB_PATH
    _DB_PATH = db_path


def _mem_get(kind: str, chat_id: int, user_id: int) -> Optional[Dict]:
    slot = _mem[kind].get((chat_id, user_id))
    if not slot:
        return None
    value, ts = slot
    if time.time() - ts > CACHE_TTL:
        _mem[kind].pop((chat_id, user_id), None)
        return None
    return value


def _db_get(kind: str, chat_id: int, user_id: int) -> Optional[Dict]:
    if not _DB_PATH:
        return None
    now = int(time.time())
    c = _open(_DB_PATH)
    try:
        r = c.execute(
            "SELECT payload, blob1, updated_at FROM check_cache"
            " WHERE chat_id=? AND user_id=? AND kind=?",
            (chat_id, user_id, kind),
        ).fetchone()
        if not r:
            return None
        if now - (r[2] or 0) > CACHE_TTL:
            c.execute(
                "DELETE FROM check_cache WHERE chat_id=? AND user_id=? AND kind=?",
                (chat_id, user_id, kind),
            )
            c.commit()
            return None
        try:
            value = json.loads(r[0])
        except Exception:
            return None
        if r[1] is not None:
            value["xlsx_out"] = r[1]
        _mem[kind][(chat_id, user_id)] = (value, time.time())
        return value
    finally:
        c.close()


def _db_set(kind: str, chat_id: int, user_id: int, value: Dict) -> None:
    if not _DB_PATH:
        return
    now = int(time.time())
    payload = dict(value)
    blob = payload.pop("xlsx_out", None)
    if blob is not None and not isinstance(blob, (bytes, bytearray)):
        blob = None
    c = _open(_DB_PATH)
    try:
        c.execute(
            """INSERT INTO check_cache (chat_id, user_id, kind, payload, blob1, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (chat_id, user_id, kind)
               DO UPDATE SET payload=excluded.payload, blob1=excluded.blob1,
                             updated_at=excluded.updated_at""",
            (chat_id, user_id, kind, json.dumps(payload, default=str), blob, now),
        )
        c.commit()
    except Exception:
        pass
    finally:
        c.close()


def check_cache_get(kind: str, chat_id: int, user_id: int) -> Optional[Dict]:
    """Doc cache ket qua check. kind: 'file' | 'cookie'."""
    hit = _mem_get(kind, chat_id, user_id)
    if hit is not None:
        return hit
    return _db_get(kind, chat_id, user_id)


def check_cache_set(kind: str, chat_id: int, user_id: int, value: Dict) -> None:
    """Ghi cache ket qua check (RAM + DB)."""
    _mem[kind][(chat_id, user_id)] = (value, time.time())
    _db_set(kind, chat_id, user_id, value)
