from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import pickle

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_regime_transition as rt
import tools.diagnose_v20_transition_structure_targets as st
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
CACHE_DIR = OUT_DIR / 'v20_transition_cache'
REL_MIN = 1.45
RAW_MINS = [30.0, 40.0]
LEG_MIN = 2.0
TARGETS = [
    ('950160', '2026-08-14 10:30', '2026-08-14 11:40'),
    ('950260', '2026-08-19 13:00', '2026-08-19 13:55'),
]


def norm_sym(x): return str(x).zfill(6)

def finite(x):
    try:
        y=float(x); return y if np.isfinite(y) else np.nan
    except Exception: return np.nan


def with_tz(ts_text, series):
    ts=pd.Timestamp(ts_text); tz=getattr(series.dt,'tz',None)
    return ts.tz_localize(tz) if tz is not None and ts.tzinfo is None else ts


def load_cache(sym,bars,cfg,completed):
    path=CACHE_DIR/f'{sym}_provisional_micro.pkl'
    if path.exists():
        with path.open('rb') as f:o=pickle.load(f)
        print(f'CACHE HIT {sym}',flush=True); return o['provisional'],o['micro']
    print(f'CACHE BUILD {sym}',flush=True)
    pf=rt.add_provisional_strength(rt.build_provisional_5m(bars,cfg),completed)
    m=h.build_micro(bars,cfg)
    CACHE_DIR.mkdir(parents=True,exist_ok=True)
    with path.open('wb') as f:pickle.dump({'provisional':pf,'micro':m},f,pickle.HIGHEST_PROTOCOL)
    return pf,m


def add_diag_features(pf,micro):
    z=st.add_structure_features(pf,micro).sort_values('time').reset_index(drop=True)
    mid=pd.to_numeric(z.mid_slope8,errors='coerce'); d=mid.diff()
    z['slope_gain3']=mid-mid.shift(3)
    z['slope_pos3']=(d>0).rolling(3,min_periods=3).mean()
    z['mid_non_up']=mid<=0
    z['macd_up']=pd.to_numeric(z.macd_slope,errors='coerce')>0
    z['rsi_up']=pd.to_numeric(z.rsi_slope,errors='coerce')>0
    z['rel_ok']=pd.to_numeric(z.strength_rel,errors='coerce')>=REL_MIN
    z['slope_ok']=(z.slope_gain3>0)&(z.slope_pos3>=.5)
    z['ready_base']=z.mid_non_up&z.macd_up&z.rsi_up&z.rel_ok&z.slope_ok

    close=pd.to_numeric(z.close,errors='coerce')
    vol=pd.to_numeric(z.get('volume',pd.Series(index=z.index,dtype=float)),errors='coerce')
    z['vol3']=vol.rolling(3,min_periods=3).mean()
    z['vol_prev10']=vol.shift(3).rolling(10,min_periods=5).mean()
    z['vol_accel']=z.vol3/z.vol_prev10

    # Recreate V structure exactly enough to show every blocker.
    z['first_leg_pct']=(close/pd.to_numeric(z.local_low_8,errors='coerce')-1.0)*100.0
    z['higher_low_ok']=pd.to_numeric(z.pullback_low_3,errors='coerce')>pd.to_numeric(z.local_low_8,errors='coerce')
    swing=[]
    for i in range(len(z)):
        if i<3: swing.append(np.nan); continue
        q=close.iloc[max(0,i-5):i-1]
        swing.append(float(q.max()) if len(q.dropna()) else np.nan)
    z['short_swing_high']=swing
    z['prev_below_swing']=close.shift(1)<=z.short_swing_high
    z['swing_break']=close>z.short_swing_high
    z['v_structure_ok']=(z.first_leg_pct>=LEG_MIN)&z.higher_low_ok&z.prev_below_swing&z.swing_break
    z['vol15_ok']=z.vol_accel>=1.5
    return z


def yn(v): return 'Y' if bool(v) else 'N'


