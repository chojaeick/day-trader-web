from __future__ import annotations

from dataclasses import replace
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_breakout_first10_hwm1pct as v17c
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

THRESHOLD=50
OPEN_MINUTE=9*60+10
TIGHT_MINUTES=10
HWM_DD=0.01


def filt_open(ev):
    return {ts:rows for ts,rows in ev.items() if pd.Timestamp(ts).hour*60+pd.Timestamp(ts).minute>=OPEN_MINUTE}

def metrics(name,trades):
    p=pd.to_numeric(trades.pnl_pct,errors='coerce').dropna() if len(trades) else pd.Series(dtype=float)
    gp=float(p[p>0].sum()); gl=float(-p[p<0].sum())
    print(f"{name}: trades={len(p)} wins={(p>0).sum()} losses={(p<=0).sum()} win={(p>0).mean()*100 if len(p) else 0:.2f}% gross={p.sum():+.4f}% avg={p.mean() if len(p) else 0:+.4f}% pf={(gp/gl if gl>0 else np.inf):.3f} maxloss={(p.min() if len(p) else np.nan):+.4f}%")

def simulate_multi(packed_exits, entry_events, state_events, threshold):
    positions={}; trades=[]; current_state={}; last_price={}; last_ts=None
    def realize(pos,frac,price):
        frac=min(float(frac),pos['remaining'])
        if frac<=0:return
        pos['realized'] += frac*(float(price)/pos['entry_price']-1.0); pos['remaining']-=frac
    def close(sym,price,ts,reason):
        pos=positions[sym]; pnl=pos['realized']+pos['remaining']*(float(price)/pos['entry_price']-1.0)
        trades.append({'symbol':sym,'entry_time':pos['entry_time'],'exit_time':pd.Timestamp(ts),'entry_price':pos['entry_price'],'exit_price':float(price),'pnl_pct':pnl*100.0,'reason':reason,'breakout_entry':pos['breakout_entry'],'first_tp_done':pos['tp1_done'],'second_tp_done':pos['tp2_done']})
        del positions[sym]
    for ts,minute,rows in packed_exits:
        last_ts=ts
        if ts in state_events: current_state.update(state_events[ts])
        for sym in list(positions):
            pos=positions.get(sym); rr=rows.get(sym)
            if pos is None or rr is None: continue
            closep,low,high,iu,il,ou,spread1,rsi1=rr; closep=float(closep); low=float(low); high=float(high); last_price[sym]=closep
            trend_up,outer_expanding,mid_slope8,spread5,rsi5=current_state.get(sym,(False,False,np.nan,np.nan,np.nan))
            fade_votes=int(np.isfinite(mid_slope8) and mid_slope8<=0)+int(np.isfinite(spread5) and spread5<=0)+int(np.isfinite(rsi5) and rsi5<=0)
            clear_5m_collapse=(not trend_up) and fade_votes>=2
            fast_fade=np.isfinite(spread1) and spread1<=0 and np.isfinite(rsi1) and rsi1<=0
            elapsed=(pd.Timestamp(ts)-pos['entry_time']).total_seconds()/60.0; tight=pos['breakout_entry'] and elapsed<TIGHT_MINUTES
            if minute>=base.FORCE_FLAT_MINUTE: close(sym,closep,ts,'SESSION_FORCE_FLAT')
            elif low<=pos['stop_price']: close(sym,pos['stop_price'],ts,'INITIAL_STRUCTURAL_STOP')
            elif tight and low<=pos['completed_hwm']*(1.0-HWM_DD): close(sym,pos['completed_hwm']*(1.0-HWM_DD),ts,'BREAKOUT_FIRST10_HWM_1PCT_EXIT')
            elif tight: pos['completed_hwm']=max(pos['completed_hwm'],high)
            elif not pos['tp1_done']:
                if high>=pos['tp1_price']:
                    realize(pos,0.50,pos['tp1_price']); pos['tp1_done']=True; pos['tp1_bar_high']=high; pos['post_tp1_high']=high; pos['fade_armed']=False; pos['fast_fade_streak']=0
                elif clear_5m_collapse: close(sym,closep,ts,'PRE_TP1_CLEAR_TREND_COLLAPSE')
            else:
                fresh=high>max(pos['tp1_bar_high'],pos['post_tp1_high']); outer=trend_up and outer_expanding and np.isfinite(ou) and high>=ou
                if fresh or outer: pos['fade_armed']=True
                pos['post_tp1_high']=max(pos['post_tp1_high'],high); pos['fast_fade_streak']=pos['fast_fade_streak']+1 if pos['fade_armed'] and fast_fade else 0
                if pos['fade_armed'] and pos['fast_fade_streak']>=2: close(sym,closep,ts,'FAST_1M_MOMENTUM_FADE_EXIT')
                else:
                    if sym in positions and (not pos['tp2_done']) and outer: realize(pos,pos['remaining']*0.50,ou); pos['tp2_done']=True
                    if sym in positions and pos['tp2_done'] and np.isfinite(il) and closep<il: close(sym,closep,ts,'INNER_LOWER_CLOSE_EXIT')
        if minute<base.NO_ENTRY_MINUTE:
            for c in entry_events.get(ts,[]):
                sym=str(c[0]).zfill(6)
                if sym in positions or c[2]<float(threshold): continue
                _,closep,score,ms,rs,band_r,stop_dist,entry_iu,entry_il,entry_ou,entry_mid,extended,breakout=c
                positions[sym]={'symbol':sym,'entry_time':pd.Timestamp(ts),'entry_price':float(closep),'stop_price':float(closep)-float(stop_dist),'tp1_price':float(closep)+2.0*float(band_r),'remaining':1.0,'realized':0.0,'tp1_done':False,'tp2_done':False,'tp1_bar_high':np.nan,'post_tp1_high':-np.inf,'fade_armed':False,'fast_fade_streak':0,'breakout_entry':bool(breakout),'completed_hwm':float(closep)}; last_price[sym]=float(closep)
    if last_ts is not None:
        for sym in list(positions):
            if sym in last_price: close(sym,last_price[sym],last_ts,'END_OF_DATA')
    return pd.DataFrame(trades)

