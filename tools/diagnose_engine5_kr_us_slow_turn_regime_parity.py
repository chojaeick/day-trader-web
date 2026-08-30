from __future__ import annotations

"""Compare realized Slow-turn behavior between KR V21 and fresh US V21E by regime.

No retuning. No DB remap.
- KR: uses existing integrated_slow_turn_rearm_deep outputs at cut=-0.15.
- US: uses fresh SQLite/USD/ET V21E map + full-trade output.
- Prints regime-level realized trade counts / WR / net / PF and representative best/worst cases.

This is diagnostic only; it does not change KR or US strategy rules.
"""

import pickle
from pathlib import Path

import numpy as np
import pandas as pd

KR_ROOT = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
KR_TRADES = KR_ROOT / 'integrated_slow_turn_rearm_deep_trades.csv'
KR_SIGNALS = KR_ROOT / 'integrated_slow_turn_rearm_deep_signals.csv'

US_ROOT = Path('/home/ubuntu/day-trader-api/engine5_v21e_fresh_validation')
US_TRADES = US_ROOT / 'v21e_fresh_trades.csv'
US_MAP = US_ROOT / 'v21e_fresh_map.pkl'
OUT = US_ROOT / 'kr_us_slow_turn_regime_parity.csv'

CUT = -0.15
FEE = 0.25


def n(x): return str(x).zfill(6)

def parse_ts(s):
    # Mixed DST offsets are expected in US CSVs; normalize to UTC for matching only.
    return pd.to_datetime(s, utc=True, errors='coerce')

def pf(net):
    x = pd.to_numeric(net, errors='coerce').dropna()
    gp = float(x[x > 0].sum()) if len(x) else 0.0
    gl = float(-x[x < 0].sum()) if len(x) else 0.0
    return gp / gl if gl > 0 else np.inf

def summarize(market, df):
    rows=[]
    for regime, g in df.groupby('regime', dropna=False):
        net=pd.to_numeric(g.net_pct, errors='coerce').dropna()
        rows.append(dict(
            market=market, regime=str(regime), trades=len(net), wins=int((net>0).sum()),
            win_pct=float((net>0).mean()*100) if len(net) else 0.0,
            net_sum_pct=float(net.sum()) if len(net) else 0.0,
            avg_net_pct=float(net.mean()) if len(net) else 0.0,
            pf=float(pf(net)), max_loss_pct=float(net.min()) if len(net) else np.nan,
            median_net_pct=float(net.median()) if len(net) else np.nan,
        ))
    net=pd.to_numeric(df.net_pct, errors='coerce').dropna()
    rows.append(dict(
        market=market, regime='ALL_SLOW', trades=len(net), wins=int((net>0).sum()),
        win_pct=float((net>0).mean()*100) if len(net) else 0.0,
        net_sum_pct=float(net.sum()) if len(net) else 0.0,
        avg_net_pct=float(net.mean()) if len(net) else 0.0,
        pf=float(pf(net)), max_loss_pct=float(net.min()) if len(net) else np.nan,
        median_net_pct=float(net.median()) if len(net) else np.nan,
    ))
    return rows

