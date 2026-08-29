from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data, summary

OUTDIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
OUTDIR.mkdir(parents=True, exist_ok=True)
THRESHOLD = 50
OPEN_MINUTE = 9*60+10
VOL_RATIO = 10.0
TIGHT_MINUTES = 10
HWM_DD = 0.01


def filt_open(ev):
    return {ts: rows for ts, rows in ev.items() if pd.Timestamp(ts).hour*60+pd.Timestamp(ts).minute >= OPEN_MINUTE}


def enrich_for_v17(f: pd.DataFrame) -> pd.DataFrame:
    z=f.copy().sort_values('time').reset_index(drop=True)
    for c in ('close','volume','macd_slope','macd_slope_spread','rsi_slope','inner_upper','inner_lower','outer_upper','mid','entry_score'):
        z[c]=pd.to_numeric(z[c], errors='coerce')
    z['volume_prev']=z['volume'].shift(1)
    z['volume_ratio_prev']=z['volume']/z['volume_prev'].replace(0,np.nan)
    z['macd_slope_prev']=z['macd_slope'].shift(1)
    z['spread_prev']=z['macd_slope_spread'].shift(1)
    z['rsi_slope_prev']=z['rsi_slope'].shift(1)
    z['macd_accel']=(z['macd_slope']>0)&(z['macd_slope']>z['macd_slope_prev'])
    z['spread_accel']=(z['macd_slope_spread']>0)&(z['macd_slope_spread']>z['spread_prev'])
    z['rsi_accel']=(z['rsi_slope']>0)&(z['rsi_slope']>z['rsi_slope_prev'])
    z['momentum_alive']=z['macd_accel']&z['spread_accel']&z['rsi_accel']
    z['both_decelerating']=(z['macd_slope']<z['macd_slope_prev'])&(z['rsi_slope']<z['rsi_slope_prev'])
    z['volume_breakout_10x']=(z['volume_ratio_prev']>=VOL_RATIO)&(z['close']>z['close'].shift(1))
    z['breakout_candidate']=z['volume_breakout_10x']&z['momentum_alive']
    return z


def tuple_from_row(sym, r, breakout=False):
    iu=float(r.inner_upper); il=float(r.inner_lower); ou=float(r.outer_upper); mid=float(r.mid)
    band_r=iu-il
    if not np.isfinite(band_r) or band_r<=0: return None
    return (sym,float(r.close),float(r.entry_score),float(getattr(r,'macd_slope_spread_strength',np.nan)),float(getattr(r,'rsi_slope_strength',np.nan)),band_r,band_r,iu,il,ou,mid,bool(float(r.close)>ou if np.isfinite(ou) else False),bool(breakout))


def add_breakout_and_decel_filter(ev16, frames):
    out={ts:[tuple(list(e)+[False]) for e in rows] for ts,rows in ev16.items()}
    removed=[]; added=[]
    for sym,f0 in frames.items():
        f=enrich_for_v17(f0)
        # Existing candidate: if both 5m MACD slope and RSI slope are decelerating,
        # remove as a diagnostic WAIT candidate. This is intentionally conservative
        # and will be judged by winner preservation, not assumed correct.
        for r in f[f['both_decelerating']].itertuples(index=False):
            ts=pd.Timestamp(r.time)
            if ts in out:
                before=len(out[ts])
                out[ts]=[e for e in out[ts] if str(e[0]).zfill(6)!=str(sym).zfill(6)]
                if len(out[ts])<before: removed.append((str(sym).zfill(6),ts))
                if not out[ts]: out.pop(ts,None)
        # High-volume breakout can bypass normal trend persistence only while
        # MACD/RSI acceleration is alive.
        for r in f[f['breakout_candidate']].itertuples(index=False):
            ts=pd.Timestamp(r.time)
            minute=ts.hour*60+ts.minute
            if minute<OPEN_MINUTE: continue
            e=tuple_from_row(str(sym).zfill(6),r,True)
            if e is None: continue
            # breakout path uses a qualifying score floor but does not require entry_gate.
            if float(r.entry_score)<THRESHOLD: continue
            already=any(str(x[0]).zfill(6)==str(sym).zfill(6) for x in out.get(ts,[]))
            if not already:
                out.setdefault(ts,[]).append(e); added.append((str(sym).zfill(6),ts,float(r.close),float(r.volume_ratio_prev)))
    return out,removed,added


