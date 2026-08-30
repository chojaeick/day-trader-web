from __future__ import annotations

import pickle
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from live_server.double_bollinger_engine5 import DoubleBollingerEngine5, DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import to_5m

DB=Path('/home/ubuntu/day-trader-api/daytrader.db')
CORE=Path('/home/ubuntu/day-trader-api/engine5_us_kr_mapped_cache/us_kr_mapped_core.pkl')
FX=1400.0
NY='America/New_York'
SYMS=['SOXL','TQQQ','QQQ','NVDA','AMD','SMH','SPY','AVGO','PLTR']

BOOL_COLS=['trend_up','gate_trend_up','gate_macd_rising','gate_macd_accel','gate_macd_context','gate_rsi_rising','gate_rsi_persistent','entry_gate','entry_signal']
INVARIANT_COLS=['rsi','rsi_slope','rsi_accel','rsi_slope_strength','macd_slope_spread_strength','outer_width_ratio','volume_ratio','entry_score']
LINEAR_COLS=['macd','macd_signal','macd_gap','macd_gap_delta','macd_slope','macd_signal_slope','macd_slope_spread','mid','inner_upper','inner_lower','outer_upper','outer_lower','outer_width','mid_slope8']

def key(s): return str(s).zfill(6)

def close_enough(a,b,atol=1e-8,rtol=1e-8):
    a=np.asarray(a,dtype=float); b=np.asarray(b,dtype=float)
    m=np.isfinite(a)&np.isfinite(b)
    if not m.any(): return True,0.0
    d=np.abs(a[m]-b[m]); scale=np.maximum(np.abs(b[m]),1.0)
    ok=bool(np.all(d <= atol + rtol*scale))
    return ok,float(d.max()) if len(d) else 0.0

def load_db(sym):
    con=sqlite3.connect(DB)
    q=pd.read_sql_query("select trade_date,et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and interval_min=1 and session='REGULAR' order by trade_date,et_time",con,params=(sym,))
    con.close()
    if q.empty: return q
    q['time']=pd.to_datetime(q.et_time,utc=True).dt.tz_convert(NY)
    for c in ['open','high','low','close','volume']: q[c]=pd.to_numeric(q[c],errors='coerce')
    return q[['time','open','high','low','close','volume']].dropna(subset=['time','open','high','low','close']).sort_values('time').reset_index(drop=True)

def enrich(bars):
    cfg=DoubleBollingerEngine5Config()
    eng=DoubleBollingerEngine5(cfg)
    return eng.enrich(to_5m(bars.copy())).sort_values('time').reset_index(drop=True)

