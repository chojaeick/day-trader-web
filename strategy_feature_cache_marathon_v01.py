#!/usr/bin/env python3
"""Strategy feature-cache marathon v0.1

Builds reusable OHLCV caches from historical_minute_bars using ONLY local SQLite data.
No Kiwoom/API credentials are needed.

Purpose
- Stop re-aggregating 1m bars in every strategy backtest.
- Create common 5m, 15m and daily OHLCV bars once.
- Resumable/idempotent: INSERT OR REPLACE into new cache tables only.
- Source historical_minute_bars is read-only.

Tables created
- strategy_bar_cache(symbol, trade_date, timeframe_min, bucket, et_time,
  open, high, low, close, volume, source_rows, built_at)
  timeframe_min: 5, 15, 1440(daily)

Example
  python -u strategy_feature_cache_marathon_v01.py
  python -u strategy_feature_cache_marathon_v01.py --symbols AMD,NVDA,SPY,QQQ
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import time
from pathlib import Path

RUNTIME = Path(os.environ.get("DAYTRADER_ROOT", "/home/ubuntu/day-trader-api"))
DB = Path(os.environ.get("DAYTRADER_DB", str(RUNTIME / "daytrader.db")))


def connect():
    c = sqlite3.connect(str(DB), timeout=60)
    c.execute("PRAGMA busy_timeout=60000")
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


def ensure_schema(c: sqlite3.Connection):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS strategy_bar_cache (
        symbol TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        timeframe_min INTEGER NOT NULL,
        bucket INTEGER NOT NULL,
        et_time TEXT,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        volume REAL,
        source_rows INTEGER NOT NULL DEFAULT 0,
        built_at TEXT NOT NULL,
        PRIMARY KEY(symbol, trade_date, timeframe_min, bucket)
    );
    CREATE INDEX IF NOT EXISTS idx_strategy_bar_cache_tf_date
      ON strategy_bar_cache(timeframe_min, trade_date, symbol);
    CREATE INDEX IF NOT EXISTS idx_strategy_bar_cache_symbol_tf
      ON strategy_bar_cache(symbol, timeframe_min, trade_date, bucket);
    """)
    c.commit()


def eligible_pairs(c, symbols):
    params=[]
    where="WHERE interval_min=1 AND session='REGULAR'"
    if symbols:
        marks=','.join('?' for _ in symbols)
        where += f" AND symbol IN ({marks})"
        params.extend(symbols)
    sql=f"""
      SELECT symbol, trade_date, COUNT(*) n
      FROM historical_minute_bars
      {where}
      GROUP BY symbol, trade_date
      HAVING COUNT(*) >= 60
      ORDER BY trade_date, symbol
    """
    return [(str(s),str(d),int(n)) for s,d,n in c.execute(sql,params).fetchall()]


def cached_complete(c, sym, day, tf):
    if tf == 1440:
        need=1
    else:
        # Do not assume a perfect 390-row day; just require at least 70% of expected regular buckets.
        need=int((390/tf)*0.70)
    n=c.execute("SELECT COUNT(*) FROM strategy_bar_cache WHERE symbol=? AND trade_date=? AND timeframe_min=?",
                (sym,day,tf)).fetchone()[0]
    return int(n) >= need


def fetch_day(c, sym, day):
    rows=c.execute("""
      SELECT et_time, open, high, low, close, volume
      FROM historical_minute_bars
      WHERE interval_min=1 AND session='REGULAR' AND symbol=? AND trade_date=?
      ORDER BY et_time
    """,(sym,day)).fetchall()
    out=[]
    for et,o,h,l,cl,v in rows:
        try:
            hh=int(str(et)[0:2]); mm=int(str(et)[3:5])
        except Exception:
            continue
        out.append((str(et),float(o),float(h),float(l),float(cl),float(v or 0),hh*60+mm))
    return out


def aggregate(rows, tf):
    if not rows:
        return []
    if tf == 1440:
        return [(0, rows[-1][0], rows[0][1], max(r[2] for r in rows), min(r[3] for r in rows), rows[-1][4], sum(r[5] for r in rows), len(rows))]
    groups={}
    for r in rows:
        bucket=r[6]//tf
        groups.setdefault(bucket,[]).append(r)
    out=[]
    for b,z in sorted(groups.items()):
        out.append((b,z[-1][0],z[0][1],max(r[2] for r in z),min(r[3] for r in z),z[-1][4],sum(r[5] for r in z),len(z)))
    return out


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--symbols',default='',help='comma-separated; default all REGULAR symbols')
    ap.add_argument('--force',action='store_true',help='rebuild even if cache appears complete')
    ap.add_argument('--commit-every',type=int,default=25)
    args=ap.parse_args()
    symbols=[x.strip().upper() for x in args.symbols.split(',') if x.strip()]

    if not DB.exists():
        raise SystemExit(f'DB not found: {DB}')
    c=connect(); ensure_schema(c)
    pairs=eligible_pairs(c,symbols)
    print('===== STRATEGY FEATURE CACHE MARATHON v0.1 =====',flush=True)
    print('DB',DB,flush=True)
    print('PAIR_DAYS',len(pairs),'SYMBOL_FILTER',','.join(symbols) if symbols else 'ALL',flush=True)
    print('TIMEFRAMES 5m,15m,DAILY / LOCAL DB ONLY / RESUMABLE',flush=True)

    t0=time.time(); built=skipped=bars=0
    now=lambda: dt.datetime.now().isoformat(timespec='seconds')
    for i,(sym,day,nsrc) in enumerate(pairs,1):
        needs=[tf for tf in (5,15,1440) if args.force or not cached_complete(c,sym,day,tf)]
        if not needs:
            skipped += 1
        else:
            rows=fetch_day(c,sym,day)
            for tf in needs:
                ag=aggregate(rows,tf)
                c.execute('DELETE FROM strategy_bar_cache WHERE symbol=? AND trade_date=? AND timeframe_min=?',(sym,day,tf))
                c.executemany("""
                  INSERT OR REPLACE INTO strategy_bar_cache
                  (symbol,trade_date,timeframe_min,bucket,et_time,open,high,low,close,volume,source_rows,built_at)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,[(sym,day,tf,b,et,o,h,l,cl,v,sr,now()) for b,et,o,h,l,cl,v,sr in ag])
                bars += len(ag)
            built += 1
        if i % max(1,args.commit_every)==0:
            c.commit()
        if i==1 or i%50==0 or i==len(pairs):
            el=time.time()-t0
            rate=i/el if el>0 else 0
            eta=(len(pairs)-i)/rate if rate else 0
            print(f'PROGRESS {i}/{len(pairs)} built_days={built} skipped_days={skipped} cached_bars={bars} elapsed={el/60:.1f}m eta={eta/60:.1f}m',flush=True)
    c.commit()
    counts=c.execute('SELECT timeframe_min,COUNT(*),COUNT(DISTINCT symbol||"|"||trade_date) FROM strategy_bar_cache GROUP BY timeframe_min ORDER BY timeframe_min').fetchall()
    print('===== CACHE COUNTS =====',flush=True)
    for tf,n,pd in counts:
        print(f'TF {tf} ROWS {n} SYMBOL_DAYS {pd}',flush=True)
    print('DONE elapsed_min',round((time.time()-t0)/60,2),flush=True)
    c.close()

if __name__=='__main__': main()
