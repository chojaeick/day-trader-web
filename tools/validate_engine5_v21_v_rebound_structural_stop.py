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
V_FIRST_LEG_MIN = 2.0
VOLUME_ACCEL_MIN = 1.5
VOLUME_FILTERS = [False, True]


def norm_sym(x): return str(x).zfill(6)

def finite(x):
    try:
        y=float(x); return y if np.isfinite(y) else np.nan
    except Exception: return np.nan


def stats(label,trades):
    g=pd.to_numeric(trades.pnl_pct,errors='coerce').dropna() if len(trades) else pd.Series(dtype=float)
    n=g-FEE_RT_PCT; gp=float(n[n>0].sum()) if len(n) else 0.; gl=float(-n[n<0].sum()) if len(n) else 0.
    return dict(label=label,trades=len(n),wins=int((n>0).sum()),losses=int((n<=0).sum()),win_pct=float((n>0).mean()*100) if len(n) else 0.,net_sum_pct=float(n.sum()) if len(n) else 0.,net_avg_pct=float(n.mean()) if len(n) else 0.,pf=gp/gl if gl>0 else np.inf,max_loss_pct=float(n.min()) if len(n) else np.nan)


def load_cache(sym,bars,cfg,completed):
    path=CACHE_DIR/f'{sym}_provisional_micro.pkl'; CACHE_DIR.mkdir(parents=True,exist_ok=True)
    if path.exists():
        with path.open('rb') as f:o=pickle.load(f)
        print(f'CACHE HIT {sym}',flush=True); return o['provisional'],o['micro']
    print(f'CACHE BUILD {sym}',flush=True)
    pf=rt.add_provisional_strength(rt.build_provisional_5m(bars,cfg),completed); m=h.build_micro(bars,cfg)
    with path.open('wb') as f:pickle.dump({'provisional':pf,'micro':m},f,pickle.HIGHEST_PROTOCOL)
    return pf,m


def make_event(sym,row,price):
    iu,il,ou,mid=[finite(row.get(k,np.nan)) for k in ['inner_upper','inner_lower','outer_upper','mid']]
    br=iu-il if np.isfinite(iu) and np.isfinite(il) else np.nan
    if not np.isfinite(br) or br<=0:return None
    score=max(THRESHOLD,finite(row.get('entry_score',THRESHOLD)))
    return (norm_sym(sym),float(price),float(score),finite(row.get('macd_slope_spread_strength',np.nan)),finite(row.get('rsi_slope_strength',np.nan)),float(br),float(br),iu,il,ou,mid,bool(np.isfinite(ou) and price>ou),False)


def build_feature_frame(pf,micro):
    z=st.add_structure_features(pf,micro).sort_values('time').reset_index(drop=True)
    mm=micro[['time','volume']].copy() if 'volume' in micro.columns else pd.DataFrame(columns=['time','volume'])
    if len(mm):
        mm['time']=pd.to_datetime(mm.time); mm['volume']=pd.to_numeric(mm.volume,errors='coerce')
        mm=mm.sort_values('time').drop_duplicates('time',keep='last')
        recent3=mm.volume.rolling(3,min_periods=3).mean()
        prior10=mm.volume.shift(3).rolling(10,min_periods=6).mean()
        mm['volume_accel_3v10']=recent3/prior10.replace(0,np.nan)
        z=z.merge(mm[['time','volume_accel_3v10']],on='time',how='left')
    else:z['volume_accel_3v10']=np.nan
    mid=pd.to_numeric(z.mid_slope8,errors='coerce'); d=mid.diff()
    z['slope_gain3']=mid-mid.shift(3); z['slope_pos3']=(d>0).rolling(3,min_periods=3).mean()
    z['ready_base']=(mid<=0)&(z.slope_gain3>0)&(z.slope_pos3>=.5)&(pd.to_numeric(z.macd_slope,errors='coerce')>0)&(pd.to_numeric(z.rsi_slope,errors='coerce')>0)&(pd.to_numeric(z.strength_rel,errors='coerce')>=REL_MIN)
    return z


