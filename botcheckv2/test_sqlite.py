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
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
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

conn = SqliteConnection()
conn.executescript("CREATE TABLE users (id BIGSERIAL PRIMARY KEY, name TEXT);")
conn.execute("INSERT INTO users(name) VALUES(?)", ("test",))
print(conn.execute("SELECT * FROM users").fetchone()["name"])
conn.execute("INSERT INTO users(name) VALUES(?) RETURNING id", ("test2",))
print(conn.execute("SELECT * FROM users").fetchall())
