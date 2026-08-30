from __future__ import annotations
import pickle
from pathlib import Path
import pandas as pd

ROOT=Path('/home/ubuntu/day-trader-api/engine5_us_kr_mapped_cache')
CORE=ROOT/'us_kr_mapped_core.pkl'
TRADES=ROOT/'us_kr_mapped_all_versions_trades.csv'
FX=1400.0
TARGET_VARIANT='V21_-0.15'
TARGET_SYMBOL='000AMD'
TARGET_ENTRY='2026-05-11 10:55:00-04:00'


def row_at(df, ts):
    q=df[df.time<=ts]
    return None if q.empty else q.iloc[-1]


def main():
    with CORE.open('rb') as fh:d=pickle.load(fh)
    raw=d['raw']; scored=d['scored']; strength=d['strength']; micros=d['micros']
    t=pd.read_csv(TRADES)
    t=t[(t.variant.astype(str)==TARGET_VARIANT)&(t.symbol.astype(str).str.zfill(6)==TARGET_SYMBOL)]
    t['entry_time']=pd.to_datetime(t.entry_time)
    q=t[t.entry_time==pd.Timestamp(TARGET_ENTRY)]
    if q.empty: raise SystemExit('target trade not found')
    tr=q.iloc[0]
    ts=pd.Timestamp(tr.entry_time)
    actual_et=ts+pd.Timedelta(minutes=30)
    print('=== TARGET TRADE ===')
    print(f"symbol={TARGET_SYMBOL} mapped_entry={ts} actual_ET={actual_et}")
    print(f"mapped_entry_price={tr.entry_price} actual_USD={float(tr.entry_price)/FX:.6f} exit={tr.exit_price} actual_exit_USD={float(tr.exit_price)/FX:.6f} pnl={tr.pnl_pct:.6f}% source={tr.source} reason={tr.reason}")

    s=strength[TARGET_SYMBOL].copy(); s['time']=pd.to_datetime(s.time)
    m=micros[TARGET_SYMBOL].copy(); m['time']=pd.to_datetime(m.time)
    r5=row_at(s,ts); r1=row_at(m,ts)
    print('\n=== 5M ROW USED BY ENGINE ===')
    cols5=['time','close','rsi','rsi_slope','macd','macd_signal','macd_gap','macd_gap_delta','macd_slope','macd_signal_slope','macd_strength_raw','macd_strength_rel','macd_strength_baseline','mid','mid_slope8','trend_up','entry_score']
    print(pd.DataFrame([{c:r5.get(c,None) for c in cols5}]).to_string(index=False))
    print('\n=== 1M ROW AT ENTRY ===')
    cols1=['time','close','rsi_1m','rsi_slope_1m','macd_1m','signal_1m','macd_gap_1m','macd_gap_delta_1m','macd_slope_1m','spread_1m','vol_ratio20']
    print(pd.DataFrame([{c:r1.get(c,None) for c in cols1}]).to_string(index=False))

    print('\n=== 5M CONTEXT -30m..+15m ===')
    z=s[(s.time>=ts-pd.Timedelta(minutes=30))&(s.time<=ts+pd.Timedelta(minutes=15))].copy()
    show5=[c for c in ['time','close','rsi','rsi_slope','macd','macd_signal','macd_gap','macd_gap_delta','macd_strength_raw','macd_strength_rel','mid_slope8','trend_up','entry_score'] if c in z.columns]
    print(z[show5].to_string(index=False))

    print('\n=== 1M CONTEXT -10m..+10m ===')
    z1=m[(m.time>=ts-pd.Timedelta(minutes=10))&(m.time<=ts+pd.Timedelta(minutes=10))].copy()
    show1=[c for c in cols1 if c in z1.columns]
    print(z1[show1].to_string(index=False))

if __name__=='__main__':main()
