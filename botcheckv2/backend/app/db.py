import threading
import time
from typing import Optional, Any
import os
import psycopg2
from psycopg2.extras import DictCursor
from dotenv import load_dotenv
from . import config

load_dotenv()
SUPABASE_URL = os.environ.get('SUPABASE_DB_URL')

class PgCursor:
    def __init__(self, conn):
        self.conn = conn
        self.cur = conn.cursor(cursor_factory=DictCursor)
        self.lastrowid = None
        self.rowcount = 0
    def execute(self, sql, params=()):
        sql = sql.replace('?', '%s')
        self.cur.execute(sql, params)
        self.rowcount = self.cur.rowcount
        if sql.strip().upper().startswith('INSERT') and 'RETURNING id' in sql:
            try:
                res = self.cur.fetchone()
                if res:
                    self.lastrowid = res['id']
            except psycopg2.ProgrammingError:
                pass
        return self
    def executescript(self, sql):
        sql = sql.replace('BIGINT PRIMARY KEY AUTOINCREMENT', 'BIGSERIAL PRIMARY KEY')
        sql = sql.replace('BIGINT PRIMARY KEY', 'BIGSERIAL PRIMARY KEY')
        sql = sql.replace('PRAGMA journal_mode=WAL;', '')
        self.cur.execute(sql)
        return self
    def fetchone(self):
        return self.cur.fetchone()
    def fetchall(self):
        return self.cur.fetchall()

class PgConnection:
    def __init__(self):
        self.conn = psycopg2.connect(SUPABASE_URL, connect_timeout=10)
        self.conn.autocommit = True
        self.row_factory = None
    def check_conn(self):
        try:
            with self.conn.cursor() as cur:
                cur.execute('SELECT 1')
        except:
            self.conn = psycopg2.connect(SUPABASE_URL, connect_timeout=10)
            self.conn.autocommit = True
    def execute(self, sql, params=()):
        self.check_conn()
        return PgCursor(self.conn).execute(sql, params)
    def executescript(self, sql):
        self.check_conn()
        return PgCursor(self.conn).executescript(sql)
    def commit(self):
        # self.conn.commit()
        pass

_lock = threading.Lock()
_pg_conn = None

import sqlite3

class SqliteCursor:
    def __init__(self, conn):
        self.conn = conn
        self.cur = conn.cursor()
        self.lastrowid = None
        self.rowcount = 0
    def execute(self, sql, params=()):
        try:
            self.cur.execute(sql, params)
        except sqlite3.OperationalError as e:
            if "RETURNING" in str(e):
                sql = sql.replace("RETURNING id", "")
                self.cur.execute(sql, params)
            else:
                raise
        self.rowcount = self.cur.rowcount
        self.lastrowid = self.cur.lastrowid
        if sql.strip().upper().startswith('INSERT') and 'RETURNING id' in sql:
            try:
                res = self.cur.fetchone()
                if res:
                    self.lastrowid = res['id']
            except Exception:
                pass
        return self
    def executescript(self, sql):
        sql = sql.replace('BIGINT PRIMARY KEY AUTOINCREMENT', 'INTEGER PRIMARY KEY AUTOINCREMENT')
        sql = sql.replace('BIGSERIAL PRIMARY KEY', 'INTEGER PRIMARY KEY AUTOINCREMENT')
        self.cur.executescript(sql)
        return self
    def fetchone(self):
        return self.cur.fetchone()
    def fetchall(self):
        return self.cur.fetchall()

class SqliteConnection:
    def __init__(self):
        self.conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
    def check_conn(self):
        pass
    def execute(self, sql, params=()):
        return SqliteCursor(self.conn).execute(sql, params)
    def executescript(self, sql):
        return SqliteCursor(self.conn).executescript(sql)
    def commit(self):
        self.conn.commit()

