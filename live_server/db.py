from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from pathlib import Path

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
CREATE TABLE IF NOT EXISTS daily_metrics (
  symbol TEXT PRIMARY KEY,
  ma5 REAL,
  ma5_slope_pct REAL,
  avg5_volume REAL,
  avg5_dollar_volume REAL,
  atr5_pct REAL,
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
CREATE TABLE IF NOT EXISTS ranking_snapshots (
  trade_date TEXT NOT NULL,
  label TEXT NOT NULL,
  symbol TEXT NOT NULL,
  rank INTEGER NOT NULL,
  score REAL NOT NULL,
  bias TEXT,
  price REAL,
  captured_at TEXT NOT NULL,
  PRIMARY KEY(trade_date,label,symbol)
);
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

    def upsert_daily_metrics(self, m: dict):
        with self.conn() as c:
            c.execute('''INSERT INTO daily_metrics(symbol,ma5,ma5_slope_pct,avg5_volume,avg5_dollar_volume,atr5_pct,updated_at)
              VALUES(:symbol,:ma5,:ma5_slope_pct,:avg5_volume,:avg5_dollar_volume,:atr5_pct,:updated_at)
              ON CONFLICT(symbol) DO UPDATE SET ma5=excluded.ma5,ma5_slope_pct=excluded.ma5_slope_pct,
              avg5_volume=excluded.avg5_volume,avg5_dollar_volume=excluded.avg5_dollar_volume,
              atr5_pct=excluded.atr5_pct,updated_at=excluded.updated_at''', m)

    def daily_metric(self, symbol: str):
        with self.conn() as c:
            r=c.execute('SELECT * FROM daily_metrics WHERE symbol=?',(symbol.upper(),)).fetchone()
            return dict(r) if r else None

    def daily_metrics(self):
        with self.conn() as c:
            return [dict(r) for r in c.execute('SELECT * FROM daily_metrics ORDER BY symbol').fetchall()]

    def add_tick_if_missing(self, symbol: str, price: float, qty: float, cum_volume: float, ts: str) -> int:
        with self.conn() as c:
            found=c.execute('SELECT 1 FROM ticks WHERE symbol=? AND ts=? AND ABS(price-?)<0.0000001 LIMIT 1',
                            (symbol.upper(),ts,float(price))).fetchone()
            if found: return 0
            c.execute('INSERT INTO ticks(symbol,price,qty,cum_volume,ts) VALUES(?,?,?,?,?)',
                      (symbol.upper(),float(price),float(qty),float(cum_volume),ts))
            return 1

    def delete_zero_qty_ticks(self, symbol: str):
        # Removes REST snapshot pseudo-ticks left by versions <=1.3.
        with self.conn() as c:
            c.execute('DELETE FROM ticks WHERE symbol=? AND COALESCE(qty,0)=0',(symbol.upper(),))

    def add_tick(self, symbol: str, price: float, qty: float, cum_volume: float, ts: str):
        with self.conn() as c:
            c.execute('INSERT INTO ticks(symbol,price,qty,cum_volume,ts) VALUES(?,?,?,?,?)',
                      (symbol, price, qty, cum_volume, ts))
            # keep roughly a few trading days per symbol
            c.execute('''DELETE FROM ticks WHERE id IN (
              SELECT id FROM ticks WHERE symbol=? ORDER BY id DESC LIMIT -1 OFFSET 250000
            )''',(symbol,))

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

    def save_ranking_snapshot(self, trade_date: str, label: str, rows: list[dict], captured_at: str):
        with self.conn() as c:
            for i,row in enumerate(rows,1):
                c.execute('''INSERT OR REPLACE INTO ranking_snapshots
                  (trade_date,label,symbol,rank,score,bias,price,captured_at) VALUES(?,?,?,?,?,?,?,?)''',
                  (trade_date,label,row['symbol'],i,row['score'],row.get('bias'),row.get('price'),captured_at))

    def ranking_history(self, trade_date: str | None=None):
        with self.conn() as c:
            if not trade_date:
                r=c.execute('SELECT MAX(trade_date) AS d FROM ranking_snapshots').fetchone()
                trade_date=r['d'] if r else None
            if not trade_date: return []
            return [dict(r) for r in c.execute('''SELECT * FROM ranking_snapshots WHERE trade_date=?
                ORDER BY CASE label WHEN 'T-10' THEN 1 WHEN 'T-1' THEN 2 WHEN 'T+7' THEN 3 ELSE 9 END, rank''',(trade_date,)).fetchall()]

    def raw(self, limit: int = 20):
        with self.conn() as c:
            return [dict(r) for r in c.execute('SELECT * FROM raw_ws ORDER BY id DESC LIMIT ?', (limit,)).fetchall()]
