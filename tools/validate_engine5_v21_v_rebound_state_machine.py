from __future__ import annotations

from dataclasses import replace
from pathlib import Path
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
import tools.validate_engine5_v21_v_rebound_structural_stop as old
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR=Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
THRESHOLD=50
REL_MIN=1.45
RAW_MINS=[30.0,40.0]
LEG_MINS=[1.5,1.75,2.0]
STOP_CAPS=[0.7,1.0,1.5,2.0]
VOL_MINS=[None,1.0,1.2,1.5]
ARM_TTL_MIN=30
FEE_RT_PCT=.25


def n(x): return str(x).zfill(6)
def f(x):
    try:
        y=float(x); return y if np.isfinite(y) else np.nan
    except Exception:return np.nan


def add_features(pf,micro,bars):
    z=st.add_structure_features(pf,micro).sort_values('time').reset_index(drop=True)
    b=bars.copy(); b['time']=pd.to_datetime(b.time)
    keep=['time']+[c for c in ['open','high','low','close','volume'] if c in b.columns]
    b=b[keep].sort_values('time').drop_duplicates('time',keep='last')
    # Preserve provisional close, but take raw high/low/volume directly from 1m bars.
    ren={c:f'raw_{c}' for c in ['open','high','low','close','volume'] if c in b.columns}
    z=z.merge(b.rename(columns=ren),on='time',how='left')
    z['px']=pd.to_numeric(z.get('raw_close',z.close),errors='coerce').fillna(pd.to_numeric(z.close,errors='coerce'))
    z['lo']=pd.to_numeric(z.get('raw_low',z.px),errors='coerce').fillna(z.px)
    z['hi']=pd.to_numeric(z.get('raw_high',z.px),errors='coerce').fillna(z.px)
    vol=pd.to_numeric(z.get('raw_volume',np.nan),errors='coerce')
    z['vol3']=vol.rolling(3,min_periods=3).mean()
    z['vol_prior10']=vol.shift(3).rolling(10,min_periods=10).mean()
    z['volume_accel_3v10']=z.vol3/z.vol_prior10.replace(0,np.nan)
    mid=pd.to_numeric(z.mid_slope8,errors='coerce'); d=mid.diff()
    z['slope_gain3']=mid-mid.shift(3); z['slope_pos3']=(d>0).rolling(3,min_periods=3).mean()
    z['ready_common']=(mid<=0)&(z.slope_gain3>0)&(z.slope_pos3>=.5)&(pd.to_numeric(z.macd_slope,errors='coerce')>0)&(pd.to_numeric(z.rsi_slope,errors='coerce')>0)&(pd.to_numeric(z.strength_rel,errors='coerce')>=REL_MIN)
    return z


