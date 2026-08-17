# FB Live/Die Checker & Tiktok Checker
import sqlite3
import threading
import time
from typing import Optional

from . import config

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None

def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
    return _conn


def init_db() -> None:
    c = get_conn()
    with _lock:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS tg_users (
                tg_id        INTEGER PRIMARY KEY,
                username     TEXT,
                name         TEXT,
                balance      INTEGER DEFAULT 0,
                sub_until    INTEGER DEFAULT 0,
                created_at   INTEGER,
                is_blocked   INTEGER DEFAULT 0,
                trial_activated INTEGER DEFAULT 0,
                referrer_id  INTEGER DEFAULT 0,
                ref_earnings INTEGER DEFAULT 0,
                expired_notified INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS watches (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id        INTEGER,
                uid          TEXT,
                note         TEXT,
                price        INTEGER DEFAULT 0,
                expire_at    INTEGER DEFAULT 0,
                last_status  TEXT,
                avatar_url   TEXT,
                last_checked INTEGER DEFAULT 0,
                active       INTEGER DEFAULT 1,
                created_at   INTEGER
            );

            CREATE TABLE IF NOT EXISTS logs (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        INTEGER,
                tg_id     INTEGER,
                uid       TEXT,
                kind      TEXT,
                message   TEXT
            );

            CREATE TABLE IF NOT EXISTS txns (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        INTEGER,
                tg_id     INTEGER,
                amount    INTEGER,
                reason    TEXT
            );
            
            -- TIKTOK & IG TABLES --
            CREATE TABLE IF NOT EXISTS tracks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_user_id      INTEGER DEFAULT 0,
                tg_username     TEXT    DEFAULT '',
                zalo_user_id    TEXT    DEFAULT '',
                tiktok_username TEXT    NOT NULL,
                last_followers  INTEGER DEFAULT 0,
                last_following  INTEGER DEFAULT 0,
                last_videos     INTEGER DEFAULT 0,
                last_video_id   TEXT    DEFAULT '',
                last_checked    INTEGER DEFAULT 0,
                created_at      INTEGER NOT NULL,
                active          INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS video_tracks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_user_id      INTEGER DEFAULT 0,
                tg_username     TEXT    DEFAULT '',
                zalo_user_id    TEXT    DEFAULT '',
                video_url       TEXT    NOT NULL,
                video_id        TEXT    DEFAULT '',
                tiktok_username TEXT    DEFAULT '',
                video_desc      TEXT    DEFAULT '',
                cover_url       TEXT    DEFAULT '',
                check_interval  INTEGER DEFAULT 3600,
                last_plays      INTEGER DEFAULT 0,
                last_likes      INTEGER DEFAULT 0,
                last_comments   INTEGER DEFAULT 0,
                last_shares     INTEGER DEFAULT 0,
                last_favorites  INTEGER DEFAULT 0,
                last_checked    INTEGER DEFAULT 0,
                created_at      INTEGER NOT NULL,
                active          INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS ig_tracks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_user_id      INTEGER DEFAULT 0,
                tg_username     TEXT    DEFAULT '',
                zalo_user_id    TEXT    DEFAULT '',
                ig_username     TEXT    NOT NULL,
                last_followers  INTEGER DEFAULT 0,
                last_following  INTEGER DEFAULT 0,
                last_posts      INTEGER DEFAULT 0,
                last_checked    INTEGER DEFAULT 0,
                created_at      INTEGER NOT NULL,
                active          INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS ig_video_tracks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_user_id      INTEGER DEFAULT 0,
                tg_username     TEXT    DEFAULT '',
                zalo_user_id    TEXT    DEFAULT '',
                post_url        TEXT    NOT NULL,
                post_id         TEXT    DEFAULT '',
                ig_username     TEXT    DEFAULT '',
                post_desc       TEXT    DEFAULT '',
                cover_url       TEXT    DEFAULT '',
                check_interval  INTEGER DEFAULT 3600,
                last_likes      INTEGER DEFAULT 0,
                last_comments   INTEGER DEFAULT 0,
                last_views      INTEGER DEFAULT 0,
                last_checked    INTEGER DEFAULT 0,
                created_at      INTEGER NOT NULL,
                active          INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS fb_tracks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_user_id      INTEGER DEFAULT 0,
                tg_username     TEXT    DEFAULT '',
                zalo_user_id    TEXT    DEFAULT '',
                fb_uid          TEXT    NOT NULL,
                last_status     TEXT    DEFAULT '',
                avatar_url      TEXT    DEFAULT '',
                created_at      INTEGER NOT NULL,
                active          INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS fb_post_tracks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_user_id      INTEGER DEFAULT 0,
                tg_username     TEXT    DEFAULT '',
                zalo_user_id    TEXT    DEFAULT '',
                post_url        TEXT    NOT NULL,
                post_id         TEXT    DEFAULT '',
                fb_username     TEXT    DEFAULT '',
                post_desc       TEXT    DEFAULT '',
                cover_url       TEXT    DEFAULT '',
                check_interval  INTEGER DEFAULT 3600,
                last_likes      INTEGER DEFAULT 0,
                last_comments   INTEGER DEFAULT 0,
                last_shares     INTEGER DEFAULT 0,
                last_checked    INTEGER DEFAULT 0,
                created_at      INTEGER NOT NULL,
                active          INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS giftcodes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                code            TEXT    NOT NULL UNIQUE,
                amount          INTEGER NOT NULL,
                is_used         INTEGER DEFAULT 0,
                used_by         INTEGER DEFAULT 0,
                created_at      INTEGER NOT NULL,
                used_at         INTEGER DEFAULT 0
            );
            """
        )
        for k, v in config.DEFAULT_SETTINGS.items():
            c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (k, v))
        c.commit()
        
def migrate_db():
    c = get_conn()
    with _lock:
        for sql in [
            "ALTER TABLE tracks ADD COLUMN zalo_user_id TEXT DEFAULT ''",
            "ALTER TABLE tracks ADD COLUMN avatar_url TEXT DEFAULT ''",
            "ALTER TABLE ig_tracks ADD COLUMN avatar_url TEXT DEFAULT ''",
            "ALTER TABLE video_tracks ADD COLUMN zalo_user_id TEXT DEFAULT ''",
            "ALTER TABLE video_tracks ADD COLUMN last_favorites INTEGER DEFAULT 0",
            "ALTER TABLE tg_users ADD COLUMN trial_activated INTEGER DEFAULT 0",
            "ALTER TABLE tg_users ADD COLUMN referrer_id INTEGER DEFAULT 0",
            "ALTER TABLE tg_users ADD COLUMN ref_earnings INTEGER DEFAULT 0",
            "ALTER TABLE tg_users ADD COLUMN expired_notified INTEGER DEFAULT 0"
        ]:
            try:
                c.execute(sql)
            except Exception:
                pass
        c.commit()

# --- SETTINGS ---
def get_setting(key: str, default: str = "") -> str:
    row = get_conn().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default

def set_setting(key: str, value: str) -> None:
    with _lock:
        c = get_conn()
        c.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        c.commit()

def clear_logs() -> None:
    with _lock:
        c = get_conn()
        c.execute("DELETE FROM logs")
        c.commit()

# --- ANALYTICS ---
def get_analytics() -> dict:
    c = get_conn()
    total_users = c.execute("SELECT COUNT(*) as c FROM tg_users").fetchone()["c"]
    today_start = int(time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d")))
    
    new_users_today = c.execute("SELECT COUNT(*) as c FROM tg_users WHERE created_at >= ?", (today_start,)).fetchone()["c"]
    
    total_revenue = c.execute("SELECT SUM(amount) as s FROM txns WHERE amount > 0").fetchone()["s"] or 0
    revenue_today = c.execute("SELECT SUM(amount) as s FROM txns WHERE amount > 0 AND ts >= ?", (today_start,)).fetchone()["s"] or 0
    
    this_month_start = int(time.mktime(time.strptime(time.strftime("%Y-%m-01"), "%Y-%m-%d")))
    revenue_month = c.execute("SELECT SUM(amount) as s FROM txns WHERE amount > 0 AND ts >= ?", (this_month_start,)).fetchone()["s"] or 0

    return {
        "total_users": total_users,
        "new_users_today": new_users_today,
        "total_revenue": total_revenue,
        "revenue_today": revenue_today,
        "revenue_month": revenue_month
    }

def all_settings() -> dict:
    rows = get_conn().execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}

# --- FB USER & BALANCE ---
def upsert_user(tg_id: int, username: str, name: str, referrer_id: int = 0) -> sqlite3.Row:
    with _lock:
        c = get_conn()
        c.execute(
            "INSERT INTO tg_users(tg_id, username, name, created_at, referrer_id) VALUES(?,?,?,?,?) "
            "ON CONFLICT(tg_id) DO UPDATE SET username=excluded.username, name=excluded.name",
            (tg_id, username, name, int(time.time()), referrer_id),
        )
        c.commit()
    return get_user(tg_id)

def get_user(tg_id: int) -> Optional[sqlite3.Row]:
    return get_conn().execute("SELECT * FROM tg_users WHERE tg_id=?", (tg_id,)).fetchone()

def list_users() -> list:
    return get_conn().execute(
        "SELECT t.*, (SELECT COUNT(*) FROM tg_users WHERE referrer_id = t.tg_id) as ref_count "
        "FROM tg_users t ORDER BY created_at DESC"
    ).fetchall()

def adjust_balance(tg_id: int, amount: int, reason: str) -> None:
    with _lock:
        c = get_conn()
        c.execute("UPDATE tg_users SET balance = balance + ? WHERE tg_id=?", (amount, tg_id))
        c.execute(
            "INSERT INTO txns(ts, tg_id, amount, reason) VALUES(?,?,?,?)",
            (int(time.time()), tg_id, amount, reason),
        )
        if amount > 0:
            user = c.execute("SELECT referrer_id FROM tg_users WHERE tg_id=?", (tg_id,)).fetchone()
            if user and user["referrer_id"]:
                ref_id = user["referrer_id"]
                ref_bonus = int(amount * 0.1) # 10%
                if ref_bonus > 0:
                    c.execute("UPDATE tg_users SET balance = balance + ?, ref_earnings = ref_earnings + ? WHERE tg_id=?", (ref_bonus, ref_bonus, ref_id))
                    c.execute(
                        "INSERT INTO txns(ts, tg_id, amount, reason) VALUES(?,?,?,?)",
                        (int(time.time()), ref_id, ref_bonus, f"Hoa hồng giới thiệu"),
                    )
                    try:
                        import asyncio
                        from .bot import manager, vnd
                        if manager.running:
                            asyncio.create_task(manager.bot.send_message(ref_id, f"🎁 <b>Hoa hồng giới thiệu!</b>\nBạn vừa nhận được <b>{vnd(ref_bonus)}</b> từ lượt nạp của bạn bè!", parse_mode="HTML"))
                    except: pass
        c.commit()

def set_sub_until(tg_id: int, epoch: int) -> None:
    with _lock:
        c = get_conn()
        c.execute("UPDATE tg_users SET sub_until=? WHERE tg_id=?", (epoch, tg_id))
        c.commit()

def reset_user(tg_id: int) -> None:
    with _lock:
        c = get_conn()
        c.execute("UPDATE tg_users SET balance=0, sub_until=0, trial_activated=0 WHERE tg_id=?", (tg_id,))
        c.commit()

def activate_trial(tg_id: int, days: int) -> bool:
    with _lock:
        c = get_conn()
        user = c.execute("SELECT trial_activated, sub_until FROM tg_users WHERE tg_id=?", (tg_id,)).fetchone()
        if not user or user["trial_activated"]:
            return False
        base = max(int(time.time()), user["sub_until"] or 0)
        c.execute("UPDATE tg_users SET trial_activated=1, sub_until=? WHERE tg_id=?", (base + days * 86400, tg_id))
        c.commit()
    return True

# --- GIFTCODES ---
def generate_code(amount: int, prefix: str = "CODE") -> str:
    import random
    import string
    with _lock:
        c = get_conn()
        while True:
            random_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            code = f"{prefix}-{random_str}"
            exists = c.execute("SELECT id FROM giftcodes WHERE code=?", (code,)).fetchone()
            if not exists:
                c.execute("INSERT INTO giftcodes(code, amount, created_at) VALUES(?, ?, ?)", (code, amount, int(time.time())))
                c.commit()
                return code

def get_unused_code(amount: int) -> str:
    with _lock:
        c = get_conn()
        row = c.execute("SELECT code FROM giftcodes WHERE amount=? AND is_used=0 LIMIT 1", (amount,)).fetchone()
        if row:
            return row["code"]
    return generate_code(amount)

def get_code_info(code: str) -> Optional[sqlite3.Row]:
    return get_conn().execute("SELECT * FROM giftcodes WHERE code=?", (code,)).fetchone()

def use_code(code: str, tg_id: int) -> tuple[bool, int]:
    with _lock:
        c = get_conn()
        row = c.execute("SELECT amount, is_used FROM giftcodes WHERE code=?", (code,)).fetchone()
        if not row: return False, 0
        if row["is_used"]: return False, 0
        amount = row["amount"]
        c.execute("UPDATE giftcodes SET is_used=1, used_by=?, used_at=? WHERE code=?", (tg_id, int(time.time()), code))
        c.commit()
        return True, amount

def get_code_history() -> list:
    return get_conn().execute("SELECT * FROM giftcodes ORDER BY created_at DESC LIMIT 500").fetchall()

# --- FB WATCHES (Live/Die) ---
def add_watch(tg_id: int, uid: str, note: str, price: int, expire_at: int) -> int:
    with _lock:
        c = get_conn()
        cur = c.execute(
            "INSERT INTO watches(tg_id, uid, note, price, expire_at, created_at, active) "
            "VALUES(?,?,?,?,?,?,1)",
            (tg_id, uid, note, price, expire_at, int(time.time())),
        )
        c.commit()
        return cur.lastrowid

def update_watch_status(watch_id: int, status: str, avatar_url: str) -> None:
    with _lock:
        c = get_conn()
        c.execute(
            "UPDATE watches SET last_status=?, avatar_url=?, last_checked=? WHERE id=?",
            (status, avatar_url, int(time.time()), watch_id),
        )
        c.commit()

def deactivate_watch(watch_id: int) -> None:
    with _lock:
        c = get_conn()
        c.execute("UPDATE watches SET active=0 WHERE id=?", (watch_id,))
        c.commit()

def remove_watch(tg_id: int, uid: str) -> int:
    with _lock:
        c = get_conn()
        cur = c.execute("DELETE FROM watches WHERE tg_id=? AND uid=?", (tg_id, uid))
        c.commit()
        return cur.rowcount

def user_watches(tg_id: int, only_active: bool = True) -> list:
    q = "SELECT * FROM watches WHERE tg_id=?"
    if only_active:
        q += " AND active=1"
    q += " ORDER BY created_at DESC"
    return get_conn().execute(q, (tg_id,)).fetchall()

def active_watches() -> list:
    return get_conn().execute("SELECT * FROM watches WHERE active=1").fetchall()

def all_watches() -> list:
    return get_conn().execute(
        "SELECT w.*, u.username, u.name FROM watches w "
        "LEFT JOIN tg_users u ON u.tg_id = w.tg_id ORDER BY w.created_at DESC"
    ).fetchall()

# --- LOGS ---
def add_log(kind: str, message: str, tg_id: int = 0, uid: str = "") -> None:
    with _lock:
        c = get_conn()
        c.execute(
            "INSERT INTO logs(ts, tg_id, uid, kind, message) VALUES(?,?,?,?,?)",
            (int(time.time()), tg_id, uid, kind, message),
        )
        c.commit()

def recent_logs(limit: int = 100) -> list:
    return get_conn().execute("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

# --- TIKTOK ACCOUNT TRACKS ---
def add_track(tg_user_id, tg_username, tiktok_username, followers=0, following=0, videos=0, zalo_user_id="", avatar_url=""):
    now = int(time.time())
    with _lock:
        c = get_conn()
        if zalo_user_id:
            r = c.execute("SELECT id FROM tracks WHERE zalo_user_id=? AND tiktok_username=? AND active=1", (zalo_user_id, tiktok_username)).fetchone()
        else:
            r = c.execute("SELECT id FROM tracks WHERE tg_user_id=? AND tiktok_username=? AND active=1", (tg_user_id, tiktok_username)).fetchone()
        if r: return -1
        cur = c.execute(
            "INSERT INTO tracks(tg_user_id,tg_username,zalo_user_id,tiktok_username,last_followers,last_following,last_videos,last_checked,created_at,avatar_url) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (tg_user_id, tg_username, zalo_user_id, tiktok_username, followers, following, videos, now, now, avatar_url))
        c.commit()
        return cur.lastrowid

def remove_track(tg_user_id, tiktok_username, zalo_user_id=""):
    with _lock:
        c = get_conn()
        if zalo_user_id:
            cur = c.execute("UPDATE tracks SET active=0 WHERE zalo_user_id=? AND tiktok_username=? AND active=1", (zalo_user_id, tiktok_username))
        else:
            cur = c.execute("UPDATE tracks SET active=0 WHERE tg_user_id=? AND tiktok_username=? AND active=1", (tg_user_id, tiktok_username))
        c.commit()
        return cur.rowcount > 0

def remove_track_by_id(track_id):
    with _lock:
        c = get_conn()
        cur = c.execute("UPDATE tracks SET active=0 WHERE id=?", (track_id,))
        c.commit()
        return cur.rowcount > 0

def user_tracks(tg_user_id, zalo_user_id=""):
    if zalo_user_id:
        return [dict(r) for r in get_conn().execute("SELECT * FROM tracks WHERE zalo_user_id=? AND active=1 ORDER BY created_at DESC", (zalo_user_id,)).fetchall()]
    return [dict(r) for r in get_conn().execute("SELECT * FROM tracks WHERE tg_user_id=? AND active=1 ORDER BY created_at DESC", (tg_user_id,)).fetchall()]

def all_active_tracks():
    return [dict(r) for r in get_conn().execute("SELECT * FROM tracks WHERE active=1 ORDER BY last_checked ASC").fetchall()]

def all_tracks():
    return [dict(r) for r in get_conn().execute("SELECT * FROM tracks ORDER BY created_at DESC").fetchall()]

def update_track_stats(track_id, followers, following, videos, video_id=""):
    with _lock:
        c = get_conn()
        c.execute("UPDATE tracks SET last_followers=?,last_following=?,last_videos=?,last_video_id=?,last_checked=? WHERE id=?",
                    (followers, following, videos, video_id, int(time.time()), track_id))
        c.commit()

# --- TIKTOK VIDEO TRACKS ---
def add_video_track(tg_user_id, tg_username, video_url, video_id, tiktok_username,
                    video_desc, cover_url, check_interval=3600,
                    plays=0, likes=0, comments=0, shares=0, favorites=0, zalo_user_id=""):
    now = int(time.time())
    with _lock:
        c = get_conn()
        if zalo_user_id:
            r = c.execute("SELECT id FROM video_tracks WHERE zalo_user_id=? AND video_id=? AND active=1", (zalo_user_id, video_id)).fetchone()
        else:
            r = c.execute("SELECT id FROM video_tracks WHERE tg_user_id=? AND video_id=? AND active=1", (tg_user_id, video_id)).fetchone()
        if r: return -1
        cur = c.execute(
            """INSERT INTO video_tracks(tg_user_id,tg_username,zalo_user_id,video_url,video_id,tiktok_username,
               video_desc,cover_url,check_interval,last_plays,last_likes,last_comments,last_shares,last_favorites,
               last_checked,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (tg_user_id, tg_username, zalo_user_id, video_url, video_id, tiktok_username,
             video_desc, cover_url, check_interval, plays, likes, comments, shares, favorites, now, now))
        c.commit()
        return cur.lastrowid

