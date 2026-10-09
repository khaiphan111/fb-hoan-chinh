import threading
import time
import re
import hashlib
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
        # Chuyen DDL SQLite -> Postgres (giong executescript): AUTOINCREMENT -> SERIAL/BIGSERIAL
        sql = sql.replace('BIGINT PRIMARY KEY AUTOINCREMENT', 'BIGSERIAL PRIMARY KEY')
        sql = sql.replace('BIGINT PRIMARY KEY', 'BIGSERIAL PRIMARY KEY')
        sql = sql.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'SERIAL PRIMARY KEY')
        # INTEGER PK thuong (khong AUTOINCREMENT): SQLite luon 64-bit -> PG phai BIGINT (vd payos_orders.order_code)
        sql = sql.replace('INTEGER PRIMARY KEY', 'BIGINT PRIMARY KEY')
        sql = re.sub(r'\bBLOB\b', 'BYTEA', sql)
        # SQLite: INSERT OR REPLACE -> Postgres: ON CONFLICT (chi co 1 cho dung: extra_admins)
        sql = sql.replace(
            "INSERT OR REPLACE INTO extra_admins(tg_id, name, perms, added_by, added_at, expires_at)",
            "INSERT INTO extra_admins(tg_id, name, perms, added_by, added_at, expires_at)"
            " ON CONFLICT(tg_id) DO UPDATE SET name=EXCLUDED.name, perms=EXCLUDED.perms,"
            " added_by=EXCLUDED.added_by, added_at=EXCLUDED.added_at, expires_at=EXCLUDED.expires_at")
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
        sql = sql.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'SERIAL PRIMARY KEY')
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
        self._last_ping = 0.0
    def _reconnect(self):
        self.conn = psycopg2.connect(SUPABASE_URL, connect_timeout=10)
        self.conn.autocommit = True
        self._last_ping = time.time()
    def check_conn(self):
        # Bỏ ping SELECT 1 mỗi query: mỗi ping là 1 round-trip qua tunnel
        # (~0.3-0.5s), handler gọi nhiều query -> bot trả lời chậm.
        # Chỉ ping lại nếu >60s chưa verify hoặc conn đã đóng.
        now = time.time()
        if self._last_ping and now - self._last_ping < 60:
            if getattr(self.conn, "closed", 1) == 0:
                return
        try:
            with self.conn.cursor() as cur:
                cur.execute('SELECT 1')
            self._last_ping = time.time()
        except Exception:
            self._reconnect()
    def execute(self, sql, params=()):
        self.check_conn()
        try:
            return PgCursor(self.conn).execute(sql, params)
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            # Chỉ reconnect khi lỗi KẾT NỐI thật (tunnel rớt, conn chết).
            # Lỗi SQL logic (sai cú pháp, cột đã tồn tại...) thì raise luôn,
            # không reconnect vô ích (mỗi reconnect tốn ~2s qua tunnel).
            self._reconnect()
            return PgCursor(self.conn).execute(sql, params)
    def executescript(self, sql):
        self.check_conn()
        try:
            return PgCursor(self.conn).executescript(sql)
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            self._reconnect()
            return PgCursor(self.conn).executescript(sql)
    def commit(self):
        # self.conn.commit()
        pass
    def rollback(self):
        # autocommit=True nên không có transaction để rollback;
        # method này tồn tại để code dùng chung với SQLite không vỡ.
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
        _ensure_migrations_table(c)
        applied = _load_applied(c)
        for _stmt in _split_sql_script(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS acc_file_tokens (
                token      TEXT PRIMARY KEY,
                tg_id      BIGINT NOT NULL,
                order_ids  TEXT NOT NULL,
                created_at BIGINT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS acc_loans (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id       BIGINT NOT NULL,
                amount      BIGINT NOT NULL,
                paid_amount BIGINT NOT NULL DEFAULT 0,
                status      TEXT NOT NULL DEFAULT 'pending',
                due_date    BIGINT DEFAULT 0,
                note        TEXT DEFAULT '',
                created_by  BIGINT DEFAULT 0,
                approved_by BIGINT DEFAULT 0,
                created_at  BIGINT NOT NULL,
                updated_at  BIGINT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_acc_loans_tg ON acc_loans(tg_id);
            CREATE INDEX IF NOT EXISTS idx_acc_loans_status ON acc_loans(status);

            CREATE TABLE IF NOT EXISTS acc_loan_payments (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                loan_id    INTEGER NOT NULL,
                amount     BIGINT NOT NULL,
                kind       TEXT NOT NULL DEFAULT 'repay_manual',
                note       TEXT DEFAULT '',
                created_by BIGINT DEFAULT 0,
                created_at BIGINT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_loan_pay_loan ON acc_loan_payments(loan_id);

            CREATE TABLE IF NOT EXISTS loan_custom_limits (
                tg_id      BIGINT PRIMARY KEY,
                max_total  BIGINT NOT NULL,
                note       TEXT DEFAULT '',
                updated_by BIGINT DEFAULT 0,
                updated_at BIGINT NOT NULL
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

            -- Khách đã nhận tin "bot tạm dừng" -> báo "đã mở lại" sau
            CREATE TABLE IF NOT EXISTS pause_notified (
                tg_id          BIGINT PRIMARY KEY,
                name           TEXT DEFAULT '',
                notified_at    BIGINT DEFAULT 0,
                last_notice_at BIGINT DEFAULT 0
            );

            -- ============ KÝ GỬI ACC ============
            -- Hồ sơ người ký gửi (đăng ký công khai, chủ shop duyệt)
            CREATE TABLE IF NOT EXISTS consignors (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id      BIGINT UNIQUE NOT NULL,
                name       TEXT DEFAULT '',
                phone      TEXT DEFAULT '',
                note       TEXT DEFAULT '',
                status     TEXT DEFAULT 'pending',
                level      TEXT DEFAULT 'new',
                max_items  BIGINT DEFAULT 20,
                max_value  BIGINT DEFAULT 10000000,
                risk_score BIGINT DEFAULT 0,
                payout_info TEXT DEFAULT '',
                sheet_email TEXT DEFAULT '',
                sheet_id TEXT DEFAULT '',
                sheet_url TEXT DEFAULT '',
                sheet_access_granted BIGINT DEFAULT 0,
                import_sheet_url TEXT DEFAULT '',
                notify_sale BIGINT DEFAULT 1,
                notify_bot_token TEXT DEFAULT '',
                notify_bot_username TEXT DEFAULT '',
                created_at BIGINT NOT NULL,
                approved_at BIGINT DEFAULT 0,
                approved_by BIGINT DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_consignors_status ON consignors(status);

            -- Lô hàng ký gửi
            CREATE TABLE IF NOT EXISTS consignment_batches (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT UNIQUE NOT NULL,
                consignor_id BIGINT NOT NULL,
                stall       TEXT DEFAULT '',
                category_id BIGINT DEFAULT 0,
                status      TEXT DEFAULT 'draft',
                floor_price BIGINT DEFAULT 0,
                sell_price  BIGINT DEFAULT 0,
                warranty_days BIGINT DEFAULT 0,
                total_items BIGINT DEFAULT 0,
                ok_items    BIGINT DEFAULT 0,
                note        TEXT DEFAULT '',
                created_at  BIGINT NOT NULL,
                decided_at  BIGINT DEFAULT 0,
                decided_by  BIGINT DEFAULT 0,
                decide_note TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_cbatches_consignor ON consignment_batches(consignor_id);
            CREATE INDEX IF NOT EXISTS idx_cbatches_status ON consignment_batches(status);

            -- Từng acc trong lô
            CREATE TABLE IF NOT EXISTS consignment_items (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id     BIGINT NOT NULL,
                consignor_id BIGINT NOT NULL,
                uid          TEXT DEFAULT '',
                password     TEXT DEFAULT '',
                backup_mail  TEXT DEFAULT '',
                totp         TEXT DEFAULT '',
                cookie       TEXT DEFAULT '',
                token        TEXT DEFAULT '',
                note         TEXT DEFAULT '',
                status       TEXT DEFAULT 'pending',
                verdict      TEXT DEFAULT '',
                acc_stock_id BIGINT DEFAULT 0,
                sold_at      BIGINT DEFAULT 0,
                sold_price   BIGINT DEFAULT 0,
                fee_fixed    BIGINT DEFAULT 0,
                fee_pct      REAL DEFAULT 0,
                net_amount   BIGINT DEFAULT 0,
                warranty_until BIGINT DEFAULT 0,
                created_at   BIGINT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_citems_batch ON consignment_items(batch_id);
            CREATE INDEX IF NOT EXISTS idx_citems_consignor ON consignment_items(consignor_id);
            CREATE INDEX IF NOT EXISTS idx_citems_stock ON consignment_items(acc_stock_id);

            -- Đơn bán hàng ký gửi (snapshot tại lúc bán)
            CREATE TABLE IF NOT EXISTS consignment_orders (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id      BIGINT NOT NULL,
                batch_id     BIGINT NOT NULL,
                consignor_id BIGINT NOT NULL,
                buyer_tg_id  BIGINT NOT NULL,
                order_ref    TEXT DEFAULT '',
                sell_price   BIGINT DEFAULT 0,
                floor_price  BIGINT DEFAULT 0,
                fee_fixed    BIGINT DEFAULT 0,
                fee_pct      REAL DEFAULT 0,
                fee_amount   BIGINT DEFAULT 0,
                net_amount   BIGINT DEFAULT 0,
                warranty_days BIGINT DEFAULT 0,
                warranty_until BIGINT DEFAULT 0,
                status       TEXT DEFAULT 'sold',
                notified     BIGINT DEFAULT 0,
                created_at   BIGINT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_corders_consignor ON consignment_orders(consignor_id);

            -- Sổ cái ví ký gửi (số dư luôn tính từ ledger, không sửa trực tiếp)
            CREATE TABLE IF NOT EXISTS consignment_ledger (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                consignor_id BIGINT NOT NULL,
                kind         TEXT NOT NULL,
                amount       BIGINT NOT NULL,
                ref_type     TEXT DEFAULT '',
                ref_id       BIGINT DEFAULT 0,
                note         TEXT DEFAULT '',
                created_at   BIGINT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cledger_consignor ON consignment_ledger(consignor_id);

            -- Outbox thông báo: chống gửi trùng/mất tin khi crash giữa chừng.
            -- dedupe_key UNIQUE đảm bảo mỗi sự kiện chỉ enqueue 1 lần.
            CREATE TABLE IF NOT EXISTS notification_outbox (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                dedupe_key   TEXT UNIQUE NOT NULL,
                kind         TEXT NOT NULL,
                target_tg_id BIGINT NOT NULL,
                message      TEXT NOT NULL,
                parse_mode   TEXT DEFAULT 'HTML',
                status       TEXT DEFAULT 'pending',
                attempts     INTEGER DEFAULT 0,
                last_error   TEXT DEFAULT '',
                created_at   BIGINT NOT NULL,
                claimed_at   BIGINT DEFAULT 0,
                ref_consignor_id BIGINT DEFAULT 0,
                sent_at      BIGINT DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_outbox_status ON notification_outbox(status, attempts);

            -- Yêu cầu rút tiền
            CREATE TABLE IF NOT EXISTS consignment_payouts (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                consignor_id BIGINT NOT NULL,
                amount       BIGINT NOT NULL,
                fee          BIGINT DEFAULT 0,
                net          BIGINT NOT NULL,
                channel      TEXT DEFAULT '',
                account_info TEXT DEFAULT '',
                status       TEXT DEFAULT 'pending',
                created_at   BIGINT NOT NULL,
                decided_at   BIGINT DEFAULT 0,
                decided_by   BIGINT DEFAULT 0,
                paid_ref     TEXT DEFAULT '',
                reject_reason TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_cpayouts_consignor ON consignment_payouts(consignor_id);
            CREATE INDEX IF NOT EXISTS idx_cpayouts_status ON consignment_payouts(status);

            -- Tranh chấp / bảo hành (bắt buộc ảnh bằng chứng)
            CREATE TABLE IF NOT EXISTS consignment_disputes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id     BIGINT NOT NULL,
                item_id      BIGINT NOT NULL,
                consignor_id BIGINT NOT NULL,
                buyer_tg_id  BIGINT NOT NULL,
                reason       TEXT DEFAULT '',
                photo_file_id TEXT DEFAULT '',
                status       TEXT DEFAULT 'open',
                decision     TEXT DEFAULT '',
                decided_by   BIGINT DEFAULT 0,
                decided_at   BIGINT DEFAULT 0,
                refund_amount BIGINT DEFAULT 0,
                created_at   BIGINT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cdisputes_status ON consignment_disputes(status);

            -- Phí kết hợp theo từng loại hàng
            CREATE TABLE IF NOT EXISTS consignment_fees (
                category_id BIGINT PRIMARY KEY,
                fee_fixed   BIGINT DEFAULT 0,
                fee_pct     REAL DEFAULT 0,
                updated_at  BIGINT DEFAULT 0,
                updated_by  BIGINT DEFAULT 0
            );

            -- Chiến dịch khuyến mãi cần đối tác đồng ý
            CREATE TABLE IF NOT EXISTS consignment_promos (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                title        TEXT DEFAULT '',
                detail       TEXT DEFAULT '',
                consignor_id BIGINT DEFAULT 0,
                status       TEXT DEFAULT 'pending',
                created_at   BIGINT NOT NULL,
                decided_at   BIGINT DEFAULT 0
            );

            -- Lịch sử tin báo cho đối tác (debug khi kêu không nhận được tin)
            CREATE TABLE IF NOT EXISTS consignment_notif_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                consignor_id BIGINT DEFAULT 0,
                kind         TEXT DEFAULT '',
                ref_id       BIGINT DEFAULT 0,
                via_bot      TEXT DEFAULT '',
                ok           BIGINT DEFAULT 0,
                error        TEXT DEFAULT '',
                created_at   BIGINT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cnotif_consignor ON consignment_notif_log(consignor_id);

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

            -- Magic link dung chung giua VM (bot tao) va Render (web xac thuc).
            -- Truoc day luu dict trong RAM nen token tao tren VM khong xac thuc duoc tren Render.
            CREATE TABLE IF NOT EXISTS magic_links (
                token   TEXT PRIMARY KEY,
                tg_id   BIGINT NOT NULL,
                exp     BIGINT NOT NULL
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

            CREATE TABLE IF NOT EXISTS buff_services (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                platform_key     TEXT NOT NULL,
                platform_name    TEXT NOT NULL,
                category_key     TEXT NOT NULL,
                category_name    TEXT NOT NULL,
                panel_service_id INTEGER NOT NULL,
                name             TEXT NOT NULL,
                description      TEXT DEFAULT '',
                cost_price       BIGINT DEFAULT 0,
                sell_price       BIGINT DEFAULT 0,
                min_qty          BIGINT DEFAULT 1,
                max_qty          BIGINT DEFAULT 1000000,
                enabled          BIGINT DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_buff_services_plat
                ON buff_services(platform_key, category_key);

            CREATE TABLE IF NOT EXISTS buff_orders (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                code         TEXT UNIQUE NOT NULL,
                tg_id        BIGINT NOT NULL,
                service_id   BIGINT NOT NULL,
                link         TEXT NOT NULL,
                quantity     BIGINT NOT NULL,
                total_price  BIGINT NOT NULL,
                total_cost   BIGINT NOT NULL,
                status       TEXT DEFAULT 'pending',
                panel_order_id TEXT DEFAULT '',
                created_at   BIGINT NOT NULL,
                updated_at   BIGINT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_buff_orders_status
                ON buff_orders(status);
            CREATE INDEX IF NOT EXISTS idx_buff_orders_user
                ON buff_orders(tg_id);
            -- Theo dõi tiến độ đơn buff (thêm 2026-10-08)
            ALTER TABLE buff_orders ADD COLUMN IF NOT EXISTS done_quantity BIGINT DEFAULT 0;
            ALTER TABLE buff_orders ADD COLUMN IF NOT EXISTS last_checked_at BIGINT DEFAULT 0;
            CREATE TABLE IF NOT EXISTS buff_link_warehouse (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id        BIGINT NOT NULL,
                platform     TEXT DEFAULT '',
                link         TEXT NOT NULL,
                first_used_at BIGINT NOT NULL,
                last_used_at  BIGINT NOT NULL,
                use_count    INTEGER DEFAULT 1,
                service_name TEXT DEFAULT '',
                cost_price   BIGINT DEFAULT 0,
                sell_price   BIGINT DEFAULT 0,
                UNIQUE(tg_id, link)
            );
            CREATE INDEX IF NOT EXISTS idx_buff_link_wh_user
                ON buff_link_warehouse(tg_id);
            CREATE INDEX IF NOT EXISTS idx_buff_link_wh_platform
                ON buff_link_warehouse(platform);
            CREATE INDEX IF NOT EXISTS idx_buff_link_wh_last
                ON buff_link_warehouse(last_used_at DESC);
            """
        ):
            _migrate_one(c, _stmt, applied)
        c.commit()
        # Keys that MUST be force-updated on every restart
        # (to ensure env-configured tokens always take effect).
        # CHỈ ghi đè khi env có giá trị non-empty: nếu .env thiếu key
        # (vd ADMIN_TG_ID) thì giữ nguyên giá trị trong DB, tránh mất
        # quyền admin toàn bot sau mỗi restart.
        _force_keys = {
            "bot_token", "setup_done", "admin_bot_token",
            "admin_tg_id", "zalo_bot_token", "web_domain",
        }
        # Gop thanh 2 cau bulk INSERT de giam round-trip qua tunnel (muc 5)
        _force_rows = []
        _normal_rows = []
        for k, v in config.DEFAULT_SETTINGS.items():
            if k in _force_keys and v not in (None, ""):
                _force_rows.append((k, v))
            else:
                _normal_rows.append((k, v))
        if _force_rows:
            _ph = ", ".join(["(?, ?)"] * len(_force_rows))
            _flat = [x for kv in _force_rows for x in kv]
            c.execute(
                f"INSERT INTO settings(key, value) VALUES {_ph} "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                tuple(_flat),
            )
        if _normal_rows:
            _ph = ", ".join(["(?, ?)"] * len(_normal_rows))
            _flat = [x for kv in _normal_rows for x in kv]
            c.execute(
                f"INSERT INTO settings(key, value) VALUES {_ph} "
                "ON CONFLICT DO NOTHING",
                tuple(_flat),
            )
        c.commit()

        # 🛍️ Shop buff tương tác: seed dịch vụ + tài khoản panel mặc định
        # (chỉ ghi khi chưa có — admin đổi qua /buffadm sẽ được giữ nguyên).
        try:
            _seed_buff_services()
        except Exception:
            pass
        try:
            _env_buff_pass = os.environ.get("BUFF_PANEL_PASS", "")
            c.execute(
                "INSERT INTO settings(key, value) VALUES('buff_panel_user', 'khaiphan111') "
                "ON CONFLICT(key) DO NOTHING",
            )
            if _env_buff_pass:
                c.execute(
                    "INSERT INTO settings(key, value) VALUES('buff_panel_pass', ?) "
                    "ON CONFLICT(key) DO NOTHING",
                    (_env_buff_pass,),
                )
            c.commit()
        except Exception:
            pass

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
        
# ============ MIGRATION TRACKING (muc 5: boot < 20s) ============
# Moi cau migrate co key = md5(SQL chuan hoa). Chay xong (hoac loi "da ton tai")
# thi ghi vao schema_migrations; boot sau skip han -> khong con ~96 cau ALTER
# fail vo ich moi lan boot.
def _migration_key(sql: str) -> str:
    norm = " ".join(sql.strip().split())
    return "m_" + hashlib.md5(norm.encode("utf-8")).hexdigest()[:16]


def _is_already_exists_error(e: Exception) -> bool:
    msg = str(e).lower()
    return "already exists" in msg or "duplicate" in msg


def _ensure_migrations_table(c) -> None:
    c.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version TEXT PRIMARY KEY, applied_at BIGINT NOT NULL)"
    )
    try:
        c.commit()
    except Exception:
        pass


def _load_applied(c) -> set:
    try:
        rows = c.execute("SELECT version FROM schema_migrations").fetchall()
        return {r["version"] for r in rows}
    except Exception:
        return set()


def _migrate_one(c, sql: str, applied: set) -> None:
    """Chay 1 cau migrate co tracking. Skip neu da applied."""
    key = _migration_key(sql)
    if key in applied:
        return
    try:
        c.execute(sql)
    except Exception as e:
        if not _is_already_exists_error(e):
            return  # loi that -> de boot sau thu lai (giong hanh vi cu)
        # cot/bang da ton tai san -> coi nhu applied de lan sau skip
    try:
        c.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (key, int(time.time())),
        )
    except Exception:
        pass
    applied.add(key)


def _split_sql_script(script: str) -> list:
    """Tach script SQL thanh tung cau lenh (bo comment --, tach theo ';')."""
    lines = [l for l in script.split("\n") if not l.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def migrate_db():
    c = get_conn()
    with _lock:
        _ensure_migrations_table(c)
        applied = _load_applied(c)
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
            "ALTER TABLE tg_users ADD COLUMN buff_balance BIGINT DEFAULT 0",
            "ALTER TABLE tg_users ADD COLUMN rent_balance BIGINT DEFAULT 0",
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
            "ALTER TABLE acc_stock ADD COLUMN source TEXT DEFAULT 'shop'",
            "ALTER TABLE acc_stock ADD COLUMN consign_item_id BIGINT DEFAULT 0",
            "ALTER TABLE acc_stock ADD COLUMN price_override BIGINT DEFAULT 0",
            "ALTER TABLE consignors ADD COLUMN sheet_email TEXT DEFAULT ''",
            "ALTER TABLE consignors ADD COLUMN sheet_id TEXT DEFAULT ''",
            "ALTER TABLE consignors ADD COLUMN sheet_url TEXT DEFAULT ''",
            "ALTER TABLE consignors ADD COLUMN sheet_access_granted BIGINT DEFAULT 0",
            "ALTER TABLE consignors ADD COLUMN import_sheet_url TEXT DEFAULT ''",
            "ALTER TABLE consignors ADD COLUMN notify_sale BIGINT DEFAULT 1",
            "ALTER TABLE consignors ADD COLUMN notify_bot_token TEXT DEFAULT ''",
            "ALTER TABLE consignors ADD COLUMN notify_bot_username TEXT DEFAULT ''",
            "ALTER TABLE consignment_orders ADD COLUMN notified BIGINT DEFAULT 0",
            "ALTER TABLE consignment_disputes ADD COLUMN deadline_at BIGINT DEFAULT 0",
            "ALTER TABLE consignment_disputes ADD COLUMN partner_responded BIGINT DEFAULT 0",
            "ALTER TABLE consignment_disputes ADD COLUMN partner_response TEXT DEFAULT ''",
            "ALTER TABLE consignment_disputes ADD COLUMN auto_resolved BIGINT DEFAULT 0",
            "ALTER TABLE consignment_disputes ADD COLUMN reminded_at BIGINT DEFAULT 0",
            "ALTER TABLE consignment_disputes ADD COLUMN partner_photo_file_id TEXT DEFAULT ''",
            "ALTER TABLE consignment_batches ADD COLUMN suspend_prev TEXT DEFAULT ''",
            "ALTER TABLE acc_categories ADD COLUMN consign_pending INTEGER DEFAULT 0",
            "ALTER TABLE giftcodes ADD COLUMN wallet TEXT DEFAULT 'main'",
            "ALTER TABLE promo_codes ADD COLUMN wallet TEXT DEFAULT 'main'"
        ]:
            _migrate_one(c, sql, applied)
        c.commit()
        # 2026-10-05: viotp_rentals.id INTEGER PRIMARY KEY khong tu tang
        # tren Postgres (SQLite thi co) -> moi INSERT deu NotNullViolation,
        # tien tru ma khong tao duoc don thue. Gan sequence idempotent.
        try:
            import psycopg2 as _pg
            if isinstance(get_conn(), PgConnection):
                c.execute("CREATE SEQUENCE IF NOT EXISTS viotp_rentals_id_seq")
                c.execute("ALTER TABLE viotp_rentals ALTER COLUMN id "
                          "SET DEFAULT nextval('viotp_rentals_id_seq')")
                c.execute("ALTER SEQUENCE viotp_rentals_id_seq "
                          "OWNED BY viotp_rentals.id")
                c.commit()
        except Exception:
            pass

# --- SETTINGS ---
# Cache settings trong RAM (xem get_setting): key -> (value, timestamp)
_settings_cache: dict = {}
_SETTINGS_TTL = 30

def get_setting(key: str, default: str = "") -> str:
    # Cache settings trong RAM 30s: get_setting được gọi rất nhiều lần mỗi tin nhắn,
    # mỗi lần là 1 round-trip qua tunnel tới Supabase (~0.3-0.5s) -> bot trả lời chậm.
    # set_setting() luôn cập nhật cache nên đổi qua bot có hiệu lực ngay;
    # đổi từ web admin chậm nhất 30s.
    now = time.time()
    hit = _settings_cache.get(key)
    if hit is not None and now - hit[1] < _SETTINGS_TTL:
        return hit[0]
    row = get_conn().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    v = row["value"] if row else default
    _settings_cache[key] = (v, now)
    return v

def settings_cache_invalidate(key: str) -> None:
    """Xóa cache của 1 setting (dùng khi ghi thẳng SQL, không qua set_setting)."""
    _settings_cache.pop(key, None)

def set_setting(key: str, value: str) -> None:
    with _lock:
        c = get_conn()
        c.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        c.commit()
        _settings_cache[key] = (str(value), time.time())

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
        from .bot import manager
        from .util import vnd
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
    col = ("shop_balance" if wallet == "shop"
           else ("buff_balance" if wallet == "buff"
           else ("rent_balance" if wallet == "rent" else "balance")))
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


WALLET_COLUMNS = {
    "main": "balance",
    "shop": "shop_balance",
    "buff": "buff_balance",
    "rent": "rent_balance",
}


def adjust_wallet(tg_id: int, wallet: str, amount: int, reason: str) -> bool:
    """Cộng/trừ tiền 1 ví bất kỳ (main/shop/buff/rent).
    Trừ tiền kiểm tra nguyên tử: không đủ -> False, không trừ.
    Ghi txns để tra soát. Trả True nếu thành công."""
    col = WALLET_COLUMNS.get(wallet)
    if not col or not amount:
        return False
    with _lock:
        c = get_conn()
        if amount < 0:
            r = c.execute(f"SELECT {col} FROM tg_users WHERE tg_id=?", (tg_id,)).fetchone()
            if not r or int(r[col] or 0) + amount < 0:
                return False
        c.execute(f"UPDATE tg_users SET {col} = {col} + ? WHERE tg_id=?", (amount, tg_id))
        c.execute(
            "INSERT INTO txns(ts, tg_id, amount, reason) VALUES(?,?,?,?)",
            (int(time.time()), tg_id, amount, f"{reason} [ví {wallet}]"),
        )
        c.commit()
    return True


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

        
def create_magic_link(tg_id: int) -> str:
    """Tao magic link luu vao DB chung (Supabase) thay vi RAM.

    Ly do: bot chay tren VM tao token, web admin chay tren Render xac thuc.
    Ban cu luu dict trong RAM nen token tao tren VM khong bao gio xac thuc
    duoc tren Render -> nut "Dang nhap Web" cua /web luon bao het han.
    Giu nguyen semantics cu: token dung 1 lan, het han sau 5 phut.
    """
    import secrets
    token = secrets.token_urlsafe(32)
    exp = int(time.time()) + 300
    with _lock:
        c = get_conn()
        c.execute("DELETE FROM magic_links WHERE exp <= ?", (int(time.time()),))
        c.execute(
            "INSERT INTO magic_links (token, tg_id, exp) VALUES (?, ?, ?)",
            (token, int(tg_id), exp),
        )
        c.commit()
    return token

def verify_magic_link(token: str) -> int:
    if not token:
        return 0
    with _lock:
        c = get_conn()
        row = c.execute(
            "SELECT tg_id, exp FROM magic_links WHERE token=?", (token,)
        ).fetchone()
        if not row:
            return 0
        # Token chi dung 1 lan: xoa ngay khi xac thuc
        c.execute("DELETE FROM magic_links WHERE token=?", (token,))
        c.commit()
        if int(row["exp"]) > int(time.time()):
            return int(row["tg_id"])
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
# ─── VÍ (wallet): 'main' = ví chính, 'shop' = ví shop, 'credits' = lượt credits ─
WALLET_LABEL = {"main": "ví chính", "shop": "ví shop", "credits": "credits",
                "buff": "ví buff", "rent": "ví thuê số"}

def wallet_label(wallet: str) -> str:
    return WALLET_LABEL.get((wallet or "main").strip().lower(), "ví chính")

def parse_wallet(s: str) -> str:
    """Chuẩn hoá lựa chọn ví của admin: 'shop' → 'shop', 'credits' → 'credits',
    còn lại → 'main'."""
    s = (s or "").strip().lower()
    if s in ("shop", "vishop", "ví shop", "vi shop"):
        return "shop"
    if s in ("credits", "credit"):
        return "credits"
    return "main"

def wallet_amount_text(wallet: str, amount: int) -> str:
    """Chuỗi hiển thị giá trị theo ví: credits → 'N credits', tiền → 'Nđ'."""
    if parse_wallet(wallet) == "credits":
        return f"{int(amount)} credits"
    try:
        return f"{int(amount):,}đ".replace(",", ".")
    except Exception:
        return str(amount)

def credit_wallet(tg_id: int, amount: int, reason: str, wallet: str = "main") -> bool:
    """Cộng tiền/credits vào ví đã chọn. Trả về True nếu cộng thành công."""
    w = parse_wallet(wallet)
    if w == "shop":
        return adjust_shop_balance(tg_id, amount, reason)
    if w == "credits":
        add_credits(tg_id, amount, reason)
        return True
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
            "VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
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
            VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 1) RETURNING id
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
            "INSERT INTO alert_rules (tg_id, platform, target, condition, created_at) VALUES (?, ?, ?, ?, ?) RETURNING id",
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
        return new_id

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
    _ensure_migrations_table(c)
    applied = _load_applied(c)
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
            updated_at     BIGINT NOT NULL,
            target         TEXT DEFAULT 'main'
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
            created_at     BIGINT NOT NULL,
            stall          TEXT DEFAULT 'Acc Facebook',
            live_check     INTEGER DEFAULT 1,
            consign_pending INTEGER DEFAULT 0
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
            source       TEXT DEFAULT 'shop',
            consign_item_id BIGINT DEFAULT 0,
            price_override BIGINT DEFAULT 0,
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
        "ALTER TABLE acc_categories ADD COLUMN mystery_weight INTEGER DEFAULT 100",
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
        # 2026-09-25: món UID cụ thể trong giỏ (khách tự chọn UID)
        """CREATE TABLE IF NOT EXISTS cart_uid_items (
            tg_id      BIGINT NOT NULL,
            cat_id     INTEGER NOT NULL,
            stock_id   INTEGER NOT NULL,
            added_at   BIGINT NOT NULL,
            PRIMARY KEY (tg_id, stock_id)
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
        """CREATE TABLE IF NOT EXISTS viotp_rentals (
            id           INTEGER PRIMARY KEY,
            tg_id        BIGINT NOT NULL DEFAULT 0,
            request_id   TEXT NOT NULL DEFAULT '',
            phone_number TEXT NOT NULL DEFAULT '',
            service_id   INTEGER NOT NULL DEFAULT 0,
            service_name TEXT NOT NULL DEFAULT '',
            country      TEXT NOT NULL DEFAULT 'vn',
            cost_price   INTEGER NOT NULL DEFAULT 0,
            sell_price   INTEGER NOT NULL DEFAULT 0,
            status       TEXT NOT NULL DEFAULT 'waiting',
            otp_code     TEXT NOT NULL DEFAULT '',
            created_at   BIGINT NOT NULL DEFAULT 0,
            updated_at   BIGINT NOT NULL DEFAULT 0
        )""",
    ]:
        _migrate_one(c, sql, applied)
    try:
        c.commit()
    except Exception:
        pass
    try:
        # 2026-09-21: don hang luu toan bo thong tin acc (ban xong xoa acc khoi kho)
        for _col in ["uid TEXT DEFAULT ''", "password TEXT DEFAULT ''",
                     "created_date TEXT DEFAULT ''", "backup_mail TEXT DEFAULT ''",
                     "note TEXT DEFAULT ''", "totp TEXT DEFAULT ''",
                     "cookie TEXT DEFAULT ''", "token TEXT DEFAULT ''",
                     "batch TEXT DEFAULT ''"]:
            _migrate_one(c, f"ALTER TABLE acc_orders ADD COLUMN {_col}", applied)
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
        # Them cot dich vu/gia vao kho link buff - chay 1 lan
        if get_setting("linkwh_service_migrated") != "1":
            for _sql in (
                "ALTER TABLE buff_link_warehouse ADD COLUMN service_name TEXT DEFAULT ''",
                "ALTER TABLE buff_link_warehouse ADD COLUMN cost_price BIGINT DEFAULT 0",
                "ALTER TABLE buff_link_warehouse ADD COLUMN sell_price BIGINT DEFAULT 0",
            ):
                try:
                    c.execute(_sql)
                except Exception:
                    pass
            c.commit()
            set_setting("linkwh_service_migrated", "1")
        # Them cot claimed_at cho outbox (thu hoi tin ket o 'sending' khi crash) - chay 1 lan
        if get_setting("outbox_claimed_at_migrated") != "1":
            try:
                c.execute("ALTER TABLE notification_outbox ADD COLUMN claimed_at BIGINT DEFAULT 0")
            except Exception:
                pass
            c.commit()
            set_setting("outbox_claimed_at_migrated", "1")
        # Them cot ref_consignor_id cho outbox (log notif dung doi tac that) - chay 1 lan
        if get_setting("outbox_ref_cid_migrated") != "1":
            try:
                c.execute("ALTER TABLE notification_outbox ADD COLUMN ref_consignor_id BIGINT DEFAULT 0")
            except Exception:
                pass
            c.commit()
            set_setting("outbox_ref_cid_migrated", "1")
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
            "INSERT INTO reseller_keys(name, api_key, credits, created_at) VALUES(?,?,?,?) RETURNING id",
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
    if wallet == "credits":
        # Mã giảm % chỉ có nghĩa khi thanh toán bằng ví chính/ví shop
        # (hiện không có luồng mua nào trả bằng credits).
        return False, "Mã giảm % chỉ áp dụng cho ví chính hoặc ví shop."
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


def finalize_user_promo(tg_id: int, code: str) -> bool:
    """Trừ lượt mã promo SAU khi đơn mua thành công.
    Chỉ trừ khi mã còn hiệu lực tại thời điểm này; luôn xóa mã đang giữ của user.
    Trả về True nếu đã trừ lượt. Không raise."""
    try:
        if not code:
            return False
        ok, _, _ = promo_valid(code)
        if ok:
            consume_promo(code)
        clear_user_promo(tg_id)
        return ok
    except Exception:
        return False

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
    if target not in ("main", "shop", "buff", "rent"):
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
        if target not in ("main", "shop", "buff", "rent"):
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
                "VALUES(?,?,?,?,1,?,?,?) RETURNING id",
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


def acc_stall_live_check(stall: str) -> int:
    """live_check của 1 gian hàng (lấy từ loại acc đầu tiên trong sạp).
    Sạp 'Acc Facebook' mặc định 1, sạp khác mặc định 0."""
    s = (stall or "").strip()
    try:
        r = get_conn().execute(
            "SELECT live_check FROM acc_categories WHERE stall=? LIMIT 1",
            (s,)).fetchone()
        if r is not None:
            return int(r["live_check"] or 0)
    except Exception:
        pass
    return 1 if s == "Acc Facebook" else 0


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
               "mystery_eligible", "hidden", "cover_photo", "mystery_weight"}
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


#: Các trường thông tin acc được phép cập nhật (cập nhật thông tin acc)
STOCK_EDITABLE_FIELDS = ("password", "created_date", "backup_mail", "note",
                         "totp", "cookie", "token")

#: Nhãn hiển thị tiếng Việt cho từng trường
STOCK_FIELD_LABELS = {
    "password": "Mật khẩu", "created_date": "Ngày tạo",
    "backup_mail": "Mail thay", "note": "Ghi chú", "totp": "2FA",
    "cookie": "Cookie", "token": "Token",
}


def _stock_row_to_dict(r) -> dict | None:
    return dict(r) if r else None


def acc_stock_find(cat_id: int, uid: str) -> dict | None:
    """Tìm 1 acc còn hàng (AVAILABLE/DIE) trong loại theo UID."""
    try:
        r = get_conn().execute(
            "SELECT * FROM acc_stock WHERE cat_id=? AND uid=? "
            "AND status IN ('AVAILABLE','DIE') LIMIT 1",
            (cat_id, (uid or "").strip())).fetchone()
        return _stock_row_to_dict(r)
    except Exception:
        return None


def acc_stock_get_by_id(stock_id: int) -> dict | None:
    """Lấy 1 dòng kho theo id (mọi trạng thái)."""
    try:
        r = get_conn().execute(
            "SELECT * FROM acc_stock WHERE id=? LIMIT 1",
            (int(stock_id),)).fetchone()
        return _stock_row_to_dict(r)
    except Exception:
        return None


def acc_stock_find_by_uid(uid: str) -> dict | None:
    """Tìm acc trong kho theo UID (mọi trạng thái, mọi loại).
    Tự strip đuôi '.0' do Excel hay convert UID dài thành float."""
    u = str(uid or "").strip()
    if u.endswith(".0"):
        u = u[:-2]
    if not u:
        return None
    try:
        r = get_conn().execute(
            "SELECT * FROM acc_stock WHERE uid=? LIMIT 1", (u,)).fetchone()
        return _stock_row_to_dict(r)
    except Exception:
        return None


def acc_mark_sold_manual(stock_id: int) -> tuple[bool, str]:
    """Đánh dấu 1 acc đã bán thủ công (bán ngoài bot).
    Chỉ áp dụng cho acc đang AVAILABLE; điều kiện UPDATE chống race
    (2 admin bấm cùng lúc chỉ 1 người thành công).
    sheet_marked giữ 0 để poller 5 phút tự đẩy '🛒 ĐÃ BÁN' lên cột J Sheet.
    Trả (True, "ok") hoặc (False, lý_do)."""
    now = int(time.time())
    with _lock:
        c = get_conn()
        row = c.execute(
            "SELECT id, status FROM acc_stock WHERE id=? LIMIT 1",
            (int(stock_id),)).fetchone()
        if not row:
            return False, "not_found"
        if row["status"] != "AVAILABLE":
            return False, f"status_{row['status']}"
        cur = c.execute(
            "UPDATE acc_stock SET status='SOLD', sold_to=0, sold_at=?, price_sold=0 "
            "WHERE id=? AND status='AVAILABLE'",
            (now, int(stock_id)))
        c.commit()
        if cur.rowcount == 0:
            return False, "race"
        return True, "ok"


def acc_stock_by_sheet_ref(sheet_ref: str) -> dict | None:
    """Tìm acc theo vị trí dòng Sheet ('tab:dòng')."""
    try:
        r = get_conn().execute(
            "SELECT * FROM acc_stock WHERE sheet_ref=? LIMIT 1",
            ((sheet_ref or "").strip(),)).fetchone()
        return _stock_row_to_dict(r)
    except Exception:
        return None


def acc_stock_update_fields(stock_id: int, fields: dict) -> int:
    """Cập nhật các trường thông tin của 1 acc còn hàng (AVAILABLE/DIE).

    fields: dict {tên_trường: giá_trị_mới} — chỉ nhận các trường trong
    STOCK_EDITABLE_FIELDS. Acc đã bán (SOLD) không được đụng.
    Trả số trường thực sự thay đổi (so với giá trị cũ)."""
    clean = {}
    for k, v in (fields or {}).items():
        if k in STOCK_EDITABLE_FIELDS:
            clean[k] = "" if v is None else str(v)
    if not clean:
        return 0
    try:
        with _lock:
            c = get_conn()
            row = c.execute(
                "SELECT * FROM acc_stock WHERE id=? "
                "AND status IN ('AVAILABLE','DIE') LIMIT 1",
                (int(stock_id),)).fetchone()
            if not row:
                return 0
            row = dict(row)
            sets, vals = [], []
            for k, v in clean.items():
                if (row.get(k) or "") != v:
                    sets.append(f"{k}=?")
                    vals.append(v)
            if not sets:
                return 0
            vals.append(int(stock_id))
            c.execute(f"UPDATE acc_stock SET {', '.join(sets)} WHERE id=?", vals)
            c.commit()
            return len(sets)
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
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
        (tg_id, row["id"], cat_id, price, now, now,
         _rv(row, "uid"), _rv(row, "password"), _rv(row, "created_date"),
         _rv(row, "backup_mail"), _rv(row, "note"), _rv(row, "totp"),
         _rv(row, "cookie"), _rv(row, "token"), _rv(row, "batch")))
    order_id = cur.lastrowid
    c.execute(
        "UPDATE acc_stock SET status='SOLD', sold_to=?, sold_at=?, price_sold=? "
        "WHERE id=?",
        (tg_id, now, price, row["id"]))
    _consign_on_sell(c, row, tg_id, order_id, price, now, cat_id)
    return order_id


def _consign_on_sell(c, row, buyer_tg_id: int, order_id: int, price: int, now: int, cat_id: int):
    """Hook: acc nguồn ký gửi được bán -> tạo consignment_orders + ghi sổ chờ.
    Chạy trong cùng kết nối c với _acc_order_create."""
    order_ref = f"ACC-{order_id}"
    try:
        if (row["source"] if "source" in row.keys() else "shop") != "consign":
            return
        item_id = row["consign_item_id"] if "consign_item_id" in row.keys() else 0
        if not item_id:
            return
        # idempotency: đơn đã ghi sổ thì thôi
        dup = c.execute("SELECT 1 FROM consignment_orders WHERE order_ref=? LIMIT 1",
                        (order_ref,)).fetchone()
        if dup:
            return
        it = c.execute(
            "SELECT i.*, b.floor_price, b.sell_price AS batch_sell_price, b.warranty_days, b.category_id"
            " FROM consignment_items i JOIN consignment_batches b ON b.id=i.batch_id"
            " WHERE i.id=?", (item_id,)).fetchone()
        if not it or it["status"] == "sold":
            return
        # Phí KẾT HỢP theo loại acc (quyết định đã chốt): phí cố định + phí % trên giá bán.
        # Cùng công thức với consign_order_create để đồng nhất.
        fee_row = c.execute("SELECT fee_fixed, fee_pct FROM consignment_fees WHERE category_id=?",
                            (it["category_id"],)).fetchone()
        fee_fixed = int(fee_row["fee_fixed"]) if fee_row and fee_row["fee_fixed"] else 0
        fee_pct = float(fee_row["fee_pct"]) if fee_row and fee_row["fee_pct"] else 0.0
        fee_amount = fee_fixed + int(price * fee_pct / 100)
        net = price - fee_amount
        if net < 0:
            net = 0
        w_days = it["warranty_days"] or 0
        w_until = now + w_days * 86400 if w_days > 0 else now
        cur = c.execute(
            "INSERT INTO consignment_orders (item_id, batch_id, consignor_id, buyer_tg_id, order_ref,"
            " sell_price, floor_price, fee_fixed, fee_pct, fee_amount, net_amount,"
            " warranty_days, warranty_until, status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'sold',?) RETURNING id",
            (item_id, it["batch_id"], it["consignor_id"], buyer_tg_id, order_ref,
             price, it["floor_price"], fee_fixed, fee_pct, fee_amount, net,
             w_days, w_until, now))
        c.execute(
            "INSERT INTO consignment_ledger (consignor_id, kind, amount, ref_type, ref_id, note, created_at)"
            " VALUES (?,?,?,?,?,?,?) RETURNING id",
            (it["consignor_id"], "pending_in", net, "order", cur.lastrowid,
             f"Bán {order_ref}", now))
        c.execute(
            "UPDATE consignment_items SET status='sold', sold_at=?, sold_price=?,"
            " fee_fixed=?, fee_pct=?, net_amount=?, warranty_until=? WHERE id=?",
            (now, price, fee_fixed, fee_pct, net, w_until, item_id))
    except Exception as e:
        # PG autocommit: đơn bán đã commit trước đó nên KHÔNG raise (không được fail đơn khách).
        # Log lớn để admin phát hiện và bù bút toán tay (idempotency theo order_ref).
        print(f"[CONSIGN-ERR] _consign_on_sell {order_ref}: {e}", flush=True)


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
    """Ứng viên hộp mù: chọn LOẠI theo tỷ trọng mystery_weight (số càng lớn
    càng dễ trúng), rồi lấy ngẫu nhiên acc trong loại đó.
    Trả list dict {"id","uid","cat_id"}."""
    import random
    c = get_conn()
    cats = c.execute(
        "SELECT id, COALESCE(mystery_weight,100) AS w FROM acc_categories "
        "WHERE active=1 AND hidden=0 AND mystery_eligible=1").fetchall()
    avail = []
    for ct in cats:
        n = c.execute(
            "SELECT COUNT(*) FROM acc_stock WHERE cat_id=? AND status='AVAILABLE'",
            (ct["id"],)).fetchone()[0]
        if n:
            avail.append(ct)
    if not avail:
        return []
    weights = [max(1, int(ct["w"] or 100)) for ct in avail]
    ct = random.choices(avail, weights=weights, k=1)[0]
    excl = set(exclude_ids or ())
    q = "SELECT id, uid, cat_id FROM acc_stock WHERE cat_id=? AND status='AVAILABLE'"
    params = [ct["id"]]
    if excl:
        q += " AND id NOT IN (%s)" % ",".join("?" for _ in excl)
        params += list(excl)
    pool = [dict(r) for r in c.execute(q, params).fetchall()]
    random.shuffle(pool)
    return pool[:max(1, int(limit))]


def acc_mystery_weights():
    """Danh sách loại tham gia hộp mù kèm tỷ trọng và % trúng quy đổi."""
    c = get_conn()
    rows = c.execute(
        "SELECT id, name, COALESCE(mystery_weight,100) AS w FROM acc_categories "
        "WHERE active=1 AND hidden=0 AND mystery_eligible=1 ORDER BY id").fetchall()
    items = [{"id": r["id"], "name": r["name"],
              "weight": max(1, int(r["w"] or 100))} for r in rows]
    total = sum(i["weight"] for i in items) or 1
    for i in items:
        i["pct"] = round(i["weight"] * 100.0 / total, 1)
    return items


def acc_mystery_set_eligible(cid, on: int) -> bool:
    """Bật/tắt 1 loại acc tham gia hộp mù (giữ nguyên trọng số cũ)."""
    return bool(acc_category_update(int(cid), mystery_eligible=1 if on else 0))


def acc_mystery_set_eligible_stall(stall: str, on: int) -> int:
    """Bật/tắt toàn bộ loại acc của 1 gian hàng trong hộp mù. Trả số loại đổi."""
    items = [i for i in acc_mystery_setup_list() if i["stall"] == stall]
    n = 0
    for i in items:
        if acc_category_update(i["id"], mystery_eligible=1 if on else 0):
            n += 1
    return n


def acc_mystery_set_multi(pct_map):
    """Đặt % cho NHIỀU loại acc 1 lúc. pct_map: {cat_id: pct}.
    - Các loại được chỉ định: đúng bằng % đã cho (tự bật tham gia nếu chưa).
    - Tổng S phải <= 100, mỗi pct 1–99.
    - Phần còn lại (100-S)% tự chia cho các loại đang tham gia KHÔNG được
      chỉ định, theo đúng tỷ lệ cũ của chúng.
    - S == 100 mà còn loại không được chỉ định -> tự tắt chúng khỏi hộp mù.
    Trả về (True, ghi_chú) / (False, lỗi)."""
    clean = {}
    for cid, p in (pct_map or {}).items():
        try:
            cid, p = int(cid), int(p)
        except Exception:
            return False, f"ID/% phải là số (lỗi ở '{cid}')."
        if not (1 <= p <= 99):
            return False, f"% của loại #{cid} phải từ 1–99."
        c = acc_category_get(cid)
        c = dict(c) if c else None
        if not c or not int(c.get("active") or 0) or int(c.get("hidden") or 0):
            return False, f"Không có loại acc #{cid}."
        clean[cid] = p
    if not clean:
        return False, "Chưa nhập cặp ID + % nào."
    s = sum(clean.values())
    if s > 100:
        return False, f"Tổng % = {s}% vượt quá 100%."
    for cid in clean:
        acc_category_update(cid, mystery_eligible=1)
    items = [i for i in acc_mystery_setup_list() if i["eligible"]]
    specified = [i for i in items if i["id"] in clean]
    rest_cats = [i for i in items if i["id"] not in clean]
    K = 10000
    assigned = 0
    for i in specified:
        w = clean[i["id"]] * (K // 100)
        acc_category_update(i["id"], mystery_weight=w)
        assigned += w
    rest = K - assigned
    note = ""
    if rest_cats:
        if rest <= 0:
            names = ", ".join(f"#{i['id']}" for i in rest_cats)
            for i in rest_cats:
                acc_category_update(i["id"], mystery_eligible=0)
            note = f"Tổng đã đủ 100% nên tự tắt khỏi hộp mù: {names}."
        else:
            old_sum = sum(i["weight"] for i in rest_cats) or 1
            acc = 0
            for idx, i in enumerate(rest_cats):
                if idx < len(rest_cats) - 1:
                    w = max(1, int(round(i["weight"] * rest / old_sum)))
                else:
                    w = max(1, rest - acc)
                acc += w
                acc_category_update(i["id"], mystery_weight=w)
    return True, note


def acc_mystery_set_pct(cid, pct):
    """Đặt % trúng TRỰC TIẾP cho 1 loại acc (1–99). Loại được chọn sẽ đúng
    bằng pct%; các loại còn lại tự chia phần % còn lại theo đúng tỷ lệ cũ
    của chúng. Loại chưa tham gia thì tự bật luôn. Trả về True/False."""
    try:
        pct = int(pct)
    except Exception:
        return False
    if not (1 <= pct <= 99):
        return False
    items = [i for i in acc_mystery_setup_list() if i["eligible"]]
    if not any(i["id"] == cid for i in items):
        acc_category_update(cid, mystery_eligible=1)
        items = [i for i in acc_mystery_setup_list() if i["eligible"]]
    target = next((i for i in items if i["id"] == cid), None)
    if not target:
        return False
    others = [i for i in items if i["id"] != cid]
    K = 10000  # thang chuẩn nội bộ
    new_w_target = pct * (K // 100)
    if others:
        old_sum = sum(i["weight"] for i in others) or 1
        rest = K - new_w_target
        assigned = 0
        for idx, o in enumerate(others):
            if idx < len(others) - 1:
                w = max(1, int(round(o["weight"] * rest / old_sum)))
            else:
                w = max(1, rest - assigned)  # trù/bu trừ phần làm tròn
            assigned += w
            acc_category_update(o["id"], mystery_weight=w)
    else:
        new_w_target = K  # chỉ còn 1 loại -> 100%
    acc_category_update(cid, mystery_weight=max(1, int(new_w_target)),
                        mystery_eligible=1)
    return True


def acc_mystery_setup_list():
    """Tất cả loại acc đang hoạt động để setup hộp mù (kể cả loại mới chưa
    tham gia). eligible=1: đang tham gia (có % trúng); 0: chưa tham gia."""
    c = get_conn()
    rows = c.execute(
        "SELECT id, name, price, COALESCE(stall,'Acc Facebook') AS stall, "
        "mystery_eligible, COALESCE(mystery_weight,100) AS w "
        "FROM acc_categories WHERE active=1 AND hidden=0 ORDER BY id").fetchall()
    items = []
    for r in rows:
        n = c.execute(
            "SELECT COUNT(*) FROM acc_stock WHERE cat_id=? AND status='AVAILABLE'",
            (r["id"],)).fetchone()[0]
        items.append({"id": r["id"], "name": r["name"], "price": r["price"],
                      "stall": r["stall"],
                      "eligible": int(r["mystery_eligible"] or 0),
                      "weight": max(1, int(r["w"] or 100)), "stock": n})
    total = sum(i["weight"] for i in items if i["eligible"]) or 1
    for i in items:
        i["pct"] = round(i["weight"] * 100.0 / total, 1) if i["eligible"] else 0
    return items


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


def acc_stock_exists_count(cat_id: int) -> int:
    """Đếm acc trạng thái EXISTS (check chỉ biết tồn tại, chưa rõ live/die)."""
    r = get_conn().execute(
        "SELECT COUNT(*) n FROM acc_stock WHERE cat_id=? AND status='EXISTS'",
        (cat_id,)).fetchone()
    return r["n"] if r else 0


def acc_stock_mark_exists(stock_ids) -> int:
    """Đánh dấu acc là EXISTS (tồn tại nhưng chưa xác định được live/die).
    Chuyển status='EXISTS' (rời kho bán, giữ lại cho admin xem/xử lý).
    Trả số dòng đã đánh dấu."""
    ids = [int(i) for i in (stock_ids or [])]
    if not ids:
        return 0
    with _lock:
        c = get_conn()
        cur = c.execute(
            "UPDATE acc_stock SET status='EXISTS' WHERE status='AVAILABLE' AND id IN (%s)"
            % ",".join("?" for _ in ids), ids)
        c.commit()
        return cur.rowcount or 0


def acc_stock_unmark_exists(stock_ids) -> int:
    """Chuyển acc từ EXISTS về AVAILABLE (admin xác nhận bán lại được).
    Trả số dòng đã chuyển."""
    ids = [int(i) for i in (stock_ids or [])]
    if not ids:
        return 0
    with _lock:
        c = get_conn()
        cur = c.execute(
            "UPDATE acc_stock SET status='AVAILABLE' WHERE status='EXISTS' AND id IN (%s)"
            % ",".join("?" for _ in ids), ids)
        c.commit()
        return cur.rowcount or 0


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
            "ON CONFLICT(tg_id, cat_id) DO UPDATE SET qty = CASE WHEN cart_items.qty + ? > 1000 THEN 1000 ELSE cart_items.qty + ? END",
            (tg_id, cat_id, qty, now, qty, qty))
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


# ---------- Món UID cụ thể trong giỏ (2026-09-25) ----------
def cart_uid_add(tg_id: int, cat_id: int, stock_id: int) -> str:
    """Thêm 1 UID cụ thể vào giỏ. Trả 'ok' | 'exists' | 'unavailable'."""
    with _lock:
        c = get_conn()
        r = c.execute(
            "SELECT id FROM acc_stock WHERE id=? AND cat_id=? AND status='AVAILABLE'",
            (stock_id, cat_id)).fetchone()
        if not r:
            return "unavailable"
        e = c.execute(
            "SELECT stock_id FROM cart_uid_items WHERE tg_id=? AND stock_id=?",
            (tg_id, stock_id)).fetchone()
        if e:
            return "exists"
        c.execute(
            "INSERT INTO cart_uid_items(tg_id, cat_id, stock_id, added_at) VALUES(?,?,?,?)",
            (tg_id, cat_id, stock_id, int(time.time())))
        c.commit()
        return "ok"


def cart_uid_list(tg_id: int) -> list:
    """Các UID cụ thể trong giỏ kèm thông tin loại acc + UID."""
    c = get_conn()
    return [dict(r) for r in c.execute(
        "SELECT i.cat_id, i.stock_id, i.added_at, s.uid, s.created_date, "
        "c.name, c.price, c.active, c.hidden FROM cart_uid_items i "
        "JOIN acc_stock s ON s.id = i.stock_id "
        "JOIN acc_categories c ON c.id = i.cat_id "
        "WHERE i.tg_id=? ORDER BY i.added_at",
        (tg_id,)).fetchall()]


def cart_uid_remove(tg_id: int, stock_id: int) -> None:
    with _lock:
        c = get_conn()
        c.execute("DELETE FROM cart_uid_items WHERE tg_id=? AND stock_id=?",
                  (tg_id, stock_id))
        c.commit()


def cart_uid_clear(tg_id: int) -> None:
    with _lock:
        c = get_conn()
        c.execute("DELETE FROM cart_uid_items WHERE tg_id=?", (tg_id,))
        c.commit()


def cart_uid_count(tg_id: int) -> int:
    r = get_conn().execute(
        "SELECT COUNT(*) n FROM cart_uid_items WHERE tg_id=?", (tg_id,)).fetchone()
    return int(r["n"] or 0) if r else 0


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


def acc_file_token_create(tg_id: int, order_ids: list) -> str:
    """Tạo token ngắn cho nút tải full nhiều acc (tránh callback_data quá 64 bytes).
    Token hết hạn sau 24h."""
    import secrets
    import time
    now = int(time.time())
    order_csv = ",".join(str(i) for i in order_ids)
    with _lock:
        c = get_conn()
        # dọn token cũ quá 24h
        try:
            c.execute("DELETE FROM acc_file_tokens WHERE created_at < ?",
                      (now - 86400,))
        except Exception:
            pass
        for _ in range(5):
            token = secrets.token_hex(4)  # 8 ký tự
            try:
                c.execute(
                    "INSERT INTO acc_file_tokens(token, tg_id, order_ids, created_at) "
                    "VALUES(?, ?, ?, ?)",
                    (token, tg_id, order_csv, now))
                c.commit()
                return token
            except Exception:
                # trùng token: rollback rồi thử token mới, KHÔNG ghi đè token người khác
                try:
                    c.rollback()
                except Exception:
                    pass
                continue
        # fallback gần như không bao giờ tới
        token = secrets.token_hex(8)
        c.execute(
            "INSERT INTO acc_file_tokens(token, tg_id, order_ids, created_at) "
            "VALUES(?, ?, ?, ?) ON CONFLICT(token) DO NOTHING",
            (token, tg_id, order_csv, now))
        c.commit()
    return token


def acc_file_token_get(token: str, tg_id: int) -> list:
    """Lấy danh sách order_id từ token, kiểm tra đúng chủ. Trả [] nếu sai."""
    import time
    try:
        r = get_conn().execute(
            "SELECT tg_id, order_ids, created_at FROM acc_file_tokens "
            "WHERE token=?", (token,)).fetchone()
        if not r:
            return []
        if int(r["tg_id"]) != int(tg_id):
            return []
        if int(time.time()) - int(r["created_at"]) > 86400:
            return []
        return [int(x) for x in str(r["order_ids"]).split(",")
                if x.strip().isdigit()]
    except Exception:
        return []


def acc_order_is_consign(order_id: int) -> bool:
    """Đơn acc_orders có phải hàng ký gửi không (dựa vào acc_stock.source).

    Dùng để đánh dấu tin báo cho admin và chặn admin phụ tự xử lý
    hoàn tiền/BH đơn ký gửi (phải qua tranh chấp do chủ shop quyết)."""
    try:
        r = get_conn().execute(
            "SELECT s.source FROM acc_orders o "
            "JOIN acc_stock s ON s.id=o.stock_id "
            "WHERE o.id=?", (order_id,)).fetchone()
        return bool(r and (r["source"] or "shop") == "consign")
    except Exception:
        return False


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
            "VALUES(?,?,?,?,'PENDING',?) RETURNING id",
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


def acc_effective_unit_price(cat_id: int, base_price: int) -> tuple:
    """Giá 1 acc thực tế khách phải trả cho acc AVAILABLE cũ nhất:
    hàng ký gửi (có price_override > 0) dùng giá duyệt, còn lại dùng giá loại.
    Trả (price, is_consign)."""
    try:
        r = get_conn().execute(
            "SELECT source, price_override FROM acc_stock WHERE cat_id=? AND status='AVAILABLE'"
            " ORDER BY id LIMIT 1", (cat_id,)).fetchone()
        if r and (r["source"] or "shop") == "consign" and (r["price_override"] or 0) > 0:
            return int(r["price_override"]), True
    except Exception:
        pass
    return int(base_price or 0), False


def acc_stock_unit_price(stock: dict, base_price: int) -> tuple:
    """Giá của 1 acc cụ thể (dùng khi khách chọn UID): ký gửi -> price_override,
    còn lại -> giá loại. Trả (price, is_consign)."""
    try:
        if (stock.get("source") or "shop") == "consign" and (stock.get("price_override") or 0) > 0:
            return int(stock["price_override"]), True
    except Exception:
        pass
    return int(base_price or 0), False


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
    """4.8 Hộp mù: giao ngẫu nhiên 1 acc — chọn LOẠI theo tỷ trọng
    mystery_weight, rồi chọn ngẫu nhiên acc trong loại.
    Trả (order_id, row, cat_name) hoặc None nếu hết."""
    import random
    now = int(time.time())
    with _lock:
        c = get_conn()
        cats = c.execute(
            "SELECT id, name, COALESCE(mystery_weight,100) AS w FROM acc_categories "
            "WHERE active=1 AND hidden=0 AND mystery_eligible=1").fetchall()
        avail = []
        for ct in cats:
            rows = c.execute(
                "SELECT * FROM acc_stock WHERE cat_id=? AND status='AVAILABLE' "
                "ORDER BY id", (ct["id"],)).fetchall()
            if rows:
                avail.append((ct, rows))
        if not avail:
            return None
        weights = [max(1, int(ct["w"] or 100)) for ct, _ in avail]
        ct, rows = random.choices(avail, weights=weights, k=1)[0]
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


def acc_has_recent_order(tg_id: int, cat_id: int, within_sec: int = 120) -> bool:
    """True nếu tg_id có đơn loại cat_id được tạo trong within_sec giây qua.

    Dùng khi luồng mua gãy giữa chừng sau khi trừ tiền: nếu đơn đã kịp commit
    thì KHÔNG tự hoàn tiền (tránh hoàn thừa khi acc đã giao), để admin xử lý tay.
    """
    cutoff = int(time.time()) - max(1, int(within_sec))
    r = get_conn().execute(
        "SELECT 1 FROM acc_orders WHERE tg_id=? AND cat_id=? AND created_at>=? LIMIT 1",
        (tg_id, cat_id, cutoff)).fetchone()
    return bool(r)


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


# ═══════════════════════════════════════════════════════════════════
# 🛍️ SHOP BUFF TƯƠNG TÁC — ví buff riêng + dịch vụ + đơn hàng
# ═══════════════════════════════════════════════════════════════════

def buff_get_balance(tg_id: int) -> int:
    """Số dư ví buff của user (0 nếu chưa có cột/user)."""
    try:
        r = get_conn().execute(
            "SELECT buff_balance FROM tg_users WHERE tg_id=?", (tg_id,)).fetchone()
        return int(r["buff_balance"] or 0) if r else 0
    except Exception:
        return 0


def buff_adjust_balance(tg_id: int, amount: int, reason: str) -> bool:
    """Cộng/trừ ví buff. Trừ tiền kiểm tra nguyên tử: không đủ -> False."""
    with _lock:
        c = get_conn()
        if amount < 0:
            try:
                r = c.execute("SELECT buff_balance FROM tg_users WHERE tg_id=?",
                              (tg_id,)).fetchone()
            except Exception:
                return False
            if not r or int(r["buff_balance"] or 0) + amount < 0:
                return False
        c.execute("UPDATE tg_users SET buff_balance = buff_balance + ? WHERE tg_id=?",
                  (amount, tg_id))
        c.execute(
            "INSERT INTO txns(ts, tg_id, amount, reason) VALUES(?,?,?,?)",
            (int(time.time()), tg_id, amount, reason),
        )
        c.commit()
    return True


def rent_get_balance(tg_id: int) -> int:
    """Số dư ví thuê số của user (0 nếu chưa có cột/user)."""
    try:
        r = get_conn().execute(
            "SELECT rent_balance FROM tg_users WHERE tg_id=?", (tg_id,)).fetchone()
        return int(r["rent_balance"] or 0) if r else 0
    except Exception:
        return 0


def rent_adjust_balance(tg_id: int, amount: int, reason: str) -> bool:
    """Cộng/trừ ví thuê số. Trừ tiền kiểm tra nguyên tử: không đủ -> False."""
    with _lock:
        c = get_conn()
        if amount < 0:
            try:
                r = c.execute("SELECT rent_balance FROM tg_users WHERE tg_id=?",
                              (tg_id,)).fetchone()
            except Exception:
                return False
            if not r or int(r["rent_balance"] or 0) + amount < 0:
                return False
        c.execute("UPDATE tg_users SET rent_balance = rent_balance + ? WHERE tg_id=?",
                  (amount, tg_id))
        c.execute(
            "INSERT INTO txns(ts, tg_id, amount, reason) VALUES(?,?,?,?)",
            (int(time.time()), tg_id, amount, reason),
        )
        c.commit()
    return True


_BUFF_PLATFORMS = [
    ("tiktok", "TikTok", "🎵"),
    ("facebook", "Facebook", "📘"),
    ("instagram", "Instagram", "📸"),
    ("youtube", "Youtube", "▶️"),
    ("telegram", "Telegram", "✈️"),
    ("shopee", "Shopee", "🛍️"),
    ("threads", "Threads", "🧵"),
    ("whatsapp", "WhatsApp", "💬"),
    ("traffic", "Traffic", "🚗"),
]

# (platform_key, platform_name, category_key, category_name,
#  panel_id, name, desc, cost, sell, min, max)
_BUFF_SEED = [
    ("tiktok", "TikTok", "views", "👁 Views", 7697, "Siêu rẻ", "Tốc độ 100M/ngày", 100, 500, 100, 10000000),
    ("tiktok", "TikTok", "views", "👁 Views", 6898, "Không tụt 137 ngày", "Bestseller bảo hành", 1663, 5000, 100, 1000000),
    ("tiktok", "TikTok", "likes", "❤️ Likes", 7516, "Like Tây", "Lên nhanh", 1290, 5000, 10, 5000000),
    ("tiktok", "TikTok", "likes", "❤️ Likes", 7452, "Like Việt thật", "Nick Việt thật", 1885, 7000, 10, 1000000),
    ("tiktok", "TikTok", "followers", "👥 Followers", 7570, "Follow Tây", "Giá rẻ", 1290, 5000, 1, 30000),
    ("tiktok", "TikTok", "followers", "👥 Followers", 7150, "Follow Việt", "Nick Việt", 28080, 60000, 50, 100000),
    ("tiktok", "TikTok", "shares", "🔄 Shares", 7489, "Share video", "Chia sẻ video", 468, 2000, 10, 1000000),
    ("facebook", "Facebook", "views", "👁 Views", 6835, "View video", "View video FB", 518, 2000, 1, 50000000),
    ("facebook", "Facebook", "likes", "👍 Likes", 7480, "Like Tây", "Đủ 7 loại cảm xúc", 2312, 8000, 10, 5000000),
    ("facebook", "Facebook", "likes", "👍 Likes", 7609, "Like Việt", "Nick Việt", 7020, 20000, 10, 1000000),
    ("facebook", "Facebook", "followers", "👥 Follow", 7521, "Follow Tây", "Follow trang cá nhân", 2996, 10000, 100, 1000000),
    ("facebook", "Facebook", "followers", "👥 Follow", 7133, "Follow Việt", "Nick Việt", 9370, 25000, 10, 1000000),
    ("facebook", "Facebook", "members", "👨‍👩‍👧‍👦 Member nhóm", 7546, "Member Việt", "Vào nhóm", 18000, 40000, 500, 50000),
    ("instagram", "Instagram", "views", "👁 Views", 7619, "View siêu rẻ", "Rẻ nhất hệ thống", 19, 200, 10, 1000000),
    ("instagram", "Instagram", "likes", "❤️ Likes", 7226, "Like Tây", "Lên nhanh", 1011, 4000, 10, 5000000),
    ("instagram", "Instagram", "followers", "👥 Followers", 7696, "Follow Tây", "Ổn định", 5991, 18000, 10, 5000000),
    ("instagram", "Instagram", "followers", "👥 Followers", 7652, "Follow Việt", "Nick Việt", 26000, 55000, 10, 1000000),
    ("youtube", "Youtube", "views", "👁 Views", 6574, "View", "View video", 4056, 12000, 100, 100000),
    ("youtube", "Youtube", "subs", "🔔 Subscribers", 7651, "Sub", "Tăng sub kênh", 344, 1500, 100, 100000),
    ("youtube", "Youtube", "likes", "👍 Likes", 7215, "Like video", "Like video", 53368, 110000, 10, 100000),
    ("telegram", "Telegram", "views", "👁 Views", 7532, "View bài viết", "View post/channel", 32, 300, 10, 100000),
    ("telegram", "Telegram", "members", "👥 Members", 6809, "Member", "20K/ngày", 1872, 6000, 10, 1000000),
    ("shopee", "Shopee", "followers", "👥 Followers", 7085, "Follow shop", "Follow gian hàng", 29718, 60000, 100, 1000000),
    ("threads", "Threads", "followers", "👥 Followers", 7553, "Follow", "Follow Threads", 16549, 35000, 10, 20000),
    ("threads", "Threads", "likes", "❤️ Likes", 7552, "Like", "Like bài viết", 16549, 35000, 1, 20000),
]


def _seed_buff_services() -> None:
    """Seed 25 dịch vụ buff — chỉ chạy khi bảng đang trống."""
    with _lock:
        c = get_conn()
        n = c.execute("SELECT COUNT(*) AS n FROM buff_services").fetchone()["n"]
        if int(n or 0) > 0:
            return
        for (pk, pn, ck, cn, pid, name, desc,
             cost, sell, mn, mx) in _BUFF_SEED:
            c.execute(
                "INSERT INTO buff_services(platform_key, platform_name, category_key,"
                " category_name, panel_service_id, name, description,"
                " cost_price, sell_price, min_qty, max_qty, enabled)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,1)",
                (pk, pn, ck, cn, pid, name, desc, cost, sell, mn, mx),
            )
        c.commit()


def buff_platforms() -> list:
    return [{"key": k, "name": n, "icon": i} for k, n, i in _BUFF_PLATFORMS]


def buff_categories(platform_key: str) -> list:
    rows = get_conn().execute(
        "SELECT DISTINCT category_key, category_name FROM buff_services"
        " WHERE platform_key=? AND enabled=1 ORDER BY category_key",
        (platform_key,)).fetchall()
    return [dict(r) for r in rows]


def buff_services_list(platform_key: str, category_key: str,
                       only_enabled: bool = True) -> list:
    q = ("SELECT * FROM buff_services WHERE platform_key=? AND category_key=?"
         + (" AND enabled=1" if only_enabled else "") + " ORDER BY sell_price")
    return [dict(r) for r in get_conn().execute(q, (platform_key, category_key)).fetchall()]


def buff_service_get(sid: int):
    r = get_conn().execute("SELECT * FROM buff_services WHERE id=?", (sid,)).fetchone()
    return dict(r) if r else None


def buff_service_set_price(sid: int, sell_price: int) -> bool:
    with _lock:
        c = get_conn()
        cur = c.execute("UPDATE buff_services SET sell_price=? WHERE id=?",
                        (int(sell_price), sid))
        c.commit()
        return cur.rowcount > 0


def buff_service_toggle(sid: int) -> bool:
    """Bật/tắt dịch vụ. Trả True nếu sau toggle đang BẬT."""
    with _lock:
        c = get_conn()
        r = c.execute("SELECT enabled FROM buff_services WHERE id=?", (sid,)).fetchone()
        if not r:
            return False
        new = 0 if int(r["enabled"]) else 1
        c.execute("UPDATE buff_services SET enabled=? WHERE id=?", (new, sid))
        c.commit()
        return bool(new)


def buff_order_create(tg_id: int, service_id: int, link: str,
                      quantity: int, total_price: int, total_cost: int) -> dict:
    import random as _rnd
    now = int(time.time())
    with _lock:
        c = get_conn()
        for _ in range(10):
            code = "B" + str(_rnd.randint(100000, 999999))
            try:
                # Postgres không có lastrowid -> dùng RETURNING id
                cur = c.execute(
                    "INSERT INTO buff_orders(code, tg_id, service_id, link, quantity,"
                    " total_price, total_cost, status, created_at, updated_at)"
                    " VALUES(?,?,?,?,?,?,?,'pending',?,?) RETURNING id",
                    (code, tg_id, service_id, link, quantity,
                     total_price, total_cost, now, now),
                )
                row = cur.fetchone()
                oid = row[0] if row else cur.lastrowid
                c.commit()
                break
            except Exception:
                continue
        else:
            raise RuntimeError("Không tạo được mã đơn buff")
    return buff_order_get(oid)


def buff_order_get(order_id: int):
    r = get_conn().execute("SELECT * FROM buff_orders WHERE id=?", (order_id,)).fetchone()
    return dict(r) if r else None


def buff_link_save(tg_id: int, platform: str, link: str,
                  service_name: str = "", cost_price: int = 0, sell_price: int = 0):
    """Lưu link vào kho (tự động khi khách đặt đơn)."""
    now = int(time.time())
    link = (link or "").strip()
    if not link:
        return
    with _lock:
        c = get_conn()
        c.execute(
            "INSERT INTO buff_link_warehouse(tg_id, platform, link, first_used_at, last_used_at, use_count,"
            " service_name, cost_price, sell_price)"
            " VALUES(?,?,?,?,?,1,?,?,?)"
            " ON CONFLICT(tg_id, link) DO UPDATE SET"
            " last_used_at=excluded.last_used_at,"
            " use_count=use_count+1,"
            " platform=excluded.platform,"
            " service_name=excluded.service_name,"
            " cost_price=excluded.cost_price,"
            " sell_price=excluded.sell_price",
            (tg_id, platform or "", link, now, now,
             service_name or "", int(cost_price or 0), int(sell_price or 0)),
        )
        c.commit()


def buff_link_by_user(tg_id: int, limit: int = 10):
    """Link đã dùng của 1 khách (mới nhất trước)."""
    rows = get_conn().execute(
        "SELECT * FROM buff_link_warehouse WHERE tg_id=? ORDER BY last_used_at DESC LIMIT ?",
        (tg_id, limit)).fetchall()
    return [dict(r) for r in rows]


def buff_link_warehouse_list(platform: str = "", search: str = "",
                             page: int = 0, per_page: int = 10):
    """Kho link cho admin: lọc theo nền tảng / tìm kiếm."""
    conds, params = [], []
    if platform:
        conds.append("platform=?")
        params.append(platform)
    if search:
        conds.append("(link LIKE ? OR CAST(tg_id AS TEXT) LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    total = get_conn().execute(
        f"SELECT COUNT(*) FROM buff_link_warehouse {where}", params).fetchone()[0]
    rows = get_conn().execute(
        f"SELECT * FROM buff_link_warehouse {where}"
        f" ORDER BY last_used_at DESC LIMIT ? OFFSET ?",
        params + [per_page, page * per_page]).fetchall()
    return total, [dict(r) for r in rows]


def buff_link_platforms():
    """Danh sách nền tảng có trong kho link."""
    rows = get_conn().execute(
        "SELECT DISTINCT platform FROM buff_link_warehouse WHERE platform<>'' ORDER BY platform"
    ).fetchall()
    return [r[0] for r in rows]


def buff_link_warehouse_export(limit: int = 5000):
    """Xuất toàn bộ kho link buff cho Google Sheet, kèm tên khách.

    Sắp xếp: mới dùng nhất trước. Trả list dict
    {tg_id, platform, link, use_count, first_used_at, last_used_at,
     service_name, cost_price, sell_price, uname, uusername}."""
    rows = get_conn().execute(
        "SELECT w.tg_id, w.platform, w.link, w.use_count,"
        " w.first_used_at, w.last_used_at,"
        " w.service_name, w.cost_price, w.sell_price,"
        " u.name AS uname, u.username AS uusername"
        " FROM buff_link_warehouse w"
        " LEFT JOIN tg_users u ON u.tg_id=w.tg_id"
        " ORDER BY w.last_used_at DESC LIMIT ?",
        (limit,)).fetchall()
    return [dict(r) for r in rows]


def buff_order_update(order_id: int, **fields) -> bool:
    allowed = {"status", "panel_order_id", "total_price", "total_cost", "link", "quantity"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return False
    sets["updated_at"] = int(time.time())
    with _lock:
        c = get_conn()
        cols = ", ".join(f"{k}=?" for k in sets)
        cur = c.execute(f"UPDATE buff_orders SET {cols} WHERE id=?",
                        (*sets.values(), order_id))
        c.commit()
        return cur.rowcount > 0


def buff_orders_pending(limit: int = 5) -> list:
    return [dict(r) for r in get_conn().execute(
        "SELECT * FROM buff_orders WHERE status='pending' ORDER BY id LIMIT ?",
        (limit,)).fetchall()]


def buff_orders_by_user(tg_id: int, limit: int = 10) -> list:
    return [dict(r) for r in get_conn().execute(
        "SELECT o.*, s.name AS service_name, s.platform_name"
        " FROM buff_orders o LEFT JOIN buff_services s ON s.id=o.service_id"
        " WHERE o.tg_id=? ORDER BY o.id DESC LIMIT ?",
        (tg_id, limit)).fetchall()]


# ================= KÝ GỬI ACC =================
# Trạng thái consignor: pending/active/suspended/banned
# Trạng thái batch: draft/submitted/approved/rejected/listed/closed
# Trạng thái item: pending/approved/rejected/listed/sold/quarantine/returned

def consignor_get(tg_id: int) -> Optional[dict]:
    r = get_conn().execute("SELECT * FROM consignors WHERE tg_id=?", (tg_id,)).fetchone()
    return dict(r) if r else None


def consignor_create(tg_id: int, name: str, phone: str, note: str = "", sheet_email: str = "") -> int:
    now = int(time.time())
    # Hạn mức 0 = KHÔNG GIỚI HẠN (chủ shop chưa cấu hình thì đối tác mới vẫn hoạt động bình thường).
    # Muốn giới hạn thì vào /kyguiadm → ⚙️ Cấu hình để đặt số cụ thể cho từng đối tác.
    _mi = get_setting("consign_default_max_items", "")
    _mv = get_setting("consign_default_max_value", "")
    max_items = int(_mi) if str(_mi).strip().isdigit() else 0
    max_value = int(_mv) if str(_mv).strip().isdigit() else 0
    with _lock:
        c = get_conn()
        cur = c.execute(
            "INSERT INTO consignors (tg_id, name, phone, note, sheet_email, status, max_items, max_value, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?) RETURNING id",
            (tg_id, name, phone, note, sheet_email, "pending", max_items, max_value, now))
        c.commit()
        return cur.lastrowid


def consignor_set_status(cid: int, status: str, by_id: int = 0) -> bool:
    now = int(time.time())
    with _lock:
        c = get_conn()
        if status == "active":
            cur = c.execute("UPDATE consignors SET status=?, approved_at=?, approved_by=? WHERE id=?",
                            (status, now, by_id, cid))
        else:
            cur = c.execute("UPDATE consignors SET status=? WHERE id=?", (status, cid))
        c.commit()
        return cur.rowcount > 0


def consign_lock_partner(cid: int, by_id: int = 0) -> dict:
    """Khóa đối tác: đổi status + tạm dừng các lô đang hoạt động.

    - Lô 'submitted'/'approved'/'listed' -> 'suspended' (lưu trạng thái cũ vào suspend_prev
      để mở khóa khôi phục đúng). Lô chờ duyệt bị khóa sẽ không duyệt được nữa.
    - Acc trong kho của lô đang bán: AVAILABLE -> SUSPENDED (không bán được nữa).
    - Tiền đang giữ trong thời gian BH: giữ nguyên, tiếp tục đếm ngược.
    - Yêu cầu rút đang pending: giữ nguyên để chủ shop xử lý tay.
    Trả dict {batches, items} số lô/acc đã tạm dừng."""
    out = {"batches": 0, "items": 0}
    now = int(time.time())
    with _lock:
        c = get_conn()
        c.execute("UPDATE consignors SET status='locked' WHERE id=?", (cid,))
        batches = c.execute(
            "SELECT id, status FROM consignment_batches WHERE consignor_id=?"
            " AND status IN ('submitted','approved','listed')",
            (cid,)).fetchall()
        for b in batches:
            bid = b["id"]
            prev = b["status"]
            items = c.execute(
                "SELECT id, acc_stock_id FROM consignment_items "
                "WHERE batch_id=? AND status IN ('pending','listed')", (bid,)).fetchall()
            for it in items:
                if it["acc_stock_id"]:
                    c.execute("UPDATE acc_stock SET status='SUSPENDED' WHERE id=? AND status='AVAILABLE'",
                              (it["acc_stock_id"],))
                c.execute("UPDATE consignment_items SET status='suspended' WHERE id=?", (it["id"],))
                out["items"] += 1
            c.execute("UPDATE consignment_batches SET status='suspended', suspend_prev=? WHERE id=?",
                      (prev, bid))
            out["batches"] += 1
        c.execute("INSERT INTO admin_audit(tg_id, name, action, detail, created_at) VALUES(?,?,?,?,?)",
                  (by_id, "", "lock_consignor", f"cid={cid} batches={out['batches']} items={out['items']}", now))
        c.commit()
    return out


def consign_unlock_partner(cid: int, by_id: int = 0) -> dict:
    """Mở khóa đối tác: đổi status + khôi phục các lô đã tạm dừng về đúng trạng thái cũ.

    - Lô 'suspended' -> trạng thái lưu trong suspend_prev (listed/submitted/approved).
    - Item 'suspended': lô về 'listed' và có acc_stock_id -> item 'listed' + acc SUSPENDED->AVAILABLE;
      ngược lại -> item 'pending'.
    Trả dict {batches, items} số lô/acc đã khôi phục."""
    out = {"batches": 0, "items": 0}
    now = int(time.time())
    with _lock:
        c = get_conn()
        c.execute("UPDATE consignors SET status='active', approved_at=?, approved_by=? WHERE id=?",
                  (now, by_id, cid))
        batches = c.execute(
            "SELECT id, COALESCE(suspend_prev,'listed') AS prev FROM consignment_batches"
            " WHERE consignor_id=? AND status='suspended'",
            (cid,)).fetchall()
        for b in batches:
            bid = b["id"]
            prev = b["prev"] if b["prev"] in ("submitted", "approved", "listed") else "listed"
            items = c.execute(
                "SELECT id, acc_stock_id FROM consignment_items "
                "WHERE batch_id=? AND status='suspended'", (bid,)).fetchall()
            for it in items:
                if prev == "listed" and it["acc_stock_id"]:
                    c.execute("UPDATE acc_stock SET status='AVAILABLE' WHERE id=? AND status='SUSPENDED'",
                              (it["acc_stock_id"],))
                    c.execute("UPDATE consignment_items SET status='listed' WHERE id=?", (it["id"],))
                else:
                    # Lô chưa lên kệ trước khi khóa -> item về pending
                    c.execute("UPDATE consignment_items SET status='pending' WHERE id=?", (it["id"],))
                out["items"] += 1
            c.execute("UPDATE consignment_batches SET status=?, suspend_prev='' WHERE id=?",
                      (prev, bid))
            out["batches"] += 1
        c.execute("INSERT INTO admin_audit(tg_id, name, action, detail, created_at) VALUES(?,?,?,?,?)",
                  (by_id, "", "unlock_consignor", f"cid={cid} batches={out['batches']} items={out['items']}", now))
        c.commit()
    return out


def consignor_update(cid: int, **fields) -> bool:
    allowed = {"name", "phone", "note", "level", "max_items", "max_value",
               "risk_score", "payout_info", "status", "sheet_email", "sheet_id",
               "sheet_url", "sheet_access_granted", "notify_sale",
               "notify_bot_token", "notify_bot_username"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return False
    with _lock:
        c = get_conn()
        cols = ", ".join(f"{k}=?" for k in sets)
        cur = c.execute(f"UPDATE consignors SET {cols} WHERE id=?", (*sets.values(), cid))
        c.commit()
        return cur.rowcount > 0


def consignors_list(status: str = "", limit: int = 50) -> list:
    q = "SELECT * FROM consignors"
    p = []
    if status:
        q += " WHERE status=?"
        p.append(status)
    q += " ORDER BY id DESC LIMIT ?"
    p.append(limit)
    return [dict(r) for r in get_conn().execute(q, p).fetchall()]


def consign_batch_create(consignor_id: int, stall: str, category_id: int,
                         floor_price: int, warranty_days: int, note: str = "") -> tuple:
    """Tạo lô nháp, trả (batch_id, code)."""
    now = int(time.time())
    code = f"KG-{now % 1000000:06d}"
    with _lock:
        c = get_conn()
        for _ in range(5):
            try:
                cur = c.execute(
                    "INSERT INTO consignment_batches (code, consignor_id, stall, category_id,"
                    " status, floor_price, warranty_days, note, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?) RETURNING id",
                    (code, consignor_id, stall, category_id, "draft",
                     floor_price, warranty_days, note, now))
                c.commit()
                return cur.lastrowid, code
            except Exception:
                code = f"KG-{int(time.time() * 1000) % 1000000:06d}"
        raise RuntimeError("Không tạo được mã lô")


def consign_batch_get(bid: int) -> Optional[dict]:
    r = get_conn().execute("SELECT * FROM consignment_batches WHERE id=?", (bid,)).fetchone()
    return dict(r) if r else None


def consign_batches_list(consignor_id: int = 0, status: str = "", limit: int = 30) -> list:
    q = ("SELECT b.*, c.name AS consignor_name FROM consignment_batches b"
         " LEFT JOIN consignors c ON c.id=b.consignor_id")
    conds, p = [], []
    if consignor_id:
        conds.append("b.consignor_id=?")
        p.append(consignor_id)
    if status:
        conds.append("b.status=?")
        p.append(status)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY b.id DESC LIMIT ?"
    p.append(limit)
    return [dict(r) for r in get_conn().execute(q, p).fetchall()]


def consign_check_limits(cid: int, add_items: int = 0, add_value: int = 0) -> tuple:
    """Kiểm tra hạn mức đối tác. Trả (ok, msg).
    Tính tổng acc + tổng giá trị các lô đang hoạt động (draft/submitted/approved/listed).
    Hạn mức 0 = không giới hạn (chủ shop chưa cấu hình)."""
    try:
        c = get_conn()
        lim = c.execute("SELECT max_items, max_value FROM consignors WHERE id=?", (cid,)).fetchone()
        if not lim:
            return False, "Không tìm thấy đối tác"
        max_items = int(lim["max_items"] or 0)
        max_value = int(lim["max_value"] or 0)
        if not max_items and not max_value:
            return True, ""  # chưa đặt hạn mức = không giới hạn
        cur = c.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(b.floor_price),0) v"
            " FROM consignment_items i JOIN consignment_batches b ON b.id=i.batch_id"
            " WHERE b.consignor_id=? AND b.status IN ('draft','submitted','approved','listed')",
            (cid,)).fetchone()
        n = int(cur["n"] or 0) + add_items
        v = int(cur["v"] or 0) + add_value
        if max_items and n > max_items:
            return False, f"Vượt hạn mức: {n}/{max_items} acc"
        if max_value and v > max_value:
            return False, f"Vượt hạn mức: {v:,}/{max_value:,}đ"
        return True, ""
    except Exception as e:
        return False, str(e)[:100]


def consign_batch_submit(bid: int) -> tuple:
    """Nộp lô để duyệt. Trả (ok, msg) — chặn nếu vượt hạn mức đối tác."""
    with _lock:
        c = get_conn()
        b = c.execute("SELECT consignor_id FROM consignment_batches WHERE id=?", (bid,)).fetchone()
        if not b:
            return False, "Không tìm thấy lô"
        n = c.execute("SELECT COUNT(*) v FROM consignment_items WHERE batch_id=?", (bid,)).fetchone()["v"]
        if not n:
            return False, "Lô trống"
        # kiểm tra hạn mức: tổng acc + tổng giá trị (floor_price × số acc) các lô đang hoạt động
        ok, msg = consign_check_limits(int(b["consignor_id"]), 0, 0)
        if not ok:
            return False, msg
        cur = c.execute("UPDATE consignment_batches SET status='submitted', total_items=? WHERE id=? AND status='draft'",
                        (n, bid))
        c.commit()
        return (cur.rowcount > 0), ""


def consign_batch_decide(bid: int, approve: bool, sell_price: int, by_id: int, note: str = "") -> bool:
    now = int(time.time())
    st = "approved" if approve else "rejected"
    with _lock:
        c = get_conn()
        # Không duyệt lô của đối tác đang bị khóa (trừ khi lô đã bị suspend do khóa)
        if approve:
            row = c.execute(
                "SELECT cr.status FROM consignment_batches b"
                " JOIN consignors cr ON cr.id=b.consignor_id WHERE b.id=?", (bid,)).fetchone()
            if row and row["status"] == "locked":
                return False
        cur = c.execute(
            "UPDATE consignment_batches SET status=?, sell_price=?, decided_at=?, decided_by=?, decide_note=?"
            " WHERE id=? AND status IN ('submitted','draft')",
            (st, sell_price, now, by_id, note, bid))
        c.commit()
        return cur.rowcount > 0


def consign_item_add(batch_id: int, consignor_id: int, fields: dict) -> int:
    now = int(time.time())
    with _lock:
        c = get_conn()
        cur = c.execute(
            "INSERT INTO consignment_items (batch_id, consignor_id, uid, password, backup_mail,"
            " totp, cookie, token, note, status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,'pending',?) RETURNING id",
            (batch_id, consignor_id, fields.get("uid", ""), fields.get("password", ""),
             fields.get("backup_mail", ""), fields.get("totp", ""),
             fields.get("cookie", ""), fields.get("token", ""),
             fields.get("note", ""), now))
        c.commit()
        return cur.lastrowid


def consign_items_of_batch(bid: int) -> list:
    return [dict(r) for r in get_conn().execute(
        "SELECT * FROM consignment_items WHERE batch_id=? ORDER BY id", (bid,)).fetchall()]


def consign_item_set(bid: int, item_id: int, status: str, verdict: str = "") -> bool:
    with _lock:
        c = get_conn()
        cur = c.execute("UPDATE consignment_items SET status=?, verdict=? WHERE id=? AND batch_id=?",
                        (status, verdict, item_id, bid))
        c.commit()
        return cur.rowcount > 0


def consign_item_link_stock(item_id: int, stock_id: int) -> bool:
    with _lock:
        c = get_conn()
        cur = c.execute("UPDATE consignment_items SET acc_stock_id=?, status='listed' WHERE id=?",
                        (stock_id, item_id))
        c.commit()
        return cur.rowcount > 0


def consign_fee_get(category_id: int) -> dict:
    r = get_conn().execute("SELECT * FROM consignment_fees WHERE category_id=?", (category_id,)).fetchone()
    if r:
        return dict(r)
    return {"category_id": category_id, "fee_fixed": 0, "fee_pct": 0.0}


def consign_fee_set(category_id: int, fee_fixed: int, fee_pct: float, by_id: int = 0) -> bool:
    now = int(time.time())
    with _lock:
        c = get_conn()
        c.execute("INSERT INTO consignment_fees (category_id, fee_fixed, fee_pct, updated_at, updated_by)"
                  " VALUES (?,?,?,?,?)"
                  " ON CONFLICT(category_id) DO UPDATE SET fee_fixed=excluded.fee_fixed,"
                  " fee_pct=excluded.fee_pct, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                  (category_id, fee_fixed, fee_pct, now, by_id))
        c.commit()
        return True


def consign_ledger_add(consignor_id: int, kind: str, amount: int,
                      ref_type: str = "", ref_id: int = 0, note: str = "") -> int:
    now = int(time.time())
    with _lock:
        c = get_conn()
        cur = c.execute(
            "INSERT INTO consignment_ledger (consignor_id, kind, amount, ref_type, ref_id, note, created_at)"
            " VALUES (?,?,?,?,?,?,?) RETURNING id",
            (consignor_id, kind, amount, ref_type, ref_id, note, now))
        c.commit()
        return cur.lastrowid


def consign_wallets(consignor_id: int) -> dict:
    """4 ví: pending (chờ BH), avail (khả dụng), withdrawing (đang rút), held (bị giữ)."""
    rows = get_conn().execute(
        "SELECT kind, COALESCE(SUM(amount),0) v FROM consignment_ledger WHERE consignor_id=? GROUP BY kind",
        (consignor_id,)).fetchall()
    s = {r["kind"]: r["v"] for r in rows}
    g = lambda k: s.get(k, 0)
    pending = g("pending_in") - g("pending_out")
    avail = g("avail_in") - g("avail_out")
    withdrawing = g("withdraw_in") - g("withdraw_out")
    held = g("hold_in") - g("hold_out")
    return {"pending": pending, "avail": avail, "withdrawing": withdrawing, "held": held}


def consign_ledger_list(consignor_id: int, limit: int = 30) -> list:
    return [dict(r) for r in get_conn().execute(
        "SELECT * FROM consignment_ledger WHERE consignor_id=? ORDER BY id DESC LIMIT ?",
        (consignor_id, limit)).fetchall()]


def consign_order_create(item_id: int, batch_id: int, consignor_id: int, buyer_tg_id: int,
                         order_ref: str, sell_price: int, floor_price: int,
                         fee_fixed: int, fee_pct: float, warranty_days: int) -> tuple:
    """Tạo đơn + ghi sổ chờ. Trả (order_id, net_amount)."""
    now = int(time.time())
    fee_amount = fee_fixed + int(sell_price * fee_pct / 100)
    net = sell_price - fee_amount
    if net < 0:
        net = 0
    w_until = now + warranty_days * 86400 if warranty_days > 0 else now
    with _lock:
        c = get_conn()
        cur = c.execute(
            "INSERT INTO consignment_orders (item_id, batch_id, consignor_id, buyer_tg_id, order_ref,"
            " sell_price, floor_price, fee_fixed, fee_pct, fee_amount, net_amount,"
            " warranty_days, warranty_until, status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'sold',?) RETURNING id",
            (item_id, batch_id, consignor_id, buyer_tg_id, order_ref, sell_price, floor_price,
             fee_fixed, fee_pct, fee_amount, net, warranty_days, w_until, now))
        oid = cur.lastrowid
        c.execute(
            "INSERT INTO consignment_ledger (consignor_id, kind, amount, ref_type, ref_id, note, created_at)"
            " VALUES (?,?,?,?,?,?,?) RETURNING id",
            (consignor_id, "pending_in", net, "order", oid, f"Bán {order_ref}", now))
        c.execute("UPDATE consignment_items SET status='sold', sold_at=?, sold_price=?,"
                  " fee_fixed=?, fee_pct=?, net_amount=?, warranty_until=? WHERE id=?",
                  (now, sell_price, fee_fixed, fee_pct, net, w_until, item_id))
        c.commit()
        return oid, net


def consign_get_sale_info(order_ref: str) -> Optional[dict]:
    """Lấy thông tin đơn ký gửi đã bán để báo đối tác. Trả None nếu không phải hàng ký gửi."""
    c = get_conn()
    r = c.execute(
        "SELECT o.*, c.tg_id as consignor_tg_id, c.name as consignor_name,"
        " COALESCE(c.notify_sale, 1) as notify_sale,"
        " COALESCE(c.notify_bot_token, '') as notify_bot_token,"
        " COALESCE(c.notify_bot_username, '') as notify_bot_username"
        " FROM consignment_orders o"
        " JOIN consignors c ON c.id = o.consignor_id"
        " WHERE o.order_ref = ? LIMIT 1", (order_ref,)).fetchone()
    return dict(r) if r else None


def consign_mark_notified(order_ref: str) -> bool:
    """Đánh dấu đơn ký gửi đã báo cho đối tác (idempotency)."""
    c = get_conn()
    cur = c.execute(
        "UPDATE consignment_orders SET notified=1 WHERE order_ref=?",
        (order_ref,))
    c.commit()
    return cur.rowcount > 0


def consign_release_due(limit: int = 200) -> list:
    """Acc hết BH mà tiền còn ở chờ -> trả (order_id, consignor_id, net).

    Bỏ qua đơn đang có tranh chấp mở (tiền phải giữ đến khi xử lý xong).
    """
    now = int(time.time())
    rows = get_conn().execute(
        "SELECT o.id, o.consignor_id, o.net_amount, o.order_ref FROM consignment_orders o"
        " WHERE o.status='sold' AND o.warranty_until<=? AND o.net_amount>0"
        " AND NOT EXISTS (SELECT 1 FROM consignment_disputes d"
        "                 WHERE d.order_id=o.id AND d.status='open')"
        " LIMIT ?",
        (now, limit)).fetchall()
    out = []
    with _lock:
        c = get_conn()
        for r in rows:
            # idempotency: chưa release order này
            ex = c.execute("SELECT 1 FROM consignment_ledger WHERE kind='pending_out'"
                           " AND ref_type='order' AND ref_id=? LIMIT 1", (r["id"],)).fetchone()
            if ex:
                c.execute("UPDATE consignment_orders SET status='released' WHERE id=?", (r["id"],))
                continue
            c.execute("INSERT INTO consignment_ledger (consignor_id, kind, amount, ref_type, ref_id, note, created_at)"
                      " VALUES (?,?,?,?,?,?,?)",
                      (r["consignor_id"], "pending_out", r["net_amount"], "order", r["id"],
                       f"Hết BH {r['order_ref']}", now))
            c.execute("INSERT INTO consignment_ledger (consignor_id, kind, amount, ref_type, ref_id, note, created_at)"
                      " VALUES (?,?,?,?,?,?,?)",
                      (r["consignor_id"], "avail_in", r["net_amount"], "order", r["id"],
                       f"Hết BH {r['order_ref']}", now))
            c.execute("UPDATE consignment_orders SET status='released' WHERE id=?", (r["id"],))
            out.append(dict(r))
        c.commit()
    return out


def consign_payout_create(consignor_id: int, amount: int, channel: str, account_info: str) -> tuple:
    """Tạo yêu cầu rút. Trả (ok, msg, payout_id).
    FAIL-CLOSED: nếu không kiểm tra được trạng thái khóa thì từ chối, không cho rút."""
    # Chặn đối tác bị khóa (fail-closed: lỗi DB -> từ chối rút)
    try:
        st = get_conn().execute("SELECT status FROM consignors WHERE id=?",
                                (consignor_id,)).fetchone()
    except Exception as e:
        log.warning("consign_payout_create: không đọc được status consignor %s: %s",
                    consignor_id, e)
        return False, "Lỗi hệ thống, vui lòng thử lại sau", 0
    if st and st["status"] == "locked":
        return False, "Tài khoản ký gửi của bạn đang bị khóa, liên hệ chủ shop", 0
    w = consign_wallets(consignor_id)
    _mw = get_setting("consign_min_withdraw", "")
    _fw = get_setting("consign_withdraw_fee", "")
    if not str(_mw).strip().isdigit():
        return False, "Shop chưa cấu hình mức rút tối thiểu, bạn liên hệ admin nhé", 0
    min_w = int(_mw)
    fee = int(_fw) if str(_fw).strip().isdigit() else 0
    if amount < min_w:
        return False, f"Tối thiểu {min_w:,}đ", 0
    if amount > w["avail"]:
        return False, "Số dư khả dụng không đủ", 0
    # chặn nếu còn tranh chấp mở
    n = get_conn().execute("SELECT COUNT(*) v FROM consignment_disputes WHERE consignor_id=? AND status='open'",
                           (consignor_id,)).fetchone()["v"]
    if n:
        return False, "Còn tranh chấp đang mở, xử lý xong mới rút được", 0
    net = amount - fee
    if net < 0:
        net = 0
    now = int(time.time())
    with _lock:
        c = get_conn()
        cur = c.execute(
            "INSERT INTO consignment_payouts (consignor_id, amount, fee, net, channel, account_info, status, created_at)"
            " VALUES (?,?,?,?,?,?,'pending',?) RETURNING id",
            (consignor_id, amount, fee, net, channel, account_info, now))
        row = cur.fetchone()
        pid = row["id"] if row else cur.lastrowid
        c.execute("INSERT INTO consignment_ledger (consignor_id, kind, amount, ref_type, ref_id, note, created_at)"
                  " VALUES (?,?,?,?,?,?,?)",
                  (consignor_id, "avail_out", amount, "payout", pid, "Yêu cầu rút", now))
        c.execute("INSERT INTO consignment_ledger (consignor_id, kind, amount, ref_type, ref_id, note, created_at)"
                  " VALUES (?,?,?,?,?,?,?)",
                  (consignor_id, "withdraw_in", amount, "payout", pid, "Yêu cầu rút", now))
        c.commit()
        return True, "Đã gửi yêu cầu rút", pid


def consign_payout_decide(pid: int, approve: bool, by_id: int, paid_ref: str = "") -> bool:
    now = int(time.time())
    with _lock:
        c = get_conn()
        p = c.execute("SELECT * FROM consignment_payouts WHERE id=?", (pid,)).fetchone()
        if not p or p["status"] != "pending":
            return False
        cid = p["consignor_id"]
        if approve:
            c.execute("UPDATE consignment_payouts SET status='paid', decided_at=?, decided_by=?, paid_ref=? WHERE id=?",
                      (now, by_id, paid_ref, pid))
            c.execute("INSERT INTO consignment_ledger (consignor_id, kind, amount, ref_type, ref_id, note, created_at)"
                      " VALUES (?,?,?,?,?,?,?)",
                      (cid, "withdraw_out", p["amount"], "payout", pid, f"Đã trả {paid_ref}", now))
        else:
            c.execute("UPDATE consignment_payouts SET status='rejected', decided_at=?, decided_by=?, reject_reason=? WHERE id=?",
                      (now, by_id, paid_ref, pid))
            # trả tiền về khả dụng
            c.execute("INSERT INTO consignment_ledger (consignor_id, kind, amount, ref_type, ref_id, note, created_at)"
                      " VALUES (?,?,?,?,?,?,?)",
                      (cid, "withdraw_out", p["amount"], "payout", pid, "Từ chối rút", now))
            c.execute("INSERT INTO consignment_ledger (consignor_id, kind, amount, ref_type, ref_id, note, created_at)"
                      " VALUES (?,?,?,?,?,?,?)",
                      (cid, "avail_in", p["amount"], "payout", pid, "Từ chối rút", now))
        c.commit()
        return True


def consign_payouts_list(status: str = "", limit: int = 30) -> list:
    q = ("SELECT p.*, c.name AS consignor_name FROM consignment_payouts p"
         " LEFT JOIN consignors c ON c.id=p.consignor_id")
    p = []
    if status:
        q += " WHERE p.status=?"
        p.append(status)
    q += " ORDER BY p.id DESC LIMIT ?"
    p.append(limit)
    return [dict(r) for r in get_conn().execute(q, p).fetchall()]


def consign_dispute_create(order_id: int, reason: str, photo_file_id: str) -> int:
    o = get_conn().execute("SELECT * FROM consignment_orders WHERE id=?", (order_id,)).fetchone()
    if not o:
        return 0
    now = int(time.time())
    deadline = now + 48 * 3600  # 48h đối tác phải phản hồi
    with _lock:
        c = get_conn()
        cur = c.execute(
            "INSERT INTO consignment_disputes (order_id, item_id, consignor_id, buyer_tg_id, reason,"
            " photo_file_id, status, created_at, deadline_at) VALUES (?,?,?,?,?,?,'open',?,?) RETURNING id",
            (order_id, o["item_id"], o["consignor_id"], o["buyer_tg_id"], reason, photo_file_id, now, deadline))
        # giữ tiền liên quan (giữ đủ sell_price vì đối tác chịu 100% nếu thua)
        c.execute("INSERT INTO consignment_ledger (consignor_id, kind, amount, ref_type, ref_id, note, created_at)"
                  " VALUES (?,?,?,?,?,?,?)",
                  (o["consignor_id"], "hold_in", o["sell_price"], "dispute", cur.lastrowid,
                   f"Giữ tiền tranh chấp #{cur.lastrowid}", now))
        c.commit()
        return cur.lastrowid


def consign_disputes_list(status: str = "open", limit: int = 30) -> list:
    q = ("SELECT d.*, c.name AS consignor_name FROM consignment_disputes d"
         " LEFT JOIN consignors c ON c.id=d.consignor_id")
    p = []
    if status:
        q += " WHERE d.status=?"
        p.append(status)
    q += " ORDER BY d.id DESC LIMIT ?"
    p.append(limit)
    return [dict(r) for r in get_conn().execute(q, p).fetchall()]


def consign_dispute_respond(did: int, response_text: str, photo_file_id: str = "") -> bool:
    """Đối tác phản hồi tranh chấp (lý do + ảnh, hoặc /boqua = đồng ý đền bù)."""
    try:
        with _lock:
            c = get_conn()
            c.execute(
                "UPDATE consignment_disputes SET partner_responded=1, partner_response=?,"
                " partner_photo_file_id=? WHERE id=?",
                (response_text, photo_file_id, did))
            c.commit()
        return True
    except Exception as e:
        log.warning("consign_dispute_respond %s: %s", did, e)
        return False


def consign_disputes_remind_due() -> list:
    """Tranh chấp mở, đối tác chưa phản hồi, deadline còn < 12h, chưa nhắc."""
    now = int(time.time())
    try:
        rows = get_conn().execute(
            "SELECT * FROM consignment_disputes WHERE status='open'"
            " AND COALESCE(partner_responded,0)=0 AND COALESCE(reminded_at,0)=0"
            " AND deadline_at > ? AND deadline_at < ?",
            (now, now + 12 * 3600)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("consign_disputes_remind_due: %s", e)
        return []


def consign_dispute_decide(did: int, decision: str, refund_amount: int, by_id: int) -> bool:
    """decision: refund_buyer / replace / reject.
    refund_buyer: đối tác chịu 100% -> khách được hoàn ĐỦ sell_price,
    trừ đủ sell_price khỏi ví đối tác (kể cả phần phí shop).
    Chạy trong 1 transaction nguyên tử: lỗi giữa chừng -> rollback toàn bộ,
    tránh trạng thái tiền một phần."""
    import psycopg2.extras as _px
    now = int(time.time())
    with _lock:
        c = get_conn()
        d = c.execute("SELECT * FROM consignment_disputes WHERE id=?", (did,)).fetchone()
        if not d or d["status"] != "open":
            return False
        cid = d["consignor_id"]
        # lấy sell_price + trạng thái đơn để biết tiền đang nằm ở ví nào
        o = c.execute("SELECT sell_price, net_amount, status FROM consignment_orders WHERE id=?",
                      (d["order_id"],)).fetchone()
        sell = o["sell_price"] if o and o["sell_price"] else refund_amount
        deduct = min(refund_amount, sell) if refund_amount > 0 else sell
        o2 = (c.execute("SELECT buyer_tg_id, sell_price FROM consignment_orders WHERE id=?",
                        (d["order_id"],)).fetchone()
              if decision == "refund_buyer" else None)
        raw = c.conn
        prev_ac = raw.autocommit
        raw.autocommit = False
        try:
            cur = raw.cursor(cursor_factory=_px.RealDictCursor)

            def _x(sql, params=()):
                cur.execute(sql.replace("?", "%s"), params)
                return cur.rowcount

            # mở giữ theo số tiền tranh chấp thực (sell_price)
            _x("UPDATE consignment_ledger SET amount=? WHERE kind='hold_in' AND ref_type='dispute' AND ref_id=?",
               (deduct, did))
            _x("INSERT INTO consignment_ledger (consignor_id, kind, amount, ref_type, ref_id, note, created_at)"
               " VALUES (?,?,?,?,?,?,?)",
               (cid, "hold_out", deduct, "dispute", did, f"Mở giữ tranh chấp #{did}", now))
            if decision in ("refund_buyer",) and deduct > 0:
                # tiền còn ở ví chờ (chưa hết BH) -> trừ pending;
                # đã giải ngân rồi -> trừ khả dụng
                kind_out = "avail_out" if (o and o["status"] == "released") else "pending_out"
                _x("INSERT INTO consignment_ledger (consignor_id, kind, amount, ref_type, ref_id, note, created_at)"
                   " VALUES (?,?,?,?,?,?,?)",
                   (cid, kind_out, deduct, "dispute", did, f"Khấu trừ BH #{did} (đối tác chịu 100%)", now))
            rc = _x("UPDATE consignment_disputes SET status='closed', decision=?, decided_by=?,"
                    " decided_at=?, refund_amount=? WHERE id=? AND status='open'",
                    (decision, by_id, now, deduct, did))
            if rc == 0:
                raw.rollback()  # đã bị đóng bởi tiến trình khác giữa chừng
                return False
            if decision == "refund_buyer":
                _x("UPDATE consignment_orders SET status='refunded' WHERE id=?", (d["order_id"],))
                # Hoàn tiền THẬT vào ví shop của khách
                if o2 and o2["buyer_tg_id"]:
                    amt = refund_amount if refund_amount > 0 else (o2["sell_price"] or 0)
                    if amt > 0:
                        # đảm bảo buyer có row (tránh UPDATE 0 dòng -> mất tiền)
                        _x("INSERT INTO tg_users(tg_id, created_at) VALUES(?,?) "
                           "ON CONFLICT(tg_id) DO NOTHING",
                           (o2["buyer_tg_id"], now))
                        _x("UPDATE tg_users SET shop_balance = shop_balance + ? WHERE tg_id=?",
                           (amt, o2["buyer_tg_id"]))
                        _x("INSERT INTO txns(ts, tg_id, amount, reason) VALUES(?,?,?,?)",
                           (now, o2["buyer_tg_id"], amt, f"Hoàn BH ký gửi #{did}"))
            raw.commit()
            return True
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.autocommit = prev_ac


def consign_promo_create(title: str, detail: str, consignor_id: int = 0) -> int:
    now = int(time.time())
    with _lock:
        c = get_conn()
        cur = c.execute(
            "INSERT INTO consignment_promos (title, detail, consignor_id, status, created_at)"
            " VALUES (?,?,?, 'pending', ?) RETURNING id", (title, detail, consignor_id, now))
        c.commit()
        return cur.lastrowid


def consign_disputes_expired() -> list:
    """Lấy tranh chấp quá 48h chưa xử lý (tự động khách đúng) hoặc quá 7 ngày (bắt buộc đóng).
    Vụ đối tác ĐÃ phản hồi trong 48h thì loại khỏi auto 48h — chờ admin xử lý tay."""
    now = int(time.time())
    rows = get_conn().execute(
        "SELECT * FROM consignment_disputes WHERE status='open' AND ("
        " (deadline_at > 0 AND deadline_at < ? AND COALESCE(partner_responded,0)=0) OR"
        " (created_at < ?))",
        (now, now - 7 * 86400)).fetchall()
    return [dict(r) for r in rows]


def consign_notif_log(consignor_id: int, kind: str, ref_id: int, via_bot: str, ok: bool, error: str = ""):
    """Ghi lịch sử tin báo cho đối tác."""
    try:
        get_conn().execute(
            "INSERT INTO consignment_notif_log (consignor_id, kind, ref_id, via_bot, ok, error, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (consignor_id, kind, ref_id, via_bot, 1 if ok else 0, error[:200], int(time.time())))
        get_conn().commit()
    except Exception as e:
        log.warning("consign_notif_log lỗi: %s", e)


def consign_notif_history(consignor_id: int, limit: int = 20) -> list:
    return [dict(r) for r in get_conn().execute(
        "SELECT * FROM consignment_notif_log WHERE consignor_id=? ORDER BY id DESC LIMIT ?",
        (consignor_id, limit)).fetchall()]


# ─── NOTIFICATION OUTBOX (chống gửi trùng/mất tin) ──────────────────────────
def outbox_enqueue(dedupe_key: str, kind: str, target_tg_id: int,
                   message: str, parse_mode: str = "HTML",
                   ref_consignor_id: int = 0) -> bool:
    """Cho tin vào outbox. Trả True nếu enqueue mới, False nếu đã có (dedupe)."""
    try:
        cur = get_conn().execute(
            "INSERT INTO notification_outbox (dedupe_key, kind, target_tg_id, message,"
            " parse_mode, status, attempts, created_at, ref_consignor_id)"
            " VALUES (?,?,?,?,?,'pending',0,?,?)"
            " ON CONFLICT(dedupe_key) DO NOTHING RETURNING id",
            (dedupe_key, kind, target_tg_id, message, parse_mode, int(time.time()),
             ref_consignor_id))
        get_conn().commit()
        # Wrapper đã fetch RETURNING vào lastrowid; None = bị dedupe (đã có)
        return cur.lastrowid is not None
    except Exception as e:
        log.warning("outbox_enqueue lỗi: %s", e)
        return False


def outbox_exists(dedupe_key: str) -> bool:
    """Kiểm tra tin đã nằm trong outbox chưa (phân biệt dedupe với lỗi DB)."""
    try:
        r = get_conn().execute(
            "SELECT 1 FROM notification_outbox WHERE dedupe_key=? LIMIT 1",
            (dedupe_key,)).fetchone()
        return r is not None
    except Exception:
        return False


def outbox_claim(limit: int = 20, max_attempts: int = 5,
                 reclaim_after: int = 600) -> list:
    """Claim tin pending/failed (chưa quá max_attempts) để gửi. Đánh dấu 'sending'
    nguyên tử để 2 worker không gửi trùng. Tin kẹt ở 'sending' quá reclaim_after
    giây (crash giữa claim và send) được thu hồi lại để gửi tiếp."""
    try:
        c = get_conn()
        now = int(time.time())
        rows = [dict(r) for r in c.execute(
            "UPDATE notification_outbox SET status='sending', attempts=attempts+1,"
            " claimed_at=?"
            " WHERE id IN (SELECT id FROM notification_outbox"
            "  WHERE (status IN ('pending','failed')"
            "     OR (status='sending' AND claimed_at < ?))"
            "   AND attempts < ?"
            "  ORDER BY created_at LIMIT ?)"
            " RETURNING *",
            (now, now - reclaim_after, max_attempts, limit)).fetchall()]
        c.commit()
        return rows
    except Exception as e:
        log.warning("outbox_claim lỗi: %s", e)
        return []


def outbox_mark_sent(outbox_id: int):
    try:
        get_conn().execute(
            "UPDATE notification_outbox SET status='sent', sent_at=? WHERE id=?",
            (int(time.time()), outbox_id))
        get_conn().commit()
    except Exception as e:
        log.warning("outbox_mark_sent lỗi: %s", e)


def outbox_mark_failed(outbox_id: int, error: str):
    try:
        get_conn().execute(
            "UPDATE notification_outbox SET status='failed', last_error=? WHERE id=?",
            (error[:200], outbox_id))
        get_conn().commit()
    except Exception as e:
        log.warning("outbox_mark_failed lỗi: %s", e)


def outbox_pending_count() -> int:
    try:
        r = get_conn().execute(
            "SELECT COUNT(*) c FROM notification_outbox"
            " WHERE status IN ('pending','failed') AND attempts < 5").fetchone()
        return r["c"] if r else 0
    except Exception:
        return 0


def consign_promos_pending(consignor_id: int) -> list:
    return [dict(r) for r in get_conn().execute(
        "SELECT * FROM consignment_promos WHERE status='pending'"
        " AND (consignor_id=0 OR consignor_id=?) ORDER BY id DESC",
        (consignor_id,)).fetchall()]


def consign_promo_decide(pid: int, accept: bool) -> bool:
    with _lock:
        c = get_conn()
        cur = c.execute("UPDATE consignment_promos SET status=?, decided_at=? WHERE id=? AND status='pending'",
                        ("accepted" if accept else "rejected", int(time.time()), pid))
        c.commit()
        return cur.rowcount > 0


def consign_stats() -> dict:
    c = get_conn()
    n_c = c.execute("SELECT COUNT(*) v FROM consignors WHERE status='active'").fetchone()["v"]
    n_p = c.execute("SELECT COUNT(*) v FROM consignors WHERE status='pending'").fetchone()["v"]
    n_b = c.execute("SELECT COUNT(*) v FROM consignment_batches WHERE status='submitted'").fetchone()["v"]
    n_l = c.execute("SELECT COUNT(*) v FROM consignment_items WHERE status='listed'").fetchone()["v"]
    gmv = c.execute("SELECT COALESCE(SUM(sell_price),0) v FROM consignment_orders").fetchone()["v"]
    n_w = c.execute("SELECT COUNT(*) v FROM consignment_payouts WHERE status='pending'").fetchone()["v"]
    n_d = c.execute("SELECT COUNT(*) v FROM consignment_disputes WHERE status='open'").fetchone()["v"]
    return {"active_consignors": n_c, "pending_consignors": n_p, "pending_batches": n_b,
            "listed_items": n_l, "gmv": gmv, "pending_payouts": n_w, "open_disputes": n_d}


def consignor_get_by_id(cid: int) -> Optional[dict]:
    row = get_conn().execute("SELECT * FROM consignors WHERE id=?", (cid,)).fetchone()
    return dict(row) if row else None


def consign_warehouse(cid: int) -> list:
    """Toàn bộ acc của đối tác cho Sheet kho riêng: mỗi dòng 1 acc + trạng thái/tiền."""
    c = get_conn()
    rows = c.execute(
        "SELECT i.*, b.code AS batch_code, b.sell_price AS batch_price, b.status AS batch_status,"
        " o.sell_price AS o_price, o.fee_amount AS o_fee, o.net_amount AS o_net,"
        " o.warranty_until AS o_wuntil, o.created_at AS o_at,"
        " (SELECT COUNT(*) FROM consignment_disputes d WHERE d.order_id=o.id AND d.status='open') AS o_dispute"
        " FROM consignment_items i"
        " JOIN consignment_batches b ON b.id=i.batch_id"
        " LEFT JOIN consignment_orders o ON o.item_id=i.id"
        " WHERE i.consignor_id=? ORDER BY b.id DESC, i.id", (cid,)).fetchall()
    return [dict(r) for r in rows]


def consign_orders_unnotified(limit: int = 50) -> list:
    """Đơn ký gửi đã bán nhưng chưa báo cho đối tác (poller gửi rồi đánh dấu)."""
    c = get_conn()
    try:
        rows = c.execute(
            "SELECT o.*, i.uid AS uid, cr.tg_id AS consignor_tg"
            " FROM consignment_orders o"
            " JOIN consignment_items i ON i.id=o.item_id"
            " JOIN consignors cr ON cr.id=o.consignor_id"
            " WHERE o.notified=0 ORDER BY o.id LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def consign_order_mark_notified(order_id: int) -> None:
    with _lock:
        c = get_conn()
        try:
            c.execute("UPDATE consignment_orders SET notified=1 WHERE id=?", (order_id,))
            c.commit()
        except Exception:
            pass


def consign_finance_stats() -> dict:
    """Báo cáo tài chính ký gửi: phí thu, tiền đang giữ, đã giải ngân, đã rút."""
    c = get_conn()
    fee = c.execute("SELECT COALESCE(SUM(fee_amount),0) v FROM consignment_orders").fetchone()["v"]
    gmv = c.execute("SELECT COALESCE(SUM(sell_price),0) v FROM consignment_orders").fetchone()["v"]
    # held = tiền đang chờ BH (net = vào - ra)
    held_in = c.execute("SELECT COALESCE(SUM(amount),0) v FROM consignment_ledger"
                        " WHERE kind='pending_in'").fetchone()["v"]
    held_out = c.execute("SELECT COALESCE(SUM(amount),0) v FROM consignment_ledger"
                         " WHERE kind='pending_out'").fetchone()["v"]
    held = held_in - held_out
    # released = đã hết BH chuyển sang khả dụng (chỉ tính từ đơn bán)
    released = c.execute("SELECT COALESCE(SUM(amount),0) v FROM consignment_ledger"
                         " WHERE kind='avail_in' AND ref_type='order'").fetchone()["v"]
    paid = c.execute("SELECT COALESCE(SUM(net),0) v FROM consignment_payouts WHERE status='paid'").fetchone()["v"]
    pending_pay = c.execute("SELECT COALESCE(SUM(amount),0) v FROM consignment_payouts WHERE status='pending'").fetchone()["v"]
    dispute_hold = c.execute(
        "SELECT COALESCE(SUM(o.net_amount),0) v FROM consignment_disputes d"
        " JOIN consignment_orders o ON o.id=d.order_id WHERE d.status='open'").fetchone()["v"]
    return {"gmv": gmv, "fee_earned": fee, "held": held, "released": released,
            "paid_out": paid, "pending_payout": pending_pay,
            "dispute_hold": dispute_hold}


def consign_disputes_of_consignor(cid: int, limit: int = 20) -> list:
    c = get_conn()
    rows = c.execute(
        "SELECT d.*, o.sell_price, o.net_amount, i.uid"
        " FROM consignment_disputes d"
        " JOIN consignment_orders o ON o.id=d.order_id"
        " JOIN consignment_items i ON i.id=o.item_id"
        " WHERE o.consignor_id=? ORDER BY d.id DESC LIMIT ?", (cid, limit)).fetchall()
    return [dict(r) for r in rows]


def consign_batch_update_price(bid: int, sell_price: int, by_id: int = 0) -> bool:
    """Admin sửa giá bán sau duyệt: update lô + price_override các acc chưa bán."""
    with _lock:
        c = get_conn()
        b = c.execute("SELECT * FROM consignment_batches WHERE id=?", (bid,)).fetchone()
        if not b or b["status"] not in ("approved", "listed"):
            return False
        now = int(time.time())
        c.execute("UPDATE consignment_batches SET sell_price=? WHERE id=?", (sell_price, bid))
        # chỉ acc chưa bán mới đổi giá
        items = c.execute(
            "SELECT acc_stock_id FROM consignment_items WHERE batch_id=? AND status='listed'",
            (bid,)).fetchall()
        for it in items:
            if it["acc_stock_id"]:
                c.execute("UPDATE acc_stock SET price_override=? WHERE id=? AND status='AVAILABLE'",
                          (sell_price, it["acc_stock_id"]))
        c.execute(
            "INSERT INTO admin_audit(tg_id, name, action, detail, created_at) VALUES(?,?,?,?,?)",
            (by_id, "", "consign_reprice", f"bid={bid} price={sell_price}", now))
        c.commit()
        return True


def consign_batch_request_return(bid: int, cid: int) -> bool:
    """Đối tác yêu cầu trả lại acc chưa bán của lô đã duyệt."""
    with _lock:
        c = get_conn()
        b = c.execute("SELECT * FROM consignment_batches WHERE id=? AND consignor_id=?",
                      (bid, cid)).fetchone()
        if not b or b["status"] not in ("approved", "listed"):
            return False
        n = c.execute("SELECT COUNT(*) v FROM consignment_items WHERE batch_id=? AND status='listed'",
                      (bid,)).fetchone()["v"]
        if not n:
            return False
        c.execute("UPDATE consignment_batches SET status='return_requested' WHERE id=?", (bid,))
        c.commit()
        return True


def consign_batch_do_return(bid: int, approve: bool, by_id: int = 0) -> bool:
    """Admin duyệt/từ chối yêu cầu trả hàng: acc chưa bán -> RETURNED (rời kệ)."""
    with _lock:
        c = get_conn()
        b = c.execute("SELECT * FROM consignment_batches WHERE id=?", (bid,)).fetchone()
        if not b or b["status"] != "return_requested":
            return False
        now = int(time.time())
        if approve:
            items = c.execute(
                "SELECT id, acc_stock_id FROM consignment_items WHERE batch_id=? AND status='listed'",
                (bid,)).fetchall()
            for it in items:
                c.execute("UPDATE consignment_items SET status='returned' WHERE id=?", (it["id"],))
                if it["acc_stock_id"]:
                    c.execute("UPDATE acc_stock SET status='RETURNED' WHERE id=? AND status='AVAILABLE'",
                              (it["acc_stock_id"],))
            left = c.execute("SELECT COUNT(*) v FROM consignment_items WHERE batch_id=? AND status='listed'",
                             (bid,)).fetchone()["v"]
            c.execute("UPDATE consignment_batches SET status=? WHERE id=?",
                      ("approved" if left else "closed", bid))
        else:
            c.execute("UPDATE consignment_batches SET status='approved' WHERE id=?", (bid,))
        c.execute(
            "INSERT INTO admin_audit(tg_id, name, action, detail, created_at) VALUES(?,?,?,?,?)",
            (by_id, "", "consign_return", f"bid={bid} approve={int(approve)}", now))
        c.commit()
        return True


# ---------------------------------------------------------------- ViOTP: shop thuê số
VIOTP_STATUS_LABEL = {
    "waiting": "⏳ Đang chờ OTP",
    "expiring": "⏳ Đang xử lý hết hạn",
    "done": "✅ Đã nhận OTP",
    "expired": "⌛ Hết hạn",
    "refunded": "💸 Đã hoàn tiền",
    "cancelled": "❌ Đã hủy",
    "failed": "⚠️ Lỗi thuê số",
}


def viotp_sell_price(cost: int) -> int:
    """Giá bán = giá vốn + markup% (setting viotp_markup_pct, mặc định 50), làm tròn 100đ."""
    try:
        pct = int(get_setting("viotp_markup_pct", "50") or 50)
    except ValueError:
        pct = 50
    sell = int(cost) * (100 + max(0, pct)) / 100
    return int(round(sell / 100) * 100)


def viotp_rental_create(tg_id: int, request_id: str, phone: str, service_id: int,
                       service_name: str, country: str, cost: int, sell: int) -> int:
    now = int(time.time())
    with _lock:
        c = get_conn()
        cur = c.execute(
            "INSERT INTO viotp_rentals(tg_id, request_id, phone_number, service_id,"
            " service_name, country, cost_price, sell_price, status, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?, 'waiting',?,?)",
            (tg_id, request_id, phone, service_id, service_name, country, cost, sell, now, now),
        )
        rid = cur.lastrowid
        if hasattr(c, "cursor"):
            pass
        # Postgres: lastrowid không có -> lấy id vừa insert
        try:
            rid = int(rid) if rid else None
        except (TypeError, ValueError):
            rid = None
        if not rid:
            r = c.execute(
                "SELECT id FROM viotp_rentals WHERE tg_id=? AND request_id=? ORDER BY id DESC LIMIT 1",
                (tg_id, request_id)).fetchone()
            rid = int(r["id"]) if r else 0
        c.commit()
        return rid or 0


def viotp_rental_get(rid: int) -> dict:
    c = get_conn()
    r = c.execute("SELECT * FROM viotp_rentals WHERE id=?", (rid,)).fetchone()
    return dict(r) if r else {}


def viotp_rental_set_status(rid: int, status: str, otp: str = "") -> None:
    now = int(time.time())
    with _lock:
        c = get_conn()
        if otp:
            c.execute("UPDATE viotp_rentals SET status=?, otp_code=?, updated_at=? WHERE id=?",
                      (status, otp, now, rid))
        else:
            c.execute("UPDATE viotp_rentals SET status=?, updated_at=? WHERE id=?",
                      (status, now, rid))
        c.commit()


def viotp_rental_list(tg_id: int, limit: int = 10) -> list:
    c = get_conn()
    rows = c.execute(
        "SELECT * FROM viotp_rentals WHERE tg_id=? ORDER BY id DESC LIMIT ?",
        (tg_id, limit)).fetchall()
    return [dict(r) for r in rows]


def viotp_rental_waiting(max_age_min: int = 15) -> list:
    """Các đơn đang chờ OTP còn trong thời gian thuê (cho poller nền)."""
    cutoff = int(time.time()) - max_age_min * 60
    c = get_conn()
    rows = c.execute(
        "SELECT * FROM viotp_rentals WHERE status='waiting' AND created_at>=? ORDER BY id",
        (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def viotp_rental_claim_expire(rid: int) -> bool:
    """Claim nguyên tử waiting -> expiring để xử lý hết hạn/hoàn tiền.

    Trả True nếu claim được (tiến trình này thắng), False nếu đơn đã được
    tiến trình khác xử lý. Chống hoàn tiền trùng khi poller và user bấm
    tay cùng lúc.
    """
    now = int(time.time())
    with _lock:
        c = get_conn()
        cur = c.execute(
            "UPDATE viotp_rentals SET status='expiring', updated_at=? "
            "WHERE id=? AND status='waiting'",
            (now, rid))
        c.commit()
        return cur.rowcount == 1


def viotp_rental_overdue(max_age_min: int = 15) -> list:
    """Các đơn waiting đã quá TTL nhưng chưa được xử lý (cho luồng hoàn tiền).

    Không tự đánh dấu gì ở đây — poller claim từng đơn rồi check cuối
    với ViOTP trước khi quyết định hoàn tiền.
    """
    cutoff = int(time.time()) - max_age_min * 60
    c = get_conn()
    rows = c.execute(
        "SELECT * FROM viotp_rentals WHERE status='waiting' AND created_at<? ORDER BY id",
        (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def viotp_rental_expire_old(max_age_min: int = 15) -> list:
    """Đánh dấu hết hạn các đơn chờ quá lâu, trả về danh sách để báo user."""
    cutoff = int(time.time()) - max_age_min * 60
    with _lock:
        c = get_conn()
        rows = c.execute(
            "SELECT * FROM viotp_rentals WHERE status='waiting' AND created_at<?",
            (cutoff,)).fetchall()
        out = [dict(r) for r in rows]
        if out:
            c.execute("UPDATE viotp_rentals SET status='expired', updated_at=? "
                      "WHERE status='waiting' AND created_at<?",
                      (int(time.time()), cutoff))
            c.commit()
        return out


def viotp_rental_list_all(limit: int = 20, q: str = "") -> list:
    """Tất cả đơn thuê số mới nhất (cho bot báo admin / web admin). Có tìm kiếm."""
    c = get_conn()
    if q:
        like = f"%{q}%"
        rows = c.execute(
            "SELECT * FROM viotp_rentals WHERE service_name LIKE ? OR phone_number LIKE ?"
            " OR CAST(tg_id AS TEXT) LIKE ? OR CAST(id AS TEXT) LIKE ?"
            " ORDER BY id DESC LIMIT ?",
            (like, like, like, like, limit)).fetchall()
    else:
        rows = c.execute(
            "SELECT * FROM viotp_rentals ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
    return [dict(r) for r in rows]


def viotp_stats() -> dict:
    """Thống kê thuê số: tổng đơn, doanh thu, lãi."""
    c = get_conn()
    r = c.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(sell_price),0) rev, "
        "COALESCE(SUM(sell_price - cost_price),0) profit FROM viotp_rentals").fetchone()
    r = dict(r) if r else {}
    w = c.execute(
        "SELECT COUNT(*) n FROM viotp_rentals WHERE status='waiting'").fetchone()
    return {
        "total": r.get("n", 0),
        "revenue": r.get("rev", 0),
        "profit": r.get("profit", 0),
        "waiting": (dict(w).get("n", 0) if w else 0),
    }


# ==================== ỨNG TIỀN MUA ACC (LOANS) ====================

LOAN_DEFAULTS = {
    "loan_enabled": "1",
    "loan_min_acc": "5",
    "loan_max_per_request": "50000",
    "loan_max_total": "100000",
    "loan_due_days": "7",
    "loan_auto_deduct": "1",
    "loan_remind": "1",
}


def loan_setting(key: str) -> str:
    return get_setting(key, LOAN_DEFAULTS.get(key, ""))


def loan_setting_int(key: str) -> int:
    try:
        return int(loan_setting(key) or 0)
    except (ValueError, TypeError):
        return 0


def loan_enabled() -> bool:
    return loan_setting("loan_enabled") == "1"


def loan_create(tg_id: int, amount: int, note: str = "", created_by: int = 0) -> int:
    """Tạo đơn xin ứng (pending). Trả về loan id."""
    import time
    now = int(time.time())
    c = get_conn()
    cur = c.execute(
        "INSERT INTO acc_loans (tg_id, amount, status, note, created_by, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?) RETURNING id",
        (tg_id, amount, "pending", note, created_by, now, now))
    lid = cur.lastrowid
    c.commit()
    return lid


def loan_get(loan_id: int) -> dict | None:
    c = get_conn()
    r = c.execute("SELECT * FROM acc_loans WHERE id=?", (loan_id,)).fetchone()
    return dict(r) if r else None


def loan_list_pending() -> list:
    c = get_conn()
    rows = c.execute(
        "SELECT * FROM acc_loans WHERE status='pending' ORDER BY id ASC").fetchall()
    return [dict(r) for r in rows]


def loan_list_active() -> list:
    """Tất cả khoản đang nợ (active + overdue)."""
    c = get_conn()
    rows = c.execute(
        "SELECT * FROM acc_loans WHERE status IN ('active','overdue')"
        " ORDER BY due_date ASC").fetchall()
    return [dict(r) for r in rows]


def loan_list_by_customer(tg_id: int, limit: int = 20) -> list:
    c = get_conn()
    rows = c.execute(
        "SELECT * FROM acc_loans WHERE tg_id=? ORDER BY id DESC LIMIT ?",
        (tg_id, limit)).fetchall()
    return [dict(r) for r in rows]


def loan_list_done(limit: int = 50) -> list:
    c = get_conn()
    rows = c.execute(
        "SELECT * FROM acc_loans WHERE status IN ('paid','rejected')"
        " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def loan_total_debt(tg_id: int) -> int:
    """Tổng còn nợ của khách (active + overdue)."""
    c = get_conn()
    r = c.execute(
        "SELECT COALESCE(SUM(amount - paid_amount),0) d FROM acc_loans"
        " WHERE tg_id=? AND status IN ('active','overdue')", (tg_id,)).fetchone()
    return int(dict(r)["d"]) if r else 0


def loan_has_pending(tg_id: int) -> bool:
    c = get_conn()
    r = c.execute(
        "SELECT COUNT(*) n FROM acc_loans WHERE tg_id=? AND status='pending'",
        (tg_id,)).fetchone()
    return (dict(r)["n"] if r else 0) > 0


def loan_has_overdue(tg_id: int) -> bool:
    c = get_conn()
    r = c.execute(
        "SELECT COUNT(*) n FROM acc_loans WHERE tg_id=? AND status='overdue'",
        (tg_id,)).fetchone()
    return (dict(r)["n"] if r else 0) > 0


def loan_accs_bought(tg_id: int) -> int:
    """Số acc khách đã mua thành công (đếm đơn acc_orders)."""
    c = get_conn()
    r = c.execute(
        "SELECT COUNT(*) n FROM acc_orders WHERE tg_id=?", (tg_id,)).fetchone()
    return int(dict(r)["n"]) if r else 0


def loan_max_total_for(tg_id: int) -> int:
    """Hạn mức tổng nợ của khách (ưu tiên hạn mức riêng)."""
    c = get_conn()
    r = c.execute(
        "SELECT max_total FROM loan_custom_limits WHERE tg_id=?", (tg_id,)).fetchone()
    if r:
        return int(dict(r)["max_total"])
    return loan_setting_int("loan_max_total")


def loan_custom_limit_set(tg_id: int, max_total: int, note: str, updated_by: int):
    import time
    c = get_conn()
    c.execute(
        "INSERT INTO loan_custom_limits (tg_id, max_total, note, updated_by, updated_at)"
        " VALUES (?,?,?,?,?)"
        " ON CONFLICT (tg_id) DO UPDATE SET max_total=excluded.max_total,"
        " note=excluded.note, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
        (tg_id, max_total, note, updated_by, int(time.time())))
    c.commit()


def loan_approve(loan_id: int, admin_id: int) -> dict | None:
    """Duyệt đơn: chuyển pending -> active, tính hạn trả. Trả về loan dict."""
    import time
    loan = loan_get(loan_id)
    if not loan or loan["status"] != "pending":
        return None
    due_days = loan_setting_int("loan_due_days") or 7
    now = int(time.time())
    due = now + due_days * 86400
    c = get_conn()
    c.execute(
        "UPDATE acc_loans SET status='active', due_date=?, approved_by=?,"
        " updated_at=? WHERE id=? AND status='pending'",
        (due, admin_id, now, loan_id))
    c.commit()
    return loan_get(loan_id)


def loan_reject(loan_id: int, admin_id: int, reason: str = "") -> bool:
    import time
    c = get_conn()
    cur = c.execute(
        "UPDATE acc_loans SET status='rejected', note=?, updated_at=?"
        " WHERE id=? AND status='pending'",
        (reason, int(time.time()), loan_id))
    c.commit()
    return cur.rowcount > 0


def loan_add_payment(loan_id: int, amount: int, kind: str, note: str = "",
                     created_by: int = 0) -> dict | None:
    """Ghi nhận trả nợ / điều chỉnh. kind: repay_auto/repay_manual/adjust_up/adjust_down."""
    import time
    loan = loan_get(loan_id)
    if not loan or loan["status"] not in ("active", "overdue"):
        return None
    now = int(time.time())
    c = get_conn()
    if kind in ("repay_auto", "repay_manual"):
        new_paid = loan["paid_amount"] + amount
        if new_paid > loan["amount"]:
            amount = loan["amount"] - loan["paid_amount"]
            new_paid = loan["amount"]
        c.execute(
            "INSERT INTO acc_loan_payments (loan_id, amount, kind, note, created_by, created_at)"
            " VALUES (?,?,?,?,?,?)", (loan_id, amount, kind, note, created_by, now))
        status = "paid" if new_paid >= loan["amount"] else loan["status"]
        c.execute(
            "UPDATE acc_loans SET paid_amount=?, status=?, updated_at=? WHERE id=?",
            (new_paid, status, now, loan_id))
    elif kind == "adjust_down":
        # Giảm nợ: giảm amount (không vượt quá còn nợ)
        remaining = loan["amount"] - loan["paid_amount"]
        amount = min(amount, remaining)
        new_amount = loan["amount"] - amount
        c.execute(
            "INSERT INTO acc_loan_payments (loan_id, amount, kind, note, created_by, created_at)"
            " VALUES (?,?,?,?,?,?)", (loan_id, -amount, kind, note, created_by, now))
        status = "paid" if new_amount <= loan["paid_amount"] else loan["status"]
        c.execute(
            "UPDATE acc_loans SET amount=?, status=?, updated_at=? WHERE id=?",
            (new_amount, status, now, loan_id))
    elif kind == "adjust_up":
        new_amount = loan["amount"] + amount
        c.execute(
            "INSERT INTO acc_loan_payments (loan_id, amount, kind, note, created_by, created_at)"
            " VALUES (?,?,?,?,?,?)", (loan_id, amount, kind, note, created_by, now))
        c.execute(
            "UPDATE acc_loans SET amount=?, updated_at=? WHERE id=?",
            (new_amount, now, loan_id))
    else:
        return None
    c.commit()
    return loan_get(loan_id)


def loan_payments(loan_id: int) -> list:
    c = get_conn()
    rows = c.execute(
        "SELECT * FROM acc_loan_payments WHERE loan_id=? ORDER BY id ASC",
        (loan_id,)).fetchall()
    return [dict(r) for r in rows]


def loan_stats() -> dict:
    """Tổng quan công nợ cho admin."""
    c = get_conn()
    r = c.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(amount - paid_amount),0) d"
        " FROM acc_loans WHERE status IN ('active','overdue')").fetchone()
    r = dict(r) if r else {}
    p = c.execute(
        "SELECT COUNT(*) n FROM acc_loans WHERE status='pending'").fetchone()
    o = c.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(amount - paid_amount),0) d"
        " FROM acc_loans WHERE status='overdue'").fetchone()
    o = dict(o) if o else {}
    return {
        "active_count": r.get("n", 0),
        "active_debt": r.get("d", 0),
        "pending_count": (dict(p).get("n", 0) if p else 0),
        "overdue_count": o.get("n", 0),
        "overdue_debt": o.get("d", 0),
    }


def loan_mark_overdue() -> int:
    """Chuyển các khoản quá hạn sang overdue. Trả về số khoản bị chuyển."""
    import time
    now = int(time.time())
    c = get_conn()
    cur = c.execute(
        "UPDATE acc_loans SET status='overdue', updated_at=?"
        " WHERE status='active' AND due_date > 0 AND due_date < ?",
        (now, now))
    c.commit()
    return cur.rowcount


# ================= YÊU CẦU CHUYỂN VÍ (cần admin duyệt) =================
# Dùng khi chuyển tiền có ví chính tham gia. 3 ví phụ chuyển nhau thì
# đi đường adjust_wallet trực tiếp, không qua bảng này.

def _wallet_transfer_ensure_table():
    c = get_conn()
    c.execute(
        "CREATE TABLE IF NOT EXISTS wallet_transfer_requests("
        "id SERIAL PRIMARY KEY, tg_id BIGINT NOT NULL, src TEXT NOT NULL, "
        "dst TEXT NOT NULL, amount INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', "
        "created_at INTEGER NOT NULL, decided_at INTEGER DEFAULT 0, decided_by BIGINT DEFAULT 0)")
    # Nếu bảng đã tồn tại từ trước với id INTEGER (không tự tăng) thì gắn sequence
    try:
        c.execute(
            "DO $$ BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM pg_sequences WHERE sequencename='wallet_transfer_requests_id_seq') THEN "
            "CREATE SEQUENCE wallet_transfer_requests_id_seq; "
            "END IF; END $$")
        c.execute(
            "ALTER TABLE wallet_transfer_requests ALTER COLUMN id "
            "SET DEFAULT nextval('wallet_transfer_requests_id_seq')")
        c.execute(
            "SELECT setval('wallet_transfer_requests_id_seq', "
            "COALESCE((SELECT MAX(id) FROM wallet_transfer_requests), 0) + 1, false)")
    except Exception:
        pass
    c.commit()


def wallet_transfer_request_add(tg_id: int, src: str, dst: str, amount: int) -> int:
    """Tạo yêu cầu chuyển ví chờ duyệt. Trả về id yêu cầu."""
    _wallet_transfer_ensure_table()
    import time
    c = get_conn()
    cur = c.execute(
        "INSERT INTO wallet_transfer_requests(tg_id, src, dst, amount, status, created_at) "
        "VALUES(?,?,?,?, 'pending', ?) RETURNING id",
        (tg_id, src, dst, amount, int(time.time())))
    # LƯU Ý: PgCursor.execute đã tự fetchone() dòng RETURNING vào cur.lastrowid,
    # gọi fetchone() thêm sẽ trả None.
    new_id = cur.lastrowid
    c.commit()
    return int(new_id)


def wallet_transfer_request_get(req_id: int) -> dict | None:
    _wallet_transfer_ensure_table()
    c = get_conn()
    r = c.execute(
        "SELECT * FROM wallet_transfer_requests WHERE id=?", (req_id,)).fetchone()
    return dict(r) if r else None


def wallet_transfer_request_approve(req_id: int, admin_id: int) -> bool:
    """Admin duyệt: trừ ví nguồn, cộng ví đích trong 1 transaction Postgres
    thật (tắt autocommit tạm thời). Trả True nếu thành công.

    Idempotent: chỉ xử lý khi status='pending'; UPDATE ... WHERE status='pending'
    đảm bảo 2 admin bấm cùng lúc chỉ 1 người thành công.
    """
    import time
    _wallet_transfer_ensure_table()
    with _lock:
        c = get_conn()
        raw = getattr(c, "conn", None)
        if raw is None:
            return False
        prev_ac = raw.autocommit
        raw.autocommit = False
        try:
            cur = raw.cursor(cursor_factory=DictCursor)

            def _x(sql, params=()):
                cur.execute(sql.replace("?", "%s"), params)
                return cur.rowcount

            # Claim nguyên tử: chỉ pending mới chuyển được sang approved
            now = int(time.time())
            rc = _x(
                "UPDATE wallet_transfer_requests SET status='approved', "
                "decided_at=?, decided_by=? WHERE id=? AND status='pending'",
                (now, admin_id, req_id))
            if rc == 0:
                raw.rollback()
                return False
            cur.execute(
                "SELECT tg_id, src, dst, amount FROM wallet_transfer_requests "
                "WHERE id=%s", (req_id,))
            r = cur.fetchone()
            src, dst, amount, tg_id = r["src"], r["dst"], int(r["amount"]), int(r["tg_id"])
            col_src = WALLET_COLUMNS.get(src)
            col_dst = WALLET_COLUMNS.get(dst)
            if not col_src or not col_dst or amount <= 0:
                # Tham số sai: hủy claim, trả request về pending để không kẹt
                _x("UPDATE wallet_transfer_requests SET status='pending', "
                   "decided_at=0, decided_by=0 WHERE id=?", (req_id,))
                raw.commit()
                return False
            cur.execute(
                f"SELECT {col_src} FROM tg_users WHERE tg_id=%s FOR UPDATE",
                (tg_id,))
            bal = cur.fetchone()
            if not bal or int(bal[col_src] or 0) < amount:
                # Không đủ tiền: hủy claim, trả request về pending
                _x("UPDATE wallet_transfer_requests SET status='pending', "
                   "decided_at=0, decided_by=0 WHERE id=?", (req_id,))
                raw.commit()
                return False
            _x(f"UPDATE tg_users SET {col_src} = {col_src} - ? WHERE tg_id=?",
               (amount, tg_id))
            _x(f"UPDATE tg_users SET {col_dst} = {col_dst} + ? WHERE tg_id=?",
               (amount, tg_id))
            _x("INSERT INTO txns(ts, tg_id, amount, reason) VALUES(?,?,?,?)",
               (now, tg_id, -amount,
                f"chuyen_vi_duyet:{src}->{dst} #{req_id} [ví {src}]"))
            _x("INSERT INTO txns(ts, tg_id, amount, reason) VALUES(?,?,?,?)",
               (now, tg_id, amount,
                f"chuyen_vi_duyet:{src}->{dst} #{req_id} [ví {dst}]"))
            raw.commit()
        except Exception:
            try:
                raw.rollback()
            except Exception:
                pass
            raise
        finally:
            raw.autocommit = prev_ac
    try:
        admin_audit_add(admin_id, "", "duyet_chuyen_vi", f"#{req_id} {src}->{dst} {amount}")
    except Exception:
        pass
    return True


def wallet_transfer_request_reject(req_id: int, admin_id: int) -> bool:
    import time
    _wallet_transfer_ensure_table()
    c = get_conn()
    cur = c.execute(
        "UPDATE wallet_transfer_requests SET status='rejected', decided_at=?, "
        "decided_by=? WHERE id=? AND status='pending'",
        (int(time.time()), admin_id, req_id))
    c.commit()
    return cur.rowcount > 0


def _wallet_transfer_idem_ensure_table(c=None):
    """Bảng chống bấm trùng nút xác nhận chuyển ví."""
    c = c or get_conn()
    c.execute(
        "CREATE TABLE IF NOT EXISTS wallet_transfer_idem("
        "key TEXT PRIMARY KEY, created_at INTEGER NOT NULL)")


def wallet_transfer_instant(tg_id: int, src: str, dst: str, amount: int,
                            idem_key: str) -> str:
    """Chuyển NGAY giữa các ví trong 1 transaction Postgres thật + chống bấm trùng.

    LƯU Ý: connection Postgres chạy autocommit=True nên phải tắt tạm thời
    để có transaction nguyên tử (không dùng c.commit()/c.rollback() vì là no-op).

    Trả về: 'ok' | 'insufficient' (số dư không đủ) | 'duplicate' (đã xử lý
    rồi — bấm trùng) | 'invalid' (tham số sai).
    """
    import time
    col_src = WALLET_COLUMNS.get(src)
    col_dst = WALLET_COLUMNS.get(dst)
    if not col_src or not col_dst or src == dst or amount <= 0 or not idem_key:
        return "invalid"
    with _lock:
        c = get_conn()
        _wallet_transfer_idem_ensure_table(c)
        raw = getattr(c, "conn", None)
        if raw is None:
            return "invalid"  # không lấy được connection thô
        prev_ac = raw.autocommit
        raw.autocommit = False
        try:
            cur = raw.cursor(cursor_factory=DictCursor)

            def _x(sql, params=()):
                cur.execute(sql.replace("?", "%s"), params)
                return cur.rowcount

            # 1) Chống bấm trùng: key đã tồn tại -> đã xử lý rồi
            rc = _x(
                "INSERT INTO wallet_transfer_idem(key, created_at) "
                "VALUES(?, ?) ON CONFLICT(key) DO NOTHING",
                (idem_key, int(time.time())))
            if rc == 0:
                raw.rollback()
                return "duplicate"
            # 2) Kiểm tra số dư ví nguồn, khóa dòng chống race
            cur.execute(
                f"SELECT {col_src} FROM tg_users WHERE tg_id=%s FOR UPDATE",
                (tg_id,))
            r = cur.fetchone()
            if not r or int(r[col_src] or 0) < amount:
                raw.rollback()  # hủy cả INSERT key để nạp thêm rồi bấm lại được
                return "insufficient"
            # 3) Trừ nguồn + cộng đích + ghi txns — tất cả trong 1 transaction
            now = int(time.time())
            _x(f"UPDATE tg_users SET {col_src} = {col_src} - ? WHERE tg_id=?",
               (amount, tg_id))
            _x(f"UPDATE tg_users SET {col_dst} = {col_dst} + ? WHERE tg_id=?",
               (amount, tg_id))
            _x("INSERT INTO txns(ts, tg_id, amount, reason) VALUES(?,?,?,?)",
               (now, tg_id, -amount, f"chuyen_vi:{src}->{dst} [ví {src}]"))
            _x("INSERT INTO txns(ts, tg_id, amount, reason) VALUES(?,?,?,?)",
               (now, tg_id, amount, f"chuyen_vi:{src}->{dst} [ví {dst}]"))
            raw.commit()
        except Exception:
            try:
                raw.rollback()
            except Exception:
                pass
            raise
        finally:
            raw.autocommit = prev_ac
    try:
        admin_audit_add(tg_id, "", "chuyen_vi",
                        f"{src}->{dst} {amount} [key {idem_key[:40]}]")
    except Exception:
        pass
    return "ok"