def state_candidates(sym,z,scored,raw_min,leg_min):
    out=[]
    ready=z.ready_common&(pd.to_numeric(z.gap_delta,errors='coerce')>=raw_min)
    day=pd.to_datetime(z.time).dt.date
    state=None
    for i in range(len(z)):
        ts=pd.Timestamp(z.time.iloc[i]); px=f(z.px.iloc[i]); lo=f(z.lo.iloc[i])
        if not np.isfinite(px) or not np.isfinite(lo):continue
        if i==0 or day.iloc[i]!=day.iloc[i-1]: state=None
        if state is not None and (ts-state['armed_time']).total_seconds()/60.0>ARM_TTL_MIN: state=None

        if state is None and bool(ready.iloc[i]):
            j=max(0,i-8)
            base_low=f(pd.to_numeric(z.lo.iloc[j:i+1],errors='coerce').min())
            if np.isfinite(base_low) and base_low>0:
                state={'armed_time':ts,'armed_i':i,'base_low':base_low,'rebound_high':px,'rebound_high_time':ts,'stage':'RISING','pullback_low':np.nan,'pullback_start':pd.NaT}

        if state is None: continue
        # If the structure makes a new low, restart only if current minute itself is a fresh READY.
        if lo<=state['base_low'] and i>state['armed_i']:
            state=None
            if bool(ready.iloc[i]):
                j=max(0,i-8); base_low=f(pd.to_numeric(z.lo.iloc[j:i+1],errors='coerce').min())
                if np.isfinite(base_low) and base_low>0: state={'armed_time':ts,'armed_i':i,'base_low':base_low,'rebound_high':px,'rebound_high_time':ts,'stage':'RISING','pullback_low':np.nan,'pullback_start':pd.NaT}
            if state is None: continue

        leg=(state['rebound_high']/state['base_low']-1)*100
        prev=f(z.px.iloc[i-1]) if i>0 else np.nan
        if state['stage']=='RISING':
            if px>state['rebound_high']:
                state['rebound_high']=px; state['rebound_high_time']=ts
                leg=(state['rebound_high']/state['base_low']-1)*100
            # Pullback begins only after a qualified first rebound leg.
            if leg>=leg_min and np.isfinite(prev) and px<prev:
                state['stage']='PULLBACK'; state['pullback_start']=ts; state['pullback_low']=lo
        else:
            state['pullback_low']=min(f(state['pullback_low']),lo)
            higher_low=np.isfinite(state['pullback_low']) and state['pullback_low']>state['base_low']
            reclaim=px>state['rebound_high']
            mom=(f(z.gap_delta.iloc[i])>0 and f(z.rsi_slope.iloc[i])>0)
            if higher_low and reclaim and mom:
                stop=state['pullback_low']; dist=(px/stop-1)*100
                minute=ts.hour*60+ts.minute
                q5=scored[scored.time<=ts.floor('5min')]
                if minute>=550 and minute<base.NO_ENTRY_MINUTE and not q5.empty:
                    ev=old.make_event(sym,q5.iloc[-1],px)
                    if ev is not None:
                        out.append(dict(symbol=n(sym),time=ts,raw_min=raw_min,leg_min=leg_min,price=px,base_low=state['base_low'],first_rebound_high=state['rebound_high'],first_rebound_high_time=state['rebound_high_time'],pullback_start=state['pullback_start'],structural_stop=stop,stop_dist_pct=dist,volume_accel=f(z.volume_accel_3v10.iloc[i]),gap_delta=f(z.gap_delta.iloc[i]),rsi_slope=f(z.rsi_slope.iloc[i]),event=ev))
                state=None
    return pd.DataFrame(out)


def select(c,raw_min,leg,cap,vol_min):
    q=c[(c.raw_min==raw_min)&(c.leg_min==leg)&(c.stop_dist_pct<=cap)].copy()
    if vol_min is not None:q=q[pd.to_numeric(q.volume_accel,errors='coerce')>=vol_min]
    if q.empty:return {},{},q
    q['day']=pd.to_datetime(q.time).dt.date; q=q.sort_values('time').drop_duplicates(['symbol','day'],keep='first')
    ev={}; meta={}
    for _,r in q.iterrows():
        ts=pd.Timestamp(r.time); sym=n(r.symbol); ev.setdefault(ts,[]).append(r.event); meta[(ts,sym)]={'structural_stop':float(r.structural_stop),'mode':'V_REBOUND'}
    return ev,meta,q


def merge(a,b):
    o={pd.Timestamp(k):list(v) for k,v in a.items()}
    for k,v in b.items():o.setdefault(pd.Timestamp(k),[]).extend(v)
    return o


def stat(label,tr):
    g=pd.to_numeric(tr.pnl_pct,errors='coerce').dropna() if len(tr) else pd.Series(dtype=float); net=g-FEE_RT_PCT
    gp=float(net[net>0].sum()); gl=float(-net[net<0].sum())
    return dict(label=label,trades=len(net),wins=int((net>0).sum()),losses=int((net<=0).sum()),win_pct=float((net>0).mean()*100) if len(net) else 0.,net_sum_pct=float(net.sum()),net_avg_pct=float(net.mean()) if len(net) else 0.,pf=gp/gl if gl>0 else np.inf,max_loss_pct=float(net.min()) if len(net) else np.nan)