def simulate_v17(packed_exits, entry_events, state_events, threshold):
    pos=None; trades=[]; collisions=0; current_state={}; last_price=None; last_ts=None
    def realize(frac,price):
        nonlocal pos
        frac=min(float(frac),pos['remaining'])
        if frac<=0:return
        pos['realized']+=frac*(float(price)/pos['entry_price']-1.0); pos['remaining']-=frac
    def close_record(price,ts,reason):
        nonlocal pos
        pnl=pos['realized']+pos['remaining']*(float(price)/pos['entry_price']-1.0)
        trades.append({'symbol':pos['symbol'],'entry_time':pos['entry_time'],'exit_time':pd.Timestamp(ts),'entry_price':pos['entry_price'],'exit_price':float(price),'entry_score':pos['entry_score'],'r_abs':pos['r_abs'],'r_pct':pos['r_abs']/pos['entry_price']*100.0,'pnl_pct':pnl*100.0,'first_tp_done':pos['tp1_done'],'second_tp_done':pos['tp2_done'],'reason':reason,'breakout_entry':pos['breakout_entry']})
        pos=None
    for ts,minute,rows in packed_exits:
        last_ts=ts
        if ts in state_events: current_state.update(state_events[ts])
        if pos is not None:
            rr=rows.get(pos['symbol'])
            if rr is not None:
                close,low,high,iu,il,ou,spread1,rsi1=rr; last_price=close
                pos['hwm']=max(pos['hwm'],high)
                trend_up,outer_expanding,mid_slope8,spread5,rsi5=current_state.get(pos['symbol'],(False,False,np.nan,np.nan,np.nan))
                fade_votes=int(np.isfinite(mid_slope8) and mid_slope8<=0)+int(np.isfinite(spread5) and spread5<=0)+int(np.isfinite(rsi5) and rsi5<=0)
                clear_5m_collapse=(not trend_up) and fade_votes>=2
                fast_fade=np.isfinite(spread1) and spread1<=0 and np.isfinite(rsi1) and rsi1<=0
                elapsed=(pd.Timestamp(ts)-pos['entry_time']).total_seconds()/60.0
                tight=pos['breakout_entry'] and elapsed < TIGHT_MINUTES
                dd=1.0-float(close)/pos['hwm'] if pos['hwm']>0 else 0.0
                momentum_cooling=(np.isfinite(spread5) and spread5<=0) or (np.isfinite(rsi5) and rsi5<=0) or fast_fade
                if minute>=base.FORCE_FLAT_MINUTE: close_record(close,ts,'SESSION_FORCE_FLAT')
                elif low<=pos['stop_price']: close_record(pos['stop_price'],ts,'INITIAL_STRUCTURAL_STOP')
                elif tight and momentum_cooling and dd>=HWM_DD: close_record(close,ts,'BREAKOUT_10M_HWM_1PCT_EXIT')
                elif tight:
                    # Preserve full size during first 10m if momentum remains alive.
                    pass
                elif not pos['tp1_done']:
                    if high>=pos['tp1_price']:
                        realize(0.50,pos['tp1_price']); pos['tp1_done']=True; pos['tp1_bar_high']=high; pos['post_tp1_high']=high; pos['fade_armed']=False; pos['fast_fade_streak']=0
                    elif clear_5m_collapse: close_record(close,ts,'PRE_TP1_CLEAR_TREND_COLLAPSE')
                else:
                    fresh=high>max(pos['tp1_bar_high'],pos['post_tp1_high']); outer=trend_up and outer_expanding and np.isfinite(ou) and high>=ou
                    if fresh or outer: pos['fade_armed']=True
                    pos['post_tp1_high']=max(pos['post_tp1_high'],high)
                    pos['fast_fade_streak']=pos['fast_fade_streak']+1 if pos['fade_armed'] and fast_fade else 0
                    if pos['fade_armed'] and pos['fast_fade_streak']>=2: close_record(close,ts,'FAST_1M_MOMENTUM_FADE_EXIT')
                    else:
                        if pos is not None and (not pos['tp2_done']) and outer:
                            realize(pos['remaining']*0.50,ou); pos['tp2_done']=True
                        if pos is not None and pos['tp2_done'] and np.isfinite(il) and close<il: close_record(close,ts,'INNER_LOWER_CLOSE_EXIT')
        if pos is None and minute<base.NO_ENTRY_MINUTE:
            cands=entry_events.get(ts)
            if cands:
                eligible=[c for c in cands if c[2]>=float(threshold)]
                if eligible:
                    if len(eligible)>1: collisions+=1
                    c=max(eligible,key=lambda x:(x[2],x[3] if np.isfinite(x[3]) else -1e9,x[4] if np.isfinite(x[4]) else -1e9,x[0]))
                    sym,close,score,ms,rs,band_r,stop_dist,entry_iu,entry_il,entry_ou,entry_mid,extended,breakout=c
                    pos={'symbol':sym,'entry_time':pd.Timestamp(ts),'entry_price':close,'entry_score':score,'r_abs':band_r,'stop_price':close-stop_dist,'tp1_price':close+2*band_r,'remaining':1.0,'realized':0.0,'tp1_done':False,'tp2_done':False,'tp1_bar_high':np.nan,'post_tp1_high':-np.inf,'fade_armed':False,'fast_fade_streak':0,'breakout_entry':bool(breakout),'hwm':close}
                    last_price=close
    if pos is not None and last_price is not None and last_ts is not None: close_record(last_price,last_ts,'END_OF_DATA')
    return pd.DataFrame(trades),collisions


