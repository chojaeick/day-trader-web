#!/usr/bin/env python3
from __future__ import annotations
import json, sqlite3, sys
from pathlib import Path
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

import pandas as pd
from live_server.analytics import ticks_to_bars

DB='/home/ubuntu/day-trader-api/daytrader.db'
SYMS=('SOXL','SOXS')


def main():
    print('=== V241 USA SOXL/SOXS LIVE DATA PREFLIGHT ===')
    print('READ_ONLY=YES ORDER=NONE FINDER=OFF SYMBOLS=SOXL,SOXS')
    con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
    try:
        for sym in SYMS:
            q=con.execute('SELECT * FROM quotes WHERE symbol=?',(sym,)).fetchone()
            ticks=[dict(r) for r in con.execute('SELECT symbol,price,qty,cum_volume,ts FROM ticks WHERE symbol=? ORDER BY ts DESC LIMIT 5000',(sym,)).fetchall()]
            ticks=list(reversed(ticks))
            b1=ticks_to_bars(ticks,1); b5=ticks_to_bars(ticks,5)
            latest_tick=ticks[-1] if ticks else None
            age=None
            if latest_tick:
                try:
                    ts=pd.to_datetime(latest_tick['ts'],utc=True)
                    age=(pd.Timestamp.now(tz='UTC')-ts).total_seconds()
                except Exception: pass
            out={
                'symbol':sym,
                'quote':dict(q) if q else None,
                'ticks':len(ticks),
                'bars1':len(b1),
                'bars5':len(b5),
                'latest_tick':latest_tick,
                'latest_tick_age_sec':age,
                'last_1m':(b1.tail(1).to_dict('records')[0] if len(b1) else None),
                'last_5m':(b5.tail(1).to_dict('records')[0] if len(b5) else None),
            }
            print(json.dumps(out,ensure_ascii=False,default=str))
    finally:
        con.close()
    print('V241_DONE=YES')

if __name__=='__main__': main()
