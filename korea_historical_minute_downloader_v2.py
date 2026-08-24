#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from live_server.config import Settings
from live_server.db import DB
from live_server.kiwoom import KiwoomClient

KST_OFFSET = '+09:00'
API_ID = 'ka10080'
API_PATH = '/api/dostk/chart'


def num(v):
    if v is None:
        return 0.0
    s = str(v).strip().replace(',', '').lstrip('+')
    try:
        return abs(float(s))
    except Exception:
        return 0.0


def ensure_table(con):
    con.execute('''
    CREATE TABLE IF NOT EXISTS historical_minute_bars (
      symbol TEXT NOT NULL,
      exchange TEXT,
      trade_date TEXT NOT NULL,
      interval_min INTEGER NOT NULL,
      ts TEXT NOT NULL,
      et_time TEXT,
      session TEXT,
      open REAL,
      high REAL,
      low REAL,
      close REAL,
      volume REAL,
      source TEXT,
      fetched_at TEXT
    )
    ''')


def has_unique_key(con):
    cols = ['symbol', 'trade_date', 'interval_min', 'ts']
    for row in con.execute("PRAGMA index_list('historical_minute_bars')").fetchall():
        idx_name = row[1]
        if not row[2]:
            continue
        idx_cols = [r[2] for r in con.execute(f"PRAGMA index_info('{idx_name}')").fetchall()]
        if idx_cols == cols:
            return True
    return False


def save_rows(con, rows, symbol, target, interval):
    ensure_table(con)
    if has_unique_key(con):
        sql = '''
        INSERT INTO historical_minute_bars
        (symbol,exchange,trade_date,interval_min,ts,et_time,session,open,high,low,close,volume,source,fetched_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(symbol,trade_date,interval_min,ts) DO UPDATE SET
          exchange=excluded.exchange, et_time=excluded.et_time, session=excluded.session,
          open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
          volume=excluded.volume, source=excluded.source, fetched_at=excluded.fetched_at
        '''
        con.executemany(sql, rows)
    else:
        # Existing production DB may predate the UNIQUE/PK definition.
        # Replace only this symbol/date/interval slice, preserving every other row.
        con.execute(
            'DELETE FROM historical_minute_bars WHERE symbol=? AND trade_date=? AND interval_min=?',
            (symbol, target, interval),
        )
        sql = '''
        INSERT INTO historical_minute_bars
        (symbol,exchange,trade_date,interval_min,ts,et_time,session,open,high,low,close,volume,source,fetched_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        '''
        con.executemany(sql, rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('symbol')
    ap.add_argument('date', help='YYYYMMDD')
    ap.add_argument('--interval', type=int, default=1)
    ap.add_argument('--db', default='daytrader.db')
    ap.add_argument('--max-pages', type=int, default=30)
    args = ap.parse_args()

    symbol = args.symbol.strip()
    target = args.date.strip()
    if len(symbol) != 6 or not symbol.isdigit():
        raise SystemExit('KRX symbol must be six digits')
    if len(target) != 8 or not target.isdigit():
        raise SystemExit('date must be YYYYMMDD')
    if args.interval not in (1,3,5,10,15,30,45,60):
        raise SystemExit('unsupported interval')

    settings = Settings()
    client = KiwoomClient(settings, DB(args.db))
    token = client.get_token()

    body = {
        'stk_cd': symbol,
        'tic_scope': str(args.interval),
        'upd_stkpc_tp': '1',
        'base_dt': target,
    }

    cont_yn = None
    next_key = None
    collected = {}
    fetched_at = datetime.now(timezone.utc).isoformat()

    print(f'SYMBOL: {symbol} DATE: {target} EXCHANGE: KRX API: {API_ID}')

    for page in range(1, args.max_pages + 1):
        headers = {
            'authorization': f'Bearer {token}',
            'api-id': API_ID,
            'Content-Type': 'application/json;charset=UTF-8',
        }
        if cont_yn == 'Y' and next_key:
            headers['cont-yn'] = cont_yn
            headers['next-key'] = next_key

        r = requests.post(settings.rest_base + API_PATH, headers=headers, json=body, timeout=30)
        r.raise_for_status()
        d = r.json()
        if d.get('return_code') not in (None, 0):
            raise RuntimeError(f"PAGE {page}: {d.get('return_code')} {d.get('return_msg')}")

        rows = d.get('stk_min_pole_chart_qry') or []
        page_target = 0
        older_seen = False
        for x in rows:
            ct = str(x.get('cntr_tm') or '').strip()
            if len(ct) < 12:
                continue
            row_date = ct[:8]
            if row_date < target:
                older_seen = True
            if row_date != target:
                continue
            hhmmss = (ct[8:14] + '000000')[:6]
            hh, mm, ss = hhmmss[:2], hhmmss[2:4], hhmmss[4:6]
            ts = f'{target[:4]}-{target[4:6]}-{target[6:8]}T{hh}:{mm}:{ss}{KST_OFFSET}'
            collected[ts] = (
                symbol, 'KRX', target, args.interval, ts, f'{hh}:{mm}:{ss}', 'REGULAR',
                num(x.get('open_pric')), num(x.get('high_pric')), num(x.get('low_pric')),
                num(x.get('cur_prc')), num(x.get('trde_qty')), 'kiwoom_ka10080', fetched_at,
            )
            page_target += 1

        print(f'PAGE {page} ROWS={len(rows)} TARGET_ROWS={page_target} TOTAL_TARGET={len(collected)}')

        cont_yn = r.headers.get('cont-yn') or r.headers.get('Cont-Yn')
        next_key = r.headers.get('next-key') or r.headers.get('Next-Key')
        if older_seen and collected:
            break
        if cont_yn != 'Y' or not next_key:
            break
        time.sleep(0.25)

    if not collected:
        raise RuntimeError(f'NO_ROWS_FOR_DATE symbol={symbol} date={target}')

    rows = [collected[k] for k in sorted(collected)]
    con = sqlite3.connect(str(Path(args.db)))
    try:
        save_rows(con, rows, symbol, target, args.interval)
        con.commit()
        n = con.execute(
            'SELECT COUNT(*) FROM historical_minute_bars WHERE symbol=? AND trade_date=? AND interval_min=?',
            (symbol, target, args.interval)
        ).fetchone()[0]
        uniq = con.execute(
            'SELECT COUNT(DISTINCT ts) FROM historical_minute_bars WHERE symbol=? AND trade_date=? AND interval_min=?',
            (symbol, target, args.interval)
        ).fetchone()[0]
    finally:
        con.close()

    print(f'KRX_MINUTE_DOWNLOAD_OK SYMBOL={symbol} DATE={target} SAVED_ROWS={n} UNIQUE_TS={uniq}')


if __name__ == '__main__':
    main()
