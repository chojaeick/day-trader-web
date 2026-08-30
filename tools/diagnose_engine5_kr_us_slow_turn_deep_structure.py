from __future__ import annotations

from pathlib import Path
import pandas as pd
import numpy as np

KR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation/integrated_slow_turn_rearm_deep_trades.csv')
KR_SIG = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation/integrated_slow_turn_rearm_deep_signals.csv')
US_TR = Path('/home/ubuntu/day-trader-api/engine5_v21e_fresh_validation/v21e_fresh_trades.csv')
US_SIG = Path('/home/ubuntu/day-trader-api/engine5_v21e_fresh_validation/v21e_fresh_signals.csv')
OUT = Path('/home/ubuntu/day-trader-api/engine5_v21e_fresh_validation/kr_us_slow_turn_deep_structure.csv')
FEE = 0.25


def n(x): return str(x).zfill(6)
def num(s): return pd.to_numeric(s, errors='coerce')

def load_kr():
    t = pd.read_csv(KR)
    s = pd.read_csv(KR_SIG)
    # current KR final comparison uses cut=-0.15
    if 'cut' in t.columns:
        t = t[t['cut'].astype(str).isin(['-0.15','-0.15000000000000002'])]
    if 'cut' in s.columns:
        s = s[s['cut'].astype(str).isin(['-0.15','-0.15000000000000002'])]
    t['symbol']=t.symbol.astype(str).str.zfill(6); s['symbol']=s.symbol.astype(str).str.zfill(6)
    t['entry_time']=pd.to_datetime(t.entry_time); s['entry_time']=pd.to_datetime(s.entry_time)
    t['net_pct']=num(t.pnl_pct)-FEE
    q=s[s.get('regime','').astype(str)=='DEEP_GT12'].copy()
    keep=[c for c in ['symbol','entry_time','ready_time','regime','zero_cross_bars','joint5_persistence','joint1_persistence','price_progress_1m_pct','close_progress_6m_pct','norm_mid_slope_pct','gap_delta_5m','rsi_slope_5m'] if c in q.columns]
    q=q[keep].drop_duplicates(['symbol','entry_time'])
    o=t.merge(q,on=['symbol','entry_time'],how='inner')
    o['market']='KR'; return o

def load_us():
    t=pd.read_csv(US_TR); s=pd.read_csv(US_SIG)
    t['symbol']=t.symbol.astype(str).str.zfill(6); s['symbol']=s.symbol.astype(str).str.zfill(6)
    t['entry_time']=pd.to_datetime(t.entry_time, utc=True); s['time']=pd.to_datetime(s.time, utc=True)
    t['net_pct']=num(t.pnl_pct)-FEE
    q=s[(s.source=='SLOW_TURN_E') & (s.get('regime','').astype(str)=='DEEP_GT12')].copy()
    q=q.rename(columns={'time':'entry_time'})
    keep=[c for c in ['symbol','entry_time','ready_time','regime','zero_cross_bars','joint5_persistence','joint1_persistence','price_progress_1m_pct','close_progress_6m_pct','norm_mid_slope_pct','gap_delta_5m','rsi_slope_5m'] if c in q.columns]
    q=q[keep].drop_duplicates(['symbol','entry_time'])
    o=t.merge(q,on=['symbol','entry_time'],how='inner')
    o['market']='US'; return o

def summary(g):
    return pd.Series({
        'trades':len(g), 'wins':int((g.net_pct>0).sum()), 'win_pct':float((g.net_pct>0).mean()*100) if len(g) else np.nan,
        'net_sum_pct':float(g.net_pct.sum()), 'median_net_pct':float(g.net_pct.median()),
        'median_zero_cross':float(num(g.get('zero_cross_bars',pd.Series(dtype=float))).median()),
        'median_joint5':float(num(g.get('joint5_persistence',pd.Series(dtype=float))).median()),
        'median_joint1':float(num(g.get('joint1_persistence',pd.Series(dtype=float))).median()),
        'median_price_prog':float(num(g.get('price_progress_1m_pct',pd.Series(dtype=float))).median()),
        'median_ext6':float(num(g.get('close_progress_6m_pct',pd.Series(dtype=float))).median()),
        'median_norm_mid':float(num(g.get('norm_mid_slope_pct',pd.Series(dtype=float))).median()),
        'median_gap_delta':float(num(g.get('gap_delta_5m',pd.Series(dtype=float))).median()),
        'median_rsi_slope':float(num(g.get('rsi_slope_5m',pd.Series(dtype=float))).median()),
    })

def main():
    kr=load_kr(); us=load_us(); x=pd.concat([kr,us],ignore_index=True,sort=False)
    x['result']=np.where(x.net_pct>0,'WIN','LOSS')
    print('=== KR vs US SLOW-TURN DEEP STRUCTURE ===')
    print('No retuning. Compares the same DEEP_GT12 family at realized entries.\n')
    rows=[]
    for m in ['KR','US']:
        for r in ['ALL','WIN','LOSS']:
            g=x[x.market==m] if r=='ALL' else x[(x.market==m)&(x.result==r)]
            z=summary(g).to_dict(); z.update(market=m,group=r); rows.append(z)
    sm=pd.DataFrame(rows)
    cols=['market','group','trades','wins','win_pct','net_sum_pct','median_net_pct','median_zero_cross','median_joint5','median_joint1','median_price_prog','median_ext6','median_norm_mid','median_gap_delta','median_rsi_slope']
    print(sm[cols].to_string(index=False,float_format=lambda v:f'{v:.4f}'))
    show=[c for c in ['market','symbol','entry_time','net_pct','zero_cross_bars','joint5_persistence','joint1_persistence','price_progress_1m_pct','close_progress_6m_pct','norm_mid_slope_pct','gap_delta_5m','rsi_slope_5m','reason'] if c in x.columns]
    print('\n=== KR DEEP CASES ===')
    print(x[x.market=='KR'][show].sort_values('net_pct',ascending=False).to_string(index=False,float_format=lambda v:f'{v:.4f}'))
    print('\n=== US DEEP BEST 8 ===')
    print(x[x.market=='US'][show].sort_values('net_pct',ascending=False).head(8).to_string(index=False,float_format=lambda v:f'{v:.4f}'))
    print('\n=== US DEEP WORST 12 ===')
    print(x[x.market=='US'][show].sort_values('net_pct').head(12).to_string(index=False,float_format=lambda v:f'{v:.4f}'))
    x.to_csv(OUT,index=False)
    print('\nWROTE',OUT)

if __name__=='__main__': main()