def v_candidates(sym,z,scored):
    rows=[]; close=pd.to_numeric(z.close,errors='coerce')
    for raw_min in RAW_MINS:
        ready=z.ready_base&(pd.to_numeric(z.gap_delta,errors='coerce')>=raw_min)
        for i in range(8,len(z)):
            if not bool(ready.iloc[i]):continue
            low8=finite(z.local_low_8.iloc[i]); px=finite(close.iloc[i])
            if not(np.isfinite(low8) and low8>0 and np.isfinite(px)):continue
            leg=(px/low8-1)*100
            if leg<V_FIRST_LEG_MIN:continue
            swing=finite(pd.to_numeric(z.close.iloc[max(0,i-5):i-1],errors='coerce').max()) if i>=3 else np.nan
            plow=finite(z.pullback_low_3.iloc[i])
            if not(np.isfinite(swing) and np.isfinite(plow) and plow>low8):continue
            if not(finite(close.iloc[i-1])<=swing and px>swing):continue
            if finite(z.gap_delta.iloc[i])<=0 or finite(z.rsi_slope.iloc[i])<=0:continue
            dist=(px/plow-1)*100
            ts=pd.Timestamp(z.time.iloc[i]); minute=ts.hour*60+ts.minute
            if minute<550 or minute>=base.NO_ENTRY_MINUTE:continue
            q5=scored[scored.time<=ts.floor('5min')]
            if q5.empty:continue
            ev=make_event(sym,q5.iloc[-1],px)
            if ev is None:continue
            rows.append(dict(symbol=norm_sym(sym),time=ts,raw_min=raw_min,price=px,structural_stop=plow,stop_dist_pct=dist,first_leg_pct=leg,volume_accel=finite(z.volume_accel_3v10.iloc[i]),event=ev))
    return pd.DataFrame(rows)


def select_v(cand,raw_min,cap,use_volume):
    q=cand[(cand.raw_min==raw_min)&(cand.stop_dist_pct<=cap)].copy()
    if use_volume:q=q[pd.to_numeric(q.volume_accel,errors='coerce')>=VOLUME_ACCEL_MIN]
    if q.empty:return {},{},q
    q['day']=pd.to_datetime(q.time).dt.date
    q=q.sort_values('time').drop_duplicates(['symbol','day'],keep='first')
    ev={}; meta={}
    for _,r in q.iterrows():
        ts=pd.Timestamp(r.time); sym=norm_sym(r.symbol)
        ev.setdefault(ts,[]).append(r.event)
        meta[(ts,sym)]={'structural_stop':float(r.structural_stop),'mode':'V_REBOUND'}
    return ev,meta,q


def merge_events(a,b):
    out={pd.Timestamp(k):list(v) for k,v in a.items()}
    for k,v in b.items():out.setdefault(pd.Timestamp(k),[]).extend(v)
    return out


