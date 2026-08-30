from __future__ import annotations

import sqlite3
from pathlib import Path
import pandas as pd

DB=Path('/home/ubuntu/day-trader-api/daytrader.db')
SYMS=['SOXL','AMD','NVDA']


def main():
    con=sqlite3.connect(DB)
    print('=== US DB DATE/TIMESTAMP SEMANTICS ===')
    print('Raw values only. No performance metrics. No writes.')
    for s in SYMS:
        q=pd.read_sql_query(
            "select symbol,trade_date,ts,et_time,session,open,high,low,close,volume "
            "from historical_minute_bars where symbol=? and interval_min=1 and session='REGULAR' "
            "order by trade_date,et_time limit 8",
            con,params=(s,))
        print(f'\n--- {s} FIRST 8 ---')
        print(q.to_string(index=False))

        # Also show rows around a day boundary from the first two distinct trade_date values.
        days=pd.read_sql_query(
            "select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 and session='REGULAR' order by trade_date limit 2",
            con,params=(s,))['trade_date'].astype(str).tolist()
        for d in days:
            z=pd.read_sql_query(
                "select symbol,trade_date,ts,et_time,open,high,low,close from historical_minute_bars "
                "where symbol=? and trade_date=? and interval_min=1 and session='REGULAR' order by et_time",
                con,params=(s,d))
            print(f'\n{s} trade_date={d} rows={len(z)}')
            if len(z):
                print('FIRST:',z.head(2).to_dict('records'))
                print('LAST :',z.tail(2).to_dict('records'))

    # Determine whether trade_date equals the calendar date represented by ET directly,
    # or whether one field is encoded in another timezone/date format.
    q=pd.read_sql_query(
        "select trade_date,ts,et_time from historical_minute_bars where interval_min=1 and session='REGULAR' order by trade_date,et_time limit 2000",
        con)
    con.close()
    td=pd.to_datetime(q.trade_date.astype(str),errors='coerce').dt.date
    et=pd.to_datetime(q.et_time,utc=True,errors='coerce').dt.tz_convert('America/New_York').dt.date
    ts_utc=pd.to_datetime(q.ts,utc=True,errors='coerce')
    ts_et=ts_utc.dt.tz_convert('America/New_York').dt.date
    print('\n=== PARSED RELATIONSHIP SAMPLE ===')
    print('rows=',len(q))
    print('trade_date == ET-date:',int((td==et).sum()),'/',len(q))
    print('trade_date == TS-as-ET-date:',int((td==ts_et).sum()),'/',len(q))
    print('et_time parse NaT=',int(pd.isna(pd.to_datetime(q.et_time,utc=True,errors='coerce')).sum()))
    print('ts parse NaT=',int(pd.isna(ts_utc).sum()))
    print('\nNOTE: If raw et_time strings already contain local ET wall-clock values but no UTC semantics, parsing with utc=True is wrong. This diagnostic exposes that directly.')

if __name__=='__main__': main()