def main():
    OUT_DIR.mkdir(parents=True,exist_ok=True)
    raw={n(k):v for k,v in load_data().items()}; base_cfg=DoubleBollingerEngine5Config(); cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.,rsi_slope_full_ratio=1.5)
    print('=== V21 V-REBOUND STATE MACHINE ===',flush=True)
    print('READY -> first rebound -> real pullback -> Higher Low -> reclaim. Pullback low is the ACTUAL structural stop.',flush=True)
    print('Volume = current 3 completed 1m avg / prior 10 completed 1m avg.',flush=True)
    packed=v8.base.pack_exit_events(raw,base_cfg); states=base.pack_state_events(base.build_cfg_frames(raw,base_cfg))
    frames=base.build_cfg_frames(raw,cfg); f10={n(s):v10._refine_entry_frame(x) for s,x in frames.items()}; scored={n(s):x for s,x in reweight(f10,cfg,0.).items()}
    strength={s:ms.add_strength(x) for s,x in scored.items()}; completed={s:rt.add_completed_strength(x) for s,x in scored.items()}
    ev10=sweep.filt_open(v8.pack_entry_events(scored)); ev16,waits=v16.build_wait_events(ev10,raw,cfg,False); ev17,_,_=v17b.build_v17b(ev16,scored,waits)
    micros={}; allc=[]
    for k,(sym,bars) in enumerate(raw.items(),1):
        print(f'[{k}/{len(raw)}] {sym}',flush=True); pf,m=old.load_cache(sym,bars,cfg,completed[sym]); micros[sym]=m; z=add_features(pf,m,bars)
        for r in RAW_MINS:
            for leg in LEG_MINS:
                c=state_candidates(sym,z,scored[sym],r,leg)
                if len(c):allc.append(c)
    ev18,_=h.build_veto_stream(ev17,micros); ev20,_=ms.filter_events(ev18,strength,raw_min=52.,rel_min=1.45)
    bt=multi.simulate_multi(packed,ev20,states,THRESHOLD); print('\nBASE',pd.DataFrame([stat('V20',bt)]).to_string(index=False),flush=True)
    cand=pd.concat(allc,ignore_index=True) if allc else pd.DataFrame()
    if cand.empty: print('NO STATE-MACHINE CANDIDATES'); return
    cand.drop(columns=['event']).to_csv(OUT_DIR/'v21_v_rebound_state_candidates.csv',index=False)
    rows=[]
    for r in RAW_MINS:
      for leg in LEG_MINS:
       for cap in STOP_CAPS:
        for vol in VOL_MINS:
          vev,meta,q=select(cand,r,leg,cap,vol); extra=old.simulate_with_v_stop(packed,vev,states,THRESHOLD,meta); merged=old.simulate_with_v_stop(packed,merge(ev20,vev),states,THRESHOLD,meta)
          se=stat('EXTRA',extra); sm=stat('MERGED',merged)
          rows.append(dict(raw_min=r,leg_min=leg,stop_cap=cap,volume_min=0. if vol is None else vol,signals=len(q),**sm,extra_trades=se['trades'],extra_wins=se['wins'],extra_win_pct=se['win_pct'],extra_net=se['net_sum_pct'],extra_pf=se['pf'],extra_max_loss=se['max_loss_pct']))
    s=pd.DataFrame(rows).sort_values(['extra_net','extra_pf','net_sum_pct'],ascending=False); s.to_csv(OUT_DIR/'v21_v_rebound_state_summary.csv',index=False)
    print('\n=== TOP 20 BY EXTRA PATH ==='); print(s.head(20).to_string(index=False))
    print('\n=== TARGET CANDIDATES (before stop/volume filtering) ===')
    for sym,date in [('950160','2026-08-14'),('950260','2026-08-19')]:
        q=cand[(cand.symbol==sym)&(pd.to_datetime(cand.time).dt.strftime('%Y-%m-%d')==date)].drop(columns=['event'])
        print(f'\n{sym} {date}:'); print(q.sort_values(['time','raw_min','leg_min']).to_string(index=False) if len(q) else 'NONE')
    print('\nWROTE v21_v_rebound_state_candidates.csv / summary.csv')

if __name__=='__main__':main()
