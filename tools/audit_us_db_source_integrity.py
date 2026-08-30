from __future__ import annotations

import pickle
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

DB = Path('/home/ubuntu/day-trader-api/daytrader.db')
E_CORE = Path('/home/ubuntu/day-trader-api/engine5_us_e_cache/us_e_core.pkl')
OLD_CORE = Path('/home/ubuntu/day-trader-api/engine5_us_kr_mapped_cache/us_kr_mapped_core.pkl')
NY = 'America/New_York'
SYMS = ['SOXL','TQQQ','QQQ','NVDA','AMD','SMH','SPY','AVGO','PLTR']


def load_db():
    con = sqlite3.connect(DB)
    q = pd.read_sql_query(
        "select symbol,exchange,trade_date,interval_min,ts,et_time,session,open,high,low,close,volume,source "
        "from historical_minute_bars where interval_min=1 and session='REGULAR' and symbol in (%s) "
        "order by symbol,trade_date,et_time" % ','.join('?'*len(SYMS)),
        con, params=SYMS
    )
    con.close()
    return q


def audit_db(q):
    issues=[]
    q=q.copy()
    for c in ['open','high','low','close','volume']:
        q[c]=pd.to_numeric(q[c],errors='coerce')
    et=pd.to_datetime(q['et_time'],utc=True,errors='coerce').dt.tz_convert(NY)
    q['_et']=et
    q['_day']=q['_et'].dt.strftime('%Y-%m-%d')
    q['_hhmm']=q['_et'].dt.strftime('%H:%M')

    print('=== RAW DB AUDIT ===')
    print('rows=',len(q),'symbols=',q.symbol.nunique(),'source=',q.source.value_counts().to_dict())
    print('sessions=',q.session.value_counts().to_dict())
    print('price range=',float(q.close.min()),'..',float(q.close.max()))

    bad_trade_date=(q.trade_date.astype(str)!=q['_day'])
    print('trade_date_vs_et_mismatch=',int(bad_trade_date.sum()))
    if bad_trade_date.any(): issues.append(f'trade_date mismatch {int(bad_trade_date.sum())}')

    bad_ohlc=(q.low>q.high)|(q.open<q.low)|(q.open>q.high)|(q.close<q.low)|(q.close>q.high)
    print('bad_ohlc=',int(bad_ohlc.sum()))
    if bad_ohlc.any(): issues.append(f'bad_ohlc {int(bad_ohlc.sum())}')

    dup=q.duplicated(['symbol','trade_date','et_time']).sum()
    print('duplicates=',int(dup))
    if dup: issues.append(f'duplicates {int(dup)}')

    per=[]
    for (s,d),g in q.groupby(['symbol','_day']):
        g=g.sort_values('_et')
        dif=g._et.diff().dropna().dt.total_seconds()/60
        per.append(dict(symbol=s,day=d,rows=len(g),first=str(g._et.iloc[0]),last=str(g._et.iloc[-1]),
                        non1=int((dif!=1).sum()),min_close=float(g.close.min()),max_close=float(g.close.max())))
    p=pd.DataFrame(per)
    print('day-symbol=',len(p),'full390=',int((p.rows==390).sum()),'minrows=',int(p.rows.min()),'maxrows=',int(p.rows.max()))
    print('first unique=',p['first'].str.extract(r'(\d\d:\d\d:\d\d)')[0].value_counts().head().to_dict())
    print('last unique=',p['last'].str.extract(r'(\d\d:\d\d:\d\d)')[0].value_counts().head().to_dict())
    print('non1min_gap_segments=',int(p.non1.sum()))
    if not (p.rows==390).all(): issues.append('not all day-symbols have 390 rows')
    if p.non1.sum(): issues.append(f'non1min gaps {int(p.non1.sum())}')

    # Strong sanity check: US regular session ET should be exactly 09:30..15:59.
    bad_session=((q['_hhmm']<'09:30')|(q['_hhmm']>'15:59'))
    print('outside_0930_1559=',int(bad_session.sum()))
    if bad_session.any(): issues.append(f'outside regular ET {int(bad_session.sum())}')

    out=Path('/home/ubuntu/day-trader-api/engine5_us_e_cache/us_db_raw_integrity_by_day.csv')
    out.parent.mkdir(parents=True,exist_ok=True)
    p.to_csv(out,index=False)
    return q,issues


def compare_e_cache(q):
    print('\n=== DB -> E CACHE EXACT PARITY ===')
    if not E_CORE.exists():
        print('E cache not ready yet:',E_CORE); return ['E cache missing']
    with E_CORE.open('rb') as fh: d=pickle.load(fh)
    raw=d['raw']
    issues=[]
    total=0
    for s in SYMS:
        k=str(s).zfill(6)
        if k not in raw: issues.append(f'{s} missing in E cache'); continue
        db=q[q.symbol==s].copy().sort_values('_et').reset_index(drop=True)
        ec=raw[k].copy().sort_values('time').reset_index(drop=True)
        dbt=pd.to_datetime(db['_et'])
        ect=pd.to_datetime(ec['time'])
        time_ok=len(db)==len(ec) and dbt.reset_index(drop=True).equals(ect.reset_index(drop=True))
        vals=[]
        for c in ['open','high','low','close','volume']:
            a=pd.to_numeric(db[c],errors='coerce').to_numpy(float)
            b=pd.to_numeric(ec[c],errors='coerce').to_numpy(float)
            vals.append(len(a)==len(b) and np.allclose(a,b,rtol=0,atol=1e-12,equal_nan=True))
        print(f'{s}: rows_db={len(db)} rows_e={len(ec)} time={"OK" if time_ok else "FAIL"} ohlcv={"OK" if all(vals) else "FAIL"}')
        total+=len(db)
        if not time_ok: issues.append(f'{s} time mismatch')
        if not all(vals): issues.append(f'{s} OHLCV mismatch')
    print('checked_rows=',total)
    return issues


def inspect_old_cache_only():
    print('\n=== OLD MAPPED CACHE FORENSICS ===')
    if not OLD_CORE.exists():
        print('old mapped cache absent'); return
    with OLD_CORE.open('rb') as fh:d=pickle.load(fh)
    print('old cache schema=',d.get('cache_schema'),'fx=',d.get('fx'),'time_shift_minutes=',d.get('time_shift_minutes'),'session=',d.get('session'))
    print('NOTE: this is a pickle cache. This audit does not write to SQLite DB.')


def main():
    if not DB.exists(): raise FileNotFoundError(DB)
    q=load_db()
    q,issues=audit_db(q)
    issues += compare_e_cache(q)
    inspect_old_cache_only()
    print('\n=== VERDICT ===')
    if issues:
        print('FAIL / NOT PROVEN')
        for x in issues: print(' -',x)
    else:
        print('PASS: SQLite US REGULAR DB is internally consistent and E cache is byte-semantic equivalent for time/OHLCV.')
        print('This proves our earlier time/FX experiments affected derived caches, not the SQLite source rows.')
    print('\nEXTERNAL PRICE TRUTH CHECK STILL REQUIRED: compare selected DB rows against an independent market-data source.')

if __name__=='__main__': main()