def remove_video_track(tg_user_id, video_id, zalo_user_id=""):
    with _lock:
        c = get_conn()
        if zalo_user_id:
            cur = c.execute("UPDATE video_tracks SET active=0 WHERE zalo_user_id=? AND video_id=? AND active=1", (zalo_user_id, video_id))
        else:
            cur = c.execute("UPDATE video_tracks SET active=0 WHERE tg_user_id=? AND video_id=? AND active=1", (tg_user_id, video_id))
        c.commit()
        return cur.rowcount > 0

def remove_video_track_by_id(track_id):
    with _lock:
        c = get_conn()
        cur = c.execute("UPDATE video_tracks SET active=0 WHERE id=?", (track_id,))
        c.commit()
        return cur.rowcount > 0

def user_video_tracks(tg_user_id, zalo_user_id=""):
    if zalo_user_id:
        return [dict(r) for r in get_conn().execute("SELECT * FROM video_tracks WHERE zalo_user_id=? AND active=1 ORDER BY created_at DESC", (zalo_user_id,)).fetchall()]
    return [dict(r) for r in get_conn().execute("SELECT * FROM video_tracks WHERE tg_user_id=? AND active=1 ORDER BY created_at DESC", (tg_user_id,)).fetchall()]

def all_active_video_tracks():
    return [dict(r) for r in get_conn().execute("SELECT * FROM video_tracks WHERE active=1 ORDER BY last_checked ASC").fetchall()]