def load_kr():
    if not KR_TRADES.exists(): raise FileNotFoundError(KR_TRADES)
    if not KR_SIGNALS.exists(): raise FileNotFoundError(KR_SIGNALS)
    tr=pd.read_csv(KR_TRADES)
    sg=pd.read_csv(KR_SIGNALS)
    if 'cut' in tr.columns:
        tr=tr[np.isclose(pd.to_numeric(tr.cut,errors='coerce'), CUT, equal_nan=False)].copy()
    if 'source' in tr.columns:
        tr=tr[tr.source.astype(str).eq('SLOW_TURN')].copy()
    if 'cut' in sg.columns:
        sg=sg[np.isclose(pd.to_numeric(sg.cut,errors='coerce'), CUT, equal_nan=False)].copy()
    tr['symbol']=tr.symbol.map(n); sg['symbol']=sg.symbol.map(n)
    tr['_t']=parse_ts(tr.entry_time); sg['_t']=parse_ts(sg.entry_time)
    tr['gross_pct']=pd.to_numeric(tr.pnl_pct,errors='coerce')
    tr['net_pct']=tr.gross_pct-FEE
    keep=[c for c in ['symbol','_t','regime','zero_cross_bars','joint5_persistence','joint1_persistence','price_progress_1m_pct','norm_mid_slope_pct','gap_delta_5m','rsi_slope_5m'] if c in sg.columns]
    meta=sg[keep].drop_duplicates(['symbol','_t'],keep='first')
    out=tr.merge(meta,on=['symbol','_t'],how='left')
    out['market']='KR'
    return out

def load_us():
    if not US_TRADES.exists(): raise FileNotFoundError(US_TRADES)
    if not US_MAP.exists(): raise FileNotFoundError(US_MAP)
    tr=pd.read_csv(US_TRADES)
    tr=tr[tr.source.astype(str).eq('SLOW_TURN_E')].copy()
    tr['symbol']=tr.symbol.map(n); tr['_t']=parse_ts(tr.entry_time)
    tr['gross_pct']=pd.to_numeric(tr.pnl_pct,errors='coerce')
    tr['net_pct']=tr.gross_pct-FEE
    with US_MAP.open('rb') as fh:d=pickle.load(fh)
    rows=[]
    for x in d.get('tags',[]):
        if x.get('source')!='SLOW_TURN_E': continue
        meta=x.get('meta') or {}
        rows.append(dict(symbol=n(x.get('symbol')), _t=parse_ts(pd.Series([x.get('time')])).iloc[0],
                         regime=meta.get('regime','UNKNOWN'),
                         norm_mid_slope_pct=meta.get('norm_mid_slope_pct',np.nan)))
    sm=pd.DataFrame(rows)
    if len(sm): sm=sm.drop_duplicates(['symbol','_t'],keep='first')
    out=tr.merge(sm,on=['symbol','_t'],how='left') if len(sm) else tr.assign(regime='UNKNOWN')
    out['market']='US'
    return out

def show_cases(label,df):
    cols=[c for c in ['symbol','entry_time','exit_time','regime','gross_pct','net_pct','reason','zero_cross_bars','joint5_persistence','joint1_persistence','price_progress_1m_pct','norm_mid_slope_pct','gap_delta_5m','rsi_slope_5m'] if c in df.columns]
    print(f'\n=== {label} BEST 6 ===')
    print(df.sort_values('net_pct',ascending=False)[cols].head(6).to_string(index=False,float_format=lambda x:f'{x:.4f}'))
    print(f'\n=== {label} WORST 6 ===')
    print(df.sort_values('net_pct',ascending=True)[cols].head(6).to_string(index=False,float_format=lambda x:f'{x:.4f}'))

def main():
    kr=load_kr(); us=load_us()
    rows=summarize('KR',kr)+summarize('US',us)
    s=pd.DataFrame(rows)
    order=['NEAR_LE1_5','MID_1_5_8','BOUNDARY_8_12','DEEP_GT12','ALL_SLOW']
    s['_o']=s.regime.map({k:i for i,k in enumerate(order)}).fillna(99)
    s=s.sort_values(['_o','market']).drop(columns='_o')
    print('=== KR vs US SLOW-TURN REGIME PARITY ===')
    print('No retuning. KR cut=-0.15; US fresh SQLite/USD/ET baseline.')
    print(s.to_string(index=False,float_format=lambda x:f'{x:.4f}'))
    show_cases('KR SLOW-TURN',kr)
    show_cases('US SLOW-TURN-E',us)
    s.to_csv(OUT,index=False)
    print('\nWROTE',OUT)

if __name__=='__main__': main()
