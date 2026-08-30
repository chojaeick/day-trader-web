from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import pickle

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_multi_symbol as multi
import tools.validate_engine5_v17c_opening_5m_hwm_sweep as sweep
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_macd_strength as ms
import tools.validate_engine5_v20_regime_transition as rt
import tools.diagnose_v20_transition_structure_targets as st
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
CACHE_DIR = OUT_DIR / 'v20_transition_cache'
FEE_RT_PCT = 0.25
THRESHOLD = 50
REL_MIN = 1.45
RAW_MINS = [30.0, 40.0]
STOP_CAPS = [0.5, 0.7, 1.0]
BOX_WIDTH_MAX = [1.5, 2.0, 2.5]
HOLD_MINS = [1, 2]
V_MIN_FIRST_LEG = [1.0, 1.5, 2.0]


def norm_sym(x): return str(x).zfill(6)

def finite(x):
    try:
        y=float(x); return y if np.isfinite(y) else np.nan
    except Exception: return np.nan


def stats(label,trades):
    g=pd.to_numeric(trades.pnl_pct,errors='coerce').dropna() if len(trades) else pd.Series(dtype=float)
    n=g-FEE_RT_PCT; gp=float(n[n>0].sum()) if len(n) else 0.; gl=float(-n[n<0].sum()) if len(n) else 0.
    return dict(label=label,trades=len(n),wins=int((n>0).sum()),losses=int((n<=0).sum()),win_pct=float((n>0).mean()*100) if len(n) else 0.,net_sum_pct=float(n.sum()),net_avg_pct=float(n.mean()) if len(n) else 0.,pf=gp/gl if gl>0 else np.inf,max_loss_pct=float(n.min()) if len(n) else np.nan)


def load_cache(sym,bars,cfg,completed):
    path=CACHE_DIR/f'{sym}_provisional_micro.pkl'; CACHE_DIR.mkdir(parents=True,exist_ok=True)
    if path.exists():
        with path.open('rb') as f: o=pickle.load(f)
        print(f'CACHE HIT {sym}',flush=True); return o['provisional'],o['micro']
    print(f'CACHE BUILD {sym}',flush=True)
    pf=rt.add_provisional_strength(rt.build_provisional_5m(bars,cfg),completed); m=h.build_micro(bars,cfg)
    with path.open('wb') as f: pickle.dump({'provisional':pf,'micro':m},f,pickle.HIGHEST_PROTOCOL)
    return pf,m


def make_event(sym,row,price):
    iu,il,ou,mid=[finite(row.get(k,np.nan)) for k in ['inner_upper','inner_lower','outer_upper','mid']]
    br=iu-il if np.isfinite(iu) and np.isfinite(il) else np.nan
    if not np.isfinite(br) or br<=0:return None
    score=max(THRESHOLD,finite(row.get('entry_score',THRESHOLD)))
    return (norm_sym(sym),float(price),float(score),finite(row.get('macd_slope_spread_strength',np.nan)),finite(row.get('rsi_slope_strength',np.nan)),float(br),float(br),iu,il,ou,mid,bool(np.isfinite(ou) and price>ou),False)


def build_feature_frame(pf,micro):
    z=st.add_structure_features(pf,micro).sort_values('time').reset_index(drop=True)
    mid=pd.to_numeric(z.mid_slope8,errors='coerce'); d=mid.diff()
    z['slope_gain3']=mid-mid.shift(3); z['slope_pos3']=(d>0).rolling(3,min_periods=3).mean()
    z['ready_base']=(mid<=0)&(z.slope_gain3>0)&(z.slope_pos3>=.5)&(pd.to_numeric(z.macd_slope,errors='coerce')>0)&(pd.to_numeric(z.rsi_slope,errors='coerce')>0)&(pd.to_numeric(z.strength_rel,errors='coerce')>=REL_MIN)
    return z