def all_video_tracks():
    return [dict(r) for r in get_conn().execute("SELECT * FROM video_tracks ORDER BY created_at DESC").fetchall()]

def update_video_track_stats(track_id, plays, likes, comments, shares, favorites):
    with _lock:
        c = get_conn()
        c.execute("UPDATE video_tracks SET last_plays=?,last_likes=?,last_comments=?,last_shares=?,last_favorites=?,last_checked=? WHERE id=?",
                    (plays, likes, comments, shares, favorites, int(time.time()), track_id))
        c.commit()

# --- IG ACCOUNT TRACKS ---
def add_ig_track(tg_user_id, tg_username, ig_username, followers=0, following=0, posts=0, zalo_user_id="", avatar_url=""):
    now = int(time.time())
    with _lock:
        c = get_conn()
        if zalo_user_id:
            r = c.execute("SELECT id FROM ig_tracks WHERE zalo_user_id=? AND ig_username=? AND active=1", (zalo_user_id, ig_username)).fetchone()
        else:
            r = c.execute("SELECT id FROM ig_tracks WHERE tg_user_id=? AND ig_username=? AND active=1", (tg_user_id, ig_username)).fetchone()
        if r: return -1
        cur = c.execute(
            "INSERT INTO ig_tracks(tg_user_id,tg_username,zalo_user_id,ig_username,last_followers,last_following,last_posts,last_checked,created_at,avatar_url) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (tg_user_id, tg_username, zalo_user_id, ig_username, followers, following, posts, now, now, avatar_url))
        c.commit()
        return cur.lastrowid

def remove_ig_track(tg_user_id, ig_username, zalo_user_id=""):
    with _lock:
        c = get_conn()
        if zalo_user_id:
            cur = c.execute("UPDATE ig_tracks SET active=0 WHERE zalo_user_id=? AND ig_username=? AND active=1", (zalo_user_id, ig_username))
        else:
            cur = c.execute("UPDATE ig_tracks SET active=0 WHERE tg_user_id=? AND ig_username=? AND active=1", (tg_user_id, ig_username))
        c.commit()
        return cur.rowcount > 0

def user_ig_tracks(tg_user_id, zalo_user_id=""):
    if zalo_user_id:
        return [dict(r) for r in get_conn().execute("SELECT * FROM ig_tracks WHERE zalo_user_id=? AND active=1 ORDER BY created_at DESC", (zalo_user_id,)).fetchall()]
    return [dict(r) for r in get_conn().execute("SELECT * FROM ig_tracks WHERE tg_user_id=? AND active=1 ORDER BY created_at DESC", (tg_user_id,)).fetchall()]

def all_active_ig_tracks():
    return [dict(r) for r in get_conn().execute("SELECT * FROM ig_tracks WHERE active=1 ORDER BY last_checked ASC").fetchall()]

def update_ig_track_stats(track_id, followers, following, posts):
    with _lock:
        c = get_conn()
        c.execute("UPDATE ig_tracks SET last_followers=?,last_following=?,last_posts=?,last_checked=? WHERE id=?",
                    (followers, following, posts, int(time.time()), track_id))
        c.commit()

# --- IG VIDEO TRACKS ---
def add_ig_video_track(tg_user_id, tg_username, post_url, post_id, ig_username,
                       post_desc, cover_url, check_interval=3600,
                       likes=0, comments=0, views=0, zalo_user_id=""):
    now = int(time.time())
    with _lock:
        c = get_conn()
        if zalo_user_id:
            r = c.execute("SELECT id FROM ig_video_tracks WHERE zalo_user_id=? AND post_id=? AND active=1", (zalo_user_id, post_id)).fetchone()
        else:
            r = c.execute("SELECT id FROM ig_video_tracks WHERE tg_user_id=? AND post_id=? AND active=1", (tg_user_id, post_id)).fetchone()
        if r: return -1
        cur = c.execute(
            """INSERT INTO ig_video_tracks(tg_user_id,tg_username,zalo_user_id,post_url,post_id,ig_username,
               post_desc,cover_url,check_interval,last_likes,last_comments,last_views,
               last_checked,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (tg_user_id, tg_username, zalo_user_id, post_url, post_id, ig_username,
             post_desc, cover_url, check_interval, likes, comments, views, now, now))
        c.commit()
        return cur.lastrowid

def remove_ig_video_track(tg_user_id, post_id, zalo_user_id=""):
    with _lock:
        c = get_conn()
        if zalo_user_id:
            cur = c.execute("UPDATE ig_video_tracks SET active=0 WHERE zalo_user_id=? AND post_id=? AND active=1", (zalo_user_id, post_id))
        else:
            cur = c.execute("UPDATE ig_video_tracks SET active=0 WHERE tg_user_id=? AND post_id=? AND active=1", (tg_user_id, post_id))
        c.commit()
        return cur.rowcount > 0

def user_ig_video_tracks(tg_user_id, zalo_user_id=""):
    if zalo_user_id:
        return [dict(r) for r in get_conn().execute("SELECT * FROM ig_video_tracks WHERE zalo_user_id=? AND active=1 ORDER BY created_at DESC", (zalo_user_id,)).fetchall()]
    return [dict(r) for r in get_conn().execute("SELECT * FROM ig_video_tracks WHERE tg_user_id=? AND active=1 ORDER BY created_at DESC", (tg_user_id,)).fetchall()]

def all_active_ig_video_tracks() -> list:
    return [dict(r) for r in get_conn().execute("SELECT * FROM ig_video_tracks WHERE active=1 ORDER BY last_checked ASC").fetchall()]

def update_ig_video_track_stats(track_id, likes, comments, views):
    with _lock:
        c = get_conn()
        c.execute("UPDATE ig_video_tracks SET last_likes=?,last_comments=?,last_views=?,last_checked=? WHERE id=?",
                    (likes, comments, views, int(time.time()), track_id))
        c.commit()

# --- FB TRACKS (Tiktok checker style) ---
def add_fb_track(tg_user_id: int, tg_username: str, fb_uid: str, last_status: str, avatar_url: str, zalo_user_id: str = "") -> int:
    with _lock:
        c = get_conn()
        if zalo_user_id:
            r = c.execute("SELECT id FROM fb_tracks WHERE zalo_user_id=? AND fb_uid=?", (zalo_user_id, fb_uid)).fetchone()
        else:
            r = c.execute("SELECT id FROM fb_tracks WHERE tg_user_id=? AND fb_uid=?", (tg_user_id, fb_uid)).fetchone()
        if r: return -1
        
        now = int(time.time())
        cur = c.execute("""
            INSERT INTO fb_tracks (tg_user_id, tg_username, zalo_user_id, fb_uid, last_status, avatar_url, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (tg_user_id, tg_username, zalo_user_id, fb_uid, last_status, avatar_url, now))
        c.commit()
        return cur.lastrowid

def remove_fb_track(tg_user_id: int, fb_uid: str, zalo_user_id: str = "") -> bool:
    with _lock:
        c = get_conn()
        if zalo_user_id:
            cur = c.execute("DELETE FROM fb_tracks WHERE zalo_user_id=? AND fb_uid=?", (zalo_user_id, fb_uid))
        else:
            cur = c.execute("DELETE FROM fb_tracks WHERE tg_user_id=? AND fb_uid=?", (tg_user_id, fb_uid))
        c.commit()
        return cur.rowcount > 0

def user_fb_tracks(tg_user_id: int, zalo_user_id: str = "") -> list:
    if zalo_user_id:
        return [dict(r) for r in get_conn().execute("SELECT * FROM fb_tracks WHERE zalo_user_id=? ORDER BY created_at DESC", (zalo_user_id,)).fetchall()]
    return [dict(r) for r in get_conn().execute("SELECT * FROM fb_tracks WHERE tg_user_id=? ORDER BY created_at DESC", (tg_user_id,)).fetchall()]

def all_active_fb_tracks() -> list:
    return [dict(r) for r in get_conn().execute("SELECT * FROM fb_tracks").fetchall()]

def update_fb_track_status(track_id: int, status: str, avatar_url: str):
    with _lock:
        c = get_conn()
        c.execute("""
            UPDATE fb_tracks
            SET last_status=?, avatar_url=?
            WHERE id=?
        """, (status, avatar_url, track_id))
        c.commit()


# ─── FB POST TRACKS ───────────────────────────────────────────

def add_fb_post_track(tg_user_id: int, tg_username: str, post_url: str, post_id: str, fb_username: str,
                     post_desc: str, cover_url: str, likes: int, comments: int, shares: int, zalo_user_id: str = "", interval: int = 1800) -> int:
    with _lock:
        c = get_conn()
        cur = c.execute("""
            SELECT id FROM fb_post_tracks 
            WHERE (tg_user_id = ? OR (zalo_user_id != '' AND zalo_user_id = ?)) AND post_id = ?
        """, (tg_user_id, zalo_user_id, post_id))
        if cur.fetchone():
            return -1
        cur = c.execute("""
            INSERT INTO fb_post_tracks 
            (tg_user_id, tg_username, zalo_user_id, post_url, post_id, fb_username, post_desc, cover_url, check_interval, last_likes, last_comments, last_shares, last_checked, created_at, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 1)
        """, (tg_user_id, tg_username, zalo_user_id, post_url, post_id, fb_username, post_desc, cover_url, interval, likes, comments, shares, int(time.time())))
        c.commit()
        return cur.lastrowid

def remove_fb_post_track(tg_user_id: int, post_id: str, zalo_user_id: str = "") -> bool:
    with _lock:
        c = get_conn()
        if zalo_user_id:
            cur = c.execute("DELETE FROM fb_post_tracks WHERE zalo_user_id = ? AND post_id = ?", (zalo_user_id, post_id))
        else:
            cur = c.execute("DELETE FROM fb_post_tracks WHERE tg_user_id = ? AND post_id = ?", (tg_user_id, post_id))
        c.commit()
        return cur.rowcount > 0

def user_fb_post_tracks(tg_user_id: int, zalo_user_id: str = "") -> list:
    if zalo_user_id:
        return [dict(r) for r in get_conn().execute("SELECT * FROM fb_post_tracks WHERE zalo_user_id = ? ORDER BY id DESC", (zalo_user_id,)).fetchall()]
    return [dict(r) for r in get_conn().execute("SELECT * FROM fb_post_tracks WHERE tg_user_id = ? ORDER BY id DESC", (tg_user_id,)).fetchall()]

def all_active_fb_post_tracks() -> list:
    return [dict(r) for r in get_conn().execute("SELECT * FROM fb_post_tracks WHERE active = 1").fetchall()]

def update_fb_post_track_stats(track_id: int, likes: int, comments: int, shares: int):
    with _lock:
        c = get_conn()
        c.execute("""
            UPDATE fb_post_tracks
            SET last_likes = ?, last_comments = ?, last_shares = ?, last_checked = ?
            WHERE id = ?
        """, (likes, comments, shares, int(time.time()), track_id))
        c.commit()

def deactivate_fb_post_track(track_id: int):
    with _lock:
        c = get_conn()
        c.execute("UPDATE fb_post_tracks SET active=0 WHERE id=?", (track_id,))
        c.commit()

def delete_user(tg_id: int) -> None:
    with _lock:
        c = get_conn()
        c.execute("DELETE FROM tg_users WHERE tg_id=?", (tg_id,))
        c.execute("DELETE FROM tracks WHERE tg_user_id=?", (tg_id,))
        c.execute("DELETE FROM ig_tracks WHERE tg_user_id=?", (tg_id,))
        c.execute("DELETE FROM fb_post_tracks WHERE tg_user_id=?", (tg_id,))
        c.execute("DELETE FROM txns WHERE tg_id=?", (tg_id,))
        c.commit()