def get_conn():
    global _pg_conn
    if _pg_conn is None:
        if SUPABASE_URL:
            try:
                _pg_conn = PgConnection()
                _pg_conn.check_conn()
            except Exception as e:
                print(f"[!] PostgreSQL connect error: {e}")
                print("[!] Falling back to local SQLite database...")
                _pg_conn = SqliteConnection()
        else:
            print("[!] SUPABASE_DB_URL not found, falling back to local SQLite database...")
            _pg_conn = SqliteConnection()
    return _pg_conn

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
                tg_id        BIGINT PRIMARY KEY,
                username     TEXT,
                name         TEXT,
                balance      BIGINT DEFAULT 0,
                sub_until    BIGINT DEFAULT 0,
                created_at   BIGINT,
                is_blocked   BIGINT DEFAULT 0,
                trial_activated BIGINT DEFAULT 0,
                referrer_id  BIGINT DEFAULT 0,
                ref_earnings BIGINT DEFAULT 0,
                expired_notified BIGINT DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS watches (
                id           BIGINT PRIMARY KEY AUTOINCREMENT,
                tg_id        BIGINT,
                uid          TEXT,
                note         TEXT,
                price        BIGINT DEFAULT 0,
                expire_at    BIGINT DEFAULT 0,
                last_status  TEXT,
                avatar_url   TEXT,
                last_checked BIGINT DEFAULT 0,
                active       BIGINT DEFAULT 1,
                created_at   BIGINT
            );

            CREATE TABLE IF NOT EXISTS logs (
                id        BIGINT PRIMARY KEY AUTOINCREMENT,
                ts        BIGINT,
                tg_id     BIGINT,
                uid       TEXT,
                kind      TEXT,
                message   TEXT
            );

            CREATE TABLE IF NOT EXISTS txns (
                id        BIGINT PRIMARY KEY AUTOINCREMENT,
                ts        BIGINT,
                tg_id     BIGINT,
                amount    BIGINT,
                reason    TEXT
            );
            
            -- YOUTUBE TABLES --

            CREATE TABLE IF NOT EXISTS yt_tracks (
                id              BIGINT PRIMARY KEY AUTOINCREMENT,
                tg_user_id      BIGINT DEFAULT 0,
                tg_username     TEXT    DEFAULT '',
                zalo_user_id    TEXT    DEFAULT '',
                yt_username     TEXT    NOT NULL,
                last_subscribers BIGINT DEFAULT 0,
                last_videos     BIGINT DEFAULT 0,
                last_checked    BIGINT DEFAULT 0,
                created_at      BIGINT NOT NULL,
                active          BIGINT DEFAULT 1,
                avatar_url      TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS yt_video_tracks (
                id              BIGINT PRIMARY KEY AUTOINCREMENT,
                tg_user_id      BIGINT DEFAULT 0,
                tg_username     TEXT    DEFAULT '',
                zalo_user_id    TEXT    DEFAULT '',
                video_url       TEXT    NOT NULL,
                video_id        TEXT    NOT NULL,
                yt_username     TEXT    DEFAULT '',
                video_desc      TEXT    DEFAULT '',
                cover_url       TEXT    DEFAULT '',
                check_interval  BIGINT DEFAULT 3600,
                last_views      BIGINT DEFAULT 0,
                last_likes      BIGINT DEFAULT 0,
                last_comments   BIGINT DEFAULT 0,
                last_checked    BIGINT DEFAULT 0,
                created_at      BIGINT NOT NULL,
                active          BIGINT DEFAULT 1
            );

            -- TIKTOK & IG TABLES --
            CREATE TABLE IF NOT EXISTS tracks (
                id              BIGINT PRIMARY KEY AUTOINCREMENT,
                tg_user_id      BIGINT DEFAULT 0,
                tg_username     TEXT    DEFAULT '',
                zalo_user_id    TEXT    DEFAULT '',
                tiktok_username TEXT    NOT NULL,
                last_followers  BIGINT DEFAULT 0,
                last_following  BIGINT DEFAULT 0,
                last_videos     BIGINT DEFAULT 0,
                last_video_id   TEXT    DEFAULT '',
                last_checked    BIGINT DEFAULT 0,
                created_at      BIGINT NOT NULL,
                active          BIGINT DEFAULT 1,
                avatar_url      TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS video_tracks (
                id              BIGINT PRIMARY KEY AUTOINCREMENT,
                tg_user_id      BIGINT DEFAULT 0,
                tg_username     TEXT    DEFAULT '',
                zalo_user_id    TEXT    DEFAULT '',
                video_url       TEXT    NOT NULL,
                video_id        TEXT    DEFAULT '',
                tiktok_username TEXT    DEFAULT '',
                video_desc      TEXT    DEFAULT '',
                cover_url       TEXT    DEFAULT '',
                check_interval  BIGINT DEFAULT 3600,
                last_plays      BIGINT DEFAULT 0,
                last_likes      BIGINT DEFAULT 0,
                last_comments   BIGINT DEFAULT 0,
                last_shares     BIGINT DEFAULT 0,
                last_favorites  BIGINT DEFAULT 0,
                last_checked    BIGINT DEFAULT 0,
                created_at      BIGINT NOT NULL,
                active          BIGINT DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS ig_tracks (
                id              BIGINT PRIMARY KEY AUTOINCREMENT,
                tg_user_id      BIGINT DEFAULT 0,
                tg_username     TEXT    DEFAULT '',
                zalo_user_id    TEXT    DEFAULT '',
                ig_username     TEXT    NOT NULL,
                last_followers  BIGINT DEFAULT 0,
                last_following  BIGINT DEFAULT 0,
                last_posts      BIGINT DEFAULT 0,
                last_checked    BIGINT DEFAULT 0,
                created_at      BIGINT NOT NULL,
                active          BIGINT DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS ig_video_tracks (
                id              BIGINT PRIMARY KEY AUTOINCREMENT,
                tg_user_id      BIGINT DEFAULT 0,
                tg_username     TEXT    DEFAULT '',
                zalo_user_id    TEXT    DEFAULT '',
                post_url        TEXT    NOT NULL,
                post_id         TEXT    DEFAULT '',
                ig_username     TEXT    DEFAULT '',
                post_desc       TEXT    DEFAULT '',
                cover_url       TEXT    DEFAULT '',
                check_interval  BIGINT DEFAULT 3600,
                last_likes      BIGINT DEFAULT 0,
                last_comments   BIGINT DEFAULT 0,
                last_views      BIGINT DEFAULT 0,
                last_checked    BIGINT DEFAULT 0,
                created_at      BIGINT NOT NULL,
                active          BIGINT DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS fb_tracks (
                id              BIGINT PRIMARY KEY AUTOINCREMENT,
                tg_user_id      BIGINT DEFAULT 0,
                tg_username     TEXT    DEFAULT '',
                zalo_user_id    TEXT    DEFAULT '',
                fb_uid          TEXT    NOT NULL,
                last_status     TEXT    DEFAULT '',
                avatar_url      TEXT    DEFAULT '',
                created_at      BIGINT NOT NULL,
                active          BIGINT DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS fb_post_tracks (
                id              BIGINT PRIMARY KEY AUTOINCREMENT,
                tg_user_id      BIGINT DEFAULT 0,
                tg_username     TEXT    DEFAULT '',
                post_url        TEXT    NOT NULL,
                post_id         TEXT    NOT NULL,
                author_name     TEXT    DEFAULT '',
                post_desc       TEXT    DEFAULT '',
                last_likes      BIGINT DEFAULT 0,
                last_comments   BIGINT DEFAULT 0,
                last_shares     BIGINT DEFAULT 0,
                check_interval  BIGINT DEFAULT 3600,
                last_checked    BIGINT DEFAULT 0,
                active          BIGINT DEFAULT 1,
                last_spike_alert_at BIGINT DEFAULT 0
            );
            
            CREATE TABLE IF NOT EXISTS proxies (
                id              BIGINT PRIMARY KEY AUTOINCREMENT,
                proxy_url       TEXT UNIQUE NOT NULL,
                fail_count      BIGINT DEFAULT 0,
                is_active       BIGINT DEFAULT 1,
                created_at      BIGINT
            );

            CREATE TABLE IF NOT EXISTS track_history (
                id              BIGINT PRIMARY KEY AUTOINCREMENT,
                track_id        BIGINT NOT NULL,
                platform        TEXT NOT NULL,
                track_type      TEXT NOT NULL,
                stat_value      BIGINT DEFAULT 0,
                created_at      BIGINT
            );
            
            CREATE TABLE IF NOT EXISTS giftcodes (
                id              BIGINT PRIMARY KEY AUTOINCREMENT,
                code            TEXT    NOT NULL UNIQUE,
                amount          BIGINT NOT NULL,
                is_used         BIGINT DEFAULT 0,
                used_by         BIGINT DEFAULT 0,
                created_at      BIGINT NOT NULL,
                used_at         BIGINT DEFAULT 0,
                max_uses        BIGINT DEFAULT 1,
                current_uses    BIGINT DEFAULT 0,
                expire_at       BIGINT DEFAULT 0
            );
            
            CREATE TABLE IF NOT EXISTS giftcode_uses (
                code            TEXT NOT NULL,
                tg_id           BIGINT NOT NULL,
                used_at         BIGINT NOT NULL,
                PRIMARY KEY (code, tg_id)
            );
            
            CREATE TABLE IF NOT EXISTS saved_codes (
                tg_id           BIGINT NOT NULL,
                code            TEXT NOT NULL,
                saved_at        BIGINT NOT NULL,
                PRIMARY KEY (tg_id, code)
            );
            """
        )
        # Keys that MUST be force-updated on every restart
        # (to ensure hardcoded tokens always take effect, even if value is empty)
        _force_keys = {
            "bot_token", "setup_done", "admin_bot_token",
            "admin_tg_id", "zalo_bot_token", "web_domain",
        }
        for k, v in config.DEFAULT_SETTINGS.items():
            if k in _force_keys:  # Always overwrite these keys (even empty string)
                c.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (k, v),
                )
            else:
                c.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?) "
                    "ON CONFLICT DO NOTHING",
                    (k, v),
                )
        c.commit()
        
def migrate_db():
    c = get_conn()
    with _lock:
        for sql in [
            "ALTER TABLE tg_users ADD COLUMN vip_level BIGINT DEFAULT 0",
            "ALTER TABLE tg_users ADD COLUMN auto_renew BIGINT DEFAULT 1",
            "ALTER TABLE tg_users ADD COLUMN role TEXT DEFAULT 'user'",
            "ALTER TABLE tg_users ADD COLUMN total_topup BIGINT DEFAULT 0",
            "ALTER TABLE tg_users ADD COLUMN daily_checks BIGINT DEFAULT 0",
            "ALTER TABLE tg_users ADD COLUMN last_check_date TEXT DEFAULT ''",
            "ALTER TABLE tracks ADD COLUMN zalo_user_id TEXT DEFAULT ''",
            "",
            "ALTER TABLE ig_tracks ADD COLUMN avatar_url TEXT DEFAULT ''",
            "ALTER TABLE video_tracks ADD COLUMN zalo_user_id TEXT DEFAULT ''",
            "ALTER TABLE ig_video_tracks ADD COLUMN last_spike_alert_at BIGINT DEFAULT 0",
            "ALTER TABLE fb_post_tracks ADD COLUMN last_spike_alert_at BIGINT DEFAULT 0",
            "CREATE TABLE IF NOT EXISTS yt_tracks (id BIGSERIAL PRIMARY KEY, tg_user_id BIGINT DEFAULT 0, tg_username TEXT DEFAULT '', zalo_user_id TEXT DEFAULT '', yt_username TEXT NOT NULL, last_subscribers BIGINT DEFAULT 0, last_videos BIGINT DEFAULT 0, last_checked BIGINT DEFAULT 0, created_at BIGINT NOT NULL, active BIGINT DEFAULT 1, avatar_url TEXT DEFAULT '')",
            "CREATE TABLE IF NOT EXISTS yt_video_tracks (id BIGSERIAL PRIMARY KEY, tg_user_id BIGINT DEFAULT 0, tg_username TEXT DEFAULT '', zalo_user_id TEXT DEFAULT '', video_url TEXT NOT NULL, video_id TEXT NOT NULL, yt_username TEXT DEFAULT '', video_desc TEXT DEFAULT '', cover_url TEXT DEFAULT '', check_interval BIGINT DEFAULT 3600, last_views BIGINT DEFAULT 0, last_likes BIGINT DEFAULT 0, last_comments BIGINT DEFAULT 0, last_checked BIGINT DEFAULT 0, created_at BIGINT NOT NULL, active BIGINT DEFAULT 1)",
            "CREATE TABLE IF NOT EXISTS zalo_tracks (id BIGSERIAL PRIMARY KEY, tg_user_id BIGINT DEFAULT 0, tg_username TEXT DEFAULT '', zalo_user_id TEXT DEFAULT '', phone TEXT NOT NULL, name TEXT DEFAULT '', avatar TEXT DEFAULT '', status TEXT DEFAULT 'LIVE', last_checked BIGINT DEFAULT 0, created_at BIGINT NOT NULL, active BIGINT DEFAULT 1)",
            "CREATE TABLE IF NOT EXISTS proxies (id BIGINT PRIMARY KEY AUTOINCREMENT, proxy_url TEXT UNIQUE NOT NULL, fail_count BIGINT DEFAULT 0, is_active BIGINT DEFAULT 1, created_at BIGINT)",
            "CREATE TABLE IF NOT EXISTS track_history (id BIGINT PRIMARY KEY AUTOINCREMENT, track_id BIGINT NOT NULL, platform TEXT NOT NULL, track_type TEXT NOT NULL, stat_value BIGINT DEFAULT 0, created_at BIGINT)"
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

    # 7 days revenue & users chart
    chart_data = []
    for i in range(6, -1, -1):
        day_ts = today_start - i * 86400
        next_day_ts = day_ts + 86400
        day_str = time.strftime("%d/%m", time.localtime(day_ts))
        
        rev = c.execute("SELECT SUM(amount) as s FROM txns WHERE amount > 0 AND ts >= ? AND ts < ?", (day_ts, next_day_ts)).fetchone()["s"] or 0
        usr = c.execute("SELECT COUNT(*) as c FROM tg_users WHERE created_at >= ? AND created_at < ?", (day_ts, next_day_ts)).fetchone()["c"]
        chart_data.append({"date": day_str, "revenue": rev, "users": usr})

    return {
        "total_users": total_users,
        "new_users_today": new_users_today,
        "total_revenue": total_revenue,
        "revenue_today": revenue_today,
        "revenue_month": revenue_month,
        "chart_data": chart_data
    }

def all_settings() -> dict:
    rows = get_conn().execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}

# --- FB USER & BALANCE ---
def upsert_user(tg_id: int, username: str, name: str, referrer_id: int = 0) -> Any:
    with _lock:
        c = get_conn()
        c.execute(
            "INSERT INTO tg_users(tg_id, username, name, created_at, referrer_id) VALUES(?,?,?,?,?) "
            "ON CONFLICT(tg_id) DO UPDATE SET username=excluded.username, name=excluded.name",
            (tg_id, username, name, int(time.time()), referrer_id),
        )
        c.commit()
    return get_user(tg_id)

def get_user(tg_id: int) -> Optional[Any]:
    return get_conn().execute("SELECT * FROM tg_users WHERE tg_id=?", (tg_id,)).fetchone()

def list_users() -> list:
    return get_conn().execute(
        "SELECT t.*, (SELECT COUNT(*) FROM tg_users WHERE referrer_id = t.tg_id) as ref_count "
        "FROM tg_users t ORDER BY created_at DESC"
    ).fetchall()

def adjust_balance(tg_id: int, amount: int, reason: str) -> None:
    with _lock:
        c = get_conn()
        if amount > 0:
            c.execute("UPDATE tg_users SET balance = balance + ?, total_topup = total_topup + ? WHERE tg_id=?", (amount, amount, tg_id))
        else:
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
        
_magic_links = {}

def create_magic_link(tg_id: int) -> str:
    import secrets
    token = secrets.token_urlsafe(32)
    _magic_links[token] = {"tg_id": tg_id, "exp": int(time.time()) + 300}
    return token

def verify_magic_link(token: str) -> int:
    if token in _magic_links:
        data = _magic_links[token]
        if data["exp"] > time.time():
            return data["tg_id"]
        else:
            del _magic_links[token]
    return 0

def check_vip_upgrade(tg_id: int) -> tuple[bool, int, bool]:
    """Returns (upgraded, new_vip_level, is_lifetime)"""
    with _lock:
        c = get_conn()
        user = c.execute("SELECT total_topup, vip_level, sub_until FROM tg_users WHERE tg_id=?", (tg_id,)).fetchone()
        if not user: return False, 0, False
        
        total = user["total_topup"]
        current_vip = user["vip_level"]
        
        def _get_price(key: str) -> int:
            row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            try: return int(row["value"]) if row and row["value"] else 0
            except: return 0
            
        p1 = _get_price("vip1_price")
        p2 = _get_price("vip2_price")
        p3 = _get_price("vip3_price")
        plife = _get_price("vip_lifetime_price")
        
        new_vip = current_vip
        if p3 > 0 and total >= p3: new_vip = 3
        elif p2 > 0 and total >= p2 and new_vip < 2: new_vip = 2
        elif p1 > 0 and total >= p1 and new_vip < 1: new_vip = 1
        
        is_lifetime = False
        updates = []
        params = []
        if new_vip > current_vip:
            updates.append("vip_level=?")
            params.append(new_vip)
            
        if plife > 0 and total >= plife and user["sub_until"] < 9999999999:
            updates.append("sub_until=?")
            params.append(9999999999)
            is_lifetime = True
            
        if updates:
            params.append(tg_id)
            c.execute(f"UPDATE tg_users SET {', '.join(updates)} WHERE tg_id=?", tuple(params))
            c.commit()
            return True, new_vip, is_lifetime
            
        return False, current_vip, False

def check_daily_limit(tg_id: int) -> tuple[bool, str]:
    """Returns (can_check, error_message)"""
    with _lock:
        c = get_conn()
        user = c.execute("SELECT vip_level, daily_checks, last_check_date FROM tg_users WHERE tg_id=?", (tg_id,)).fetchone()
        if not user: return False, "Bạn chưa đăng ký tài khoản."
        
        today = time.strftime("%Y-%m-%d")
        if user["last_check_date"] != today:
            c.execute("UPDATE tg_users SET daily_checks=0, last_check_date=? WHERE tg_id=?", (today, tg_id))
            daily_checks = 0
        else:
            daily_checks = user["daily_checks"]
            
        vip = user["vip_level"]
        
        # Default limits if not set: VIP0=5, VIP1=50, VIP2=200, VIP3=1000
        row = c.execute("SELECT value FROM settings WHERE key=?", (f"vip{vip}_daily_check",)).fetchone()
        try: limit = int(row["value"]) if row and row["value"] else [5, 50, 200, 1000][vip if vip <= 3 else 3]
        except: limit = [5, 50, 200, 1000][vip if vip <= 3 else 3]
        
        if daily_checks >= limit:
            return False, f"Bạn đã đạt giới hạn {limit} lượt check hôm nay. Nâng cấp VIP để check thêm!"
            
        c.execute("UPDATE tg_users SET daily_checks = daily_checks + 1 WHERE tg_id=?", (tg_id,))
        c.commit()
        return True, ""

def set_sub_until(tg_id: int, epoch: int) -> None:
    with _lock:
        c = get_conn()
        c.execute("UPDATE tg_users SET sub_until=? WHERE tg_id=?", (epoch, tg_id))
        c.commit()

def reset_user(tg_id: int) -> None:
    with _lock:
        c = get_conn()
        c.execute("UPDATE tg_users SET balance=0, sub_until=0, trial_activated=0 WHERE tg_id=?", (tg_id,))
        c.execute("DELETE FROM fb_post_tracks WHERE tg_user_id=?", (tg_id,))
        c.execute("DELETE FROM txns WHERE tg_id=?", (tg_id,))
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
def generate_code(amount: int, prefix: str = "CODE", max_uses: int = 1, expire_at: int = 0) -> str:
    import random
    import string
    with _lock:
        c = get_conn()
        while True:
            random_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            code = f"{prefix}-{random_str}"
            exists = c.execute("SELECT id FROM giftcodes WHERE code=?", (code,)).fetchone()
            if not exists:
                c.execute(
                    "INSERT INTO giftcodes(code, amount, created_at, max_uses, expire_at) VALUES(?, ?, ?, ?, ?)", 
                    (code, amount, int(time.time()), max_uses, expire_at)
                )
                c.commit()
                return code

def get_unused_code(amount: int) -> str:
    with _lock:
        c = get_conn()
        row = c.execute("SELECT code FROM giftcodes WHERE amount=? AND is_used=0 AND max_uses=1 LIMIT 1", (amount,)).fetchone()
        if row:
            return row["code"]
    return generate_code(amount)

def get_code_info(code: str) -> Optional[Any]:
    return get_conn().execute("SELECT * FROM giftcodes WHERE code=?", (code,)).fetchone()

def use_code(code: str, tg_id: int) -> tuple[bool, int, str]:
    now_ts = int(time.time())
    with _lock:
        c = get_conn()
        row = c.execute("SELECT amount, is_used, max_uses, current_uses, expire_at FROM giftcodes WHERE code=?", (code,)).fetchone()
        if not row: return False, 0, "Mã không tồn tại"
        if row["is_used"] or (row["max_uses"] > 0 and row["current_uses"] >= row["max_uses"]): 
            return False, 0, "Mã đã hết lượt sử dụng"
        if row["expire_at"] > 0 and now_ts > row["expire_at"]:
            return False, 0, "Mã đã hết hạn"
        
        # Check if user already used it
        used = c.execute("SELECT 1 FROM giftcode_uses WHERE code=? AND tg_id=?", (code, tg_id)).fetchone()
        if used: return False, 0, "Bạn đã sử dụng mã này rồi"
        
        amount = row["amount"]
        new_uses = row["current_uses"] + 1
        is_used_now = 1 if (row["max_uses"] > 0 and new_uses >= row["max_uses"]) else 0
        
        c.execute("UPDATE giftcodes SET current_uses=?, is_used=?, used_by=?, used_at=? WHERE code=?", 
                  (new_uses, is_used_now, tg_id, now_ts, code))
        c.execute("INSERT INTO giftcode_uses(code, tg_id, used_at) VALUES(?, ?, ?)", (code, tg_id, now_ts))
        
        # Delete from saved_codes if exists
        c.execute("DELETE FROM saved_codes WHERE tg_id=? AND code=?", (tg_id, code))
        
        c.commit()
        return True, amount, "Thành công"

def save_code_for_user(tg_id: int, code: str) -> tuple[bool, str]:
    now_ts = int(time.time())
    with _lock:
        c = get_conn()
        row = c.execute("SELECT is_used, max_uses, current_uses, expire_at FROM giftcodes WHERE code=?", (code,)).fetchone()
        if not row: return False, "Mã không tồn tại"
        if row["is_used"] or (row["max_uses"] > 0 and row["current_uses"] >= row["max_uses"]): 
            return False, "Mã đã hết lượt sử dụng"
        if row["expire_at"] > 0 and now_ts > row["expire_at"]:
            return False, "Mã đã hết hạn"
            
        used = c.execute("SELECT 1 FROM giftcode_uses WHERE code=? AND tg_id=?", (code, tg_id)).fetchone()
        if used: return False, "Bạn đã sử dụng mã này rồi"
        
        saved = c.execute("SELECT 1 FROM saved_codes WHERE code=? AND tg_id=?", (code, tg_id)).fetchone()
        if saved: return False, "Bạn đã lưu mã này rồi"
        
        c.execute("INSERT INTO saved_codes(tg_id, code, saved_at) VALUES(?, ?, ?)", (tg_id, code, now_ts))
        c.commit()
        return True, "Đã lưu"

def get_user_saved_codes(tg_id: int) -> list:
    q = """
        SELECT s.code, g.amount, g.expire_at 
        FROM saved_codes s
        JOIN giftcodes g ON s.code = g.code
        WHERE s.tg_id = ? 
        AND g.is_used = 0 
        AND (g.expire_at = 0 OR g.expire_at > ?)
        ORDER BY s.saved_at DESC
    """
    return get_conn().execute(q, (tg_id, int(time.time()))).fetchall()

def get_code_history() -> list:
    return get_conn().execute("SELECT * FROM giftcodes ORDER BY created_at DESC LIMIT 500").fetchall()

def get_code_detailed(code: str) -> dict:
    info = get_code_info(code)
    if not info: return {}
    with _lock:
        c = get_conn()
        uses = c.execute("SELECT u.tg_id, u.used_at, tg.username FROM giftcode_uses u LEFT JOIN tg_users tg ON u.tg_id=tg.tg_id WHERE u.code=? ORDER BY u.used_at DESC", (code,)).fetchall()
        saves = c.execute("SELECT s.tg_id, s.saved_at, tg.username FROM saved_codes s LEFT JOIN tg_users tg ON s.tg_id=tg.tg_id WHERE s.code=? ORDER BY s.saved_at DESC", (code,)).fetchall()
        
    return {
        "info": dict(info),
        "uses": [dict(u) for u in uses],
        "saves": [dict(s) for s in saves]
    }

# --- FB WATCHES (Live/Die) ---
def add_watch(tg_id: int, uid: str, note: str, price: int, expire_at: int) -> int:
    with _lock:
        c = get_conn()
        cur = c.execute(
            "INSERT INTO watches(tg_id, uid, note, price, expire_at, created_at, active) "
            "VALUES(?,?,?,?,?,?,1) RETURNING id",
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
            "INSERT INTO tracks(tg_user_id,tg_username,zalo_user_id,tiktok_username,last_followers,last_following,last_videos,last_checked,created_at,avatar_url) VALUES(?,?,?,?,?,?,?,?,?,?) RETURNING id",
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
               last_checked,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id""",
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
            "INSERT INTO ig_tracks(tg_user_id,tg_username,zalo_user_id,ig_username,last_followers,last_following,last_posts,last_checked,created_at,avatar_url) VALUES(?,?,?,?,?,?,?,?,?,?) RETURNING id",
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
               last_checked,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id""",
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

def update_fb_track_status_new(track_id: int, status: str, avatar_url: str):
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

def get_random_proxy() -> str:
    with _lock:
        c = get_conn()
        row = c.execute("SELECT proxy_url FROM proxies WHERE is_active=1 ORDER BY RANDOM() LIMIT 1").fetchone()
        if row:
            return row["proxy_url"]
    return None

def mark_proxy_failed(proxy_url: str):
    if not proxy_url: return
    with _lock:
        c = get_conn()
        c.execute("UPDATE proxies SET fail_count = fail_count + 1 WHERE proxy_url=?", (proxy_url,))
        c.execute("UPDATE proxies SET is_active = 0 WHERE proxy_url=? AND fail_count > 3", (proxy_url,))
        c.commit()

def record_track_history(track_id: int, platform: str, track_type: str, stat_value: int):
    with _lock:
        c = get_conn()
        c.execute(
            "INSERT INTO track_history(track_id, platform, track_type, stat_value, created_at) VALUES (?,?,?,?,?)",
            (track_id, platform, track_type, stat_value, int(time.time()))
        )
        c.commit()

def get_proxies() -> list:
    c = get_conn()
    rows = c.execute("SELECT * FROM proxies ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]

def add_proxy(url: str) -> bool:
    with _lock:
        c = get_conn()
        try:
            c.execute("INSERT INTO proxies(proxy_url, created_at) VALUES (?,?)", (url, int(time.time())))
            c.commit()
            return True
        except Exception:
            return False

def delete_proxy(proxy_id: int) -> bool:
    with _lock:
        c = get_conn()
        c.execute("DELETE FROM proxies WHERE id=?", (proxy_id,))
        c.commit()
        return True

def toggle_proxy(proxy_id: int) -> bool:
    with _lock:
        c = get_conn()
        c.execute("UPDATE proxies SET is_active = CASE WHEN is_active=1 THEN 0 ELSE 1 END WHERE id=?", (proxy_id,))
        c.commit()
        return True


# --- YOUTUBE CRUD ---
def add_yt_track(tg_user_id, tg_username, yt_username, subs=0, videos=0, zalo_user_id="", avatar=""):
    with _lock:
        c = get_conn()
        res = c.execute(
            '''INSERT INTO yt_tracks(tg_user_id, tg_username, yt_username, last_subscribers, last_videos, created_at, zalo_user_id, avatar_url)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?) RETURNING id''',
            (tg_user_id, tg_username, yt_username, subs, videos, now(), zalo_user_id, avatar)
        )
        c.commit()
        return res.lastrowid

def get_yt_tracks(tg_id: int):
    with _lock:
        return get_conn().execute("SELECT * FROM yt_tracks WHERE tg_user_id=? ORDER BY id DESC", (tg_id,)).fetchall()

def remove_yt_track(track_id: int, tg_id: int):
    with _lock:
        c = get_conn()
        c.execute("DELETE FROM yt_tracks WHERE id=? AND tg_user_id=?", (track_id, tg_id))
        c.commit()

def all_active_yt_tracks():
    with _lock:
        return get_conn().execute("SELECT * FROM yt_tracks WHERE active=1").fetchall()

def update_yt_track_status(track_id: int, subs: int, videos: int):
    with _lock:
        c = get_conn()
        c.execute("UPDATE yt_tracks SET last_subscribers=?, last_videos=?, last_checked=? WHERE id=?", 
                  (subs, videos, now(), track_id))
        c.commit()

def add_yt_video_track(tg_user_id, tg_username, video_url, video_id, yt_username="", video_desc="", cover_url="", check_interval=3600, zalo_user_id="", views=0, likes=0, comments=0):
    with _lock:
        c = get_conn()
        res = c.execute(
            '''INSERT INTO yt_video_tracks(tg_user_id, tg_username, video_url, video_id, yt_username, video_desc, cover_url, check_interval, zalo_user_id, created_at, last_views, last_likes, last_comments)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id''',
            (tg_user_id, tg_username, video_url, video_id, yt_username, video_desc, cover_url, check_interval, zalo_user_id, now(), views, likes, comments)
        )
        c.commit()
        return res.lastrowid

def get_yt_video_tracks(tg_id: int):
    with _lock:
        return get_conn().execute("SELECT * FROM yt_video_tracks WHERE tg_user_id=? ORDER BY id DESC", (tg_id,)).fetchall()

def remove_yt_video_track(track_id: int, tg_id: int):
    with _lock:
        c = get_conn()
        c.execute("DELETE FROM yt_video_tracks WHERE id=? AND tg_user_id=?", (track_id, tg_id))
        c.commit()

def all_active_yt_video_tracks():
    with _lock:
        return get_conn().execute("SELECT * FROM yt_video_tracks WHERE active=1").fetchall()

def update_yt_video_track(track_id: int, views: int, likes: int, comments: int):
    with _lock:
        c = get_conn()
        c.execute("UPDATE yt_video_tracks SET last_views=?, last_likes=?, last_comments=?, last_checked=? WHERE id=?", 
                  (views, likes, comments, now(), track_id))
        c.commit()

# --- ZALO TRACKS ---
def add_zalo_track(tg_user_id: int, tg_username: str, phone: str, name: str, avatar: str, status: str = "LIVE") -> None:
    with _lock:
        c = get_conn()
        c.execute(
            "INSERT INTO zalo_tracks(tg_user_id, tg_username, phone, name, avatar, status, created_at) VALUES(?,?,?,?,?,?,?)",
            (tg_user_id, tg_username, phone, name, avatar, status, int(time.time()))
        )
        c.commit()

def all_active_zalo_tracks() -> list:
    with _lock:
        return get_conn().execute("SELECT * FROM zalo_tracks WHERE active=1").fetchall()

def get_zalo_track(track_id: int) -> Optional[dict]:
    with _lock:
        return get_conn().execute("SELECT * FROM zalo_tracks WHERE id=?", (track_id,)).fetchone()

def update_zalo_track_status(track_id: int, status: str, name: str, avatar: str) -> None:
    with _lock:
        c = get_conn()
        c.execute("UPDATE zalo_tracks SET status=?, name=?, avatar=?, last_checked=? WHERE id=?", (status, name, avatar, int(time.time()), track_id))
        c.commit()

def user_zalo_tracks(tg_user_id: int) -> list:
    with _lock:
        return get_conn().execute("SELECT * FROM zalo_tracks WHERE tg_user_id=? ORDER BY id DESC", (tg_user_id,)).fetchall()

def remove_zalo_track(tg_user_id: int, phone: str) -> bool:
    with _lock:
        c = get_conn()
        res = c.execute("DELETE FROM zalo_tracks WHERE tg_user_id=? AND phone=?", (tg_user_id, phone))
        c.commit()
        return res.rowcount > 0
