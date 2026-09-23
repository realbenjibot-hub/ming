"""Journal: SQLite on DATA_DIR. Cross-thread safe, WAL. Tables: meta, theses, trades, equity, log."""
import sqlite3, json, threading, datetime as dt
from .config import DATA_DIR

class Journal:
    def __init__(self, path=DATA_DIR / "journal.sqlite"):
        self.path = str(path)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS theses(id INTEGER PRIMARY KEY, day TEXT, ts TEXT, symbol TEXT, sector TEXT, catalyst TEXT, reason TEXT,
            invalidation TEXT, conviction INTEGER, acted INTEGER DEFAULT 0, reject_reason TEXT, source TEXT, operator_directed INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY, symbol TEXT, side TEXT, qty REAL, entry REAL, entry_ts TEXT, stop REAL,
            exit REAL, exit_ts TEXT, exit_reason TEXT, pl REAL, thesis_id INTEGER, order_id TEXT, stop_order_id TEXT, status TEXT DEFAULT 'open');
        CREATE TABLE IF NOT EXISTS equity(ts TEXT PRIMARY KEY, equity REAL, cash REAL, spy REAL);
        CREATE TABLE IF NOT EXISTS log(id INTEGER PRIMARY KEY, ts TEXT, level TEXT, msg TEXT);
        """)
        self.db.commit()
        for tbl, col, typ in [("trades", "book", "TEXT DEFAULT 'swing'"), ("trades", "target_pct", "REAL"), ("trades", "trail_trigger_pct", "REAL"), ("trades", "trail_pct", "REAL"), ("trades", "stop_pct", "REAL"),
                              ("theses", "book", "TEXT"), ("theses", "direction", "TEXT"),
                              ("trades", "underlying", "TEXT"), ("trades", "opt_type", "TEXT"), ("trades", "strike", "REAL"), ("trades", "expiry", "TEXT")]:
            if col not in [r[1] for r in self.db.execute(f"PRAGMA table_info({tbl})").fetchall()]:
                self.db.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {typ}"); self.db.commit()

    def _x(self, sql, *args):
        with self.lock:
            cur = self.db.execute(sql, args); self.db.commit(); return cur
    def _q(self, sql, *args):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    @staticmethod
    def now():
        return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    # meta
    def get(self, key, default=None):
        r = self._q("SELECT value FROM meta WHERE key=?", key)
        return json.loads(r[0]["value"]) if r else default
    def set(self, key, value):
        self._x("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", key, json.dumps(value))

    # log
    def log(self, level, msg):
        self._x("INSERT INTO log(ts,level,msg) VALUES(?,?,?)", self.now(), level, msg)
        print(f"[{level}] {msg}", flush=True)
    def logs(self, n=50):
        return self._q("SELECT ts,level,msg FROM log ORDER BY id DESC LIMIT ?", n)

    # theses
    def add_theses(self, day, theses, source="research"):
        ids = []
        for t in theses:
            cur = self._x("INSERT INTO theses(day,ts,symbol,sector,catalyst,reason,invalidation,conviction,source,operator_directed,book,direction) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                          day, self.now(), t["symbol"], t.get("sector"), t.get("catalyst"), t.get("reason"), t.get("invalidation"),
                          int(t.get("conviction", 0)), source, int(bool(t.get("operator_directed"))), t.get("book"), t.get("direction", "up"))
            ids.append(cur.lastrowid)
        return ids
    def theses_for(self, day):
        return self._q("SELECT * FROM theses WHERE day=? ORDER BY conviction DESC, id", day)
    def mark_thesis(self, tid, acted=None, reject_reason=None):
        if acted is not None: self._x("UPDATE theses SET acted=? WHERE id=?", int(acted), tid)
        if reject_reason is not None: self._x("UPDATE theses SET reject_reason=? WHERE id=?", reject_reason, tid)
    def replace_day(self, day, theses, source):
        # the 9:00 refresh rewrites unacted theses for the day
        self._x("DELETE FROM theses WHERE day=? AND acted=0", day)
        return self.add_theses(day, theses, source)

    # trades
    def open_trade(self, symbol, qty, entry, stop, thesis_id, order_id, stop_order_id, book="swing", exits=None, contract=None):
        e = exits or {}; c = contract or {}
        cur = self._x("INSERT INTO trades(symbol,side,qty,entry,entry_ts,stop,thesis_id,order_id,stop_order_id,book,stop_pct,target_pct,trail_trigger_pct,trail_pct,underlying,opt_type,strike,expiry) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      symbol, "long", qty, entry, self.now(), stop, thesis_id, order_id, stop_order_id, book, e.get("stop_pct"), e.get("target_pct"), e.get("trail_trigger_pct"), e.get("trail_pct"),
                      c.get("underlying"), c.get("type"), c.get("strike"), c.get("expiry"))
        return cur.lastrowid
    def open_trades(self, book=None):
        if book: return self._q("SELECT * FROM trades WHERE status='open' AND book=? ORDER BY id", book)
        return self._q("SELECT * FROM trades WHERE status='open' ORDER BY id")
    def trade_for(self, symbol):
        r = self._q("SELECT * FROM trades WHERE status='open' AND symbol=? ORDER BY id DESC LIMIT 1", symbol)
        return r[0] if r else None
    def update_stop(self, tid, stop, stop_order_id=None):
        self._x("UPDATE trades SET stop=?, stop_order_id=COALESCE(?,stop_order_id) WHERE id=?", stop, stop_order_id, tid)
    def close_trade(self, tid, exit_price, reason):
        t = self._q("SELECT * FROM trades WHERE id=?", tid)[0]
        pl = (exit_price - t["entry"]) * t["qty"] * (100 if t.get("opt_type") else 1)
        self._x("UPDATE trades SET exit=?, exit_ts=?, exit_reason=?, pl=?, status='closed' WHERE id=?", exit_price, self.now(), reason, pl, tid)
        return pl
    def closed_trades(self, n=200, book=None):
        if book: return self._q("SELECT * FROM trades WHERE status='closed' AND book=? ORDER BY id DESC LIMIT ?", book, n)
        return self._q("SELECT * FROM trades WHERE status='closed' ORDER BY id DESC LIMIT ?", n)
    def all_trades(self, n=200):
        return self._q("SELECT * FROM trades ORDER BY id DESC LIMIT ?", n)

    # equity
    def mark_equity(self, equity, cash, spy):
        self._x("INSERT OR REPLACE INTO equity(ts,equity,cash,spy) VALUES(?,?,?,?)", self.now(), equity, cash, spy)
    def equity_curve(self, n=500):
        return self._q("SELECT * FROM equity ORDER BY ts DESC LIMIT ?", n)[::-1]
