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

_lock = threading.RLock()
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
    def rollback(self):
        try:
            self.conn.rollback()
        except Exception:
            pass

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

            CREATE TABLE IF NOT EXISTS extra_admins (
                tg_id    BIGINT PRIMARY KEY,
                name     TEXT DEFAULT '',
                perms    TEXT DEFAULT '',
                added_by BIGINT DEFAULT 0,
                added_at BIGINT DEFAULT 0,
                expires_at BIGINT DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS admin_audit (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id      BIGINT NOT NULL,
                name       TEXT DEFAULT '',
                action     TEXT DEFAULT '',
                detail     TEXT DEFAULT '',
                created_at BIGINT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_admin_audit_time
                ON admin_audit(created_at DESC);

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
                expired_notified BIGINT DEFAULT 0,
                ref_code     TEXT,
                ref_withdrawn BIGINT DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS ref_commissions (
                id           BIGINT PRIMARY KEY AUTOINCREMENT,
                referrer_id  BIGINT,
                from_user_id BIGINT,
                level        BIGINT,
                amount       BIGINT,
                commission   BIGINT,
                created_at   BIGINT
            );

            CREATE TABLE IF NOT EXISTS withdrawal_requests (
                id           BIGINT PRIMARY KEY AUTOINCREMENT,
                tg_id        BIGINT,
                amount       BIGINT,
                status       TEXT DEFAULT 'pending',
                created_at   BIGINT,
                updated_at   BIGINT
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
                wallet          TEXT    NOT NULL DEFAULT 'main',
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

            CREATE TABLE IF NOT EXISTS admin_users (
                id              BIGINT PRIMARY KEY AUTOINCREMENT,
                username        TEXT UNIQUE NOT NULL,
                password_hash   TEXT NOT NULL,
                display_name    TEXT,
                role            TEXT DEFAULT 'moderator',
                tg_id           BIGINT DEFAULT 0,
                is_active       BIGINT DEFAULT 1,
                last_login      BIGINT DEFAULT 0,
                created_at      BIGINT NOT NULL,
                created_by      BIGINT DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS admin_audit_log (
                id          BIGINT PRIMARY KEY AUTOINCREMENT,
                admin_id    BIGINT,
                action      TEXT,
                target      TEXT,
                details     TEXT,
                ip_address  TEXT,
                created_at  BIGINT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS alert_rules (
                id           BIGINT PRIMARY KEY AUTOINCREMENT,
                tg_id        TEXT,
                platform     TEXT,
                target       TEXT,
                condition    TEXT,
                is_active    BIGINT DEFAULT 1,
                created_at   BIGINT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS alert_history (
                id           BIGINT PRIMARY KEY AUTOINCREMENT,
                tg_id        TEXT,
                rule_id      BIGINT,
                message      TEXT,
                triggered_at BIGINT NOT NULL
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

        # Seed super admin if empty
        admin_count = c.execute("SELECT COUNT(*) as c FROM admin_users").fetchone()["c"]
        if admin_count == 0:
            import hashlib
            admin_pw = config.DEFAULT_SETTINGS.get("admin_password", "admin")
            hash_pw = hashlib.sha256(admin_pw.encode()).hexdigest()
            c.execute(
                "INSERT INTO admin_users (username, password_hash, display_name, role, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("khaiphan111", hash_pw, "Super Admin", "super_admin", int(time.time()))
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
            "ALTER TABLE tg_users ADD COLUMN ref_code TEXT",
            "ALTER TABLE tg_users ADD COLUMN ref_earnings BIGINT DEFAULT 0",
            "ALTER TABLE tg_users ADD COLUMN ref_withdrawn BIGINT DEFAULT 0",
            "CREATE TABLE IF NOT EXISTS ref_commissions (id BIGINT PRIMARY KEY AUTOINCREMENT, referrer_id BIGINT, from_user_id BIGINT, level BIGINT, amount BIGINT, commission BIGINT, created_at BIGINT)",
            "CREATE TABLE IF NOT EXISTS withdrawal_requests (id BIGINT PRIMARY KEY AUTOINCREMENT, tg_id BIGINT, amount BIGINT, status TEXT DEFAULT 'pending', created_at BIGINT, updated_at BIGINT)",
            "ALTER TABLE ig_tracks ADD COLUMN avatar_url TEXT DEFAULT ''",
            "ALTER TABLE video_tracks ADD COLUMN zalo_user_id TEXT DEFAULT ''",
            "ALTER TABLE ig_video_tracks ADD COLUMN last_spike_alert_at BIGINT DEFAULT 0",
            "ALTER TABLE fb_post_tracks ADD COLUMN last_spike_alert_at BIGINT DEFAULT 0",
            "CREATE TABLE IF NOT EXISTS zalo_tracks (id BIGSERIAL PRIMARY KEY, tg_user_id BIGINT DEFAULT 0, tg_username TEXT DEFAULT '', zalo_user_id TEXT DEFAULT '', phone TEXT NOT NULL, name TEXT DEFAULT '', avatar TEXT DEFAULT '', status TEXT DEFAULT 'LIVE', last_checked BIGINT DEFAULT 0, created_at BIGINT NOT NULL, active BIGINT DEFAULT 1)",
            "CREATE TABLE IF NOT EXISTS proxies (id BIGINT PRIMARY KEY AUTOINCREMENT, proxy_url TEXT UNIQUE NOT NULL, fail_count BIGINT DEFAULT 0, is_active BIGINT DEFAULT 1, created_at BIGINT)",
            "CREATE TABLE IF NOT EXISTS track_history (id BIGINT PRIMARY KEY AUTOINCREMENT, track_id BIGINT NOT NULL, platform TEXT NOT NULL, track_type TEXT NOT NULL, stat_value BIGINT DEFAULT 0, created_at BIGINT)",
            "ALTER TABLE withdrawal_requests ADD COLUMN bank_info TEXT",
            "ALTER TABLE withdrawal_requests ADD COLUMN fee BIGINT DEFAULT 0",
            "CREATE TABLE IF NOT EXISTS admin_users (id BIGSERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, display_name TEXT, role TEXT DEFAULT 'moderator', tg_id BIGINT DEFAULT 0, is_active BIGINT DEFAULT 1, last_login BIGINT DEFAULT 0, created_at BIGINT NOT NULL, created_by BIGINT DEFAULT 0)",
            "CREATE TABLE IF NOT EXISTS admin_audit_log (id BIGSERIAL PRIMARY KEY, admin_id BIGINT, action TEXT, target TEXT, details TEXT, ip_address TEXT, created_at BIGINT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS alert_rules (id BIGSERIAL PRIMARY KEY, tg_id TEXT, platform TEXT, target TEXT, condition TEXT, is_active BIGINT DEFAULT 1, created_at BIGINT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS alert_history (id BIGSERIAL PRIMARY KEY, tg_id TEXT, rule_id BIGINT, message TEXT, triggered_at BIGINT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS campaigns (id BIGSERIAL PRIMARY KEY, name TEXT, type TEXT, status TEXT DEFAULT 'pending', scheduled_for BIGINT DEFAULT 0, config TEXT, stats TEXT, text_content TEXT, image_url TEXT, created_at BIGINT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS campaign_participants (id BIGSERIAL PRIMARY KEY, campaign_id BIGINT, tg_id BIGINT, status TEXT, extra_data TEXT, created_at BIGINT NOT NULL)",
            "ALTER TABLE watches ADD COLUMN campaign_id BIGINT",
            "ALTER TABLE watches ADD COLUMN tags TEXT",
            "ALTER TABLE tracks ADD COLUMN campaign_id BIGINT",
            "ALTER TABLE tracks ADD COLUMN tags TEXT",
            "ALTER TABLE ig_tracks ADD COLUMN campaign_id BIGINT",
            "ALTER TABLE ig_tracks ADD COLUMN tags TEXT",
            "ALTER TABLE fb_tracks ADD COLUMN campaign_id BIGINT",
            "ALTER TABLE fb_tracks ADD COLUMN tags TEXT",
            "ALTER TABLE zalo_tracks ADD COLUMN campaign_id BIGINT",
            "ALTER TABLE zalo_tracks ADD COLUMN tags TEXT",
            "CREATE TABLE IF NOT EXISTS daily_checkins (tg_id BIGINT PRIMARY KEY, last_checkin BIGINT, streak BIGINT DEFAULT 1, total_checkins BIGINT DEFAULT 1)",
            "CREATE TABLE IF NOT EXISTS batch_notifications (id BIGSERIAL PRIMARY KEY, tg_id BIGINT, message TEXT, created_at BIGINT NOT NULL)",
            "ALTER TABLE tg_users ADD COLUMN daily_report_hour INTEGER DEFAULT -1",
            "ALTER TABLE tg_users ADD COLUMN shop_balance BIGINT DEFAULT 0",
            "ALTER TABLE payos_orders ADD COLUMN target TEXT DEFAULT 'main'",
            "ALTER TABLE acc_restock_subs ADD COLUMN qty INTEGER DEFAULT 1",
            "ALTER TABLE acc_restock_subs ADD COLUMN auto_buy INTEGER DEFAULT 0",
            "ALTER TABLE extra_admins ADD COLUMN expires_at BIGINT DEFAULT 0",
            "ALTER TABLE acc_categories ADD COLUMN stall TEXT DEFAULT 'Acc Facebook'",
            "ALTER TABLE acc_categories ADD COLUMN live_check INTEGER DEFAULT 1",
            "ALTER TABLE acc_stock ADD COLUMN sold_to BIGINT DEFAULT 0",
            "ALTER TABLE acc_stock ADD COLUMN sold_at BIGINT DEFAULT 0",
            "ALTER TABLE acc_stock ADD COLUMN price_sold BIGINT DEFAULT 0",
            "ALTER TABLE acc_stock ADD COLUMN sheet_ref TEXT DEFAULT ''",
            "ALTER TABLE acc_stock ADD COLUMN sheet_marked INTEGER DEFAULT 0",
            "ALTER TABLE giftcodes ADD COLUMN wallet TEXT DEFAULT 'main'",
            "ALTER TABLE promo_codes ADD COLUMN wallet TEXT DEFAULT 'main'"
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

# ─── REF COMMISSION RATES (cấu hình được qua settings) ─────────────────────
def _ref_num(key: str, default: float) -> float:
    try:
        v = str(get_setting(key, "")).strip().replace(",", ".")
        return float(v) if v else default
    except (ValueError, TypeError):
        return default

def get_ref_rates() -> dict:
    """Tỉ lệ hoa hồng giới thiệu. Đổi qua settings:
    ref_f1_pct, ref_f1_silver_min, ref_f1_silver_pct,
    ref_f1_gold_min, ref_f1_gold_pct, ref_f2_pct."""
    return {
        "f1_pct": _ref_num("ref_f1_pct", 10),
        "f1_silver_min": _ref_num("ref_f1_silver_min", 5000000),
        "f1_silver_pct": _ref_num("ref_f1_silver_pct", 15),
        "f1_gold_min": _ref_num("ref_f1_gold_min", 20000000),
        "f1_gold_pct": _ref_num("ref_f1_gold_pct", 20),
        "f2_pct": _ref_num("ref_f2_pct", 3),
    }

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
    if referrer_id and int(referrer_id) == int(tg_id):
        referrer_id = 0  # chống tự giới thiệu chính mình
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

def notify_commission_bonus(ref_id: int, bonus: int, level: int) -> None:
    """Báo tin nhắn hoa hồng F1/F2 (gọi sau khi commit, ngoài lock)."""
    try:
        import asyncio
        from .bot import manager, vnd
        if manager.running:
            asyncio.create_task(manager.bot.send_message(
                ref_id,
                f"🎁 <b>Hoa hồng giới thiệu F{level}!</b>\nBạn vừa nhận được <b>{vnd(bonus)}</b> từ lượt nạp của F{level}!",
                parse_mode="HTML"))
    except Exception:
        pass


def _credit_topup_nolock(c, tg_id: int, amount: int, reason: str, wallet: str = "main") -> dict:
    """Cộng tiền nạp vào ví chỉ định + total_topup + hoa hồng F1/F2. KHÔNG commit, KHÔNG lock.

    Caller phải giữ _lock và tự commit/rollback. Trả {'f1': (id, bonus),
    'f2': (id, bonus)} cho các mức có bonus > 0 (để caller báo tin nhắn sau).
    """
    now = int(time.time())
    col = "shop_balance" if wallet == "shop" else "balance"
    c.execute(
        f"UPDATE tg_users SET {col} = {col} + ?, total_topup = total_topup + ? WHERE tg_id=?",
        (amount, amount, tg_id),
    )
    c.execute(
        "INSERT INTO txns(ts, tg_id, amount, reason) VALUES(?,?,?,?)",
        (now, tg_id, amount, reason),
    )
    bonuses: dict = {}
    user = c.execute("SELECT referrer_id FROM tg_users WHERE tg_id=?", (tg_id,)).fetchone()
    if user and user["referrer_id"]:
        f1_id = user["referrer_id"]
        f1_topup_row = c.execute(
            "SELECT SUM(total_topup) as s FROM tg_users WHERE referrer_id=?", (f1_id,)
        ).fetchone()
        total_f1_topup = f1_topup_row["s"] if f1_topup_row and f1_topup_row["s"] else 0
        rates = get_ref_rates()
        percentage = rates["f1_pct"] / 100
        if total_f1_topup >= rates["f1_gold_min"]:
            percentage = rates["f1_gold_pct"] / 100
        elif total_f1_topup >= rates["f1_silver_min"]:
            percentage = rates["f1_silver_pct"] / 100
        f1_bonus = int(amount * percentage)
        if f1_bonus > 0:
            c.execute("UPDATE tg_users SET ref_earnings = ref_earnings + ? WHERE tg_id=?",
                      (f1_bonus, f1_id))
            c.execute(
                "INSERT INTO txns(ts, tg_id, amount, reason) VALUES(?,?,?,?)",
                (now, f1_id, f1_bonus, "Hoa hồng giới thiệu F1"),
            )
            c.execute(
                "INSERT INTO ref_commissions(referrer_id, from_user_id, level, amount, commission, created_at)"
                " VALUES(?,?,?,?,?,?)",
                (f1_id, tg_id, 1, amount, f1_bonus, now),
            )
            bonuses["f1"] = (f1_id, f1_bonus)
        f1_user = c.execute("SELECT referrer_id FROM tg_users WHERE tg_id=?", (f1_id,)).fetchone()
        if f1_user and f1_user["referrer_id"]:
            f2_id = f1_user["referrer_id"]
            f2_bonus = int(amount * rates["f2_pct"] / 100)
            if f2_bonus > 0:
                c.execute("UPDATE tg_users SET ref_earnings = ref_earnings + ? WHERE tg_id=?",
                          (f2_bonus, f2_id))
                c.execute(
                    "INSERT INTO txns(ts, tg_id, amount, reason) VALUES(?,?,?,?)",
                    (now, f2_id, f2_bonus, "Hoa hồng giới thiệu F2"),
                )
                c.execute(
                    "INSERT INTO ref_commissions(referrer_id, from_user_id, level, amount, commission, created_at)"
                    " VALUES(?,?,?,?,?,?)",
                    (f2_id, tg_id, 2, amount, f2_bonus, now),
                )
                bonuses["f2"] = (f2_id, f2_bonus)
    return bonuses


def adjust_balance(tg_id: int, amount: int, reason: str) -> bool:
    """Cong/tru so du. Tru tien thi kiem tra nguyen tu: khong du -> False, khong tru."""
    with _lock:
        c = get_conn()
        bonuses: dict = {}
        if amount < 0:
            r = c.execute("SELECT balance FROM tg_users WHERE tg_id=?", (tg_id,)).fetchone()
            if not r or int(r["balance"] or 0) + amount < 0:
                return False
            c.execute("UPDATE tg_users SET balance = balance + ? WHERE tg_id=?", (amount, tg_id))
            c.execute(
                "INSERT INTO txns(ts, tg_id, amount, reason) VALUES(?,?,?,?)",
                (int(time.time()), tg_id, amount, reason),
            )
        else:
            bonuses = _credit_topup_nolock(c, tg_id, amount, reason)
        c.commit()
    for key, level in (("f1", 1), ("f2", 2)):
        info = bonuses.get(key)
        if info:
            notify_commission_bonus(info[0], info[1], level)
    return True


def adjust_shop_balance(tg_id: int, amount: int, reason: str) -> bool:
    """Cộng/trừ ví shop (mua acc). Trừ tiền kiểm tra nguyên tử: không đủ -> False."""
    with _lock:
        c = get_conn()
        if amount < 0:
            r = c.execute("SELECT shop_balance FROM tg_users WHERE tg_id=?", (tg_id,)).fetchone()
            if not r or int(r["shop_balance"] or 0) + amount < 0:
                return False
        c.execute("UPDATE tg_users SET shop_balance = shop_balance + ? WHERE tg_id=?", (amount, tg_id))
        c.execute(
            "INSERT INTO txns(ts, tg_id, amount, reason) VALUES(?,?,?,?)",
            (int(time.time()), tg_id, amount, reason),
        )
        c.commit()
    return True


def add_shop_balance_only(tg_id: int, amount: int, reason: str) -> None:
    """Cộng/trừ ví shop KHÔNG động vào total_topup (dùng cho hoàn tiền)."""
    with _lock:
        c = get_conn()
        c.execute("UPDATE tg_users SET shop_balance = shop_balance + ? WHERE tg_id=?", (amount, tg_id))
        c.execute("INSERT INTO txns(ts, tg_id, amount, reason) VALUES(?,?,?,?)",
                  (int(time.time()), tg_id, amount, reason))
        c.commit()


def credit_topup(tg_id: int, amount: int, reason: str, wallet: str = "main") -> bool:
    """Cộng tiền nạp vào ví chỉ định + total_topup + hoa hồng F1/F2 (như adjust_balance chiều cộng)."""
    with _lock:
        c = get_conn()
        bonuses = _credit_topup_nolock(c, tg_id, amount, reason, wallet)
        c.commit()
    for key, level in (("f1", 1), ("f2", 2)):
        info = bonuses.get(key)
        if info:
            notify_commission_bonus(info[0], info[1], level)
    return True

        
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
# ─── VÍ (wallet): 'main' = ví chính, 'shop' = ví shop ──────────────────────────
WALLET_LABEL = {"main": "ví chính", "shop": "ví shop"}

def wallet_label(wallet: str) -> str:
    return WALLET_LABEL.get((wallet or "main").strip().lower(), "ví chính")

def parse_wallet(s: str) -> str:
    """Chuẩn hoá lựa chọn ví của admin: 'shop' → 'shop', còn lại → 'main'."""
    s = (s or "").strip().lower()
    if s in ("shop", "vishop", "ví shop", "vi shop"):
        return "shop"
    return "main"

def credit_wallet(tg_id: int, amount: int, reason: str, wallet: str = "main") -> bool:
    """Cộng tiền vào ví đã chọn. Trả về True nếu cộng thành công."""
    if parse_wallet(wallet) == "shop":
        return adjust_shop_balance(tg_id, amount, reason)
    return adjust_balance(tg_id, amount, reason)

def generate_code(amount: int, prefix: str = "CODE", max_uses: int = 1, expire_at: int = 0, wallet: str = "main") -> str:
    import random
    import string
    wallet = parse_wallet(wallet)
    with _lock:
        c = get_conn()
        while True:
            random_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            code = f"{prefix}-{random_str}"
            exists = c.execute("SELECT id FROM giftcodes WHERE code=?", (code,)).fetchone()
            if not exists:
                c.execute(
                    "INSERT INTO giftcodes(code, amount, wallet, created_at, max_uses, expire_at) VALUES(?, ?, ?, ?, ?, ?)",
                    (code, amount, wallet, int(time.time()), max_uses, expire_at)
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

def use_code(code: str, tg_id: int) -> tuple[bool, int, str, str]:
    """Đổi giftcode. Trả về (thành_công, số_tiền, thông_báo, ví)."""
    now_ts = int(time.time())
    with _lock:
        c = get_conn()
        row = c.execute("SELECT amount, wallet, is_used, max_uses, current_uses, expire_at FROM giftcodes WHERE code=?", (code,)).fetchone()
        if not row: return False, 0, "Mã không tồn tại", "main"
        if row["is_used"] or (row["max_uses"] > 0 and row["current_uses"] >= row["max_uses"]):
            return False, 0, "Mã đã hết lượt sử dụng", "main"
        if row["expire_at"] > 0 and now_ts > row["expire_at"]:
            return False, 0, "Mã đã hết hạn", "main"

        # Check if user already used it
        used = c.execute("SELECT 1 FROM giftcode_uses WHERE code=? AND tg_id=?", (code, tg_id)).fetchone()
        if used: return False, 0, "Bạn đã sử dụng mã này rồi", "main"

        try:
            wallet = parse_wallet(row["wallet"])
        except Exception:
            wallet = "main"
        
        amount = row["amount"]
        new_uses = row["current_uses"] + 1
        is_used_now = 1 if (row["max_uses"] > 0 and new_uses >= row["max_uses"]) else 0
        
        c.execute("UPDATE giftcodes SET current_uses=?, is_used=?, used_by=?, used_at=? WHERE code=?", 
                  (new_uses, is_used_now, tg_id, now_ts, code))
        c.execute("INSERT INTO giftcode_uses(code, tg_id, used_at) VALUES(?, ?, ?)", (code, tg_id, now_ts))
        
        # Delete from saved_codes if exists
        c.execute("DELETE FROM saved_codes WHERE tg_id=? AND code=?", (tg_id, code))
        
        c.commit()
        return True, amount, "Thành công", wallet

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
        SELECT s.code, g.amount, g.expire_at, g.wallet 
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
def add_watch(tg_id: int, uid: str, note: str, price: int, expire_at: int) -> tuple:
    """Thêm theo dõi UID. Trả (watch_id, is_new).
    Nếu đã có watch active của (tg_id, uid) thì KHÔNG tạo mới (tránh báo
    trùng mỗi lần đổi trạng thái), chỉ nới hạn/điền ghi chú khi cần."""
    with _lock:
        c = get_conn()
        row = c.execute(
            "SELECT id, expire_at, note FROM watches "
            "WHERE tg_id=? AND uid=? AND active=1 ORDER BY id LIMIT 1",
            (tg_id, uid),
        ).fetchone()
        if row:
            wid = row["id"]
            sets, params = [], []
            if expire_at and (not row["expire_at"] or expire_at > row["expire_at"]):
                sets.append("expire_at=?")
                params.append(expire_at)
            if note and not (row["note"] or ""):
                sets.append("note=?")
                params.append(note)
            if sets:
                c.execute("UPDATE watches SET %s WHERE id=?" % ", ".join(sets),
                          (*params, wid))
                c.commit()
            return wid, False
        cur = c.execute(
            "INSERT INTO watches(tg_id, uid, note, price, expire_at, created_at, active) "
            "VALUES(?,?,?,?,?,?,1) RETURNING id",
            (tg_id, uid, note, price, expire_at, int(time.time())),
        )
        c.commit()
        return cur.lastrowid, True

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

def recent_logs(limit: int = 50) -> list:
    return get_conn().execute("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

def get_user_logs(tg_id: int, kind: str = None, limit: int = 15) -> list:
    """Lịch sử check của 1 user, lọc theo kind (fb/tiktok/ig/yt/zalo)."""
    c = get_conn()
    if kind:
        return c.execute(
            "SELECT * FROM logs WHERE tg_id=? AND kind=? ORDER BY id DESC LIMIT ?",
            (tg_id, kind, limit),
        ).fetchall()
    return c.execute(
        "SELECT * FROM logs WHERE tg_id=? ORDER BY id DESC LIMIT ?",
        (tg_id, limit),
    ).fetchall()

# --- ADMIN USERS & AUDIT LOG ---
def create_admin(username: str, password_hash: str, display_name: str, role: str = 'moderator', tg_id: int = 0, created_by: int = 0):
    with _lock:
        c = get_conn()
        cur = c.execute(
            "INSERT INTO admin_users (username, password_hash, display_name, role, tg_id, created_at, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (username, password_hash, display_name, role, tg_id, int(time.time()), created_by)
        )
        c.commit()
        return cur.lastrowid

def get_admin_by_username(username: str):
    return get_conn().execute("SELECT * FROM admin_users WHERE username=?", (username,)).fetchone()

def get_admin_by_id(admin_id: int):
    return get_conn().execute("SELECT * FROM admin_users WHERE id=?", (admin_id,)).fetchone()

def list_admins():
    return get_conn().execute("SELECT * FROM admin_users ORDER BY created_at DESC").fetchall()

def update_admin(admin_id: int, password_hash: str = None, display_name: str = None, role: str = None, tg_id: int = None, is_active: int = None):
    with _lock:
        c = get_conn()
        updates = []
        params = []
        if password_hash is not None:
            updates.append("password_hash=?")
            params.append(password_hash)
        if display_name is not None:
            updates.append("display_name=?")
            params.append(display_name)
        if role is not None:
            updates.append("role=?")
            params.append(role)
        if tg_id is not None:
            updates.append("tg_id=?")
            params.append(tg_id)
        if is_active is not None:
            updates.append("is_active=?")
            params.append(is_active)
        if updates:
            params.append(admin_id)
            c.execute(f"UPDATE admin_users SET {', '.join(updates)} WHERE id=?", tuple(params))
            c.commit()

def update_admin_last_login(admin_id: int):
    with _lock:
        c = get_conn()
        c.execute("UPDATE admin_users SET last_login=? WHERE id=?", (int(time.time()), admin_id))
        c.commit()

def delete_admin(admin_id: int):
    with _lock:
        c = get_conn()
        c.execute("DELETE FROM admin_users WHERE id=?", (admin_id,))
        c.commit()

def log_admin_action(admin_id: int, action: str, target: str, details: str, ip_address: str = ""):
    with _lock:
        c = get_conn()
        c.execute(
            "INSERT INTO admin_audit_log (admin_id, action, target, details, ip_address, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (admin_id, action, target, details, ip_address, int(time.time()))
        )
        c.commit()

def get_admin_audit_log(limit: int = 100):
    return get_conn().execute(
        "SELECT a.*, u.username as admin_username FROM admin_audit_log a "
        "LEFT JOIN admin_users u ON a.admin_id = u.id ORDER BY a.created_at DESC LIMIT ?", (limit,)
    ).fetchall()


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

# --- ALERTS ---
def create_alert_rule(tg_id: str, platform: str, target: str, condition: str = 'status_change') -> int:
    with _lock:
        c = get_conn()
        cur = c.execute(
            "INSERT INTO alert_rules (tg_id, platform, target, condition, created_at) VALUES (?, ?, ?, ?, ?)",
            (tg_id, platform, target, condition, int(time.time()))
        )
        c.commit()
        # get_conn() trả về wrapper không có .lastrowid -> lấy từ cursor, fallback SELECT
        if getattr(cur, "lastrowid", None):
            return cur.lastrowid
        row = c.execute("SELECT id FROM alert_rules ORDER BY id DESC LIMIT 1").fetchone()
        return row["id"] if row else 0

def get_alert_rules(tg_id: str = None, target: str = None) -> list:
    c = get_conn()
    query = "SELECT * FROM alert_rules WHERE is_active=1"
    params = []
    if tg_id:
        query += " AND tg_id=?"
        params.append(tg_id)
    if target:
        query += " AND target=?"
        params.append(target)
    return c.execute(query, tuple(params)).fetchall()

def delete_alert_rule(rule_id: int) -> None:
    with _lock:
        c = get_conn()
        c.execute("DELETE FROM alert_rules WHERE id=?", (rule_id,))
        c.commit()

def log_alert_history(tg_id: str, rule_id: int, message: str) -> None:
    with _lock:
        c = get_conn()
        c.execute(
            "INSERT INTO alert_history (tg_id, rule_id, message, triggered_at) VALUES (?, ?, ?, ?)",
            (tg_id, rule_id, message, int(time.time()))
        )
        c.commit()

def create_withdrawal_request(tg_id: int, amount: int, bank_info: str, fee: int) -> int:
    with _lock:
        now_ts = int(time.time())
        c = get_conn()
        cur = c.execute(
            "INSERT INTO withdrawal_requests(tg_id, amount, bank_info, fee, status, created_at, updated_at) VALUES(?,?,?,?,?,?,?) RETURNING id",
            (tg_id, amount, bank_info, fee, 'pending', now_ts, now_ts)
        )
        req_id = cur.lastrowid
        if not req_id:
            row = c.execute("SELECT id FROM withdrawal_requests WHERE tg_id=? AND status='pending' ORDER BY id DESC LIMIT 1", (tg_id,)).fetchone()
            if row:
                req_id = row["id"] if isinstance(row, dict) or hasattr(row, "__getitem__") else row[0]
        c.commit()
        return int(req_id or 0)

# --- V2 PRO FEATURES ---

# Audit Logs
def add_audit_log(admin_id: int, action: str, target: str, details: str, ip_address: str = "") -> None:
    with _lock:
        c = get_conn()
        c.execute("INSERT INTO admin_audit_log(admin_id, action, target, details, ip_address, created_at) VALUES(?,?,?,?,?,?)",
                  (admin_id, action, target, details, ip_address, int(time.time())))
        c.commit()

def get_audit_logs(limit: int = 100, offset: int = 0) -> list:
    return [dict(r) for r in get_conn().execute("SELECT * FROM admin_audit_log ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()]

# Campaigns
def add_campaign(tg_id: int, name: str) -> int:
    with _lock:
        c = get_conn()
        cur = c.execute("INSERT INTO campaigns(tg_id, name, created_at) VALUES(?,?,?) RETURNING id",
                        (tg_id, name, int(time.time())))
        c.commit()
        if cur.lastrowid:
            return cur.lastrowid
        row = c.execute("SELECT id FROM campaigns WHERE tg_id=? ORDER BY id DESC LIMIT 1", (tg_id,)).fetchone()
        return row["id"] if row else 0

# (Xóa định nghĩa cũ bị trùng tên — dùng get_campaigns/delete_campaign hợp nhất bên dưới)

# Gamification Daily Check-in
def checkin_daily(tg_id: int) -> tuple[bool, int, int]:
    with _lock:
        now_ts = int(time.time())
        day_start = now_ts - (now_ts % 86400)
        c = get_conn()
        row = c.execute("SELECT * FROM daily_checkins WHERE tg_id=?", (tg_id,)).fetchone()
        
        streak = 1
        
        if row:
            last = row["last_checkin"]
            if last >= day_start:
                return False, row["streak"], 0
            
            if last >= day_start - 86400:
                streak = row["streak"] + 1
            else:
                streak = 1
                
            c.execute("UPDATE daily_checkins SET last_checkin=?, streak=?, total_checkins=total_checkins+1 WHERE tg_id=?", (now_ts, streak, tg_id))
        else:
            c.execute("INSERT INTO daily_checkins(tg_id, last_checkin, streak, total_checkins) VALUES(?,?,1,1)", (tg_id, now_ts))
            
        try:
            reward = int(get_setting("daily_credit_base", "2"))
        except: reward = 2
        
        if streak % 30 == 0:
            try:
                reward = int(get_setting("daily_credit_30d", "30"))
            except: reward = 30
        elif streak % 7 == 0:
            try:
                reward = int(get_setting("daily_credit_7d", "10"))
            except: reward = 10
            
        # Cộng credits NGAY TRONG lock (RLock) để 2 request song song không thể nhận 2 lần
        add_credits(tg_id, reward, f"Diem danh hang ngay (Chuoi {streak} ngay)")
        c.commit()
    return True, streak, reward

# Batch Notifications
def add_batch_notification(tg_id: int, message: str) -> None:
    with _lock:
        c = get_conn()
        c.execute("INSERT INTO batch_notifications(tg_id, message, created_at) VALUES(?,?,?)", (tg_id, message, int(time.time())))
        c.commit()

def get_and_clear_batch_notifications() -> dict:
    with _lock:
        c = get_conn()
        rows = c.execute("SELECT * FROM batch_notifications ORDER BY created_at ASC").fetchall()
        c.execute("DELETE FROM batch_notifications")
        c.commit()
        
    res = {}
    for r in rows:
        tg_id = r["tg_id"]
        if tg_id not in res:
            res[tg_id] = []
        res[tg_id].append(r["message"])
    return res

# --- V2 PRO CAMPAIGNS ---
def create_campaign(name: str, ctype: str, scheduled_for: int, config: str, text_content: str, image_url: str) -> int:
    with _lock:
        c = get_conn()
        # SQLite: cot id BIGSERIAL khong tu tang -> tu gan id
        row = c.execute("SELECT COALESCE(MAX(id), 0) + 1 AS nid FROM campaigns").fetchone()
        new_id = row["nid"] if row else 1
        cur = c.execute(
            "INSERT INTO campaigns(id, name, type, status, scheduled_for, config, stats, text_content, image_url, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (new_id, name, ctype, 'pending', scheduled_for, config, '{}', text_content, image_url, int(time.time()))
        )
        c.commit()
        if hasattr(cur, "lastrowid"): return cur.lastrowid
        res = c.execute("SELECT id FROM campaigns ORDER BY id DESC LIMIT 1").fetchone()
        return res["id"] if res else 0

def get_campaigns(tg_id: int = None, status: str = None) -> list:
    """Lấy campaigns, lọc theo tg_id và/hoặc status (hợp nhất 2 định nghĩa cũ bị trùng tên)."""
    with _lock:
        c = get_conn()
        conds, params = [], []
        if tg_id is not None:
            conds.append("tg_id=?")
            params.append(tg_id)
        if status:
            conds.append("status=?")
            params.append(status)
        q = "SELECT * FROM campaigns"
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY id DESC"
        return [dict(r) for r in c.execute(q, params).fetchall()]

def get_campaign(campaign_id: int) -> Optional[dict]:
    with _lock:
        c = get_conn()
        return c.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()

def update_campaign_status(campaign_id: int, status: str):
    with _lock:
        c = get_conn()
        c.execute("UPDATE campaigns SET status=? WHERE id=?", (status, campaign_id))
        c.commit()

def update_campaign_stats(campaign_id: int, stats_str: str):
    with _lock:
        c = get_conn()
        c.execute("UPDATE campaigns SET stats=? WHERE id=?", (stats_str, campaign_id))
        c.commit()

def check_campaign_participation(campaign_id: int, tg_id: int) -> bool:
    with _lock:
        c = get_conn()
        row = c.execute("SELECT id FROM campaign_participants WHERE campaign_id=? AND tg_id=?", (campaign_id, tg_id)).fetchone()
        return bool(row)

def add_campaign_participant(campaign_id: int, tg_id: int, status: str, extra_data: str = ""):
    with _lock:
        c = get_conn()
        c.execute(
            "INSERT INTO campaign_participants(campaign_id, tg_id, status, extra_data, created_at) VALUES(?,?,?,?,?)",
            (campaign_id, tg_id, status, extra_data, int(time.time()))
        )
        c.commit()

def delete_campaign(campaign_id: int, tg_id: int = None) -> bool:
    """Xóa campaign; nếu có tg_id thì chỉ xóa campaign của đúng user đó."""
    with _lock:
        c = get_conn()
        if tg_id is not None:
            cur = c.execute("DELETE FROM campaigns WHERE id=? AND tg_id=?", (campaign_id, tg_id))
        else:
            cur = c.execute("DELETE FROM campaigns WHERE id=?", (campaign_id,))
        c.execute("DELETE FROM campaign_participants WHERE campaign_id=?", (campaign_id,))
        c.commit()
        return cur.rowcount > 0


# ─── USER LISTS (newlist, addtolist, scanlist...) ────────────────────────────

def migrate_new_features():
    """Migrate DB for new features — call at startup."""
    c = get_conn()
    for sql in [
        """CREATE TABLE IF NOT EXISTS user_lists (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id      BIGINT NOT NULL,
            name       TEXT NOT NULL,
            platform   TEXT DEFAULT 'fb',
            created_at BIGINT NOT NULL,
            UNIQUE(tg_id, name)
        )""",
        """CREATE TABLE IF NOT EXISTS user_list_items (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            list_id    BIGINT NOT NULL,
            tg_id      BIGINT NOT NULL,
            value      TEXT NOT NULL,
            note       TEXT DEFAULT '',
            added_at   BIGINT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS check_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id      BIGINT NOT NULL,
            platform   TEXT NOT NULL,
            target     TEXT NOT NULL,
            result     TEXT DEFAULT '',
            checked_at BIGINT NOT NULL
        )""",
        "ALTER TABLE alert_rules ADD COLUMN is_paused BIGINT DEFAULT 0",
        "ALTER TABLE alert_rules ADD COLUMN snooze_until BIGINT DEFAULT 0",
        "ALTER TABLE tg_users ADD COLUMN credits BIGINT DEFAULT 0",
        """CREATE TABLE IF NOT EXISTS reseller_keys (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            api_key    TEXT NOT NULL UNIQUE,
            credits    BIGINT DEFAULT 0,
            is_active  BIGINT DEFAULT 1,
            created_at BIGINT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS reseller_usage (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            key_id      BIGINT NOT NULL,
            endpoint    TEXT NOT NULL,
            target      TEXT DEFAULT '',
            credits_used BIGINT DEFAULT 0,
            created_at  BIGINT NOT NULL
        )""",
        "ALTER TABLE watches ADD COLUMN alert_mode TEXT DEFAULT 'all'",
        """CREATE TABLE IF NOT EXISTS check_stats (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id      BIGINT NOT NULL,
            platform   TEXT NOT NULL,
            target     TEXT NOT NULL,
            result     TEXT NOT NULL,
            via        TEXT DEFAULT '',
            checked_at BIGINT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS credit_txns (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ts         BIGINT NOT NULL,
            tg_id      BIGINT,
            key_id     BIGINT,
            delta      BIGINT NOT NULL,
            reason     TEXT DEFAULT ''
        )""",
        """CREATE TABLE IF NOT EXISTS promo_codes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            code       TEXT NOT NULL UNIQUE,
            pct        BIGINT NOT NULL DEFAULT 10,
            wallet     TEXT NOT NULL DEFAULT 'main',
            max_uses   BIGINT DEFAULT 0,
            used_count BIGINT DEFAULT 0,
            expires_at BIGINT DEFAULT 0,
            created_at BIGINT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS user_promos (
            tg_id      BIGINT PRIMARY KEY,
            code       TEXT NOT NULL,
            applied_at BIGINT NOT NULL
        )""",
        "ALTER TABLE reseller_keys ADD COLUMN webhook_url TEXT DEFAULT ''",
        """CREATE TABLE IF NOT EXISTS payos_orders (
            order_code     INTEGER PRIMARY KEY,
            tg_id          BIGINT NOT NULL,
            amount         BIGINT NOT NULL,
            status         TEXT DEFAULT 'PENDING',
            payment_link_id TEXT DEFAULT '',
            checkout_url   TEXT DEFAULT '',
            qr_code        TEXT DEFAULT '',
            created_at     BIGINT NOT NULL,
            updated_at     BIGINT NOT NULL
        )""",
        "ALTER TABLE payos_orders ADD COLUMN qr_code TEXT DEFAULT ''",
        "ALTER TABLE payos_orders ADD COLUMN final_checked INTEGER DEFAULT 0",
        """CREATE TABLE IF NOT EXISTS acc_categories (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT UNIQUE NOT NULL,
            price          BIGINT NOT NULL DEFAULT 0,
            warranty_hours INTEGER NOT NULL DEFAULT 24,
            description    TEXT DEFAULT '',
            active         INTEGER DEFAULT 1,
            created_at     BIGINT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS acc_stock (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            cat_id       INTEGER NOT NULL,
            uid          TEXT NOT NULL,
            password     TEXT DEFAULT '',
            created_date TEXT DEFAULT '',
            backup_mail  TEXT DEFAULT '',
            note         TEXT DEFAULT '',
            totp         TEXT DEFAULT '',
            cookie       TEXT DEFAULT '',
            token        TEXT DEFAULT '',
            status       TEXT DEFAULT 'AVAILABLE',
            sold_to      BIGINT DEFAULT 0,
            sold_at      BIGINT DEFAULT 0,
            price_sold   BIGINT DEFAULT 0,
            sheet_ref    TEXT DEFAULT '',
            sheet_marked INTEGER DEFAULT 0,
            added_at     BIGINT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_acc_stock_cat_status ON acc_stock(cat_id, status)",
        """CREATE TABLE IF NOT EXISTS acc_orders (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id      BIGINT NOT NULL,
            stock_id   INTEGER NOT NULL,
            cat_id     INTEGER NOT NULL,
            price      BIGINT NOT NULL,
            created_at BIGINT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS acc_warranty_claims (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id     INTEGER NOT NULL,
            tg_id        BIGINT NOT NULL,
            stock_id     INTEGER NOT NULL,
            check_result TEXT DEFAULT '',
            status       TEXT DEFAULT 'PENDING',
            created_at   BIGINT NOT NULL,
            handled_at   BIGINT DEFAULT 0
        )""",
        "ALTER TABLE acc_categories ADD COLUMN credit_bonus INTEGER DEFAULT 0",
        """CREATE TABLE IF NOT EXISTS spin_tickets (
            tg_id   BIGINT PRIMARY KEY,
            tickets INTEGER NOT NULL DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS spin_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id       BIGINT NOT NULL,
            prize_label TEXT DEFAULT '',
            prize_kind  TEXT DEFAULT '',
            prize_value BIGINT DEFAULT 0,
            created_at  BIGINT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS acc_restock_subs (
            tg_id      BIGINT NOT NULL,
            cat_id     INTEGER NOT NULL,
            created_at BIGINT NOT NULL,
            qty        INTEGER DEFAULT 1,
            auto_buy   INTEGER DEFAULT 0,
            PRIMARY KEY (tg_id, cat_id)
        )""",
        """CREATE TABLE IF NOT EXISTS loyalty_points (
            tg_id  BIGINT PRIMARY KEY,
            points INTEGER NOT NULL DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS loyalty_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id      BIGINT NOT NULL,
            delta      INTEGER NOT NULL,
            reason     TEXT DEFAULT '',
            created_at BIGINT NOT NULL
        )""",
        "ALTER TABLE acc_stock ADD COLUMN batch TEXT DEFAULT ''",
        "ALTER TABLE acc_warranty_claims ADD COLUMN reminded_at BIGINT DEFAULT 0",
        "ALTER TABLE acc_warranty_claims ADD COLUMN handled_by BIGINT DEFAULT 0",
        # ---- GĐ4/GĐ5 đợt 4: giá khan hiếm, hộp mù, cọc, review, NCC, lô hàng ----
        "ALTER TABLE acc_categories ADD COLUMN low_threshold INTEGER DEFAULT 10",
        "ALTER TABLE acc_categories ADD COLUMN scarcity_pct INTEGER DEFAULT 15",
        "ALTER TABLE acc_categories ADD COLUMN mystery_eligible INTEGER DEFAULT 0",
        "ALTER TABLE acc_categories ADD COLUMN hidden INTEGER DEFAULT 0",
        """CREATE TABLE IF NOT EXISTS acc_deposits (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id      BIGINT NOT NULL,
            cat_id     INTEGER NOT NULL,
            amount     BIGINT NOT NULL DEFAULT 0,
            status     TEXT DEFAULT 'WAITING',
            order_id   BIGINT DEFAULT 0,
            created_at BIGINT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS acc_reviews (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id   INTEGER NOT NULL UNIQUE,
            tg_id      BIGINT NOT NULL,
            cat_id     INTEGER NOT NULL,
            stars      INTEGER NOT NULL DEFAULT 5,
            created_at BIGINT NOT NULL
        )""",
        # ---- Làm đẹp shop: ảnh bìa, lời đánh giá, nhắc đánh giá ----
        "ALTER TABLE acc_categories ADD COLUMN cover_photo TEXT DEFAULT ''",
        "ALTER TABLE acc_reviews ADD COLUMN comment TEXT DEFAULT ''",
        "ALTER TABLE acc_orders ADD COLUMN review_nudged_at BIGINT DEFAULT 0",
        """CREATE TABLE IF NOT EXISTS suppliers (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            contact    TEXT DEFAULT '',
            rating     INTEGER DEFAULT 0,
            created_at BIGINT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS acc_batches (
            batch        TEXT PRIMARY KEY,
            cat_id       INTEGER NOT NULL DEFAULT 0,
            supplier_id  INTEGER DEFAULT 0,
            cost_per_acc BIGINT DEFAULT 0,
            created_at   BIGINT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS supplier_scores (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_id INTEGER NOT NULL,
            batch       TEXT DEFAULT '',
            total       INTEGER DEFAULT 0,
            alive       INTEGER DEFAULT 0,
            note        TEXT DEFAULT '',
            scored_at   BIGINT NOT NULL
        )""",
        "ALTER TABLE acc_stock ADD COLUMN stale_warned INTEGER DEFAULT 0",
        "ALTER TABLE acc_orders ADD COLUMN followup_sent INTEGER DEFAULT 0",
        "ALTER TABLE acc_orders ADD COLUMN delivered_at BIGINT DEFAULT 0",
        # Van hanh tu dong: sinh nhat + chong spam canh bao (xem app/ops.py)
        "ALTER TABLE tg_users ADD COLUMN dob TEXT DEFAULT ''",
        "ALTER TABLE tg_users ADD COLUMN dob_set_at BIGINT DEFAULT 0",
        "ALTER TABLE tg_users ADD COLUMN birthday_gift_year INTEGER DEFAULT 0",
        """CREATE TABLE IF NOT EXISTS ops_alerts (
            kind       TEXT NOT NULL,
            ref        TEXT NOT NULL,
            sent_at    BIGINT NOT NULL,
            PRIMARY KEY (kind, ref)
        )""",
        # Luu tru ben vung cho FSM aiogram + cache ket qua check file/cookie
        # (song sot qua restart backend; xem app/persist.py)
        """CREATE TABLE IF NOT EXISTS fsm_storage (
            bot_id     BIGINT NOT NULL,
            chat_id    BIGINT NOT NULL,
            user_id    BIGINT NOT NULL,
            state      TEXT,
            data       TEXT,
            updated_at BIGINT NOT NULL,
            PRIMARY KEY (bot_id, chat_id, user_id)
        )""",
        """CREATE TABLE IF NOT EXISTS check_cache (
            chat_id    BIGINT NOT NULL,
            user_id    BIGINT NOT NULL,
            kind       TEXT NOT NULL,
            payload    TEXT NOT NULL,
            blob1      BLOB,
            updated_at BIGINT NOT NULL,
            PRIMARY KEY (chat_id, user_id, kind)
        )""",
        # Gio hang shop acc: chon nhieu loai acc roi thanh toan 1 lan
        """CREATE TABLE IF NOT EXISTS cart_items (
            tg_id      BIGINT NOT NULL,
            cat_id     INTEGER NOT NULL,
            qty        INTEGER NOT NULL DEFAULT 1,
            added_at   BIGINT NOT NULL,
            PRIMARY KEY (tg_id, cat_id)
        )""",
        # 2026-09-24: cấu hình nhập kho tự động từng gian hàng (từ tab Sheet riêng)
        """CREATE TABLE IF NOT EXISTS stall_import_cfg (
            stall        TEXT PRIMARY KEY,
            enabled      INTEGER NOT NULL DEFAULT 0,
            interval_min INTEGER NOT NULL DEFAULT 60,
            last_run     BIGINT NOT NULL DEFAULT 0,
            cat_id       INTEGER NOT NULL DEFAULT 0,
            supplier_id  INTEGER NOT NULL DEFAULT 0,
            cost         INTEGER NOT NULL DEFAULT 0
        )""",
    ]:
        try:
            c.execute(sql)
        except Exception:
            pass
    try:
        # 2026-09-21: don hang luu toan bo thong tin acc (ban xong xoa acc khoi kho)
        for _col in ["uid TEXT DEFAULT ''", "password TEXT DEFAULT ''",
                     "created_date TEXT DEFAULT ''", "backup_mail TEXT DEFAULT ''",
                     "note TEXT DEFAULT ''", "totp TEXT DEFAULT ''",
                     "cookie TEXT DEFAULT ''", "token TEXT DEFAULT ''",
                     "batch TEXT DEFAULT ''"]:
            try:
                c.execute(f"ALTER TABLE acc_orders ADD COLUMN {_col}")
            except Exception:
                pass
        if get_setting("order_details_migrated") != "1":
            c.execute("""UPDATE acc_orders SET
                uid=(SELECT s.uid FROM acc_stock s WHERE s.id=acc_orders.stock_id),
                password=(SELECT s.password FROM acc_stock s WHERE s.id=acc_orders.stock_id),
                created_date=(SELECT s.created_date FROM acc_stock s WHERE s.id=acc_orders.stock_id),
                backup_mail=(SELECT s.backup_mail FROM acc_stock s WHERE s.id=acc_orders.stock_id),
                note=(SELECT s.note FROM acc_stock s WHERE s.id=acc_orders.stock_id),
                totp=(SELECT s.totp FROM acc_stock s WHERE s.id=acc_orders.stock_id),
                cookie=(SELECT s.cookie FROM acc_stock s WHERE s.id=acc_orders.stock_id),
                token=(SELECT s.token FROM acc_stock s WHERE s.id=acc_orders.stock_id),
                batch=(SELECT s.batch FROM acc_stock s WHERE s.id=acc_orders.stock_id)
                WHERE (uid='' OR uid IS NULL)""")
            c.commit()
            set_setting("order_details_migrated", "1")
        # Doi warranty_hours tu GIO sang PHUT (cho phep BH dang 30p) - chay 1 lan duy nhat
        if get_setting("warranty_min_migrated") != "1":
            c.execute("UPDATE acc_categories SET warranty_hours = warranty_hours * 60")
            c.commit()
            set_setting("warranty_min_migrated", "1")
        # Them cot evidence cho bang bao hanh (anh bang chung log sai mk) - chay 1 lan
        if get_setting("warranty_evidence_migrated") != "1":
            try:
                c.execute("ALTER TABLE acc_warranty_claims ADD COLUMN evidence TEXT DEFAULT ''")
            except Exception:
                pass
            c.commit()
            set_setting("warranty_evidence_migrated", "1")
    except Exception:
        pass
def create_user_list(tg_id: int, name: str, platform: str = 'fb') -> tuple[bool, str]:
    """Returns (success, message)"""
    with _lock:
        c = get_conn()
        try:
            c.execute(
                "INSERT INTO user_lists(tg_id, name, platform, created_at) VALUES(?,?,?,?)",
                (tg_id, name, platform, int(time.time()))
            )
            c.commit()
            return True, "ok"
        except Exception:
            return False, "Danh sách này đã tồn tại!"


def delete_user_list(tg_id: int, name: str) -> bool:
    with _lock:
        c = get_conn()
        lst = c.execute("SELECT id FROM user_lists WHERE tg_id=? AND name=?", (tg_id, name)).fetchone()
        if not lst:
            return False
        c.execute("DELETE FROM user_list_items WHERE list_id=?", (lst["id"],))
        c.execute("DELETE FROM user_lists WHERE id=?", (lst["id"],))
        c.commit()
        return True


def get_user_lists(tg_id: int) -> list:
    c = get_conn()
    return c.execute(
        "SELECT ul.*, COUNT(uli.id) as item_count FROM user_lists ul "
        "LEFT JOIN user_list_items uli ON ul.id = uli.list_id "
        "WHERE ul.tg_id=? GROUP BY ul.id ORDER BY ul.created_at DESC",
        (tg_id,)
    ).fetchall()


def add_to_user_list(tg_id: int, list_name: str, value: str, note: str = '') -> tuple[bool, str]:
    """Returns (success, message)"""
    with _lock:
        c = get_conn()
        lst = c.execute("SELECT id FROM user_lists WHERE tg_id=? AND name=?", (tg_id, list_name)).fetchone()
        if not lst:
            return False, f"Danh sách '{list_name}' không tồn tại! Dùng /newlist {list_name} để tạo."
        exists = c.execute("SELECT id FROM user_list_items WHERE list_id=? AND value=?", (lst["id"], value)).fetchone()
        if exists:
            return False, f"'{value}' đã có trong danh sách này rồi!"
        c.execute(
            "INSERT INTO user_list_items(list_id, tg_id, value, note, added_at) VALUES(?,?,?,?,?)",
            (lst["id"], tg_id, value, note, int(time.time()))
        )
        c.commit()
        return True, "ok"


def get_list_items(tg_id: int, list_name: str) -> list:
    c = get_conn()
    lst = c.execute("SELECT id FROM user_lists WHERE tg_id=? AND name=?", (tg_id, list_name)).fetchone()
    if not lst:
        return []
    return c.execute("SELECT * FROM user_list_items WHERE list_id=? ORDER BY added_at ASC", (lst["id"],)).fetchall()


def remove_from_user_list(tg_id: int, list_name: str, value: str) -> bool:
    with _lock:
        c = get_conn()
        lst = c.execute("SELECT id FROM user_lists WHERE tg_id=? AND name=?", (tg_id, list_name)).fetchone()
        if not lst:
            return False
        cur = c.execute("DELETE FROM user_list_items WHERE list_id=? AND value=?", (lst["id"], value))
        c.commit()
        return cur.rowcount > 0


# ─── CHECK HISTORY ────────────────────────────────────────────────────────────

def add_check_history(tg_id: int, platform: str, target: str, result: str = '') -> None:
    with _lock:
        c = get_conn()
        c.execute(
            "INSERT INTO check_history(tg_id, platform, target, result, checked_at) VALUES(?,?,?,?,?)",
            (tg_id, platform, target, result, int(time.time()))
        )
        # Keep only last 100 records per user
        c.execute(
            "DELETE FROM check_history WHERE tg_id=? AND id NOT IN "
            "(SELECT id FROM check_history WHERE tg_id=? ORDER BY checked_at DESC LIMIT 100)",
            (tg_id, tg_id)
        )
        c.commit()


def get_check_history(tg_id: int, platform: str = None, days: int = 7) -> list:
    c = get_conn()
    since = int(time.time()) - days * 86400
    if platform:
        return c.execute(
            "SELECT * FROM check_history WHERE tg_id=? AND platform=? AND checked_at>=? ORDER BY checked_at DESC LIMIT 50",
            (tg_id, platform, since)
        ).fetchall()
    return c.execute(
        "SELECT * FROM check_history WHERE tg_id=? AND checked_at>=? ORDER BY checked_at DESC LIMIT 50",
        (tg_id, since)
    ).fetchall()


# ─── PERSONAL STATS ─────────────────────────────────────────────────────────

def get_user_stats(tg_id: int) -> dict:
    c = get_conn()
    total_checks = c.execute("SELECT COUNT(*) as c FROM check_history WHERE tg_id=?", (tg_id,)).fetchone()["c"]
    fb_tracks = c.execute("SELECT COUNT(*) as c FROM fb_tracks WHERE tg_user_id=? AND active=1", (tg_id,)).fetchone()["c"]
    tk_tracks = c.execute("SELECT COUNT(*) as c FROM tracks WHERE tg_user_id=? AND active=1", (tg_id,)).fetchone()["c"]
    ig_tracks = c.execute("SELECT COUNT(*) as c FROM ig_tracks WHERE tg_user_id=? AND active=1", (tg_id,)).fetchone()["c"]
    try:
        zalo_tracks = c.execute("SELECT COUNT(*) as c FROM zalo_tracks WHERE tg_user_id=? AND active=1", (tg_id,)).fetchone()["c"]
    except Exception:
        zalo_tracks = 0
    user_lists = c.execute("SELECT COUNT(*) as c FROM user_lists WHERE tg_id=?", (tg_id,)).fetchone()["c"]
    user = c.execute("SELECT created_at, vip_level, total_topup, balance FROM tg_users WHERE tg_id=?", (tg_id,)).fetchone()
    checkin = c.execute("SELECT streak, total_checkins FROM daily_checkins WHERE tg_id=?", (tg_id,)).fetchone()
    return {
        "total_checks": total_checks,
        "fb_tracks": fb_tracks,
        "tk_tracks": tk_tracks,
        "ig_tracks": ig_tracks,
        "zalo_tracks": zalo_tracks,
        "user_lists": user_lists,
        "created_at": user["created_at"] if user else 0,
        "vip_level": user["vip_level"] if user else 0,
        "total_topup": user["total_topup"] if user else 0,
        "balance": user["balance"] if user else 0,
        "streak": checkin["streak"] if checkin else 0,
        "total_checkins": checkin["total_checkins"] if checkin else 0,
    }


# ─── LEADERBOARD ─────────────────────────────────────────────────────────────

def get_leaderboard(kind: str = 'topup', limit: int = 10) -> list:
    """kind: 'topup' | 'ref'"""
    c = get_conn()
    if kind == 'ref':
        return c.execute(
            "SELECT tg_id, username, name, ref_earnings FROM tg_users "
            "WHERE ref_earnings > 0 ORDER BY ref_earnings DESC LIMIT ?",
            (limit,)
        ).fetchall()
    # default: topup this month
    this_month_start = int(time.mktime(time.strptime(time.strftime("%Y-%m-01"), "%Y-%m-%d")))
    return c.execute(
        "SELECT t.tg_id, t.username, t.name, SUM(tx.amount) as monthly_topup "
        "FROM tg_users t JOIN txns tx ON t.tg_id = tx.tg_id "
        "WHERE tx.ts >= ? AND tx.amount > 0 "
        "GROUP BY t.tg_id ORDER BY monthly_topup DESC LIMIT ?",
        (this_month_start, limit)
    ).fetchall()


# ─── TRANSFER BALANCE ────────────────────────────────────────────────────────

def transfer_balance(from_id: int, to_id: int, amount: int) -> tuple[bool, str]:
    """Transfer balance from one user to another. Returns (success, message)."""
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return False, "Số tiền không hợp lệ."
    if amount <= 0:
        return False, "Số tiền chuyển phải lớn hơn 0."
    if from_id == to_id:
        return False, "Không thể chuyển tiền cho chính mình."
    with _lock:
        c = get_conn()
        sender = c.execute("SELECT balance FROM tg_users WHERE tg_id=?", (from_id,)).fetchone()
        if not sender:
            return False, "Không tìm thấy tài khoản người gửi."
        if (sender["balance"] or 0) < amount:
            return False, f"Số dư không đủ! (Hiện có: {sender['balance']:,.0f}đ)"
        receiver = c.execute("SELECT tg_id, name FROM tg_users WHERE tg_id=?", (to_id,)).fetchone()
        if not receiver:
            return False, "Không tìm thấy người nhận."
        ts = int(time.time())
        c.execute("UPDATE tg_users SET balance = balance - ? WHERE tg_id=?", (amount, from_id))
        c.execute("UPDATE tg_users SET balance = balance + ? WHERE tg_id=?", (amount, to_id))
        c.execute("INSERT INTO txns(ts, tg_id, amount, reason) VALUES(?,?,?,?)",
                  (ts, from_id, -amount, f"Chuyển tiền cho {to_id}"))
        c.execute("INSERT INTO txns(ts, tg_id, amount, reason) VALUES(?,?,?,?)",
                  (ts, to_id, amount, f"Nhận tiền từ {from_id}"))
        c.commit()
        return True, receiver["name"] or str(to_id)


# ─── ALERT PAUSE / SNOOZE ───────────────────────────────────────────────────

def pause_alert(tg_id: int, alert_id: int) -> bool:
    with _lock:
        c = get_conn()
        cur = c.execute(
            "UPDATE alert_rules SET is_paused=1 WHERE id=? AND tg_id=?",
            (alert_id, str(tg_id))
        )
        c.commit()
        return cur.rowcount > 0


def resume_alert(tg_id: int, alert_id: int) -> bool:
    with _lock:
        c = get_conn()
        cur = c.execute(
            "UPDATE alert_rules SET is_paused=0, snooze_until=0 WHERE id=? AND tg_id=?",
            (alert_id, str(tg_id))
        )
        c.commit()
        return cur.rowcount > 0


def snooze_alert(tg_id: int, alert_id: int, hours: float) -> bool:
    until = int(time.time()) + int(hours * 3600)
    with _lock:
        c = get_conn()
        cur = c.execute(
            "UPDATE alert_rules SET snooze_until=?, is_paused=0 WHERE id=? AND tg_id=?",
            (until, alert_id, str(tg_id))
        )
        c.commit()
        return cur.rowcount > 0


# ─── ADMIN: SET BALANCE ─────────────────────────────────────────────────────

def admin_set_balance(tg_id: int, amount: int, reason: str = "Admin set balance") -> bool:
    with _lock:
        c = get_conn()
        old = c.execute("SELECT balance FROM tg_users WHERE tg_id=?", (tg_id,)).fetchone()
        if not old:
            return False
        diff = amount - (old["balance"] or 0)
        c.execute("UPDATE tg_users SET balance=? WHERE tg_id=?", (amount, tg_id))
        c.execute("INSERT INTO txns(ts, tg_id, amount, reason) VALUES(?,?,?,?)",
                  (int(time.time()), tg_id, diff, reason))
        c.commit()
        return True


# ─── ADMIN: BAN / UNBAN ──────────────────────────────────────────────────────

def ban_user(tg_id: int) -> bool:
    with _lock:
        c = get_conn()
        cur = c.execute("UPDATE tg_users SET is_blocked=1 WHERE tg_id=?", (tg_id,))
        c.commit()
        return cur.rowcount > 0


def unban_user(tg_id: int) -> bool:
    with _lock:
        c = get_conn()
        cur = c.execute("UPDATE tg_users SET is_blocked=0 WHERE tg_id=?", (tg_id,))
        c.commit()
        return cur.rowcount > 0


# ─── ADMIN: SET VIP ─────────────────────────────────────────────────────────

def admin_set_vip(tg_id: int, vip_level: int, days: int = 0) -> bool:
    with _lock:
        c = get_conn()
        updates = "vip_level=?"
        params = [vip_level]
        if days > 0:
            user = c.execute("SELECT sub_until FROM tg_users WHERE tg_id=?", (tg_id,)).fetchone()
            base = max(int(time.time()), (user["sub_until"] or 0) if user else 0)
            new_until = base + days * 86400
            updates += ", sub_until=?"
            params.append(new_until)
        params.append(tg_id)
        cur = c.execute(f"UPDATE tg_users SET {updates} WHERE tg_id=?", tuple(params))
        c.commit()
        return cur.rowcount > 0


# ─── ADMIN: FIND USER ───────────────────────────────────────────────────────

def find_user_by_username(username: str):
    c = get_conn()
    username_clean = username.lstrip('@')
    return c.execute(
        "SELECT * FROM tg_users WHERE username=? OR username=?",
        (username_clean, '@' + username_clean)
    ).fetchone()


# ─── ADMIN: REVENUE STATS ───────────────────────────────────────────────────

def get_revenue_stats() -> dict:
    c = get_conn()
    today_start = int(time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d")))
    month_start = int(time.mktime(time.strptime(time.strftime("%Y-%m-01"), "%Y-%m-%d")))
    revenue_today = c.execute("SELECT SUM(amount) as s FROM txns WHERE ts>=? AND amount>0", (today_start,)).fetchone()["s"] or 0
    revenue_month = c.execute("SELECT SUM(amount) as s FROM txns WHERE ts>=? AND amount>0", (month_start,)).fetchone()["s"] or 0
    revenue_total = c.execute("SELECT SUM(amount) as s FROM txns WHERE amount>0").fetchone()["s"] or 0
    total_users = c.execute("SELECT COUNT(*) as c FROM tg_users").fetchone()["c"]
    active_today = c.execute("SELECT COUNT(DISTINCT tg_id) as c FROM check_history WHERE checked_at>=?", (today_start,)).fetchone()["c"]
    new_today = c.execute("SELECT COUNT(*) as c FROM tg_users WHERE created_at>=?", (today_start,)).fetchone()["c"]
    pending_topup = c.execute("SELECT COUNT(*) as c FROM txns WHERE ts>=? AND amount=0", (month_start,)).fetchone()["c"]
    return {
        "revenue_today": revenue_today,
        "revenue_month": revenue_month,
        "revenue_total": revenue_total,
        "total_users": total_users,
        "active_today": active_today,
        "new_today": new_today,
        "pending_topup": pending_topup,
    }


def get_all_users_for_broadcast(vip_only: bool = False, inactive_days: int = 0) -> list:
    c = get_conn()
    if vip_only:
        return c.execute("SELECT tg_id FROM tg_users WHERE vip_level > 0 AND is_blocked=0").fetchall()
    if inactive_days > 0:
        cutoff = int(time.time()) - inactive_days * 86400
        return c.execute(
            "SELECT tg_id FROM tg_users WHERE is_blocked=0 AND tg_id NOT IN "
            "(SELECT DISTINCT tg_id FROM check_history WHERE checked_at >= ?)",
            (cutoff,)
        ).fetchall()
    return c.execute("SELECT tg_id FROM tg_users WHERE is_blocked=0").fetchall()


def set_daily_report_hour(tg_id: int, hour: int) -> bool:
    """Cấu hình giờ nhận báo cáo tự động định kỳ cho user (-1 = Tắt, 0..23 = Giờ)."""
    with _lock:
        c = get_conn()
        c.execute("UPDATE tg_users SET daily_report_hour=? WHERE tg_id=?", (hour, tg_id))
        c.commit()
        return True


def set_dob(tg_id: int, dob_iso: str) -> None:
    """Lưu ngày sinh (YYYY-MM-DD) của user."""
    with _lock:
        c = get_conn()
        c.execute("UPDATE tg_users SET dob=?, dob_set_at=? WHERE tg_id=?",
                  (dob_iso, int(time.time()), tg_id))
        c.commit()


def mark_birthday_gift(tg_id: int, year: int) -> None:
    """Đánh dấu đã tặng quà sinh nhật năm `year`."""
    with _lock:
        c = get_conn()
        c.execute("UPDATE tg_users SET birthday_gift_year=? WHERE tg_id=?", (year, tg_id))
        c.commit()


def ops_alert_dedup(kind: str, ref: str, cooldown_sec: int) -> bool:
    """True nếu được phép gửi cảnh báo (chưa gửi trong cooldown); đồng thời
    đánh dấu đã gửi để lần sau không spam."""
    now = int(time.time())
    with _lock:
        c = get_conn()
        r = c.execute("SELECT sent_at FROM ops_alerts WHERE kind=? AND ref=?",
                      (kind, ref)).fetchone()
        if r and now - r["sent_at"] < cooldown_sec:
            return False
        c.execute("INSERT INTO ops_alerts(kind, ref, sent_at) VALUES(?,?,?) "
                  "ON CONFLICT(kind, ref) DO UPDATE SET sent_at=excluded.sent_at",
                  (kind, ref, now))
        c.commit()
        return True


def get_users_for_daily_report(hour: int) -> list:
    """Lấy danh sách user đã đăng ký báo cáo vào giờ `hour`."""
    c = get_conn()
    return c.execute("SELECT * FROM tg_users WHERE daily_report_hour=? AND is_blocked=0", (hour,)).fetchall()



# ─── CREDITS (lượt check) ──────────────────────────────────────────────
def get_credits(tg_id: int) -> int:
    """Số credits còn lại của user (0 nếu chưa có cột/user)."""
    try:
        with _lock:
            c = get_conn()
            row = c.execute("SELECT credits FROM tg_users WHERE tg_id=?", (tg_id,)).fetchone()
            return int(row["credits"] or 0) if row else 0
    except Exception:
        return 0


def add_credits(tg_id: int, n: int, reason: str = "") -> int:
    """Cộng/trừ credits cho user, ghi log. Trả về số dư mới."""
    n = int(n)
    with _lock:
        c = get_conn()
        c.execute("UPDATE tg_users SET credits = COALESCE(credits,0) + ? WHERE tg_id=?", (n, tg_id))
        c.execute(
            "INSERT INTO credit_txns(ts, tg_id, delta, reason) VALUES(?,?,?,?)",
            (int(time.time()), tg_id, n, reason),
        )
        c.commit()
        row = c.execute("SELECT credits FROM tg_users WHERE tg_id=?", (tg_id,)).fetchone()
        return int(row["credits"] or 0) if row else 0


def consume_credits(tg_id: int, n: int) -> bool:
    """Trừ n credits nếu đủ. Atomic — trả về True nếu trừ thành công."""
    n = int(n)
    if n <= 0:
        return True
    with _lock:
        c = get_conn()
        row = c.execute("SELECT credits FROM tg_users WHERE tg_id=?", (tg_id,)).fetchone()
        cur = int(row["credits"] or 0) if row else 0
        if cur < n:
            return False
        c.execute("UPDATE tg_users SET credits = credits - ? WHERE tg_id=?", (n, tg_id))
        c.execute(
            "INSERT INTO credit_txns(ts, tg_id, delta, reason) VALUES(?,?,?,?)",
            (int(time.time()), tg_id, -n, "check_bulk"),
        )
        c.commit()
        return True


# ─── RESELLER API KEYS ─────────────────────────────────────────────────
def create_reseller_key(name: str, credits: int = 0) -> dict:
    """Tạo API key mới cho reseller. Trả về dict gồm id, name, api_key, credits."""
    import secrets
    key = "rsk_" + secrets.token_urlsafe(32)
    with _lock:
        c = get_conn()
        cur = c.execute(
            "INSERT INTO reseller_keys(name, api_key, credits, created_at) VALUES(?,?,?,?)",
            (name, key, int(credits), int(time.time())),
        )
        c.commit()
        return {"id": cur.lastrowid, "name": name, "api_key": key, "credits": int(credits)}


def get_reseller_key(api_key: str):
    """Lấy reseller key còn active theo api_key. Trả về Row hoặc None."""
    try:
        c = get_conn()
        return c.execute(
            "SELECT * FROM reseller_keys WHERE api_key=? AND is_active=1", (api_key,)
        ).fetchone()
    except Exception:
        return None


def list_reseller_keys():
    c = get_conn()
    return c.execute("SELECT id, name, credits, is_active, created_at FROM reseller_keys ORDER BY id DESC").fetchall()


def set_reseller_active(key_id: int, active: bool) -> None:
    with _lock:
        c = get_conn()
        c.execute("UPDATE reseller_keys SET is_active=? WHERE id=?", (1 if active else 0, key_id))
        c.commit()


def add_reseller_credits(key_id: int, n: int) -> None:
    with _lock:
        c = get_conn()
        c.execute("UPDATE reseller_keys SET credits = credits + ? WHERE id=?", (int(n), key_id))
        c.execute(
            "INSERT INTO credit_txns(ts, key_id, delta, reason) VALUES(?,?,?,?)",
            (int(time.time()), key_id, int(n), "reseller_topup"),
        )
        c.commit()


def consume_reseller_credits(key_id: int, n: int = 1) -> bool:
    """Trừ credits của reseller key. Atomic — True nếu thành công."""
    n = int(n)
    with _lock:
        c = get_conn()
        row = c.execute("SELECT credits FROM reseller_keys WHERE id=? AND is_active=1", (key_id,)).fetchone()
        cur = int(row["credits"] or 0) if row else 0
        if cur < n:
            return False
        c.execute("UPDATE reseller_keys SET credits = credits - ? WHERE id=?", (n, key_id))
        c.commit()
        return True


def log_reseller_usage(key_id: int, endpoint: str, target: str = "", credits_used: int = 0) -> None:
    try:
        with _lock:
            c = get_conn()
            c.execute(
                "INSERT INTO reseller_usage(key_id, endpoint, target, credits_used, created_at) VALUES(?,?,?,?,?)",
                (key_id, endpoint, target or "", int(credits_used), int(time.time())),
            )
            c.commit()
    except Exception:
        pass


def get_reseller_usage(key_id: int, limit: int = 50):
    c = get_conn()
    return c.execute(
        "SELECT endpoint, target, credits_used, created_at FROM reseller_usage WHERE key_id=? ORDER BY id DESC LIMIT ?",
        (key_id, limit),
    ).fetchall()


def set_reseller_webhook(key_id: int, url: str) -> bool:
    with _lock:
        c = get_conn()
        cur = c.execute("UPDATE reseller_keys SET webhook_url=? WHERE id=?", (url or "", key_id))
        c.commit()
        return cur.rowcount > 0


def get_reseller_by_name_or_id(ref: str):
    c = get_conn()
    try:
        kid = int(ref)
        return c.execute("SELECT * FROM reseller_keys WHERE id=?", (kid,)).fetchone()
    except (ValueError, TypeError):
        return c.execute("SELECT * FROM reseller_keys WHERE name=?", (ref,)).fetchone()


# ─── PROMO CODES (flash sale giảm giá gói credit) ──────────────────────

def create_promo(code: str, pct: int, max_uses: int = 0, hours: int = 0, wallet: str = "main") -> tuple[bool, str]:
    code = (code or "").strip().upper()
    if not code or len(code) > 32:
        return False, "Mã không hợp lệ (1-32 ký tự)."
    pct = max(1, min(90, int(pct)))
    wallet = parse_wallet(wallet)
    exp = int(time.time()) + int(hours) * 3600 if hours > 0 else 0
    with _lock:
        c = get_conn()
        try:
            c.execute(
                "INSERT INTO promo_codes(code, pct, wallet, max_uses, used_count, expires_at, created_at) VALUES(?,?,?,?,?,?,?)",
                (code, pct, wallet, int(max_uses), 0, exp, int(time.time())),
            )
            c.commit()
            return True, f"Đã tạo mã <b>{code}</b> giảm <b>{pct}%</b> ({wallet_label(wallet)})."
        except Exception:
            return False, f"Mã <b>{code}</b> đã tồn tại."


def get_promo(code: str):
    c = get_conn()
    return c.execute("SELECT * FROM promo_codes WHERE code=?", ((code or "").strip().upper(),)).fetchone()


def delete_promo(code: str) -> bool:
    with _lock:
        c = get_conn()
        cur = c.execute("DELETE FROM promo_codes WHERE code=?", ((code or "").strip().upper(),))
        c.execute("DELETE FROM user_promos WHERE code=?", ((code or "").strip().upper(),))
        c.commit()
        return cur.rowcount > 0


def list_promos():
    c = get_conn()
    return c.execute("SELECT * FROM promo_codes ORDER BY id DESC LIMIT 30").fetchall()


def promo_valid(code: str) -> tuple[bool, str, object]:
    """Trả về (ok, lý_do, row)."""
    row = get_promo(code)
    if not row:
        return False, "Mã không tồn tại.", None
    now = int(time.time())
    if row["expires_at"] and row["expires_at"] < now:
        return False, "Mã đã hết hạn.", None
    if row["max_uses"] and row["used_count"] >= row["max_uses"]:
        return False, "Mã đã hết lượt dùng.", None
    return True, "", row


def set_user_promo(tg_id: int, code: str) -> None:
    with _lock:
        c = get_conn()
        c.execute(
            "INSERT INTO user_promos(tg_id, code, applied_at) VALUES(?,?,?) "
            "ON CONFLICT(tg_id) DO UPDATE SET code=excluded.code, applied_at=excluded.applied_at",
            (tg_id, code.strip().upper(), int(time.time())),
        )
        c.commit()


def get_user_promo(tg_id: int):
    c = get_conn()
    return c.execute("SELECT * FROM user_promos WHERE tg_id=?", (tg_id,)).fetchone()


def clear_user_promo(tg_id: int) -> None:
    with _lock:
        c = get_conn()
        c.execute("DELETE FROM user_promos WHERE tg_id=?", (tg_id,))
        c.commit()


def consume_promo(code: str) -> None:
    with _lock:
        c = get_conn()
        c.execute("UPDATE promo_codes SET used_count = used_count + 1 WHERE code=?", (code.strip().upper(),))
        c.commit()


def apply_user_promo(tg_id: int, price: int, wallet: str = "main") -> tuple[int, str]:
    """Áp mã giảm giá đang giữ của user vào giá — chỉ áp khi ví của mã khớp ví thanh toán.
    Trả về (giá_mới, mã_đã_dùng)."""
    wallet = parse_wallet(wallet)
    up = get_user_promo(tg_id)
    if not up:
        return price, ""
    ok, _, row = promo_valid(up["code"])
    if not ok:
        clear_user_promo(tg_id)
        return price, ""
    try:
        code_wallet = parse_wallet(row["wallet"])
    except Exception:
        code_wallet = "main"
    if code_wallet != wallet:
        return price, ""
    pct = int(row["pct"])
    new_price = int(price * (100 - pct) / 100)
    consume_promo(up["code"])
    clear_user_promo(tg_id)
    return new_price, up["code"]


def preview_user_promo(tg_id: int, price: int, wallet: str = "main") -> tuple[int, str]:
    """Xem trước giá sau khi áp mã giảm giá (KHÔNG trừ lượt dùng). Trả về (giá_mới, mã)."""
    wallet = parse_wallet(wallet)
    up = get_user_promo(tg_id)
    if not up:
        return price, ""
    ok, _, row = promo_valid(up["code"])
    if not ok:
        return price, ""
    try:
        code_wallet = parse_wallet(row["wallet"])
    except Exception:
        code_wallet = "main"
    if code_wallet != wallet:
        return price, ""
    pct = int(row["pct"])
    return int(price * (100 - pct) / 100), up["code"]


# ─── CHECK ACCURACY STATS ──────────────────────────────────────────────
def log_check_stat(tg_id: int, platform: str, target: str, result: str, via: str = "") -> None:
    """Ghi 1 lượt check vào bảng thống kê (dùng tính độ chính xác/nhất quán)."""
    try:
        with _lock:
            c = get_conn()
            c.execute(
                "INSERT INTO check_stats(tg_id, platform, target, result, via, checked_at) VALUES(?,?,?,?,?,?)",
                (tg_id, platform, target, result, via or "", int(time.time())),
            )
            # Xóa dữ liệu quá 90 ngày để bảng không phình
            c.execute("DELETE FROM check_stats WHERE checked_at < ?", (int(time.time()) - 90 * 86400,))
            c.commit()
    except Exception:
        pass


def get_accuracy_stats(tg_id: int) -> dict:
    """Thống kê độ chính xác/nhất quán của user từ check_stats (90 ngày)."""
    out = {"total": 0, "unique": 0, "live": 0, "die": 0, "error": 0,
           "rechecked": 0, "consistent": 0}
    try:
        c = get_conn()
        row = c.execute(
            "SELECT COUNT(*) n, COUNT(DISTINCT target) u FROM check_stats WHERE tg_id=?",
            (tg_id,),
        ).fetchone()
        if not row or not row["n"]:
            return out
        out["total"], out["unique"] = row["n"], row["u"]
        for r in c.execute(
            "SELECT result, COUNT(*) n FROM check_stats WHERE tg_id=? GROUP BY result", (tg_id,)
        ).fetchall():
            if r["result"] in out:
                out[r["result"]] = r["n"]
        # Độ nhất quán: các target check >= 2 lần, kết quả các lần có giống nhau không
        for r in c.execute(
            "SELECT target, COUNT(*) n, COUNT(DISTINCT result) d FROM check_stats "
            "WHERE tg_id=? GROUP BY target HAVING n >= 2", (tg_id,)
        ).fetchall():
            out["rechecked"] += 1
            if r["d"] == 1:
                out["consistent"] += 1
    except Exception:
        pass
    return out


def set_watch_alert_mode(watch_id: int, tg_id: int, mode: str) -> bool:
    """Đặt chế độ báo cho watch: 'all' (mọi thay đổi) hoặc 'die_only' (chỉ khi DIE)."""
    mode = "die_only" if mode == "die_only" else "all"
    with _lock:
        c = get_conn()
        cur = c.execute(
            "UPDATE watches SET alert_mode=? WHERE id=? AND tg_id=?", (mode, watch_id, tg_id)
        )
        c.commit()
        return cur.rowcount > 0


def get_user_watches(tg_id: int):
    c = get_conn()
    return c.execute(
        "SELECT id, uid, note, last_status, alert_mode, created_at FROM watches "
        "WHERE tg_id=? AND active=1 ORDER BY created_at DESC", (tg_id,)
    ).fetchall()


# ---------------------------------------------------------------- PayOS orders
def create_payos_order(order_code: int, tg_id: int, amount: int,
                       payment_link_id: str = "", checkout_url: str = "",
                       qr_code: str = "", target: str = "main") -> None:
    now = int(time.time())
    if target not in ("main", "shop"):
        target = "main"
    with _lock:
        c = get_conn()
        c.execute(
            "INSERT INTO payos_orders(order_code, tg_id, amount, status, "
            "payment_link_id, checkout_url, qr_code, created_at, updated_at, target) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (order_code, tg_id, amount, "PENDING",
             payment_link_id or "", checkout_url or "", qr_code or "", now, now, target),
        )
        c.commit()


def get_payos_order(order_code: int):
    c = get_conn()
    return c.execute(
        "SELECT * FROM payos_orders WHERE order_code=?", (order_code,)
    ).fetchone()


def get_user_pending_payos_order(tg_id: int, max_age_sec: int, target: str = None):
    """Đơn chờ mới nhất của user (chống tạo đơn trùng). Lọc theo ví nếu có target."""
    c = get_conn()
    sql = ("SELECT * FROM payos_orders WHERE tg_id=? AND status='PENDING' "
           "AND created_at > ?")
    params: list = [tg_id, int(time.time()) - max_age_sec]
    if target in ("main", "shop"):
        sql += " AND COALESCE(target,'main')=?"
        params.append(target)
    sql += " ORDER BY created_at DESC LIMIT 1"
    return c.execute(sql, params).fetchone()


def get_pending_payos_orders(max_age_sec: int):
    c = get_conn()
    return c.execute(
        "SELECT * FROM payos_orders WHERE status='PENDING' "
        "AND created_at > ? ORDER BY created_at",
        (int(time.time()) - max_age_sec,),
    ).fetchall()


def touch_payos_order(order_code: int) -> None:
    with _lock:
        c = get_conn()
        c.execute(
            "UPDATE payos_orders SET updated_at=? WHERE order_code=?",
            (int(time.time()), order_code),
        )
        c.commit()


def mark_payos_paid(order_code: int) -> bool:
    """Đánh dấu PAID nguyên tử — True nếu đơn đang PENDING/EXPIRED (chống cộng trùng)."""
    with _lock:
        c = get_conn()
        cur = c.execute(
            "UPDATE payos_orders SET status='PAID', updated_at=? "
            "WHERE order_code=? AND status IN ('PENDING','EXPIRED')",
            (int(time.time()), order_code),
        )
        c.commit()
        return cur.rowcount > 0


def settle_payos_order(order_code: int) -> dict:
    """Quyet toan don PayOS trong 1 transaction duy nhat.

    PENDING/EXPIRED -> PAID + cong vao vi dich (target) + total_topup + hoa hong
    F1/F2, tat ca trong cung 1 commit. Tra {'ok': True, 'target': ...} khi quyet
    toan xong, {'ok': False} khi don da duoc xu ly truoc do (webhook/poller trung).
    """
    with _lock:
        c = get_conn()
        order = c.execute(
            "SELECT tg_id, amount, COALESCE(target,'main') AS target "
            "FROM payos_orders WHERE order_code=?",
            (order_code,),
        ).fetchone()
        if not order:
            return {"ok": False}
        tg_id, amount = int(order["tg_id"]), int(order["amount"])
        target = order["target"] or "main"
        if target not in ("main", "shop"):
            target = "main"
        cur = c.execute(
            "UPDATE payos_orders SET status='PAID', updated_at=? "
            "WHERE order_code=? AND status IN ('PENDING','EXPIRED')",
            (int(time.time()), order_code),
        )
        if cur.rowcount == 0:
            return {"ok": False}
        bonuses = _credit_topup_nolock(c, tg_id, amount, "payos", target)
        c.commit()
    out: dict = {"ok": True, "target": target}
    out.update(bonuses)
    return out


def get_overdue_pending_payos_orders(max_age_sec: int) -> list:
    """Đơn PENDING đã quá TTL (poll thường không quét tới)."""
    c = get_conn()
    return c.execute(
        "SELECT * FROM payos_orders WHERE status='PENDING' AND created_at <= ?",
        (int(time.time()) - max_age_sec,)).fetchall()


def mark_payos_expired_if_pending(order_code: int) -> bool:
    """PENDING -> EXPIRED, nguyên tử. False nếu đơn đã đổi trạng thái
    (vd webhook vừa quyết toán PAID xong)."""
    with _lock:
        c = get_conn()
        cur = c.execute(
            "UPDATE payos_orders SET status='EXPIRED', updated_at=? "
            "WHERE order_code=? AND status='PENDING'",
            (int(time.time()), order_code),
        )
        c.commit()
        return cur.rowcount > 0


def get_recently_expired_payos_orders(max_age_sec: int) -> list:
    """Đơn EXPIRED trong max_age_sec chưa kiểm tra lần cuối (khách trả trễ)."""
    c = get_conn()
    try:
        return c.execute(
            "SELECT * FROM payos_orders WHERE status='EXPIRED' "
            "AND COALESCE(final_checked,0)=0 AND created_at > ? "
            "ORDER BY created_at DESC LIMIT 50",
            (int(time.time()) - max_age_sec,)).fetchall()
    except Exception:
        # DB cũ chưa có cột final_checked
        return []


def mark_payos_final_checked(order_code: int) -> None:
    try:
        c = get_conn()
        c.execute("UPDATE payos_orders SET final_checked=1 WHERE order_code=?",
                  (order_code,))
        c.commit()
    except Exception:
        pass


def mark_payos_status(order_code: int, status: str) -> None:
    with _lock:
        c = get_conn()
        c.execute(
            "UPDATE payos_orders SET status=?, updated_at=? WHERE order_code=?",
            (status, int(time.time()), order_code),
        )
        c.commit()


def payos_stats_today() -> dict:
    """Thống kê đơn PayOS hôm nay cho admin."""
    import datetime
    start = int(datetime.datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp())
    c = get_conn()
    out = {"pending": 0, "paid": 0, "paid_amount": 0}
    try:
        for r in c.execute(
            "SELECT status, COUNT(*) n, COALESCE(SUM(amount),0) s FROM payos_orders "
            "WHERE created_at >= ? GROUP BY status", (start,)
        ).fetchall():
            st = str(r["status"]).upper()
            if st == "PENDING":
                out["pending"] = r["n"]
            elif st == "PAID":
                out["paid"] = r["n"]
                out["paid_amount"] = r["s"]
    except Exception:
        pass
    return out


# ============================ SHOP ACC FB ============================
def acc_category_add(name: str, price: int, warranty_hours: int,
                     description: str = "", stall: str = "Acc Facebook",
                     live_check: int = 1) -> int:
    """Thêm loại acc. Trả id, -1 nếu tên đã tồn tại."""
    now = int(time.time())
    with _lock:
        c = get_conn()
        try:
            cur = c.execute(
                "INSERT INTO acc_categories(name, price, warranty_hours, description, active, created_at, stall, live_check) "
                "VALUES(?,?,?,?,1,?,?,?)",
                (name.strip(), int(price), int(warranty_hours), description or "", now,
                 (stall or "Acc Facebook").strip(), int(live_check)),
            )
            cid = cur.lastrowid
            c.commit()
            return cid
        except Exception:
            return -1


def acc_stall_list() -> list:
    """Danh sách gian hàng (distinct stall), kèm số loại acc."""
    c = get_conn()
    try:
        rows = c.execute(
            "SELECT COALESCE(stall,'Acc Facebook') AS stall, COUNT(*) n "
            "FROM acc_categories WHERE active=1 GROUP BY stall ORDER BY MIN(id)"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return [{"stall": "Acc Facebook", "n": 0}]


def acc_stock_count_stall(stall: str) -> int:
    """Tổng acc AVAILABLE của 1 gian hàng."""
    c = get_conn()
    try:
        r = c.execute(
            "SELECT COUNT(*) n FROM acc_stock s JOIN acc_categories c ON c.id=s.cat_id "
            "WHERE s.status='AVAILABLE' AND COALESCE(c.stall,'Acc Facebook')=?",
            (stall,)).fetchone()
        return int(r["n"]) if r else 0
    except Exception:
        return 0


# ================== NHẬP KHO TỰ ĐỘNG THEO GIAN HÀNG ==================
def stall_import_cfg_get(stall: str) -> dict:
    """Cấu hình nhập kho tự động của 1 gian hàng (mặc định: tắt, 60 phút/lần)."""
    c = get_conn()
    try:
        r = c.execute("SELECT * FROM stall_import_cfg WHERE stall=?", (stall,)).fetchone()
        if r:
            return dict(r)
    except Exception:
        pass
    return {"stall": stall, "enabled": 0, "interval_min": 60, "last_run": 0,
            "cat_id": 0, "supplier_id": 0, "cost": 0}


def stall_import_cfg_set(stall: str, **kw) -> None:
    """Lưu cấu hình nhập tự động: enabled, interval_min, last_run, cat_id, supplier_id, cost."""
    allowed = {"enabled", "interval_min", "last_run", "cat_id", "supplier_id", "cost"}
    vals = {k: int(kw[k]) for k in allowed if k in kw}
    if not vals:
        return
    with _lock:
        c = get_conn()
        try:
            c.execute("INSERT INTO stall_import_cfg(stall) VALUES(?) "
                      "ON CONFLICT(stall) DO NOTHING", (stall,))
            sets = ", ".join(f"{k}=?" for k in vals)
            c.execute(f"UPDATE stall_import_cfg SET {sets} WHERE stall=?",
                      (*vals.values(), stall))
            c.commit()
        except Exception:
            pass


def stall_import_cfg_due(now: int) -> list:
    """Các gian hàng đang BẬT nhập tự động và đã đến giờ quét."""
    c = get_conn()
    try:
        rows = c.execute("SELECT * FROM stall_import_cfg WHERE enabled=1").fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        d = dict(r)
        iv = max(5, min(10080, int(d.get("interval_min") or 60)))
        if now - int(d.get("last_run") or 0) >= iv * 60:
            out.append(d)
    return out


def acc_category_list(active_only: bool = True, include_hidden: bool = False) -> list:
    c = get_conn()
    q = "SELECT * FROM acc_categories"
    conds = []
    if active_only:
        conds.append("active=1")
    if not include_hidden:
        conds.append("hidden=0")
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY id"
    return c.execute(q).fetchall()


def acc_category_get(cat_id: int):
    return get_conn().execute(
        "SELECT * FROM acc_categories WHERE id=?", (cat_id,)).fetchone()


def acc_stock_delete_available(cat_id: int) -> int:
    """Xóa toàn bộ acc CHƯA BÁN trong kho của 1 loại. Trả số acc đã xóa."""
    with _lock:
        c = get_conn()
        cur = c.execute("DELETE FROM acc_stock WHERE cat_id=? AND status='AVAILABLE'",
                        (cat_id,))
        c.commit()
        return cur.rowcount


def acc_category_delete_hard(cat_id: int) -> tuple[bool, str]:
    """Xóa loại acc. Chỉ cho xóa khi không còn acc CHƯA BÁN.
    - Không còn acc nào: xóa hẳn dòng loại.
    - Còn acc ĐÃ BÁN: đổi tên loại thành "<tên> [xóa ...]" + ẩn đi (giữ lịch sử
      đơn hàng/bảo hành của khách), tên gốc được nhả để tạo lại.
    Trả (ok, thông_báo)."""
    import datetime as _dt
    with _lock:
        c = get_conn()
        cat = c.execute("SELECT * FROM acc_categories WHERE id=?",
                        (cat_id,)).fetchone()
        if not cat:
            return (False, "Không tìm thấy loại này.")
        n_av = c.execute("SELECT COUNT(*) n FROM acc_stock WHERE cat_id=? "
                         "AND status='AVAILABLE'", (cat_id,)).fetchone()["n"]
        if n_av:
            return (False, f"Loại này còn <b>{n_av}</b> acc chưa bán trong kho.\n"
                           f"Xóa hết bằng <code>/xoakho {cat_id} yes</code> trước "
                           f"(nhớ <code>/xuatkho {cat_id}</code> sao lưu nếu cần).")
        n_sold = c.execute("SELECT COUNT(*) n FROM acc_stock WHERE cat_id=? "
                           "AND status!='AVAILABLE'", (cat_id,)).fetchone()["n"]
        if n_sold:
            stamp = _dt.datetime.now().strftime("%d/%m")
            new_name = f"{cat['name']} [xóa {stamp}]"
            c.execute("UPDATE acc_categories SET name=?, active=0, hidden=1 WHERE id=?",
                      (new_name, cat_id))
            c.commit()
            return (True, f"Đã xóa loại <b>#{cat_id}</b>. "
                           f"Giữ lại {n_sold} acc đã bán trong lịch sử "
                           f"(loại cũ đổi tên thành {new_name}).")
        c.execute("DELETE FROM acc_categories WHERE id=?", (cat_id,))
        c.commit()
        return (True, f"Đã xóa hẳn loại <b>#{cat_id}</b> khỏi hệ thống.")


def acc_category_update(cat_id: int, **kw) -> bool:
    allowed = {"name", "price", "warranty_hours", "description", "active",
               "credit_bonus", "low_threshold", "scarcity_pct",
               "mystery_eligible", "hidden", "cover_photo"}
    sets, params = [], []
    for k, v in kw.items():
        if k in allowed:
            sets.append(f"{k}=?")
            params.append(v)
    if not sets:
        return False
    params.append(cat_id)
    with _lock:
        c = get_conn()
        try:
            c.execute(f"UPDATE acc_categories SET {', '.join(sets)} WHERE id=?", tuple(params))
            c.commit()
            return True
        except Exception:
            return False


def acc_stock_add_batch(cat_id: int, rows: list[dict], batch: str = "",
                        supplier_id: int = 0, cost_per_acc: int = 0) -> tuple[int, int]:
    """Nhập kho. rows: list dict với keys uid,password,created_date,backup_mail,note,totp,cookie,token.
    batch: nhãn lô nhập (VD "Lô 21/09 10:52"). Trả (added, skipped)."""
    now = int(time.time())
    added = skipped = 0
    with _lock:
        c = get_conn()
        for r in rows:
            uid = (r.get("uid") or "").strip()
            if not uid:
                skipped += 1
                continue
            c.execute(
                "INSERT INTO acc_stock(cat_id, uid, password, created_date, backup_mail, "
                "note, totp, cookie, token, status, added_at, batch, sheet_ref) "
                "VALUES(?,?,?,?,?,?,?,?,?,'AVAILABLE',?,?,?)",
                (cat_id, uid, r.get("password", ""), r.get("created_date", ""),
                 r.get("backup_mail", ""), r.get("note", ""), r.get("totp", ""),
                 r.get("cookie", ""), r.get("token", ""), now, batch or "",
                 r.get("_sheet_ref") or ""),
            )
            added += 1
        if added:
            if batch:
                try:
                    c.execute(
                        "INSERT INTO acc_batches(batch, cat_id, supplier_id, cost_per_acc, created_at) "
                        "VALUES(?,?,?,?,?) ON CONFLICT(batch) DO UPDATE SET "
                        "cat_id=excluded.cat_id, supplier_id=excluded.supplier_id, "
                        "cost_per_acc=excluded.cost_per_acc",
                        (batch, cat_id, int(supplier_id or 0),
                         int(cost_per_acc or 0), now))
                except Exception:
                    pass
        c.commit()
    return added, skipped


def acc_sold_count(cat_id: int) -> int:
    """Tổng số acc đã bán của 1 loại (social proof cho shop)."""
    try:
        r = get_conn().execute(
            "SELECT COUNT(*) AS c FROM acc_orders WHERE cat_id=?", (cat_id,)).fetchone()
        return int(r["c"] or 0)
    except Exception:
        return 0


def acc_stock_sold_count(cat_id: int) -> int:
    """Số acc trong kho đã được đánh dấu SOLD (đã bán, giữ lại lịch sử)."""
    try:
        r = get_conn().execute(
            "SELECT COUNT(*) n FROM acc_stock WHERE cat_id=? AND status='SOLD'",
            (cat_id,)).fetchone()
        return int(r["n"] or 0)
    except Exception:
        return 0


def acc_sold_unmarked_sheet(limit: int = 200) -> list:
    """Các acc đã bán, nhập từ Google Sheet, chưa đẩy dấu 'đã bán' lên Sheet.
    Trả list dict {id, uid, tab, row, sold_at}."""
    out = []
    try:
        rows = get_conn().execute(
            "SELECT id, uid, sheet_ref, sold_at FROM acc_stock "
            "WHERE status='SOLD' AND sheet_ref != '' AND sheet_marked=0 "
            "ORDER BY id LIMIT ?", (int(limit),)).fetchall()
    except Exception:
        return out
    for r in rows:
        try:
            ref = str(r["sheet_ref"] or "")
            tab, row = ref.rsplit(":", 1)
            out.append({"id": int(r["id"]), "uid": str(r["uid"]),
                        "tab": tab, "row": int(row),
                        "sold_at": int(r["sold_at"] or 0)})
        except Exception:
            continue
    return out


def acc_mark_sheet_done(ids) -> int:
    """Đánh dấu các acc đã xử lý đẩy 'đã bán' lên Sheet (xong hoặc bỏ qua)."""
    ids = [int(i) for i in (ids or [])]
    if not ids:
        return 0
    with _lock:
        c = get_conn()
        c.execute(
            "UPDATE acc_stock SET sheet_marked=1 WHERE id IN (%s)" %
            ",".join("?" * len(ids)), ids)
        c.commit()
        return len(ids)


def acc_stock_count(cat_id: int) -> int:
    r = get_conn().execute(
        "SELECT COUNT(*) n FROM acc_stock WHERE cat_id=? AND status='AVAILABLE'",
        (cat_id,)).fetchone()
    return r["n"] if r else 0


def _rv(row, key: str) -> str:
    """Đọc an toàn 1 cột từ sqlite3.Row (trả '' nếu thiếu/None)."""
    try:
        v = row[key]
        return "" if v is None else v
    except Exception:
        return ""


def _acc_order_create(c, tg_id: int, row, cat_id: int, price: int, now: int) -> int:
    """Tạo đơn hàng kèm TOÀN BỘ thông tin acc, rồi ĐÁNH DẤU acc đã bán
    (status='SOLD') thay vì xóa — giữ lại lịch sử trong kho.
    Trả order_id."""
    cur = c.execute(
        "INSERT INTO acc_orders(tg_id, stock_id, cat_id, price, created_at, delivered_at,"
        " uid, password, created_date, backup_mail, note, totp, cookie, token, batch)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tg_id, row["id"], cat_id, price, now, now,
         _rv(row, "uid"), _rv(row, "password"), _rv(row, "created_date"),
         _rv(row, "backup_mail"), _rv(row, "note"), _rv(row, "totp"),
         _rv(row, "cookie"), _rv(row, "token"), _rv(row, "batch")))
    order_id = cur.lastrowid
    c.execute(
        "UPDATE acc_stock SET status='SOLD', sold_to=?, sold_at=?, price_sold=? "
        "WHERE id=?",
        (tg_id, now, price, row["id"]))
    return order_id


def acc_sell_one(cat_id: int, tg_id: int, price: int):
    """Bán 1 acc: lấy acc AVAILABLE cũ nhất, lưu chi tiết vào đơn rồi
    ĐÁNH DẤU SOLD trong kho (không xóa).
    Trả (order_id, stock_row) hoặc None nếu hết hàng."""
    now = int(time.time())
    with _lock:
        c = get_conn()
        row = c.execute(
            "SELECT * FROM acc_stock WHERE cat_id=? AND status='AVAILABLE' "
            "ORDER BY id LIMIT 1", (cat_id,)).fetchone()
        if not row:
            return None
        order_id = _acc_order_create(c, tg_id, row, cat_id, price, now)
        c.commit()
        return order_id, row


def acc_sell_many(cat_id: int, tg_id: int, price_total: int, qty: int):
    """Bán nhiều acc cùng lúc, nguyên tử: lấy qty acc AVAILABLE cũ nhất, xóa khỏi kho.
    price_total là TỔNG tiền khách trả (đã giảm giá) — chia đều từng đơn, đơn cuối
    nhận phần dư để tổng doanh thu khớp số tiền thực thu.
    Acc bán xong được ĐÁNH DẤU SOLD trong kho (không xóa).
    Trả list [(order_id, stock_row)] hoặc None nếu không đủ hàng."""
    qty = max(1, int(qty))
    now = int(time.time())
    with _lock:
        c = get_conn()
        rows = c.execute(
            "SELECT * FROM acc_stock WHERE cat_id=? AND status='AVAILABLE' "
            "ORDER BY id LIMIT ?", (cat_id, qty)).fetchall()
        if len(rows) < qty:
            return None
        out = []
        each = price_total // qty if qty else price_total
        for i, row in enumerate(rows):
            chk = c.execute("SELECT id FROM acc_stock WHERE id=? AND status='AVAILABLE'",
                            (row["id"],)).fetchone()
            if not chk:
                c.rollback()
                return None
            p = each if i < len(rows) - 1 else price_total - each * (len(rows) - 1)
            out.append((_acc_order_create(c, tg_id, row, cat_id, p, now), row))
        c.commit()
        return out


def acc_stock_pick_candidates(cat_id: int, limit: int, exclude_ids=()):
    """Lấy ứng viên AVAILABLE để check live trước khi bán (không khóa hàng).
    Trả list dict {"id","uid","cat_id"}."""
    c = get_conn()
    q = "SELECT id, uid, cat_id FROM acc_stock WHERE cat_id=? AND status='AVAILABLE'"
    params = [cat_id]
    if exclude_ids:
        q += " AND id NOT IN (%s)" % ",".join("?" for _ in exclude_ids)
        params += list(exclude_ids)
    q += " ORDER BY id LIMIT ?"
    params.append(max(1, int(limit)))
    return [dict(r) for r in c.execute(q, params).fetchall()]


def acc_mystery_pick_candidates(limit: int, exclude_ids=()):
    """Ứng viên hộp mù: ngẫu nhiên từ các loại mystery_eligible còn hàng.
    Trả list dict {"id","uid","cat_id"}."""
    import random
    c = get_conn()
    cats = c.execute(
        "SELECT id FROM acc_categories WHERE active=1 AND hidden=0 "
        "AND mystery_eligible=1").fetchall()
    pool = []
    for ct in cats:
        q = "SELECT id, uid, cat_id FROM acc_stock WHERE cat_id=? AND status='AVAILABLE'"
        params = [ct["id"]]
        if exclude_ids:
            q += " AND id NOT IN (%s)" % ",".join("?" for _ in exclude_ids)
            params += list(exclude_ids)
        pool += [dict(r) for r in c.execute(q, params).fetchall()]
    random.shuffle(pool)
    return pool[:max(1, int(limit))]


def acc_stock_quarantine(stock_ids) -> int:
    """Cách ly acc DIE: chuyển status='DIE' (rời kho bán, giữ lại cho admin xem/xóa).
    Trả số dòng đã cách ly."""
    ids = [int(i) for i in (stock_ids or [])]
    if not ids:
        return 0
    with _lock:
        c = get_conn()
        cur = c.execute(
            "UPDATE acc_stock SET status='DIE' WHERE status='AVAILABLE' AND id IN (%s)"
            % ",".join("?" for _ in ids), ids)
        c.commit()
        return cur.rowcount or 0


def acc_stock_die_count(cat_id: int) -> int:
    r = get_conn().execute(
        "SELECT COUNT(*) n FROM acc_stock WHERE cat_id=? AND status='DIE'",
        (cat_id,)).fetchone()
    return r["n"] if r else 0


def acc_stock_delete_die(cat_id=None) -> int:
    """Xóa hẳn các acc đã cách ly DIE (dọn kho). Trả số dòng đã xóa."""
    with _lock:
        c = get_conn()
        if cat_id is None:
            cur = c.execute("DELETE FROM acc_stock WHERE status='DIE'")
        else:
            cur = c.execute(
                "DELETE FROM acc_stock WHERE status='DIE' AND cat_id=?", (cat_id,))
        c.commit()
        return cur.rowcount or 0


def acc_sell_stock_ids(cat_id: int, tg_id: int, price_total: int, stock_ids):
    """Bán các acc theo stock id cụ thể (đã check LIVE trước).
    Nguyên tử: id nào không còn AVAILABLE → rollback toàn bộ, trả None.
    Trả list [(order_id, stock_row)]."""
    ids = [int(i) for i in (stock_ids or [])]
    if not ids:
        return None
    now = int(time.time())
    qty = len(ids)
    with _lock:
        c = get_conn()
        rows = []
        for sid in ids:
            r = c.execute(
                "SELECT * FROM acc_stock WHERE id=? AND status='AVAILABLE'",
                (sid,)).fetchone()
            if not r:
                c.rollback()
                return None
            rows.append(r)
        out = []
        each = price_total // qty if qty else price_total
        for i, row in enumerate(rows):
            p = each if i < qty - 1 else price_total - each * (qty - 1)
            out.append((_acc_order_create(c, tg_id, row, cat_id, p, now), row))
        c.commit()
        return out


# ============================ GIỎ HÀNG SHOP ACC ============================
def cart_add(tg_id: int, cat_id: int, qty: int = 1) -> int:
    """Thêm vào giỏ (cộng dồn nếu đã có). Trả tổng qty của loại đó trong giỏ."""
    qty = max(1, min(1000, int(qty or 1)))
    now = int(time.time())
    with _lock:
        c = get_conn()
        c.execute(
            "INSERT INTO cart_items(tg_id, cat_id, qty, added_at) VALUES(?,?,?,?) "
            "ON CONFLICT(tg_id, cat_id) DO UPDATE SET qty = MIN(1000, cart_items.qty + ?)",
            (tg_id, cat_id, qty, now, qty))
        c.commit()
        r = c.execute("SELECT qty FROM cart_items WHERE tg_id=? AND cat_id=?",
                      (tg_id, cat_id)).fetchone()
        return int(r["qty"]) if r else 0


def cart_set_qty(tg_id: int, cat_id: int, qty: int) -> int:
    """Đặt số lượng (<=0 thì xóa). Trả qty mới (0 = đã xóa)."""
    qty = max(0, min(1000, int(qty or 0)))
    with _lock:
        c = get_conn()
        if qty <= 0:
            c.execute("DELETE FROM cart_items WHERE tg_id=? AND cat_id=?",
                      (tg_id, cat_id))
        else:
            c.execute(
                "INSERT INTO cart_items(tg_id, cat_id, qty, added_at) VALUES(?,?,?,?) "
                "ON CONFLICT(tg_id, cat_id) DO UPDATE SET qty=?",
                (tg_id, cat_id, qty, int(time.time()), qty))
        c.commit()
        return qty


def cart_remove(tg_id: int, cat_id: int) -> None:
    with _lock:
        c = get_conn()
        c.execute("DELETE FROM cart_items WHERE tg_id=? AND cat_id=?",
                  (tg_id, cat_id))
        c.commit()


def cart_clear(tg_id: int) -> None:
    with _lock:
        c = get_conn()
        c.execute("DELETE FROM cart_items WHERE tg_id=?", (tg_id,))
        c.commit()


def cart_list(tg_id: int) -> list:
    """Các món trong giỏ kèm thông tin loại acc. Bỏ qua loại đã ẩn/xóa."""
    c = get_conn()
    return [dict(r) for r in c.execute(
        "SELECT i.cat_id, i.qty, i.added_at, c.name, c.price, c.active, c.hidden, "
        "c.credit_bonus FROM cart_items i "
        "JOIN acc_categories c ON c.id = i.cat_id "
        "WHERE i.tg_id=? ORDER BY i.added_at",
        (tg_id,)).fetchall()]


def cart_count(tg_id: int) -> int:
    r = get_conn().execute(
        "SELECT COUNT(*) n, COALESCE(SUM(qty),0) q FROM cart_items WHERE tg_id=?",
        (tg_id,)).fetchone()
    return int(r["q"] or 0) if r else 0


def ref_shop_commission(buyer_tg_id: int, amount: int) -> tuple[int, int]:
    """Hoa hồng F1 cho đơn mua acc shop. Trả (f1_tg_id, bonus). Không lồng F2."""
    try:
        pct = float(get_setting("ref_shop_pct", "10") or 10)
    except Exception:
        pct = 10
    bonus = int(int(amount) * pct / 100)
    if bonus <= 0:
        return 0, 0
    now = int(time.time())
    with _lock:
        c = get_conn()
        u = c.execute("SELECT referrer_id FROM tg_users WHERE tg_id=?",
                      (buyer_tg_id,)).fetchone()
        if not u or not u["referrer_id"]:
            return 0, 0
        f1_id = int(u["referrer_id"])
        c.execute("UPDATE tg_users SET ref_earnings = ref_earnings + ? WHERE tg_id=?",
                  (bonus, f1_id))
        c.execute("INSERT INTO txns(ts, tg_id, amount, reason) VALUES(?,?,?,?)",
                  (now, f1_id, bonus, "Hoa hồng shop acc F1"))
        c.execute(
            "INSERT INTO ref_commissions(referrer_id, from_user_id, level, amount, commission, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (f1_id, buyer_tg_id, 1, int(amount), bonus, now))
        c.commit()
        return f1_id, bonus
    return 0, 0


# ============================ VÒNG QUAY MAY MẮN ============================
def spin_add_tickets(tg_id: int, n: int) -> int:
    """Cộng vé quay. Trả tổng vé hiện tại."""
    n = max(0, int(n))
    with _lock:
        c = get_conn()
        c.execute("INSERT INTO spin_tickets(tg_id, tickets) VALUES(?,?) "
                  "ON CONFLICT(tg_id) DO UPDATE SET tickets = tickets + ?",
                  (tg_id, n, n))
        c.commit()
        r = c.execute("SELECT tickets FROM spin_tickets WHERE tg_id=?",
                      (tg_id,)).fetchone()
        return int(r["tickets"]) if r else 0


def spin_get_tickets(tg_id: int) -> int:
    r = get_conn().execute("SELECT tickets FROM spin_tickets WHERE tg_id=?",
                           (tg_id,)).fetchone()
    return int(r["tickets"]) if r else 0


def spin_consume_ticket(tg_id: int) -> bool:
    """Trừ 1 vé nguyên tử. True nếu trừ được."""
    with _lock:
        c = get_conn()
        cur = c.execute("UPDATE spin_tickets SET tickets = tickets - 1 "
                        "WHERE tg_id=? AND tickets > 0", (tg_id,))
        c.commit()
        return cur.rowcount > 0


def spin_default_prizes() -> list:
    return [
        {"label": "🎁 +10 credits", "kind": "credits", "value": 10, "weight": 30},
        {"label": "🎁 +25 credits", "kind": "credits", "value": 25, "weight": 18},
        {"label": "🎁 +50 credits", "kind": "credits", "value": 50, "weight": 10},
        {"label": "💵 +5.000đ số dư", "kind": "balance", "value": 5000, "weight": 15},
        {"label": "💵 +15.000đ số dư", "kind": "balance", "value": 15000, "weight": 7},
        {"label": "😅 Chúc may mắn lần sau", "kind": "none", "value": 0, "weight": 20},
    ]


def spin_get_prizes() -> list:
    """Đọc cấu hình giải từ setting spin_prizes (JSON), fallback mặc định."""
    import json
    try:
        raw = get_setting("spin_prizes", "")
        if raw:
            prizes = json.loads(raw)
            if isinstance(prizes, list) and prizes:
                return prizes
    except Exception:
        pass
    return spin_default_prizes()


def spin_roll() -> dict:
    """Quay weighted random, trả 1 prize dict."""
    import random
    prizes = spin_get_prizes()
    total = sum(max(0, int(p.get("weight", 0))) for p in prizes) or 1
    r = random.uniform(0, total)
    acc = 0
    for p in prizes:
        acc += max(0, int(p.get("weight", 0)))
        if r <= acc:
            return p
    return prizes[-1]


def spin_award(tg_id: int, prize: dict) -> None:
    """Trao giải: cộng credits / số dư ví chính. Ghi lịch sử."""
    kind = prize.get("kind", "none")
    value = int(prize.get("value", 0) or 0)
    if kind == "credits" and value > 0:
        add_credits(tg_id, value, "trung_vong_quay")
    elif kind == "balance" and value > 0:
        adjust_balance(tg_id, value, "trung_vong_quay")
    with _lock:
        c = get_conn()
        c.execute(
            "INSERT INTO spin_history(tg_id, prize_label, prize_kind, prize_value, created_at) "
            "VALUES(?,?,?,?,?)",
            (tg_id, prize.get("label", ""), kind, value, int(time.time())))
        c.commit()


# ============================ BÁO HÀNG MỚI + HẠNG TV + LOYALTY ============================
def acc_sub_restock(tg_id: int, cat_id: int, qty: int = 1, auto_buy: int = 0) -> bool:
    """Đăng ký báo khi có hàng. True nếu đăng ký mới.
    qty: số acc muốn tự mua khi hàng về; auto_buy=1: tự trừ ví shop mua ngay."""
    qty = max(1, min(1000, int(qty or 1)))
    auto_buy = 1 if auto_buy else 0
    with _lock:
        c = get_conn()
        is_new = c.execute(
            "SELECT 1 FROM acc_restock_subs WHERE tg_id=? AND cat_id=?",
            (tg_id, cat_id)).fetchone() is None
        c.execute(
            "INSERT INTO acc_restock_subs(tg_id, cat_id, created_at, qty, auto_buy)"
            " VALUES(?,?,?,?,?)"
            " ON CONFLICT(tg_id, cat_id) DO UPDATE SET qty=excluded.qty,"
            " auto_buy=excluded.auto_buy",
            (tg_id, cat_id, int(time.time()), qty, auto_buy))
        c.commit()
        return is_new


def acc_unsub_restock(tg_id: int, cat_id: int) -> bool:
    with _lock:
        c = get_conn()
        cur = c.execute("DELETE FROM acc_restock_subs WHERE tg_id=? AND cat_id=?",
                        (tg_id, cat_id))
        c.commit()
        return cur.rowcount > 0


def acc_is_sub_restock(tg_id: int, cat_id: int) -> bool:
    r = get_conn().execute(
        "SELECT 1 FROM acc_restock_subs WHERE tg_id=? AND cat_id=?",
        (tg_id, cat_id)).fetchone()
    return bool(r)


def acc_restock_sub(tg_id: int, cat_id: int):
    """Lấy thông tin đăng ký báo hàng của 1 user (None nếu chưa đăng ký)."""
    r = get_conn().execute(
        "SELECT tg_id, cat_id, COALESCE(qty,1) qty, COALESCE(auto_buy,0) auto_buy,"
        " created_at FROM acc_restock_subs WHERE tg_id=? AND cat_id=?",
        (tg_id, cat_id)).fetchone()
    return dict(r) if r else None


def acc_restock_subscribers(cat_id: int) -> list:
    """Danh sách đăng ký báo hàng, FIFO theo created_at.
    Mỗi phần tử: dict(tg_id, qty, auto_buy, created_at)."""
    rows = get_conn().execute(
        "SELECT tg_id, COALESCE(qty,1) qty, COALESCE(auto_buy,0) auto_buy, created_at"
        " FROM acc_restock_subs WHERE cat_id=? ORDER BY created_at",
        (cat_id,)).fetchall()
    return [dict(r) for r in rows]


def acc_clear_restock_subs(cat_id: int) -> int:
    with _lock:
        c = get_conn()
        cur = c.execute("DELETE FROM acc_restock_subs WHERE cat_id=?", (cat_id,))
        c.commit()
        return cur.rowcount


def acc_user_spent(tg_id: int) -> int:
    """Tổng tiền user đã mua acc shop."""
    r = get_conn().execute(
        "SELECT COALESCE(SUM(price),0) s FROM acc_orders WHERE tg_id=?",
        (tg_id,)).fetchone()
    return int(r["s"]) if r else 0


def member_tier_info(tg_id: int) -> dict:
    """Hạng thành viên theo tổng chi tiêu shop acc.
    Settings: member_tier_silver_min/pct, member_tier_gold_min/pct."""
    def _num(key, default):
        try:
            return int(get_setting(key, str(default)) or default)
        except Exception:
            return default
    spent = acc_user_spent(tg_id)
    silver_min = _num("member_tier_silver_min", 500000)
    gold_min = _num("member_tier_gold_min", 2000000)
    silver_pct = _num("member_tier_silver_pct", 3)
    gold_pct = _num("member_tier_gold_pct", 7)
    if spent >= gold_min:
        tier, pct = "🥇 Vàng", gold_pct
        nxt, nxt_min = None, 0
    elif spent >= silver_min:
        tier, pct = "🥈 Bạc", silver_pct
        nxt, nxt_min = "🥇 Vàng", gold_min
    else:
        tier, pct = "🥉 Đồng", 0
        nxt, nxt_min = "🥈 Bạc", silver_min
    return {"tier": tier, "pct": pct, "spent": spent,
            "next_tier": nxt, "next_min": nxt_min,
            "silver_min": silver_min, "gold_min": gold_min,
            "silver_pct": silver_pct, "gold_pct": gold_pct}


def loyalty_get(tg_id: int) -> int:
    r = get_conn().execute("SELECT points FROM loyalty_points WHERE tg_id=?",
                           (tg_id,)).fetchone()
    return int(r["points"]) if r else 0


def loyalty_add(tg_id: int, delta: int, reason: str = "") -> int:
    """Cộng/trừ điểm loyalty. Trả tổng điểm mới."""
    delta = int(delta)
    if delta == 0:
        return loyalty_get(tg_id)
    now = int(time.time())
    with _lock:
        c = get_conn()
        c.execute("INSERT INTO loyalty_points(tg_id, points) VALUES(?,?) "
                  "ON CONFLICT(tg_id) DO UPDATE SET points = points + ?",
                  (tg_id, delta, delta))
        c.execute("INSERT INTO loyalty_history(tg_id, delta, reason, created_at) "
                  "VALUES(?,?,?,?)", (tg_id, delta, reason or "", now))
        c.commit()
        return loyalty_get(tg_id)


def loyalty_consume(tg_id: int, n: int) -> bool:
    """Trừ n điểm nếu đủ. Atomic."""
    n = int(n)
    if n <= 0:
        return True
    with _lock:
        c = get_conn()
        cur = c.execute("UPDATE loyalty_points SET points = points - ? "
                        "WHERE tg_id=? AND points >= ?", (n, tg_id, n))
        if cur.rowcount:
            c.execute("INSERT INTO loyalty_history(tg_id, delta, reason, created_at) "
                      "VALUES(?,?,?,?)", (tg_id, -n, "doi_qua", int(time.time())))
        c.commit()
        return cur.rowcount > 0


def loyalty_redeem_cat() -> int:
    """ID loại acc dùng để đổi quà. 0 = chưa cấu hình."""
    try:
        return int(get_setting("loyalty_redeem_cat", "0") or 0)
    except Exception:
        return 0


# ------------------------- admin phụ (phân quyền) -------------------------
def extra_admin_list() -> list:
    rows = get_conn().execute(
        "SELECT * FROM extra_admins ORDER BY added_at DESC").fetchall()
    return [dict(r) for r in rows]


def extra_admin_get(tg_id: int):
    r = get_conn().execute(
        "SELECT * FROM extra_admins WHERE tg_id=?", (int(tg_id),)).fetchone()
    return dict(r) if r else None


def extra_admin_add(tg_id: int, name: str, perms: str, added_by: int,
                    expires_at: int = 0) -> bool:
    """Thêm admin phụ. perms: chuỗi 'kho,price,...' (đã validate ở tầng gọi).
    expires_at: timestamp hết hạn (0 = vĩnh viễn)."""
    try:
        with _lock:
            c = get_conn()
            c.execute(
                "INSERT OR REPLACE INTO extra_admins(tg_id, name, perms, added_by, added_at, expires_at)"
                " VALUES(?,?,?,?,?,?)",
                (int(tg_id), (name or "")[:64], perms or "",
                 int(added_by or 0), int(time.time()), int(expires_at or 0)))
            c.commit()
        return True
    except Exception:
        return False


def extra_admin_set_perms(tg_id: int, perms: str) -> bool:
    try:
        with _lock:
            c = get_conn()
            cur = c.execute("UPDATE extra_admins SET perms=? WHERE tg_id=?",
                            (perms or "", int(tg_id)))
            c.commit()
        return cur.rowcount > 0
    except Exception:
        return False


def extra_admin_set_expiry(tg_id: int, expires_at: int) -> bool:
    """Đặt/gia hạn thời hạn quyền (0 = vĩnh viễn)."""
    try:
        with _lock:
            c = get_conn()
            cur = c.execute("UPDATE extra_admins SET expires_at=? WHERE tg_id=?",
                            (int(expires_at or 0), int(tg_id)))
            c.commit()
        return cur.rowcount > 0
    except Exception:
        return False


def extra_admin_expired() -> list:
    """Danh sách admin phụ đã hết hạn (expires_at>0 và đã qua)."""
    try:
        rows = get_conn().execute(
            "SELECT * FROM extra_admins WHERE expires_at>0 AND expires_at<=?",
            (int(time.time()),)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def extra_admin_del(tg_id: int) -> bool:
    try:
        with _lock:
            c = get_conn()
            cur = c.execute("DELETE FROM extra_admins WHERE tg_id=?", (int(tg_id),))
            c.commit()
        return cur.rowcount > 0
    except Exception:
        return False


# ---------------- Nhật ký hoạt động admin (audit log) ----------------
def admin_audit_add(tg_id: int, name: str, action: str, detail: str = "") -> bool:
    """Ghi 1 dòng nhật ký: ai (tg_id/name) đã làm gì (action/detail)."""
    try:
        import time as _t
        with _lock:
            c = get_conn()
            c.execute(
                "INSERT INTO admin_audit(tg_id, name, action, detail, created_at)"
                " VALUES(?,?,?,?,?)",
                (int(tg_id), (name or "")[:80], (action or "")[:60],
                 (detail or "")[:500], int(_t.time())))
            c.commit()
        return True
    except Exception:
        return False


def admin_audit_list(limit: int = 15, offset: int = 0):
    try:
        rows = get_conn().execute(
            "SELECT * FROM admin_audit ORDER BY id DESC LIMIT ? OFFSET ?",
            (int(limit), int(offset))).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def admin_audit_count() -> int:
    try:
        r = get_conn().execute("SELECT COUNT(*) AS c FROM admin_audit").fetchone()
        return int(r["c"] or 0)
    except Exception:
        return 0


def acc_get_order(order_id: int):
    return get_conn().execute(
        "SELECT o.*, COALESCE(c.name,'[đã xóa]') AS cat_name, "
        "COALESCE(c.warranty_hours,0) AS warranty_hours "
        "FROM acc_orders o "
        "LEFT JOIN acc_categories c ON c.id=o.cat_id "
        "WHERE o.id=?", (order_id,)).fetchone()


def acc_user_orders(tg_id: int, limit: int = 20) -> list:
    return get_conn().execute(
        "SELECT o.*, COALESCE(c.name,'[đã xóa]') AS cat_name, "
        "COALESCE(c.warranty_hours,0) AS warranty_hours "
        "FROM acc_orders o LEFT JOIN acc_categories c ON c.id=o.cat_id "
        "WHERE o.tg_id=? ORDER BY o.id DESC LIMIT ?", (tg_id, limit)).fetchall()


def acc_recent_orders(limit: int = 20) -> list:
    return get_conn().execute(
        "SELECT o.*, COALESCE(c.name,'[đã xóa]') AS cat_name, u.username FROM acc_orders o "
        "LEFT JOIN acc_categories c ON c.id=o.cat_id "
        "LEFT JOIN tg_users u ON u.tg_id=o.tg_id "
        "ORDER BY o.id DESC LIMIT ?", (limit,)).fetchall()


def acc_revenue() -> dict:
    c = get_conn()
    out = {"total": 0, "count": 0, "by_cat": []}
    try:
        r = c.execute("SELECT COUNT(*) n, COALESCE(SUM(price),0) s FROM acc_orders").fetchone()
        out["count"], out["total"] = r["n"], r["s"]
        out["by_cat"] = c.execute(
            "SELECT c.name, COUNT(*) n, COALESCE(SUM(o.price),0) s FROM acc_orders o "
            "JOIN acc_categories c ON c.id=o.cat_id GROUP BY c.id ORDER BY s DESC").fetchall()
    except Exception:
        pass
    return out


def acc_warranty_claim(order_id: int, tg_id: int, stock_id: int,
                       check_result: str = "") -> int:
    """Tạo khiếu nại bảo hành. Trả claim id, -1 nếu đã có claim PENDING cho đơn này."""
    now = int(time.time())
    with _lock:
        c = get_conn()
        dup = c.execute(
            "SELECT id FROM acc_warranty_claims WHERE order_id=? AND status IN ('PENDING','NEEDS_REVIEW')",
            (order_id,)).fetchone()
        if dup:
            return -1
        cur = c.execute(
            "INSERT INTO acc_warranty_claims(order_id, tg_id, stock_id, check_result, status, created_at) "
            "VALUES(?,?,?,?,'PENDING',?)",
            (order_id, tg_id, stock_id, check_result or "", now))
        cid = cur.lastrowid
        c.commit()
        return cid


def acc_warranty_has_pending(order_id: int) -> bool:
    """True nếu đơn đã có claim PENDING/NEEDS_REVIEW."""
    r = get_conn().execute(
        "SELECT id FROM acc_warranty_claims WHERE order_id=? AND status IN ('PENDING','NEEDS_REVIEW')",
        (order_id,)).fetchone()
    return r is not None


def acc_warranty_set_evidence(claim_id: int, file_id: str) -> None:
    with _lock:
        c = get_conn()
        c.execute("UPDATE acc_warranty_claims SET evidence=? WHERE id=?",
                  (file_id or "", claim_id))
        c.commit()


def acc_warranty_pending(limit: int = 30) -> list:
    return get_conn().execute(
        "SELECT w.*, COALESCE(c.name,'[đã xóa]') AS cat_name, o.uid FROM acc_warranty_claims w "
        "JOIN acc_orders o ON o.id=w.order_id "
        "LEFT JOIN acc_categories c ON c.id=o.cat_id "
        "WHERE w.status IN ('PENDING','NEEDS_REVIEW') ORDER BY w.id DESC LIMIT ?", (limit,)).fetchall()


def acc_auto_replace(order_id: int, old_stock_id: int, cat_id: int, tg_id: int):
    """Bảo hành tự động: acc cũ (bot check DIE) đánh dấu DEAD, giao acc AVAILABLE
    khác cùng loại giá 0đ. Trả (new_order_id, stock_row) hoặc None nếu hết hàng đổi."""
    now = int(time.time())
    with _lock:
        c = get_conn()
        # acc cũ đã bị xóa khỏi kho lúc bán nên không cần đánh dấu DEAD nữa
        row = c.execute(
            "SELECT * FROM acc_stock WHERE cat_id=? AND status='AVAILABLE' "
            "ORDER BY id LIMIT 1", (cat_id,)).fetchone()
        if not row:
            c.commit()
            return None
        chk = c.execute("SELECT id FROM acc_stock WHERE id=? AND status='AVAILABLE'",
                        (row["id"],)).fetchone()
        if not chk:
            c.commit()
            return None
        order_id = _acc_order_create(c, tg_id, row, cat_id, 0, now)
        c.commit()
        return order_id, dict(row)


def acc_claims_overdue(hours: int = 12) -> list:
    """Các claim PENDING quá hạn chưa được nhắc."""
    cutoff = int(time.time()) - hours * 3600
    return get_conn().execute(
        "SELECT w.*, COALESCE(c.name,'[đã xóa]') AS cat_name, o.uid FROM acc_warranty_claims w "
        "JOIN acc_orders o ON o.id=w.order_id "
        "LEFT JOIN acc_categories c ON c.id=o.cat_id "
        "WHERE w.status='PENDING' AND w.created_at < ? AND w.reminded_at = 0 "
        "ORDER BY w.created_at", (cutoff,)).fetchall()


def acc_claim_mark_reminded(claim_ids: list) -> None:
    if not claim_ids:
        return
    now = int(time.time())
    with _lock:
        c = get_conn()
        for i in claim_ids:
            c.execute("UPDATE acc_warranty_claims SET reminded_at=? WHERE id=?",
                      (now, int(i)))
        c.commit()


def acc_stock_by_uid(uid: str):
    r = get_conn().execute(
        "SELECT s.*, c.name AS cat_name FROM acc_stock s "
        "LEFT JOIN acc_categories c ON c.id=s.cat_id "
        "WHERE s.uid=? ORDER BY s.id DESC LIMIT 1", (uid.strip(),)).fetchone()
    return dict(r) if r else None


def acc_stock_orders(stock_id: int) -> list:
    return [dict(r) for r in get_conn().execute(
        "SELECT o.*, u.username FROM acc_orders o "
        "LEFT JOIN tg_users u ON u.tg_id=o.tg_id "
        "WHERE o.stock_id=? ORDER BY o.id", (stock_id,)).fetchall()]


def acc_stock_claim_count(stock_id: int) -> int:
    r = get_conn().execute(
        "SELECT COUNT(*) n FROM acc_warranty_claims WHERE stock_id=?",
        (stock_id,)).fetchone()
    return int(r["n"]) if r else 0


# ============================ FAQ TỰ ĐỘNG ============================
_DEFAULT_FAQ = [
    {"kw": ["bảo hành", "bh acc", "acc die thì", "acc chết thì", "đổi acc"],
     "a": "🛡 <b>BẢO HÀNH SHOP ACC</b>\n\n"
          "• Mỗi loại acc có thời gian bảo hành riêng (xem ở /shop).\n"
          "• Acc die trong thời gian BH: vào /damua → bấm nút <b>Bảo hành</b> ở đơn hàng.\n"
          "• Bot tự kiểm tra, nếu die thật sẽ <b>tự đổi acc mới ngay</b>, bạn không cần chờ.\n"
          "• Hết hàng đổi thì admin xử lý tay sớm nhất."},
    {"kw": ["2fa", "2 fa", "xác thực 2"],
     "a": "🔐 <b>VỀ 2FA</b>\n\n"
          "Acc có mã 2FA sẽ được giao kèm trong thông tin acc sau khi mua.\n"
          "Nhớ bật 2FA mới của bạn ngay sau khi đổi mật khẩu nhé."},
    {"kw": ["mua acc", "mua như nào", "mua thế nào", "mua sao", "đặt hàng", "cách mua"],
     "a": "🛒 <b>CÁCH MUA ACC</b>\n\n"
          "1. Gõ /shop → chọn loại acc\n"
          "2. Bấm nút Mua (có thể mua 5/10 acc để được giảm giá)\n"
          "3. Bot trừ số dư và giao acc ngay trong chat\n\n"
          "Chưa đủ số dư? Nạp bằng /nap (quét QR, tự cộng tiền)."},
    {"kw": ["nạp tiền", "nap tien", "nạp sao", "nạp như nào"],
     "a": "💳 <b>NẠP TIỀN</b>\n\n"
          "Gõ /nap + số tiền (VD: <code>/nap 100000</code>), quét mã QR để thanh toán.\n"
          "Tiền tự cộng vào số dư sau vài giây, bot báo ngay khi nhận được."},
    {"kw": ["vòng quay", "quay thưởng", "vé quay"],
     "a": "🎡 <b>VÒNG QUAY MAY MẮN</b>\n\n"
          "Mua 1 acc = 1 vé quay. Gõ /quay để thử vận may: trúng credits, số dư..."},
    {"kw": ["điểm", "loyalty", "đổi quà", "tích điểm"],
     "a": "⭐ <b>ĐIỂM LOYALTY</b>\n\n"
          "Mua 100k = 1 điểm. Đủ 10 điểm gõ /doiqua để đổi acc miễn phí.\n"
          "Xem điểm ở /damua, xem hạng thành viên ở /hang."},
]


def shop_faq_list() -> list:
    """Danh sách FAQ: mặc định + admin thêm. Lưu ở setting shop_faq_json."""
    import json as _json
    try:
        extra = _json.loads(get_setting("shop_faq_json", "[]") or "[]")
        if not isinstance(extra, list):
            extra = []
    except Exception:
        extra = []
    return list(_DEFAULT_FAQ) + extra


def shop_faq_add(keywords: str, answer: str) -> int:
    """Thêm 1 câu FAQ. keywords: chuỗi cách nhau dấu phẩy. Trả tổng số câu custom."""
    import json as _json
    kws = [k.strip().lower() for k in keywords.split(",") if k.strip()]
    if not kws or not answer.strip():
        return -1
    try:
        extra = _json.loads(get_setting("shop_faq_json", "[]") or "[]")
        if not isinstance(extra, list):
            extra = []
    except Exception:
        extra = []
    extra.append({"kw": kws, "a": answer.strip()})
    set_setting("shop_faq_json", _json.dumps(extra, ensure_ascii=False))
    return len(extra)


def shop_faq_del(idx: int) -> bool:
    """Xóa câu FAQ custom theo số thứ tự (bắt đầu từ 1)."""
    import json as _json
    try:
        extra = _json.loads(get_setting("shop_faq_json", "[]") or "[]")
        if not isinstance(extra, list):
            extra = []
    except Exception:
        extra = []
    if 1 <= idx <= len(extra):
        extra.pop(idx - 1)
        set_setting("shop_faq_json", _json.dumps(extra, ensure_ascii=False))
        return True
    return False


def shop_faq_match(text: str):
    """Tìm câu FAQ khớp với tin nhắn. Trả answer hoặc None."""
    t = (text or "").lower().strip()
    if not t or len(t) > 200:
        return None
    for item in shop_faq_list():
        for kw in item.get("kw", []):
            if kw and kw in t:
                return item.get("a", "")
    return None


def acc_warranty_get(claim_id: int):
    return get_conn().execute(
        "SELECT * FROM acc_warranty_claims WHERE id=?", (claim_id,)).fetchone()


def acc_warranty_set_status(claim_id: int, status: str, handled_by: int = 0) -> bool:
    with _lock:
        c = get_conn()
        c.execute("UPDATE acc_warranty_claims SET status=?, handled_at=?, handled_by=? WHERE id=?",
                  (status, int(time.time()), int(handled_by or 0), claim_id))
        c.commit()
        return True


def add_balance_only(tg_id: int, amount: int, reason: str) -> None:
    """Cộng/trừ số dư KHÔNG động vào total_topup (dùng cho hoàn tiền)."""
    with _lock:
        c = get_conn()
        c.execute("UPDATE tg_users SET balance = balance + ? WHERE tg_id=?", (amount, tg_id))
        c.execute("INSERT INTO txns(ts, tg_id, amount, reason) VALUES(?,?,?,?)",
                  (int(time.time()), tg_id, amount, reason))
        c.commit()


def acc_mark_status(stock_id: int, status: str) -> None:
    with _lock:
        c = get_conn()
        c.execute("UPDATE acc_stock SET status=? WHERE id=?", (status, stock_id))
        c.commit()


# ================= SHOP MỞ RỘNG — GĐ4/GĐ5 đợt 4 =================
# 4.3 giá khan hiếm, 4.6 upsell, 4.8 hộp mù, 4.9 đặt cọc, 4.10 giờ vàng,
# 4.11 đánh giá, 5.2/5.15 NCC + chấm điểm, 5.3 chống lạm dụng BH,
# 5.7 ẩn/hiện (hook trong sell/add), 5.10 acc nằm kho lâu, 5.13 hỏi thăm 24h,
# 5.14 giá vốn/lãi theo lô.

def shop_unit_price(cat) -> dict:
    """Giá 1 acc sau: giá khan hiếm (4.3) + giờ vàng (4.10).
    Trả dict(price, base, stock, scarcity, scarcity_pct, happy, happy_pct)."""
    cat = dict(cat)
    base = int(cat.get("price") or 0)
    price = base
    cat_id = int(cat.get("id") or 0)
    n = acc_stock_count(cat_id)
    # Tính năng giá khan hiếm đã BỎ (2026-09-21): giá luôn = giá gốc.
    scarcity = False
    s_pct = 0
    happy = False
    h_pct = 0
    try:
        h_pct = int(get_setting("happy_hour_pct", "10") or 0)
        h_range = (get_setting("happy_hour_range", "20-22") or "20-22").strip()
        h_cat = int(get_setting("happy_hour_cat", "0") or 0)
    except Exception:
        h_pct, h_range, h_cat = 0, "20-22", 0
    if h_pct > 0 and (h_cat == 0 or h_cat == cat_id):
        try:
            a, b = [int(x) for x in h_range.split("-")]
            h = time.localtime().tm_hour
            in_range = (a <= h < b) if a < b else (h >= a or h < b)
            if in_range:
                price = price * (100 - h_pct) // 100
                happy = True
        except Exception:
            pass
    return {"price": price, "base": base, "stock": n, "scarcity": scarcity,
            "scarcity_pct": s_pct, "happy": happy,
            "happy_pct": h_pct if happy else 0}


def happy_hour_active() -> tuple[bool, int, int]:
    """(đang giờ vàng?, % giảm, cat_id áp dụng)."""
    try:
        h_pct = int(get_setting("happy_hour_pct", "10") or 0)
        h_range = (get_setting("happy_hour_range", "20-22") or "20-22").strip()
        h_cat = int(get_setting("happy_hour_cat", "0") or 0)
    except Exception:
        return False, 0, 0
    if h_pct <= 0:
        return False, 0, h_cat
    try:
        a, b = [int(x) for x in h_range.split("-")]
        h = time.localtime().tm_hour
        in_range = (a <= h < b) if a < b else (h >= a or h < b)
        return in_range, h_pct, h_cat
    except Exception:
        return False, 0, h_cat


def acc_mystery_sell(tg_id: int, price: int):
    """4.8 Hộp mù: giao ngẫu nhiên 1 acc từ các loại mystery_eligible còn hàng.
    Trả (order_id, row, cat_name) hoặc None nếu hết."""
    import random
    now = int(time.time())
    with _lock:
        c = get_conn()
        cats = c.execute(
            "SELECT id, name FROM acc_categories WHERE active=1 AND hidden=0 "
            "AND mystery_eligible=1").fetchall()
        avail = []
        for ct in cats:
            rows = c.execute(
                "SELECT * FROM acc_stock WHERE cat_id=? AND status='AVAILABLE' "
                "ORDER BY id", (ct["id"],)).fetchall()
            if rows:
                avail.append((ct, rows))
        if not avail:
            return None
        ct, rows = random.choice(avail)
        row = random.choice(rows)
        chk = c.execute("SELECT id FROM acc_stock WHERE id=? AND status='AVAILABLE'",
                        (row["id"],)).fetchone()
        if not chk:
            return None
        order_id = _acc_order_create(c, tg_id, row, ct["id"], price, now)
        c.commit()
        return order_id, row, ct["name"]


# ---- 4.9 Đặt cọc giữ hàng ----
def acc_deposit_create(tg_id: int, cat_id: int, amount: int) -> int:
    now = int(time.time())
    with _lock:
        c = get_conn()
        cur = c.execute(
            "INSERT INTO acc_deposits(tg_id, cat_id, amount, status, created_at) "
            "VALUES(?,?,?,?,?)", (tg_id, cat_id, int(amount), "WAITING", now))
        c.commit()
        return cur.lastrowid


def acc_deposit_list(tg_id: int) -> list:
    return get_conn().execute(
        "SELECT d.*, c.name cat_name, c.price cat_price FROM acc_deposits d "
        "LEFT JOIN acc_categories c ON c.id=d.cat_id "
        "WHERE d.tg_id=? ORDER BY d.id DESC LIMIT 20", (tg_id,)).fetchall()


def acc_deposit_waiting(cat_id: int) -> list:
    return get_conn().execute(
        "SELECT * FROM acc_deposits WHERE cat_id=? AND status='WAITING' "
        "ORDER BY created_at LIMIT 50", (cat_id,)).fetchall()


def acc_deposit_set_status(dep_id: int, status: str, order_id: int = 0) -> None:
    with _lock:
        c = get_conn()
        c.execute("UPDATE acc_deposits SET status=?, order_id=? WHERE id=?",
                  (status, int(order_id or 0), dep_id))
        c.commit()


def acc_deposit_cancel(dep_id: int, tg_id: int):
    """Hủy cọc của chính user. Trả amount hoàn lại hoặc None."""
    with _lock:
        c = get_conn()
        r = c.execute(
            "SELECT * FROM acc_deposits WHERE id=? AND tg_id=? AND status='WAITING'",
            (dep_id, tg_id)).fetchone()
        if not r:
            return None
        c.execute("UPDATE acc_deposits SET status='CANCELLED' WHERE id=?", (dep_id,))
        c.commit()
        return int(r["amount"])


# ---- 4.11 Đánh giá có thưởng ----
def acc_review_add(order_id: int, tg_id: int, cat_id: int, stars: int) -> bool:
    now = int(time.time())
    stars = max(1, min(5, int(stars)))
    with _lock:
        c = get_conn()
        try:
            c.execute(
                "INSERT INTO acc_reviews(order_id, tg_id, cat_id, stars, created_at) "
                "VALUES(?,?,?,?,?)", (order_id, tg_id, cat_id, stars, now))
            c.commit()
            return True
        except Exception:
            return False


def acc_order_reviewed(order_id: int) -> bool:
    r = get_conn().execute(
        "SELECT 1 FROM acc_reviews WHERE order_id=?", (order_id,)).fetchone()
    return bool(r)


def acc_review_avg(cat_id: int) -> tuple[float, int]:
    r = get_conn().execute(
        "SELECT AVG(stars) a, COUNT(*) n FROM acc_reviews WHERE cat_id=?",
        (cat_id,)).fetchone()
    if not r or not r["n"]:
        return 0.0, 0
    return round(float(r["a"] or 0), 1), int(r["n"])


def acc_review_set_comment(order_id: int, text: str) -> bool:
    """Lưu lời nhận xét của khách cho đánh giá."""
    try:
        with _lock:
            c = get_conn()
            c.execute("UPDATE acc_reviews SET comment=? WHERE order_id=?",
                      ((text or "")[:500], order_id))
            c.commit()
        return True
    except Exception:
        return False


def acc_review_list(cat_id: int, limit: int = 3) -> list:
    """Các đánh giá có lời nhận xét mới nhất của 1 loại acc."""
    try:
        return get_conn().execute(
            "SELECT stars, comment, created_at FROM acc_reviews "
            "WHERE cat_id=? AND comment<>'' ORDER BY id DESC LIMIT ?",
            (cat_id, limit)).fetchall()
    except Exception:
        return []


def acc_orders_need_review_nudge(minutes: int) -> list:
    """Đơn mua đã qua `minutes` phút, chưa nhắc đánh giá, chưa đánh giá."""
    try:
        cutoff = int(time.time()) - minutes * 60
        return get_conn().execute(
            "SELECT o.id, o.tg_id, o.cat_id, c.name AS cat_name FROM acc_orders o "
            "LEFT JOIN acc_categories c ON c.id=o.cat_id "
            "WHERE o.created_at<=? AND o.review_nudged_at=0 "
            "AND NOT EXISTS(SELECT 1 FROM acc_reviews r WHERE r.order_id=o.id)",
            (cutoff,)).fetchall()
    except Exception:
        return []


def acc_order_mark_review_nudged(order_id: int):
    try:
        with _lock:
            c = get_conn()
            c.execute("UPDATE acc_orders SET review_nudged_at=? WHERE id=?",
                      (int(time.time()), order_id))
            c.commit()
    except Exception:
        pass


# ---- 5.15 Sổ NCC ----
def supplier_add(name: str, contact: str = "") -> int:
    now = int(time.time())
    with _lock:
        c = get_conn()
        cur = c.execute(
            "INSERT INTO suppliers(name, contact, rating, created_at) VALUES(?,?,0,?)",
            (name.strip(), contact.strip(), now))
        c.commit()
        return cur.lastrowid


def supplier_list() -> list:
    return get_conn().execute("SELECT * FROM suppliers ORDER BY id DESC").fetchall()


def supplier_get(sid: int):
    return get_conn().execute("SELECT * FROM suppliers WHERE id=?", (sid,)).fetchone()


def supplier_rate(sid: int, stars: int) -> bool:
    stars = max(1, min(5, int(stars)))
    with _lock:
        c = get_conn()
        cur = c.execute("UPDATE suppliers SET rating=? WHERE id=?", (stars, sid))
        c.commit()
        return cur.rowcount > 0


def supplier_score_add(supplier_id: int, batch: str, total: int, alive: int,
                       note: str = "") -> int:
    now = int(time.time())
    with _lock:
        c = get_conn()
        cur = c.execute(
            "INSERT INTO supplier_scores(supplier_id, batch, total, alive, note, scored_at) "
            "VALUES(?,?,?,?,?,?)",
            (supplier_id, batch or "", int(total), int(alive), note or "", now))
        c.commit()
        return cur.lastrowid


def supplier_scores(supplier_id: int, limit: int = 10) -> list:
    return get_conn().execute(
        "SELECT * FROM supplier_scores WHERE supplier_id=? ORDER BY id DESC LIMIT ?",
        (supplier_id, limit)).fetchall()


def supplier_live_rate(supplier_id: int) -> tuple[float, int, int]:
    """Tỉ lệ sống tổng hợp từ các lần chấm điểm. Trả (pct, alive, total)."""
    r = get_conn().execute(
        "SELECT COALESCE(SUM(alive),0) a, COALESCE(SUM(total),0) t "
        "FROM supplier_scores WHERE supplier_id=?", (supplier_id,)).fetchone()
    a, t = int(r["a"] or 0), int(r["t"] or 0)
    return (round(a * 100 / t, 1) if t else 0.0), a, t


# ---- 5.14 Giá vốn / lãi theo lô ----
def acc_batches_by_cat(cat_id: int) -> list:
    return get_conn().execute(
        "SELECT * FROM acc_batches WHERE cat_id=? ORDER BY created_at DESC LIMIT 30",
        (cat_id,)).fetchall()


def acc_profit_by_batch(batch: str) -> dict:
    """Vốn, doanh thu, lãi của 1 lô."""
    b = get_conn().execute("SELECT * FROM acc_batches WHERE batch=?", (batch,)).fetchone()
    c = get_conn()
    n_stock = c.execute("SELECT COUNT(*) n FROM acc_stock WHERE batch=?", (batch,)).fetchone()["n"]
    n_sold = c.execute("SELECT COUNT(*) n FROM acc_orders WHERE batch=?", (batch,)).fetchone()["n"]
    n = n_stock + n_sold
    rev = c.execute(
        "SELECT COALESCE(SUM(price),0) s FROM acc_orders WHERE batch=?", (batch,)).fetchone()["s"]
    cost_per = int(b["cost_per_acc"]) if b and b["cost_per_acc"] else 0
    cost = cost_per * n
    return {"batch": batch, "count": n, "cost_per": cost_per,
            "cost": cost, "revenue": int(rev or 0),
            "profit": int(rev or 0) - cost,
            "supplier_id": int(b["supplier_id"]) if b else 0}


# ---- 5.3 Chống lạm dụng bảo hành ----
def acc_warranty_week_count(tg_id: int) -> int:
    week_ago = int(time.time()) - 7 * 86400
    r = get_conn().execute(
        "SELECT COUNT(*) n FROM acc_warranty_claims WHERE tg_id=? AND created_at>=?",
        (tg_id, week_ago)).fetchone()
    return int(r["n"]) if r else 0


# ---- 5.10 Acc nằm kho lâu / 5.5 dọn kho ----
def acc_stale_stock(days: int, limit: int = 50) -> list:
    cutoff = int(time.time()) - int(days) * 86400
    return get_conn().execute(
        "SELECT s.*, c.name cat_name FROM acc_stock s "
        "LEFT JOIN acc_categories c ON c.id=s.cat_id "
        "WHERE s.status='AVAILABLE' AND s.added_at<? AND s.stale_warned=0 "
        "ORDER BY s.added_at LIMIT ?", (cutoff, limit)).fetchall()


def acc_mark_stale_warned(ids: list) -> None:
    if not ids:
        return
    with _lock:
        c = get_conn()
        for sid in ids:
            c.execute("UPDATE acc_stock SET stale_warned=1 WHERE id=?", (sid,))
        c.commit()


def acc_old_stock(days: int, limit: int = 50) -> list:
    cutoff = int(time.time()) - int(days) * 86400
    return get_conn().execute(
        "SELECT id, uid, cat_id FROM acc_stock WHERE status='AVAILABLE' "
        "AND added_at<? ORDER BY added_at LIMIT ?", (cutoff, limit)).fetchall()


# ---- 5.13 Hỏi thăm sau 24h / 4.6 upsell ----
def acc_orders_need_followup() -> list:
    cutoff = int(time.time()) - 86400
    return get_conn().execute(
        "SELECT o.*, c.name cat_name FROM acc_orders o "
        "LEFT JOIN acc_categories c ON c.id=o.cat_id "
        "WHERE o.delivered_at>0 AND o.delivered_at<? AND o.followup_sent=0 "
        "ORDER BY o.delivered_at LIMIT 30", (cutoff,)).fetchall()


def acc_order_mark_followup(order_id: int) -> None:
    with _lock:
        c = get_conn()
        c.execute("UPDATE acc_orders SET followup_sent=1 WHERE id=?", (order_id,))
        c.commit()


def acc_last_order_at(tg_id: int) -> int:
    r = get_conn().execute(
        "SELECT MAX(created_at) m FROM acc_orders WHERE tg_id=?", (tg_id,)).fetchone()
    return int(r["m"] or 0)


def db_backup(keep: int = 7) -> str:
    """Sao lưu database mỗi đêm. SQLite: dùng backup API (an toàn khi DB đang
    chạy). Postgres: bỏ qua (cần pg_dump riêng). Trả về đường dẫn file backup
    hoặc chuỗi rỗng nếu không backup được."""
    import datetime as _dt
    if SUPABASE_URL:
        return ""  # Postgres: không tự backup ở đây
    d = os.path.expanduser("~/workspace/fb-hoan-chinh/backups/db")
    os.makedirs(d, exist_ok=True)
    fn = _dt.datetime.now().strftime("db_%Y%m%d_%H%M.db")
    path = os.path.join(d, fn)
    src = sqlite3.connect(config.DB_PATH, timeout=30)
    try:
        dst = sqlite3.connect(path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    files = sorted(f for f in os.listdir(d)
                   if f.startswith("db_") and f.endswith(".db"))
    for old in files[:-keep]:
        try:
            os.remove(os.path.join(d, old))
        except Exception:
            pass
    return path
