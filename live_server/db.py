from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

SCHEMA = '''
CREATE TABLE IF NOT EXISTS quotes (
  symbol TEXT PRIMARY KEY,
  exchange TEXT,
  price REAL,
  change_pct REAL,
  volume REAL,
  open REAL,
  high REAL,
  low REAL,
  prev_close REAL,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS ticks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  price REAL NOT NULL,
  qty REAL DEFAULT 0,
  cum_volume REAL DEFAULT 0,
  ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ticks_symbol_ts ON ticks(symbol, ts);
CREATE TABLE IF NOT EXISTS raw_ws (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  payload TEXT NOT NULL,
  ts TEXT NOT NULL
);
'''

class DB:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def upsert_quote(self, q: dict):
        with self.conn() as c:
            c.execute('''INSERT INTO quotes(symbol,exchange,price,change_pct,volume,open,high,low,prev_close,updated_at)
              VALUES(:symbol,:exchange,:price,:change_pct,:volume,:open,:high,:low,:prev_close,:updated_at)
              ON CONFLICT(symbol) DO UPDATE SET exchange=excluded.exchange, price=excluded.price,
              change_pct=excluded.change_pct, volume=excluded.volume, open=excluded.open, high=excluded.high,
              low=excluded.low, prev_close=excluded.prev_close, updated_at=excluded.updated_at''', q)

    def add_tick(self, symbol: str, price: float, qty: float, cum_volume: float, ts: str):
        with self.conn() as c:
            c.execute('INSERT INTO ticks(symbol,price,qty,cum_volume,ts) VALUES(?,?,?,?,?)',
                      (symbol, price, qty, cum_volume, ts))

    def add_raw(self, payload: str, ts: str):
        with self.conn() as c:
            c.execute('INSERT INTO raw_ws(payload,ts) VALUES(?,?)', (payload, ts))
            c.execute('DELETE FROM raw_ws WHERE id NOT IN (SELECT id FROM raw_ws ORDER BY id DESC LIMIT 500)')

    def quote(self, symbol: str):
        with self.conn() as c:
            r = c.execute('SELECT * FROM quotes WHERE symbol=?', (symbol.upper(),)).fetchone()
            return dict(r) if r else None

    def quotes(self):
        with self.conn() as c:
            return [dict(r) for r in c.execute('SELECT * FROM quotes ORDER BY symbol').fetchall()]

    def ticks(self, symbol: str, limit: int = 5000):
        with self.conn() as c:
            rows = c.execute('SELECT symbol,price,qty,cum_volume,ts FROM ticks WHERE symbol=? ORDER BY ts DESC LIMIT ?',
                             (symbol.upper(), limit)).fetchall()
            return [dict(r) for r in reversed(rows)]

    def raw(self, limit: int = 20):
        with self.conn() as c:
            return [dict(r) for r in c.execute('SELECT * FROM raw_ws ORDER BY id DESC LIMIT ?', (limit,)).fetchall()]