def simulate_with_v_stop(packed_exits,entry_events,state_events,threshold,v_meta):
    positions={}; trades=[]; current_state={}; last_price={}; last_ts=None
    def realize(pos,frac,price):
        frac=min(float(frac),pos['remaining'])
        if frac<=0:return
        pos['realized']+=frac*(float(price)/pos['entry_price']-1.); pos['remaining']-=frac
    def close_pos(sym,price,ts,reason):
        pos=positions[sym]; pnl=pos['realized']+pos['remaining']*(float(price)/pos['entry_price']-1.)
        trades.append({'symbol':sym,'entry_time':pos['entry_time'],'exit_time':pd.Timestamp(ts),'entry_price':pos['entry_price'],'exit_price':float(price),'pnl_pct':pnl*100.,'reason':reason,'entry_mode':pos['entry_mode'],'structural_stop':pos.get('v_structural_stop',np.nan)})
        del positions[sym]
    for ts,minute,rows in packed_exits:
        last_ts=ts
        if ts in state_events:current_state.update(state_events[ts])
        for sym in list(positions):
            pos=positions.get(sym); rr=rows.get(sym)
            if pos is None or rr is None:continue
            closep,low,high,iu,il,ou,spread1,rsi1=rr; closep=float(closep); low=float(low); high=float(high); last_price[sym]=closep
            trend_up,outer_expanding,mid_slope8,spread5,rsi5=current_state.get(sym,(False,False,np.nan,np.nan,np.nan))
            fade_votes=int(np.isfinite(mid_slope8) and mid_slope8<=0)+int(np.isfinite(spread5) and spread5<=0)+int(np.isfinite(rsi5) and rsi5<=0)
            clear_5m_collapse=(not trend_up) and fade_votes>=2; fast_fade=np.isfinite(spread1) and spread1<=0 and np.isfinite(rsi1) and rsi1<=0
            elapsed=(pd.Timestamp(ts)-pos['entry_time']).total_seconds()/60.; tight=pos['breakout_entry'] and elapsed<multi.TIGHT_MINUTES
            if minute>=base.FORCE_FLAT_MINUTE:close_pos(sym,closep,ts,'SESSION_FORCE_FLAT')
            elif pos['entry_mode']=='V_REBOUND' and low<=pos['v_structural_stop']:close_pos(sym,pos['v_structural_stop'],ts,'V_HIGHER_LOW_STRUCTURAL_STOP')
            elif low<=pos['stop_price']:close_pos(sym,pos['stop_price'],ts,'INITIAL_STRUCTURAL_STOP')
            elif tight and low<=pos['completed_hwm']*(1.-multi.HWM_DD):close_pos(sym,pos['completed_hwm']*(1.-multi.HWM_DD),ts,'BREAKOUT_FIRST10_HWM_1PCT_EXIT')
            elif tight:pos['completed_hwm']=max(pos['completed_hwm'],high)
            elif not pos['tp1_done']:
                if high>=pos['tp1_price']:
                    realize(pos,.50,pos['tp1_price']); pos['tp1_done']=True; pos['tp1_bar_high']=high; pos['post_tp1_high']=high; pos['fade_armed']=False; pos['fast_fade_streak']=0
                elif clear_5m_collapse:close_pos(sym,closep,ts,'PRE_TP1_CLEAR_TREND_COLLAPSE')
            else:
                fresh=high>max(pos['tp1_bar_high'],pos['post_tp1_high']); outer=trend_up and outer_expanding and np.isfinite(ou) and high>=ou
                if fresh or outer:pos['fade_armed']=True
                pos['post_tp1_high']=max(pos['post_tp1_high'],high); pos['fast_fade_streak']=pos['fast_fade_streak']+1 if pos['fade_armed'] and fast_fade else 0
                if pos['fade_armed'] and pos['fast_fade_streak']>=2:close_pos(sym,closep,ts,'FAST_1M_MOMENTUM_FADE_EXIT')
                else:
                    if sym in positions and (not pos['tp2_done']) and outer:realize(pos,pos['remaining']*.50,ou); pos['tp2_done']=True
                    if sym in positions and pos['tp2_done'] and np.isfinite(il) and closep<il:close_pos(sym,closep,ts,'INNER_LOWER_CLOSE_EXIT')
        if minute<base.NO_ENTRY_MINUTE:
            for c in entry_events.get(ts,[]):
                sym=str(c[0]).zfill(6)
                if sym in positions or c[2]<float(threshold):continue
                _,closep,score,msv,rsv,band_r,stop_dist,entry_iu,entry_il,entry_ou,entry_mid,extended,breakout=c
                meta=v_meta.get((pd.Timestamp(ts),sym)); is_v=meta is not None
                positions[sym]={'symbol':sym,'entry_time':pd.Timestamp(ts),'entry_price':float(closep),'stop_price':float(closep)-float(stop_dist),'tp1_price':float(closep)+2.*float(band_r),'remaining':1.,'realized':0.,'tp1_done':False,'tp2_done':False,'tp1_bar_high':np.nan,'post_tp1_high':-np.inf,'fade_armed':False,'fast_fade_streak':0,'breakout_entry':bool(breakout),'completed_hwm':float(closep),'entry_mode':'V_REBOUND' if is_v else 'V20','v_structural_stop':float(meta['structural_stop']) if is_v else np.nan}; last_price[sym]=float(closep)
    if last_ts is not None:
        for sym in list(positions):
            if sym in last_price:close_pos(sym,last_price[sym],last_ts,'END_OF_DATA')
    return pd.DataFrame(trades)


