from __future__ import annotations

"""One-axis diagnostic: soften the hard mid_slope8 > 0 trend boundary.

This does NOT change production/V20.  It asks whether a small negative DBB-mid
slope that is recovering should be treated as transition/neutral rather than a
hard trend failure.  All other V10/V16/V17/V18/V20 rules stay unchanged.

Variants are deliberately expressed as a fraction of the recent absolute
mid-slope scale so the test is not tied to one stock's price level:
  BASE      : original trend_up (mid_slope8 > 0)
  N025_RISE : mid_slope8 >= -0.25 * recent_abs_median AND slope delta > 0
  N050_RISE : mid_slope8 >= -0.50 * recent_abs_median AND slope delta > 0
  N100_RISE : mid_slope8 >= -1.00 * recent_abs_median AND slope delta > 0

The relaxed state is used ONLY in V10's established/continuation trend test for
this ablation.  V20 RAW52/REL1.45 and every downstream veto are untouched.
"""

from dataclasses import replace
from pathlib import Path
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_opening_5m_hwm_sweep as sweep
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_macd_strength as ms
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

RAW_MIN=52.0
REL_MIN=1.45
TARGET='950160'
DAY=pd.Timestamp('2026-08-14').date()
OUT=Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation/trend_neutral_zone_ablation.csv')
VARIANTS=[('BASE',None),('N025_RISE',0.25),('N050_RISE',0.50),('N100_RISE',1.00)]


def norm(s): return str(s).zfill(6)
def finite(x):
    try:
        y=float(x); return y if np.isfinite(y) else np.nan
    except Exception: return np.nan


def relax_frame(frame: pd.DataFrame, frac: float|None) -> pd.DataFrame:
    z=frame.copy()
    if frac is None:
        return v10._refine_entry_frame(z)
    slope=pd.to_numeric(z['mid_slope8'],errors='coerce')
    d=slope.diff()
    scale=slope.abs().shift(1).rolling(8,min_periods=4).median()
    neutral=(slope>=(-float(frac)*scale)) & (d>0)
    z['trend_up_original']=z['trend_up'].fillna(False)
    z['trend_up']=(z['trend_up'].fillna(False)|neutral.fillna(False))
    z['trend_neutral_relaxed']=neutral.fillna(False) & ~z['trend_up_original']
    return v10._refine_entry_frame(z)


def event_rows(ev):
    rows=[]
    for ts,cands in ev.items():
        for c in cands:
            rows.append({'symbol':norm(c[0]),'time':pd.Timestamp(ts),'close':float(c[1])})
    return pd.DataFrame(rows)


def main():
    raw=load_data()
    cfg0=DoubleBollingerEngine5Config()
    cfg=replace(cfg0,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    frames0=base.build_cfg_frames(raw,cfg)
    micros={norm(s):h.build_micro(bb,cfg) for s,bb in raw.items()}
    summaries=[]; details=[]

    for name,frac in VARIANTS:
        refined={norm(s):relax_frame(f,frac) for s,f in frames0.items()}
        scored={norm(s):f for s,f in reweight(refined,cfg,0.0).items()}
        strength={s:ms.add_strength(f) for s,f in scored.items()}
        raw_entries=v8.pack_entry_events(scored)
        ev10=sweep.filt_open(raw_entries)
        ev16,waits=v16.build_wait_events(ev10,raw,cfg,False)
        ev17,_,_=v17b.build_v17b(ev16,scored,waits)
        ev18,_=h.build_veto_stream(ev17,micros)
        ev20,_=ms.filter_events(ev18,strength,raw_min=RAW_MIN,rel_min=REL_MIN)
        e=event_rows(ev20)
        if len(e):
            e['variant']=name; details.append(e)
        target=e[(e.symbol==TARGET)&(e.time.dt.date==DAY)] if len(e) else e
        early=target[(target.time.dt.hour<12)|((target.time.dt.hour==12)&(target.time.dt.minute<=20))] if len(target) else target
        summaries.append({
            'variant':name,
            'v20_signal_count':len(e),
            'delta_vs_base':0,
            'target_day_signals':','.join(target.time.dt.strftime('%H:%M').tolist()) if len(target) else 'NONE',
            'target_early_first':early.time.min().strftime('%H:%M') if len(early) else 'NONE',
        })

    s=pd.DataFrame(summaries)
    base_n=int(s.loc[s.variant=='BASE','v20_signal_count'].iloc[0])
    s['delta_vs_base']=s.v20_signal_count-base_n
    OUT.parent.mkdir(parents=True,exist_ok=True)
    s.to_csv(OUT,index=False)
    if details:
        pd.concat(details,ignore_index=True).to_csv(OUT.with_name('trend_neutral_zone_ablation_signals.csv'),index=False)

    print('=== TREND NEUTRAL-ZONE ONE-AXIS ABLATION ===')
    print('V20 RAW52 / REL1.45 and downstream logic unchanged.')
    print(s.to_string(index=False))
    print('\nInterpretation rule: useful only if 950160 early timing improves WITHOUT a large signal-count explosion.')
    print('DETAIL_CSV',OUT)

if __name__=='__main__': main()