def run(name,packed_exits,state_events,events):
    t,c=simulate_v17(packed_exits,events,state_events,THRESHOLD)
    s=summary(name,t)
    print(f'{name}: {len(t)}t win={s["win_rate"]:.2f} avg={s["avg_pct"]:+.4f} gross={s["gross_pct"]:+.4f} pf={s["pf"]:.3f} max={t.pnl_pct.min():+.4f} collisions={c}')
    return t,s


def main():
    raw=load_data(); base_cfg=DoubleBollingerEngine5Config(); cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    packed_exits=v8.base.pack_exit_events(raw,base_cfg); state_events=base.pack_state_events(base.build_cfg_frames(raw,base_cfg))
    raw_frames=base.build_cfg_frames(raw,cfg); f10={s:v10._refine_entry_frame(f) for s,f in raw_frames.items()}; scored=reweight(f10,cfg,0.0)
    ev10=filt_open(v8.pack_entry_events(scored)); ev16,waits=v16.build_wait_events(ev10,raw,cfg,False)
    ev16x={ts:[tuple(list(e)+[False]) for e in rows] for ts,rows in ev16.items()}
    ev17,removed,added=add_breakout_and_decel_filter(ev16,scored)
    print('=== V17 FOCUSED STRUCTURE TEST ===')
    print('A=V16 baseline under same simulator; B=+deceleration no-new-buy + >=10x volume breakout trend bypass + first10m full-size protection with 1% HWM exit only when momentum cools.')
    t16,s16=run('A_V16',packed_exits,state_events,ev16x); t17,s17=run('B_V17_CANDIDATE',packed_exits,state_events,ev17)
    print('\nDELAY/REMOVE CANDIDATES=',removed)
    print('BREAKOUT_ADDED=',added)
    print('\nBREAKOUT REALIZED TRADES')
    q=t17[t17.breakout_entry==True]
    print(q.to_string(index=False) if len(q) else 'none')
    print('\nDELTA win={:+.2f} gross={:+.4f} pf={:+.3f}'.format(s17['win_rate']-s16['win_rate'],s17['gross_pct']-s16['gross_pct'],s17['pf']-s16['pf']))
    out=t17.copy(); out.to_csv(OUTDIR/'v17_volume_bypass_tight10_trades.csv',index=False)
    print('[CSV]',OUTDIR/'v17_volume_bypass_tight10_trades.csv')

if __name__=='__main__': main()