def main():
    print('=== ENGINE5 US DB TRANSFORM INVARIANCE AUDIT ===')
    print('NO PERFORMANCE METRICS. ONLY DB/CACHE/TIME/INDICATOR/GATE PARITY.')
    if not CORE.exists(): raise FileNotFoundError(CORE)
    with CORE.open('rb') as fh: core=pickle.load(fh)
    print(f"cache_shift={core.get('time_shift_minutes')} fx={core.get('fx')} symbols={len(core['raw'])}")
    if core.get('time_shift_minutes') not in (0,0.0,None): raise SystemExit('FAIL: cache clock shifted')

    failures=[]
    rows=[]
    for sym in SYMS:
        db=load_db(sym)
        ck=core['raw'][key(sym)].copy().sort_values('time').reset_index(drop=True)
        if len(db)!=len(ck):
            failures.append(f'{sym}: row_count db={len(db)} cache={len(ck)}'); continue
        time_same=bool((pd.to_datetime(db.time).astype(str).to_numpy()==pd.to_datetime(ck.time).astype(str).to_numpy()).all())
        price_max=0.0
        for c in ['open','high','low','close']:
            target=pd.to_numeric(db[c],errors='coerce').to_numpy(float)*FX
            got=pd.to_numeric(ck[c],errors='coerce').to_numpy(float)
            ok,mx=close_enough(got,target,atol=1e-5,rtol=1e-10); price_max=max(price_max,mx)
            if not ok: failures.append(f'{sym}: cache {c} != DB*FX max_abs={mx}')
        vok,vmx=close_enough(pd.to_numeric(ck.volume,errors='coerce'),pd.to_numeric(db.volume,errors='coerce'),atol=1e-8,rtol=1e-10)
        if not time_same: failures.append(f'{sym}: cache timestamps differ from DB ET')
        if not vok: failures.append(f'{sym}: volume changed max_abs={vmx}')

        usd=db.copy(); krw=db.copy()
        for c in ['open','high','low','close']: krw[c]=krw[c]*FX
        f_usd=enrich(usd); f_krw=enrich(krw)
        same5=len(f_usd)==len(f_krw) and (pd.to_datetime(f_usd.time).astype(str).to_numpy()==pd.to_datetime(f_krw.time).astype(str).to_numpy()).all()
        if not same5: failures.append(f'{sym}: 5m timestamps/count change after price scaling')

        inv_bad=[]; lin_bad=[]; bool_bad=[]
        for c in INVARIANT_COLS:
            if c in f_usd and c in f_krw:
                ok,mx=close_enough(pd.to_numeric(f_usd[c],errors='coerce'),pd.to_numeric(f_krw[c],errors='coerce'),atol=1e-7,rtol=1e-9)
                if not ok: inv_bad.append(f'{c}:{mx:.3g}')
        for c in LINEAR_COLS:
            if c in f_usd and c in f_krw:
                target=pd.to_numeric(f_usd[c],errors='coerce')*FX
                ok,mx=close_enough(pd.to_numeric(f_krw[c],errors='coerce'),target,atol=1e-4,rtol=1e-9)
                if not ok: lin_bad.append(f'{c}:{mx:.3g}')
        for c in BOOL_COLS:
            if c in f_usd and c in f_krw:
                a=f_usd[c].fillna(False).astype(bool).to_numpy(); b=f_krw[c].fillna(False).astype(bool).to_numpy()
                if not np.array_equal(a,b): bool_bad.append(f'{c}:{int((a!=b).sum())}')
        if inv_bad: failures.append(f'{sym}: invariant indicator mismatch '+','.join(inv_bad))
        if lin_bad: failures.append(f'{sym}: linear indicator scaling mismatch '+','.join(lin_bad))
        if bool_bad: failures.append(f'{sym}: gate mismatch '+','.join(bool_bad))

        rows.append(dict(symbol=sym,rows1=len(db),bars5=len(f_usd),time_same=time_same,cache_price_max_abs=price_max,volume_max_abs=vmx,invariant_bad=';'.join(inv_bad),linear_bad=';'.join(lin_bad),gate_bad=';'.join(bool_bad),first1=str(db.time.iloc[0]),last1=str(db.time.iloc[-1]),first5=str(f_usd.time.iloc[0]),last5=str(f_usd.time.iloc[-1])))
        print(f"{sym}: 1m={len(db)} 5m={len(f_usd)} time={'OK' if time_same else 'FAIL'} price*FX={'OK' if price_max<1e-4 else 'CHECK'} inv={'OK' if not inv_bad else 'FAIL'} linear={'OK' if not lin_bad else 'FAIL'} gates={'OK' if not bool_bad else 'FAIL'}")

    out=Path('/home/ubuntu/day-trader-api/engine5_us_kr_mapped_cache/us_db_transform_invariance.csv')
    pd.DataFrame(rows).to_csv(out,index=False)
    print('\n=== VERDICT ===')
    if failures:
        print(f'FAIL count={len(failures)}')
        for x in failures[:30]: print(' -',x)
        if len(failures)>30: print(f' ... {len(failures)-30} more in CSV/context')
    else:
        print('PASS: DB->cache time/price/volume mapping is exact, price scaling preserves RSI/relative features/gates, and MACD/price-linear features scale linearly.')
        print('If entries are still visually wrong, the fault is AFTER DB transformation: event construction / V20-V21 add-on gates / exit logic, not DB mapping.')
    print('WROTE',out)

if __name__=='__main__': main()
