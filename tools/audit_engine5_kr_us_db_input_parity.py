from __future__ import annotations

"""Audit whether KR and US minute data are equivalent inputs for Engine5.

This is a DATA audit, not a strategy backtest.
It compares the raw SQLite representation and the exact DataFrames handed to Engine5.
No strategy threshold or cache is changed.
"""

import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd

from tools.backtest_dbb_kr_v2_v21_v22 import load_data as load_kr
from tools.build_engine5_us_oos_cache import load_us, DEFAULT_SYMBOLS
from tools.backtest_dbb_engine5_tuner import to_5m

DB = Path('/home/ubuntu/day-trader-api/daytrader.db')
OUT = Path('/home/ubuntu/day-trader-api/engine5_us_oos_cache')
KR_SOURCE = 'kiwoom_ka10080'
US_SYMBOLS = DEFAULT_SYMBOLS


def q(con, sql, params=()):
    return pd.read_sql_query(sql, con, params=params)


def fmt_ts(x):
    if pd.isna(x): return 'NA'
    return str(x)


def engine_stats(market: str, raw: dict, expected_open: str, expected_close: str):
    per_day=[]; bad_ohlc=0; dup=0; null_volume=0; neg_volume=0
    five_rows=[]
    for sym,b in raw.items():
        x=b.copy(); x['time']=pd.to_datetime(x.time)
        dup += int(x.time.duplicated().sum())
        o=pd.to_numeric(x.open,errors='coerce'); h=pd.to_numeric(x.high,errors='coerce'); l=pd.to_numeric(x.low,errors='coerce'); c=pd.to_numeric(x.close,errors='coerce')
        bad_ohlc += int(((h < pd.concat([o,c,l],axis=1).max(axis=1)) | (l > pd.concat([o,c,h],axis=1).min(axis=1))).sum())
        v=pd.to_numeric(x.volume,errors='coerce') if 'volume' in x else pd.Series(np.nan,index=x.index)
        null_volume += int(v.isna().sum()); neg_volume += int((v<0).sum())
        local_date=x.time.dt.date
        for day,g in x.groupby(local_date):
            s=g.time.sort_values()
            dif=s.diff().dropna().dt.total_seconds().div(60)
            missing=max(0,390-len(g))
            non1=int((dif!=1).sum())
            per_day.append(dict(market=market,symbol=str(sym),day=str(day),rows=len(g),first=fmt_ts(s.iloc[0]),last=fmt_ts(s.iloc[-1]),missing_vs_390=missing,non_1min_gaps=non1))
        f5=to_5m(x)
        if len(f5):
            f5['time']=pd.to_datetime(f5.time)
            for day,g in f5.groupby(f5.time.dt.date):
                five_rows.append(dict(market=market,symbol=str(sym),day=str(day),bars5=len(g),first5=fmt_ts(g.time.min()),last5=fmt_ts(g.time.max())))
    d=pd.DataFrame(per_day); f=pd.DataFrame(five_rows)
    summary=dict(
        market=market, symbols=len(raw), rows=sum(len(x) for x in raw.values()),
        days=int(d.day.nunique()) if len(d) else 0,
        median_1m_rows=float(d.rows.median()) if len(d) else np.nan,
        min_1m_rows=int(d.rows.min()) if len(d) else 0,
        max_1m_rows=int(d.rows.max()) if len(d) else 0,
        full_390_days=int((d.rows==390).sum()) if len(d) else 0,
        day_symbol_pairs=len(d), duplicate_times=dup, non_1min_gap_segments=int(d.non_1min_gaps.sum()) if len(d) else 0,
        bad_ohlc=bad_ohlc, null_volume=null_volume, negative_volume=neg_volume,
        median_5m_bars=float(f.bars5.median()) if len(f) else np.nan,
        full_78_5m_days=int((f.bars5==78).sum()) if len(f) else 0,
        expected_session=f'{expected_open}-{expected_close}',
    )
    return summary,d,f


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(DB)
    schema=q(con,"PRAGMA table_info(historical_minute_bars)")
    print('=== KR vs US DB INPUT PARITY AUDIT ===')
    print('DATA ONLY. NO STRATEGY / NO THRESHOLD CHANGES.')
    print('\n[SCHEMA] historical_minute_bars')
    print(schema[['name','type','notnull','pk']].to_string(index=False))

    src=q(con,"select source, session, interval_min, count(*) rows, count(distinct symbol) symbols from historical_minute_bars group by source,session,interval_min order by rows desc")
    print('\n[RAW DB SOURCES]')
    print(src.to_string(index=False))

    kr_raw_meta=q(con,"select count(*) rows,count(distinct symbol) symbols,min(ts) min_ts,max(ts) max_ts from historical_minute_bars where source=? and interval_min=1",(KR_SOURCE,))
    us_raw_meta=q(con,"select count(*) rows,count(distinct symbol) symbols,min(et_time) min_et,max(et_time) max_et from historical_minute_bars where interval_min=1 and session='REGULAR' and symbol in (%s)" % ','.join('?'*len(US_SYMBOLS)),tuple(US_SYMBOLS))
    print('\n[RAW DB COVERAGE]')
    print('KR',kr_raw_meta.to_dict('records')[0])
    print('US',us_raw_meta.to_dict('records')[0])
    con.close()

    print('\n[LOAD EXACT ENGINE INPUTS]')
    kr={str(k).zfill(6):v for k,v in load_kr().items()}
    us=load_us(DB,US_SYMBOLS)
    ks,kd,k5=engine_stats('KR',kr,'09:00','15:30')
    uss,ud,u5=engine_stats('US',us,'09:30','16:00')
    summ=pd.DataFrame([ks,uss])
    print('\n[ENGINE INPUT SUMMARY]')
    print(summ.to_string(index=False))

    def timing(label,d):
        if d.empty:return
        first=pd.to_datetime(d['first'],errors='coerce',utc=True)
        last=pd.to_datetime(d['last'],errors='coerce',utc=True)
        print(f"{label}: rows/day median={d.rows.median():.1f} min={d.rows.min()} max={d.rows.max()} | 390-row day-symbol={int((d.rows==390).sum())}/{len(d)} | non1min gaps={int(d.non_1min_gaps.sum())}")
        print(f"{label}: sample first={d.iloc[0]['first']} last={d.iloc[0]['last']}")
    print('\n[SESSION SHAPE]')
    timing('KR',kd); timing('US',ud)

    print('\n[5M SHAPE]')
    print(f"KR median bars/day={k5.bars5.median() if len(k5) else np.nan} full78={int((k5.bars5==78).sum()) if len(k5) else 0}/{len(k5)}")
    print(f"US median bars/day={u5.bars5.median() if len(u5) else np.nan} full78={int((u5.bars5==78).sum()) if len(u5) else 0}/{len(u5)}")
    if len(k5): print('KR sample',k5.iloc[0].to_dict())
    if len(u5): print('US sample',u5.iloc[0].to_dict())

    # Structural verdict only. Different date spans / symbols are allowed; bar semantics are what matter.
    hard=[]
    if ks['duplicate_times'] or uss['duplicate_times']: hard.append('duplicate timestamps')
    if ks['bad_ohlc'] or uss['bad_ohlc']: hard.append('invalid OHLC relationships')
    if ks['median_1m_rows']!=390 or uss['median_1m_rows']!=390: hard.append('median session is not 390 one-minute bars')
    if ks['median_5m_bars']!=78 or uss['median_5m_bars']!=78: hard.append('median session is not 78 completed 5m bars')
    if ks['non_1min_gap_segments'] or uss['non_1min_gap_segments']: hard.append('minute gaps exist; inspect day CSV')
    print('\n[VERDICT]')
    if hard:
        print('PARITY_NOT_PROVEN:', '; '.join(hard))
        print('Do NOT interpret KR-vs-US engine performance until these data differences are resolved or intentionally normalized.')
    else:
        print('BASIC_BAR_PARITY_PASS: both loaders produce contiguous 390x1m sessions and 78x5m sessions with valid OHLC.')
        print('Next compare timestamp LABEL semantics and indicator distributions on equivalent session-relative bars.')

    schema.to_csv(OUT/'kr_us_db_schema.csv',index=False)
    src.to_csv(OUT/'kr_us_db_sources.csv',index=False)
    summ.to_csv(OUT/'kr_us_engine_input_summary.csv',index=False)
    pd.concat([kd,ud],ignore_index=True).to_csv(OUT/'kr_us_1m_day_audit.csv',index=False)
    pd.concat([k5,u5],ignore_index=True).to_csv(OUT/'kr_us_5m_day_audit.csv',index=False)
    print('WROTE',OUT/'kr_us_engine_input_summary.csv')
    print('WROTE',OUT/'kr_us_1m_day_audit.csv')
    print('WROTE',OUT/'kr_us_5m_day_audit.csv')

if __name__=='__main__': main()