def main():
    raw=load_data(); base_cfg=DoubleBollingerEngine5Config(); cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    packed=v8.base.pack_exit_events(raw,base_cfg); states=base.pack_state_events(base.build_cfg_frames(raw,base_cfg))
    frames=base.build_cfg_frames(raw,cfg); f10={s:v10._refine_entry_frame(f) for s,f in frames.items()}; scored=reweight(f10,cfg,0.0)
    ev10=filt_open(v8.pack_entry_events(scored)); ev16,waits=v16.build_wait_events(ev10,raw,cfg,False); ev17b,_,_=v17b.build_v17b(ev16,scored,waits)
    single=v17c.simulate_unconditional_hwm(packed,ev17b,states,THRESHOLD); multi=simulate_multi(packed,ev17b,states,THRESHOLD)
    print('=== V17C SINGLE-POSITION VS MULTI-SYMBOL ==='); print('MULTI rule: one active position per symbol, different symbols may trade concurrently. Entry/exit logic unchanged.')
    metrics('SINGLE_CURRENT',single); metrics('MULTI_SYMBOL',multi)
    print('\n=== TARGET EARLY ENTRIES ===')
    for sym,ts in [('950160','2026-08-13 09:20:00+09:00'),('257720','2026-08-18 14:20:00+09:00')]:
        q=multi[(multi.symbol.astype(str).str.zfill(6)==sym)&(pd.to_datetime(multi.entry_time)==pd.Timestamp(ts))]
        print(sym,ts,'PRESENT=',not q.empty)
        if len(q): print(q.to_string(index=False))
    print('\n=== PER SYMBOL ===')
    if len(multi):
        x=multi.copy(); x['win']=x.pnl_pct>0; print(x.groupby('symbol').agg(trades=('pnl_pct','size'),wins=('win','sum'),win_rate=('win','mean'),gross=('pnl_pct','sum'),avg=('pnl_pct','mean')).assign(win_rate=lambda d:d.win_rate*100).sort_values(['win_rate','gross']).to_string())
    out='/home/ubuntu/day-trader-api/engine5_v16_full_validation/v17c_multi_symbol_trades.csv'; multi.to_csv(out,index=False); print('\n[CSV]',out)

if __name__=='__main__': main()
