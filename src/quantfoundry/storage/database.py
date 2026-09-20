"""Versioned SQLite owned by QuantFoundry; legacy databases are read-only inputs."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

SCHEMA_VERSION = 3
APPLICATION_ID = 0x51464E44
from ..indicators.spec import FEATURE_COLUMNS
from .comments import sync_comments



class Database:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()

    def initialize(self):
        """Create a fresh schema. Never adopt or alter an unversioned existing DB."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection(require_schema=False) as conn:
            conn.execute("BEGIN IMMEDIATE")
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            owner = conn.execute("PRAGMA application_id").fetchone()[0]
            if tables:
                if version not in (1, 2, SCHEMA_VERSION) or owner != APPLICATION_ID:
                    raise ValueError("Not a supported QuantFoundry DB; legacy migration must be explicit")
                if version == 1:
                    # SQLite rewrites price's FK to instruments during this rename.
                    # No price rows are copied or removed. DDL is transactional.
                    conn.execute("ALTER TABLE stock RENAME TO instruments")
                    conn.execute("CREATE TABLE stock (market TEXT NOT NULL, ticker TEXT NOT NULL, update_time TEXT NOT NULL, in_current_listing INTEGER NOT NULL DEFAULT 1 CHECK(in_current_listing IN (0,1)), PRIMARY KEY(market,ticker))")
                    conn.execute("INSERT INTO stock SELECT market,ticker,update_time,in_current_listing FROM instruments")
                    conn.execute("PRAGMA user_version=2")
                sync_comments(conn)
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                conn.commit()
                return
            statements = [
                "CREATE TABLE instruments (market TEXT NOT NULL,ticker TEXT NOT NULL,update_time TEXT NOT NULL,PRIMARY KEY(market,ticker))",
                "CREATE TABLE stock (market TEXT NOT NULL, ticker TEXT NOT NULL, update_time TEXT NOT NULL, in_current_listing INTEGER NOT NULL DEFAULT 1 CHECK(in_current_listing IN (0,1)), PRIMARY KEY(market,ticker))",
                "CREATE TABLE price (ticker TEXT NOT NULL, market TEXT NOT NULL, date TEXT NOT NULL, adj_close REAL NOT NULL CHECK(adj_close>0), volume REAL CHECK(volume>=0), close REAL CHECK(close>0), source TEXT NOT NULL, insert_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(ticker,market,date), FOREIGN KEY(market,ticker) REFERENCES instruments(market,ticker))",
                "CREATE INDEX price_market_date ON price(market,date,ticker)",
                "CREATE TABLE price_detail (ticker TEXT NOT NULL, market TEXT NOT NULL, date TEXT NOT NULL, adj_close REAL NOT NULL, volume REAL, " + ",".join(c + " REAL" for c in FEATURE_COLUMNS) + ", calculation_version TEXT NOT NULL, insert_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(ticker,market,date), FOREIGN KEY(ticker,market,date) REFERENCES price(ticker,market,date))",
                "CREATE TABLE rs_rating_history (ticker TEXT NOT NULL, market TEXT NOT NULL, date TEXT NOT NULL, rs_percentile INTEGER NOT NULL CHECK(rs_percentile BETWEEN 0 AND 99), return_12m REAL NOT NULL, lookback INTEGER NOT NULL, universe_size INTEGER NOT NULL, universe_json TEXT NOT NULL, calculation_version TEXT NOT NULL, insert_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(ticker,market,date), FOREIGN KEY(ticker,market,date) REFERENCES price(ticker,market,date))",
                "CREATE TABLE price_revisions (id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, market TEXT NOT NULL, date TEXT NOT NULL, old_json TEXT NOT NULL, new_json TEXT NOT NULL, changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
                "CREATE TABLE update_runs (id TEXT PRIMARY KEY, market TEXT NOT NULL, start_date TEXT NOT NULL, as_of TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('RUNNING','SUCCESS','PARTIAL','FAILED')), result_json TEXT, started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, finished_at TEXT)",
            ]
            for statement in statements:
                conn.execute(statement)
            sync_comments(conn)
            conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.commit()
            conn.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def connection(self, require_schema=True):
        # mode=rw prevents typos from silently creating an empty DB during updates.
        uri = self.path.as_uri() + ("?mode=rw" if require_schema else "?mode=rwc")
        conn = sqlite3.connect(uri, uri=True, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=FULL")
            if require_schema:
                if (conn.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                        or conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION):
                    raise ValueError("Initialize a new QuantFoundry DB first; legacy DB is not writable here")
            yield conn
        finally:
            if conn.in_transaction:
                conn.rollback()
            conn.close()

    @contextmanager
    def transaction(self):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