def confirmed_candidates(sym,z,scored):
    rows=[]
    close=pd.to_numeric(z.close,errors='coerce')
    for raw_min in RAW_MINS:
      ready=z.ready_base&(pd.to_numeric(z.gap_delta,errors='coerce')>=raw_min)
      for width_max in BOX_WIDTH_MAX:
        # BOX: compressed box -> breakout -> 1/2 minute hold above breakout level.
        for hold in HOLD_MINS:
          for i in range(len(z)-hold):
            if not bool(ready.iloc[i]) or not bool(z.box_break.fillna(False).iloc[i]): continue
            if not np.isfinite(finite(z.box_width_pct.iloc[i])) or finite(z.box_width_pct.iloc[i])>width_max: continue
            level=finite(z.box_high_10.iloc[i]); entry_i=i+hold
            if not np.isfinite(level): continue
            held=True
            for j in range(i+1,entry_i+1):
                if finite(z.close.iloc[j])<level or finite(z.gap_delta.iloc[j])<=0 or finite(z.rsi_slope.iloc[j])<=0: held=False; break
            if not held: continue
            px=finite(z.close.iloc[entry_i]); stop=level
            if not(np.isfinite(px) and px>stop): continue
            dist=(px/stop-1)*100
            rows.append((entry_i,'BOX_CONFIRMED',raw_min,width_max,hold,np.nan,px,stop,dist))

      # V: first rebound >= threshold, pullback holds a higher low, then reclaim prior short swing high.
      for leg_min in V_MIN_FIRST_LEG:
        for i in range(8,len(z)):
          if not bool(ready.iloc[i]): continue
          low8=finite(z.local_low_8.iloc[i]); px=finite(close.iloc[i])
          if not(np.isfinite(low8) and low8>0 and np.isfinite(px)): continue
          leg=(px/low8-1)*100
          if leg<leg_min: continue
          # Need an actual pullback before reclaim: previous bar below earlier short high, current closes above it.
          swing=finite(pd.to_numeric(z.close.iloc[max(0,i-5):i-1],errors='coerce').max()) if i>=3 else np.nan
          plow=finite(z.pullback_low_3.iloc[i])
          if not(np.isfinite(swing) and np.isfinite(plow) and plow>low8): continue
          if not(finite(close.iloc[i-1])<=swing and px>swing): continue
          if finite(z.gap_delta.iloc[i])<=0 or finite(z.rsi_slope.iloc[i])<=0: continue
          dist=(px/plow-1)*100
          rows.append((i,'V_HIGHER_LOW_BREAK',raw_min,np.nan,np.nan,leg_min,px,plow,dist))

    out=[]
    for i,mode,raw_min,width,hold,leg,px,stop,dist in rows:
        ts=pd.Timestamp(z.time.iloc[i]); minute=ts.hour*60+ts.minute
        if minute<550 or minute>=base.NO_ENTRY_MINUTE: continue
        q5=scored[scored.time<=ts.floor('5min')]
        if q5.empty: continue
        ev=make_event(sym,q5.iloc[-1],px)
        if ev is None: continue
        out.append(dict(symbol=norm_sym(sym),time=ts,mode=mode,raw_min=raw_min,box_width_max=width,hold_min=hold,v_leg_min=leg,price=px,structural_stop=stop,stop_dist_pct=dist,event=ev))
    return pd.DataFrame(out)


def event_stream(cand,raw_min,cap,mode,width=None,hold=None,leg=None):
    q=cand[(cand['raw_min']==raw_min)&(cand['mode']==mode)&(cand.stop_dist_pct<=cap)].copy()
    if width is not None:q=q[q.box_width_max==width]
    if hold is not None:q=q[q.hold_min==hold]
    if leg is not None:q=q[q.v_leg_min==leg]
    if q.empty:return {},q
    q['day']=pd.to_datetime(q.time).dt.date
    q=q.sort_values('time').drop_duplicates(['symbol','day','mode'],keep='first')
    ev={}
    for _,r in q.iterrows():ev.setdefault(pd.Timestamp(r.time),[]).append(r.event)
    return ev,q


def merge(a,b):
    o={pd.Timestamp(k):list(v) for k,v in a.items()}
    for k,v in b.items():o.setdefault(pd.Timestamp(k),[]).extend(v)
    return o


