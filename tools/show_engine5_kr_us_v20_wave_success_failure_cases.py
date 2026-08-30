from __future__ import annotations

"""Compare representative KR V20 and US V20E wave shapes around realized entries.

Purpose:
- Do NOT retune.
- Show pre/post 5m wave context for representative winners and losers.
- Help distinguish mapping/semantic issues from genuine market-shape differences.
"""

from pathlib import Path
import pickle
import numpy as np
import pandas as pd

import tools.validate_engine5_v20_macd_strength as ms
import tools.validate_engine5_v20_regime_transition as rt
import tools.validate_engine5_integrated_full_history as integ
import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data
from dataclasses import replace

ROOT = Path('/home/ubuntu/day-trader-api/engine5_v21e_fresh_validation')
US_MAP = ROOT / 'v21e_fresh_map.pkl'
US_TRADES = ROOT / 'v21e_fresh_trades.csv'
OUT = ROOT / 'kr_us_v20_wave_cases.csv'


def n(x): return str(x).zfill(6)
def num(x):
    try:
        y=float(x); return y if np.isfinite(y) else np.nan
    except Exception: return np.nan


def build_kr():
    raw={n(k):v for k,v in load_data().items()}
    cfg0=DoubleBollingerEngine5Config()
    cfg=replace(cfg0,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    frames0=base.build_cfg_frames(raw,cfg)
    f10={n(s):v10._refine_entry_frame(f) for s,f in frames0.items()}
    scored={n(s):f for s,f in reweight(f10,cfg,0.0).items()}
    strength={s:ms.add_strength(f) for s,f in scored.items()}
    completed={s:rt.add_completed_strength(f) for s,f in scored.items()}
    tagged=integ.build_sources(raw,cfg,scored,strength,completed,{s:None for s in raw})
    # build_sources needs real micros in current repo; fallback to direct historical output if available is unsafe.
    return raw, scored, strength


def load_us():
    with US_MAP.open('rb') as fh: d=pickle.load(fh)
    return d['raw'], d['scored'], d['strength']


def wave_rows(market,sym,entry_ts,scored,strength,net_pct,label):
    f=scored[n(sym)].copy(); f['time']=pd.to_datetime(f.time)
    s=strength[n(sym)].copy(); s['time']=pd.to_datetime(s.time)
    t=pd.Timestamp(entry_ts)
    q=f[(f.time>=t-pd.Timedelta(minutes=30))&(f.time<=t+pd.Timedelta(minutes=30))].copy()
    if q.empty: return []
    q=q.merge(s[['time','macd_strength_raw','macd_strength_rel']],on='time',how='left',suffixes=('','_s'))
    out=[]
    for _,r in q.iterrows():
        gap=num(r.get('macd_gap'))
        px=num(r.get('close'))
        raw=num(r.get('macd_strength_raw'))
        out.append(dict(
            market=market,case=label,symbol=n(sym),entry_time=t,time=pd.Timestamp(r.time),
            offset_min=int((pd.Timestamp(r.time)-t).total_seconds()/60),net_pct=float(net_pct),
            close=px,trend_up=bool(r.get('trend_up',False)),macd=num(r.get('macd')),
            signal=num(r.get('macd_signal')),gap=gap,gap_delta=num(r.get('macd_gap_delta')),
            rsi=num(r.get('rsi')),rsi_slope=num(r.get('rsi_slope')),
            strength_bps=(raw/px*10000.0 if np.isfinite(raw) and np.isfinite(px) and px!=0 else np.nan),
            strength_rel=num(r.get('macd_strength_rel')),
        ))
    return out


def pick_cases(tr,source):
    q=tr[tr.source==source].copy()
    q['net_pct']=pd.to_numeric(q.pnl_pct,errors='coerce')-0.25
    q=q.dropna(subset=['net_pct'])
    wins=q[q.net_pct>0].nlargest(2,'net_pct')
    losses=q[q.net_pct<=0].nsmallest(2,'net_pct')
    return [('WIN',r) for _,r in wins.iterrows()]+[('LOSS',r) for _,r in losses.iterrows()]


def main():
    print('=== KR vs US V20 WAVE SUCCESS/FAILURE CASES ===')
    print('Shows 5m state from -30m to +30m around representative realized entries.')
    print('No retuning. No market-difference conclusion is assumed.\n')

    # US cases from fresh baseline.
    us_raw,us_scored,us_strength=load_us()
    utr=pd.read_csv(US_TRADES)
    utr['entry_time']=pd.to_datetime(utr.entry_time,utc=True)
    # restore ET display timezone for matching against scored timestamps
    utr['entry_time']=utr.entry_time.dt.tz_convert('America/New_York')
    rows=[]
    for typ,r in pick_cases(utr,'V20E'):
        rows.extend(wave_rows('US_V20E',r.symbol,r.entry_time,us_scored,us_strength,r.net_pct,typ))

    # KR representative trades: rebuild exact V20 and simulate.
    raw={n(k):v for k,v in load_data().items()}
    cfg0=DoubleBollingerEngine5Config(); cfg=replace(cfg0,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    packed=v8.base.pack_exit_events(raw,cfg0)
    states=base.pack_state_events(base.build_cfg_frames(raw,cfg0))
    frames0=base.build_cfg_frames(raw,cfg)
    f10={n(s):v10._refine_entry_frame(f) for s,f in frames0.items()}
    scored={n(s):f for s,f in reweight(f10,cfg,0.0).items()}
    strength={s:ms.add_strength(f) for s,f in scored.items()}
    completed={s:rt.add_completed_strength(f) for s,f in scored.items()}

    # Use integrated source builder, but only V20 tags. Build real micros.
    import tools.validate_engine5_v17c_5m_context_1m_trigger as h
    micros={s:h.build_micro(raw[s],cfg) for s in raw}
    tags=[x for x in integ.build_sources(raw,cfg,scored,strength,completed,micros) if x['source']=='V20']
    tr=integ.simulate(packed,states,tags)
    tr['net_pct']=pd.to_numeric(tr.pnl_pct,errors='coerce')-0.25
    for typ,r in pick_cases(tr,'V20'):
        rows.extend(wave_rows('KR_V20',r.symbol,r.entry_time,scored,strength,r.net_pct,typ))

    out=pd.DataFrame(rows)
    out.to_csv(OUT,index=False)

    for market in ['KR_V20','US_V20E']:
        for case in ['WIN','LOSS']:
            q=out[(out.market==market)&(out.case==case)]
            if q.empty: continue
            print(f'=== {market} {case} ===')
            for (sym,et),g in q.groupby(['symbol','entry_time'],sort=False):
                print(f'\n{sym} entry={et} net={g.net_pct.iloc[0]:+.4f}%')
                cols=['offset_min','time','close','trend_up','gap','gap_delta','rsi','rsi_slope','strength_bps','strength_rel']
                print(g[cols].to_string(index=False,float_format=lambda x:f'{x:.4f}'))

    print('\nWROTE',OUT)


if __name__=='__main__': main()