def main():
    raw={norm_sym(k):v for k,v in load_data().items()}
    base_cfg=DoubleBollingerEngine5Config(); cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    frames0=base.build_cfg_frames(raw,cfg)
    f10={norm_sym(s):v10._refine_entry_frame(f) for s,f in frames0.items()}
    scored={norm_sym(s):f for s,f in reweight(f10,cfg,0.0).items()}
    completed={s:rt.add_completed_strength(f) for s,f in scored.items()}

    print('=== V21 TARGET PASS/FAIL DIAGNOSTIC ===',flush=True)
    print('Columns show why 950160/950260 fail V21 candidate construction minute by minute.',flush=True)

    all_rows=[]
    for sym,t0,t1 in TARGETS:
        print(f'\n===== {sym} {t0} ~ {t1} =====',flush=True)
        bars=raw[sym].copy(); bars['time']=pd.to_datetime(bars['time'])
        pf,m=load_cache(sym,bars,cfg,completed[sym])
        z=add_diag_features(pf,m)
        start=with_tz(t0,z.time); end=with_tz(t1,z.time)
        q=z[(z.time>=start)&(z.time<=end)].copy().reset_index(drop=True)
        if q.empty:
            print('NO ROWS',flush=True); continue

        rows=[]
        for _,r in q.iterrows():
            rawv=finite(r.gap_delta)
            # Print rows with any meaningful turn/momentum/structure activity.
            interesting=(bool(r.ready_base) or finite(r.first_leg_pct)>=1.0 or bool(r.swing_break) or bool(r.higher_low_ok))
            if not interesting: continue
            row=dict(
                symbol=sym,time=r.time,close=finite(r.close),mid=finite(r.mid_slope8),gain3=finite(r.slope_gain3),pos3=finite(r.slope_pos3),
                raw=rawv,rel=finite(r.strength_rel),rsi=finite(r.rsi),rsi_slope=finite(r.rsi_slope),
                mid_non_up=bool(r.mid_non_up),slope_ok=bool(r.slope_ok),macd_up=bool(r.macd_up),rsi_up=bool(r.rsi_up),rel_ok=bool(r.rel_ok),ready_base=bool(r.ready_base),
                leg=finite(r.first_leg_pct),local_low=finite(r.local_low_8),pullback_low=finite(r.pullback_low_3),higher_low=bool(r.higher_low_ok),
                swing=finite(r.short_swing_high),prev_below=bool(r.prev_below_swing),swing_break=bool(r.swing_break),v_ok=bool(r.v_structure_ok),
                vol_accel=finite(r.vol_accel),vol15=bool(r.vol15_ok),raw30=(rawv>=30 if np.isfinite(rawv) else False),raw40=(rawv>=40 if np.isfinite(rawv) else False),
            )
            rows.append(row); all_rows.append(row)

        if not rows:
            print('NO INTERESTING ROWS',flush=True); continue
        d=pd.DataFrame(rows)
        for _,r in d.iterrows():
            print(
                f"{pd.Timestamp(r.time)} px={r.close:.0f} mid={r.mid:.2f} gain3={r.gain3:.2f} pos3={r.pos3:.2f} "
                f"RAW={r.raw:.2f}[30:{yn(r.raw30)} 40:{yn(r.raw40)}] REL={r.rel:.2f} "
                f"READY[mid:{yn(r.mid_non_up)} slope:{yn(r.slope_ok)} macd:{yn(r.macd_up)} rsi:{yn(r.rsi_up)} rel:{yn(r.rel_ok)} => {yn(r.ready_base)}] "
                f"V[leg={r.leg:.2f}% HL:{yn(r.higher_low)} swing={r.swing:.0f} prev<=:{yn(r.prev_below)} break:{yn(r.swing_break)} => {yn(r.v_ok)}] "
                f"VOL={r.vol_accel:.2f}x[1.5:{yn(r.vol15)}]",
                flush=True,
            )

        print('\nFIRST FULL-PASS CANDIDATES:',flush=True)
        for raw_min in RAW_MINS:
            full=d[d.ready_base & d.v_ok & (d.raw>=raw_min)]
            if full.empty:
                print(f'RAW>={raw_min:g}: NONE',flush=True)
            else:
                r=full.iloc[0]
                print(f'RAW>={raw_min:g}: {r.time} px={r.close:.0f} stop={r.pullback_low:.0f} stop_dist={(r.close/r.pullback_low-1)*100:.3f}% vol={r.vol_accel:.2f}x',flush=True)

    out=pd.DataFrame(all_rows)
    path=OUT_DIR/'v21_v_rebound_targets_passfail.csv'
    out.to_csv(path,index=False)
    print(f'\nWROTE {path}',flush=True)

if __name__=='__main__':main()