def main():
    raw={norm_sym(k):v for k,v in load_data().items()}; base_cfg=DoubleBollingerEngine5Config(); cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.,rsi_slope_full_ratio=1.5)
    print('=== V20 CONFIRMED TRANSITION VALIDATION ===',flush=True)
    print('BOX = compression -> break -> hold. V = rebound -> higher low -> short swing-high break.',flush=True)
    packed=v8.base.pack_exit_events(raw,base_cfg); states=base.pack_state_events(base.build_cfg_frames(raw,base_cfg))
    frames0=base.build_cfg_frames(raw,cfg); f10={norm_sym(s):v10._refine_entry_frame(f) for s,f in frames0.items()}; scored={norm_sym(s):f for s,f in reweight(f10,cfg,0.).items()}
    strength={s:ms.add_strength(f) for s,f in scored.items()}; completed={s:rt.add_completed_strength(f) for s,f in scored.items()}
    ev10=sweep.filt_open(v8.pack_entry_events(scored)); ev16,waits=v16.build_wait_events(ev10,raw,cfg,False); ev17,_,_=v17b.build_v17b(ev16,scored,waits)
    micros={}; cs=[]
    for n,(sym,bars) in enumerate(raw.items(),1):
        print(f'[{n}/{len(raw)}] {sym}',flush=True); pf,m=load_cache(sym,bars,cfg,completed[sym]); micros[sym]=m
        c=confirmed_candidates(sym,build_feature_frame(pf,m),scored[sym]);
        if len(c):cs.append(c)
    ev18,_=h.build_veto_stream(ev17,micros); ev20,_=ms.filter_events(ev18,strength,raw_min=52.,rel_min=1.45)
    bt=multi.simulate_multi(packed,ev20,states,THRESHOLD); print('BASE',pd.DataFrame([stats('V20',bt)]).to_string(index=False),flush=True)
    cand=pd.concat(cs,ignore_index=True) if cs else pd.DataFrame();
    if cand.empty: print('NO CONFIRMED CANDIDATES'); return
    cand.drop(columns=['event']).to_csv(OUT_DIR/'v20_confirmed_transition_candidates.csv',index=False)
    rows=[]
    for raw_min in RAW_MINS:
      for cap in STOP_CAPS:
       for width in BOX_WIDTH_MAX:
        for hold in HOLD_MINS:
          bev,bq=event_stream(cand,raw_min,cap,'BOX_CONFIRMED',width=width,hold=hold); btr=multi.simulate_multi(packed,bev,states,THRESHOLD); sb=stats('BOX',btr)
          merged=multi.simulate_multi(packed,merge(ev20,bev),states,THRESHOLD); sm=stats('MERGED_BOX',merged)
          rows.append(dict(kind='BOX',raw_min=raw_min,stop_cap=cap,width=width,hold=hold,leg=np.nan,**sm,extra_trades=sb['trades'],extra_win_pct=sb['win_pct'],extra_net=sb['net_sum_pct'],extra_pf=sb['pf']))
       for leg in V_MIN_FIRST_LEG:
          vev,vq=event_stream(cand,raw_min,cap,'V_HIGHER_LOW_BREAK',leg=leg); vtr=multi.simulate_multi(packed,vev,states,THRESHOLD); sv=stats('V',vtr)
          merged=multi.simulate_multi(packed,merge(ev20,vev),states,THRESHOLD); sm=stats('MERGED_V',merged)
          rows.append(dict(kind='V',raw_min=raw_min,stop_cap=cap,width=np.nan,hold=np.nan,leg=leg,**sm,extra_trades=sv['trades'],extra_win_pct=sv['win_pct'],extra_net=sv['net_sum_pct'],extra_pf=sv['pf']))
    s=pd.DataFrame(rows).sort_values(['net_sum_pct','win_pct'],ascending=False); s.to_csv(OUT_DIR/'v20_confirmed_transition_summary.csv',index=False)
    print('\n=== TOP 20 ==='); print(s.head(20).to_string(index=False)); print('\nWROTE confirmed transition CSVs')

if __name__=='__main__':main()
