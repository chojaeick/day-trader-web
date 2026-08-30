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


def key(s): return str(s).zfill(6)

def raw_us(sym):
    con=sqlite3.connect(DB)
    q=pd.read_sql_query(
        "select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and interval_min=1 and session='REGULAR' order by et_time",
        con,params=(sym,))
    con.close()
    q['time']=pd.to_datetime(q.et_time,utc=True).dt.tz_convert(NY)
    for c in ['open','high','low','close','volume']:
        q[c]=pd.to_numeric(q[c],errors='coerce')
    return q[['time','open','high','low','close','volume']].dropna(subset=['time','open','high','low','close']).reset_index(drop=True)

def rsi_components(close,period=14):
    c=pd.to_numeric(close,errors='coerce').astype(float)
    d=c.diff(); gain=d.clip(lower=0.0); loss=-d.clip(upper=0.0)
    ag=gain.ewm(alpha=1.0/period,adjust=False,min_periods=period).mean()
    al=loss.ewm(alpha=1.0/period,adjust=False,min_periods=period).mean()
    rs=ag/al.mask(al==0.0,np.nan)
    rsi=(100.0-100.0/(1.0+rs)).astype(float)
    return pd.DataFrame({'close':c,'diff':d,'gain':gain,'loss':loss,'ag':ag,'al':al,'rs':rs,'rsi':rsi,'rsi_slope':rsi.diff()})

def main():
    with CORE.open('rb') as fh: core=pickle.load(fh)
    cfg=DoubleBollingerEngine5Config()
    eng=DoubleBollingerEngine5(cfg)
    rows=[]
    print('=== RSI SCALE MISMATCH ROOT-CAUSE DIAGNOSTIC ===')
    print('NO PERFORMANCE METRICS. USD vs USD*1400 SAME BARS ONLY.')
    for sym in SYMS:
        usd=raw_us(sym)
        krw=usd.copy()
        for c in ['open','high','low','close']: krw[c]=krw[c]*FX
        u5=to_5m(usd); k5=to_5m(krw)
        ue=eng.enrich(u5); ke=eng.enrich(k5)
        m=ue[['time','rsi','rsi_slope','gate_rsi_rising','gate_rsi_persistent','entry_gate','entry_signal']].merge(
            ke[['time','rsi','rsi_slope','gate_rsi_rising','gate_rsi_persistent','entry_gate','entry_signal']],on='time',suffixes=('_usd','_fx'))
        bad=m[(m.gate_rsi_rising_usd!=m.gate_rsi_rising_fx)|(m.gate_rsi_persistent_usd!=m.gate_rsi_persistent_fx)|(m.entry_gate_usd!=m.entry_gate_fx)|(m.entry_signal_usd!=m.entry_signal_fx)].copy()
        uc=rsi_components(u5.close,cfg.rsi_period); kc=rsi_components(k5.close,cfg.rsi_period)
        uc['time']=u5.time.values; kc['time']=k5.time.values
        print(f'{sym}: mismatches={len(bad)}')
        for _,r in bad.head(5).iterrows():
            ts=pd.Timestamp(r.time)
            i=u5.index[u5.time==ts]
            if len(i)==0: continue
            j=int(i[0])
            u=uc.iloc[j]; k=kc.iloc[j]
            prevu=uc.iloc[j-1] if j>0 else u; prevk=kc.iloc[j-1] if j>0 else k
            rows.append(dict(symbol=sym,time=ts,
                rsi_usd=u.rsi,rsi_fx=k.rsi,rsi_diff=float(k.rsi-u.rsi) if pd.notna(u.rsi) and pd.notna(k.rsi) else np.nan,
                slope_usd=u.rsi_slope,slope_fx=k.rsi_slope,
                prev_rsi_usd=prevu.rsi,prev_rsi_fx=prevk.rsi,
                ag_usd=u.ag,ag_fx=k.ag,ag_fx_over_usd=(k.ag/u.ag if pd.notna(u.ag) and u.ag!=0 else np.nan),
                al_usd=u.al,al_fx=k.al,al_fx_over_usd=(k.al/u.al if pd.notna(u.al) and u.al!=0 else np.nan),
                rising_usd=bool(r.gate_rsi_rising_usd),rising_fx=bool(r.gate_rsi_rising_fx),
                persistent_usd=bool(r.gate_rsi_persistent_usd),persistent_fx=bool(r.gate_rsi_persistent_fx),
                entry_gate_usd=bool(r.entry_gate_usd),entry_gate_fx=bool(r.entry_gate_fx),
                entry_signal_usd=bool(r.entry_signal_usd),entry_signal_fx=bool(r.entry_signal_fx)))
    out=pd.DataFrame(rows)
    path=CORE.parent/'rsi_scale_mismatch_detail.csv'; out.to_csv(path,index=False)
    print('\n=== FIRST MISMATCHES ===')
    if len(out):
        cols=['symbol','time','rsi_usd','rsi_fx','rsi_diff','slope_usd','slope_fx','ag_fx_over_usd','al_fx_over_usd','rising_usd','rising_fx','persistent_usd','persistent_fx','entry_gate_usd','entry_gate_fx']
        print(out[cols].head(30).to_string(index=False))
        maxdiff=pd.to_numeric(out.rsi_diff,errors='coerce').abs().max()
        print(f'rows={len(out)} max_abs_rsi_diff={maxdiff:.12g}')
    else:
        print('NO MISMATCHES')
    print('WROTE',path)

if __name__=='__main__': main()