def main():
    OUT_DIR.mkdir(parents=True,exist_ok=True)
    raw={norm_sym(k):v for k,v in load_data().items()}; base_cfg=DoubleBollingerEngine5Config(); cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.,rsi_slope_full_ratio=1.5)
    print('=== V21 V-REBOUND STRUCTURAL STOP VALIDATION ===',flush=True)
    print('V20 unchanged. V only: >=2% first leg -> higher low -> short swing-high break.',flush=True)
    print('V entries use Higher Low as an ACTUAL stop. Volume acceleration 1.5x is swept OFF/ON.',flush=True)
    packed=v8.base.pack_exit_events(raw,base_cfg); states=base.pack_state_events(base.build_cfg_frames(raw,base_cfg))
    frames0=base.build_cfg_frames(raw,cfg); f10={norm_sym(s):v10._refine_entry_frame(f) for s,f in frames0.items()}; scored={norm_sym(s):f for s,f in reweight(f10,cfg,0.).items()}
    strength={s:ms.add_strength(f) for s,f in scored.items()}; completed={s:rt.add_completed_strength(f) for s,f in scored.items()}
    ev10=sweep.filt_open(v8.pack_entry_events(scored)); ev16,waits=v16.build_wait_events(ev10,raw,cfg,False); ev17,_,_=v17b.build_v17b(ev16,scored,waits)
    micros={}; cs=[]
    for n,(sym,bars) in enumerate(raw.items(),1):
        print(f'[{n}/{len(raw)}] {sym}',flush=True); pf,m=load_cache(sym,bars,cfg,completed[sym]); micros[sym]=m
        c=v_candidates(sym,build_feature_frame(pf,m),scored[sym]);
        if len(c):cs.append(c)
    ev18,_=h.build_veto_stream(ev17,micros); ev20,_=ms.filter_events(ev18,strength,raw_min=52.,rel_min=1.45)
    base_tr=multi.simulate_multi(packed,ev20,states,THRESHOLD); print('\nBASE',pd.DataFrame([stats('V20_BASE',base_tr)]).to_string(index=False),flush=True)
    cand=pd.concat(cs,ignore_index=True) if cs else pd.DataFrame()
    if cand.empty:print('NO V CANDIDATES');return
    cand.drop(columns=['event']).to_csv(OUT_DIR/'v21_v_rebound_candidates.csv',index=False)
    rows=[]; case_rows=[]
    for raw_min in RAW_MINS:
      for cap in STOP_CAPS:
       for use_vol in VOLUME_FILTERS:
        vev,vmeta,q=select_v(cand,raw_min,cap,use_vol)
        extra=simulate_with_v_stop(packed,vev,states,THRESHOLD,vmeta); merged=simulate_with_v_stop(packed,merge_events(ev20,vev),states,THRESHOLD,vmeta)
        se=stats('V_EXTRA',extra); sm=stats('MERGED',merged)
        rows.append(dict(raw_min=raw_min,stop_cap=cap,volume_filter=use_vol,volume_accel_min=VOLUME_ACCEL_MIN if use_vol else 0.,signals=len(q),**sm,extra_trades=se['trades'],extra_wins=se['wins'],extra_win_pct=se['win_pct'],extra_net=se['net_sum_pct'],extra_pf=se['pf'],extra_max_loss=se['max_loss_pct']))
        print(f"RAW{raw_min:g} STOP<={cap:.1f}% VOL={'ON' if use_vol else 'OFF'} | merged {sm['trades']} WR {sm['win_pct']:.2f}% NET {sm['net_sum_pct']:+.3f}% PF {sm['pf']:.3f} | extra {se['trades']} WR {se['win_pct']:.2f}% NET {se['net_sum_pct']:+.3f}% PF {se['pf']:.3f} MAX {se['max_loss_pct']:+.3f}%",flush=True)
        if len(extra):
            xx=extra.copy(); xx['cfg_raw']=raw_min; xx['cfg_cap']=cap; xx['cfg_vol']=use_vol; case_rows.append(xx)
    s=pd.DataFrame(rows).sort_values(['net_sum_pct','extra_net','win_pct'],ascending=False); s.to_csv(OUT_DIR/'v21_v_rebound_structural_stop_summary.csv',index=False)
    if case_rows:pd.concat(case_rows,ignore_index=True).to_csv(OUT_DIR/'v21_v_rebound_structural_stop_cases.csv',index=False)
    print('\n=== TOP ==='); print(s.to_string(index=False))
    print('\n=== EXIT REASONS FOR BEST CONFIG ===')
    best=s.iloc[0]; vev,vmeta,q=select_v(cand,float(best.raw_min),float(best.stop_cap),bool(best.volume_filter)); extra=simulate_with_v_stop(packed,vev,states,THRESHOLD,vmeta)
    if len(extra):print(extra.groupby('reason').agg(trades=('pnl_pct','size'),gross=('pnl_pct','sum'),avg=('pnl_pct','mean')).to_string())
    for sym,day,t0,t1 in [('950160','2026-08-14','10:30','11:40'),('950260','2026-08-19','13:00','13:55')]:
        qq=q[(q.symbol==sym)&(pd.to_datetime(q.time).dt.date==pd.Timestamp(day).date())]
        print(f'\nTARGET {sym} {day}:'); print(qq.drop(columns=['event']).to_string(index=False) if len(qq) else 'NONE')
    print('\nWROTE v21_v_rebound_structural_stop_summary.csv / cases.csv / candidates.csv')

if __name__=='__main__':main()
