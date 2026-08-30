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
import tools.validate_engine5_slow_turn_regime_integrated as sri
import tools.validate_engine5_slow_turn_structure_ablation as sab
import tools.diagnose_engine5_slow_turn_zero_cross_distance as szd
import tools.validate_engine5_v21_v_rebound_structural_stop as vold
import tools.validate_engine5_v21_v_rebound_state_machine as vsm
import tools.validate_engine5_v21_v_rebound_reaccel as vra
import tools.validate_engine5_v21_v_rebound_momentum_preservation as vmp
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
PERSIST_SRC = OUT_DIR / 'slow_turn_persistence_candidates.csv'
OUT_SUMMARY = OUT_DIR / 'integrated_full_history_summary.csv'
OUT_TRADES = OUT_DIR / 'integrated_full_history_trades.csv'
OUT_SIGNALS = OUT_DIR / 'integrated_full_history_signals.csv'

THRESHOLD = 50
FEE_RT_PCT = 0.25
V20_RAW = 52.0
V20_REL = 1.45
V20_EXTREME_CAP = 8.0  # structural guard proxy from in-sample plateau; NOT production frozen.

# Slow-turn provisional structural settings.
NEAR_PX_MIN = 0.75
NEAR_EXTENSION_MAX = 4.0
MID_P5_MIN = 0.60
MID_P1_MIN = 0.60
MID_PX_MIN = 1.00
BOUNDARY_MACD_MIN = 30.0
BOUNDARY_RSI_MIN = 10.0
BOUNDARY_PX_MIN = 1.50

# V-rebound selected structural settings.
V_RAW_MIN = 30.0
V_LEG_MIN = 2.0
V_STOP_CAP = 2.0
V_VOL_MIN = 1.0
V_GAP_KEEP_MIN = 0.9
RUN_ACTIVATE_PCT = 1.0  # descriptive broad activation; 1/2/3% sensitivity matched in current sample.


def n(x): return str(x).zfill(6)

def f(x):
    try:
        y=float(x)
        return y if np.isfinite(y) else np.nan
    except Exception:
        return np.nan

def stat(label, tr):
    g=pd.to_numeric(tr.pnl_pct,errors='coerce').dropna() if len(tr) else pd.Series(dtype=float)
    net=g-FEE_RT_PCT
    gp=float(net[net>0].sum()) if len(net) else 0.0
    gl=float(-net[net<0].sum()) if len(net) else 0.0
    return dict(label=label,trades=len(net),wins=int((net>0).sum()),losses=int((net<=0).sum()),
                win_pct=float((net>0).mean()*100) if len(net) else 0.0,
                net_sum_pct=float(net.sum()) if len(net) else 0.0,
                avg_net_pct=float(net.mean()) if len(net) else 0.0,
                pf=(gp/gl if gl>0 else np.inf),max_loss_pct=float(net.min()) if len(net) else np.nan)


def classify_slow(r):
    z=f(r.zero_cross_bars); p5=f(r.joint5_persistence); p1=f(r.joint1_persistence)
    px=f(r.price_progress_1m_pct); gd=f(r.gap_delta_5m); rs=f(r.rsi_slope_5m); ext=f(r.close_progress_6m_pct)
    if not np.isfinite(z): return False,'INVALID'
    if z<=1.5:
        return bool(np.isfinite(px) and px>=NEAR_PX_MIN and np.isfinite(ext) and ext<NEAR_EXTENSION_MAX),'NEAR_LE1_5'
    if z<=8.0:
        return bool(np.isfinite(p5) and p5>=MID_P5_MIN and np.isfinite(p1) and p1>=MID_P1_MIN and np.isfinite(px) and px>=MID_PX_MIN),'MID_1_5_8'
    if z<=12.0:
        return bool(np.isfinite(gd) and gd>=BOUNDARY_MACD_MIN and np.isfinite(rs) and rs>=BOUNDARY_RSI_MIN and np.isfinite(px) and px>=BOUNDARY_PX_MIN),'BOUNDARY_8_12'
    return False,'DEEP_GT12'


def entry_extension_5m(scored, sym, ts):
    q=scored[n(sym)]
    q=q[q.time<=pd.Timestamp(ts)]
    if q.empty:return np.nan
    r=q.iloc[-1]
    close=f(r.get('close',np.nan)); mid=f(r.get('mid',np.nan))
    if not(np.isfinite(close) and np.isfinite(mid) and mid!=0):return np.nan
    return (close/mid-1.0)*100.0


def build_sources(raw,cfg,scored,strength,completed,micros):
    # V20 protected stream.
    ev10=sweep.filt_open(v8.pack_entry_events(scored))
    ev16,waits=v16.build_wait_events(ev10,raw,cfg,False)
    ev17,_,_=v17b.build_v17b(ev16,scored,waits)
    ev18,_=h.build_veto_stream(ev17,micros)
    ev20,_=ms.filter_events(ev18,strength,raw_min=V20_RAW,rel_min=V20_REL)

    tagged=[]
    for ts,cs in ev20.items():
        for c in cs:
            ext=entry_extension_5m(scored,c[0],ts)
            if np.isfinite(ext) and ext>=V20_EXTREME_CAP:
                continue
            tagged.append(dict(source='V20',symbol=n(c[0]),time=pd.Timestamp(ts),event=c,meta={}))

    # Slow-turn provisional selection.
    if not PERSIST_SRC.exists():
        raise FileNotFoundError(f'{PERSIST_SRC} missing; run slow-turn persistence diagnostic first')
    base_cand=sri.reconstruct_base_candidates(raw,cfg,scored,completed,micros)
    base_cand['symbol']=base_cand.symbol.astype(str).str.zfill(6)
    base_cand['entry_time']=pd.to_datetime(base_cand.entry_time)
    persist=pd.read_csv(PERSIST_SRC)
    persist['symbol']=persist.symbol.astype(str).str.zfill(6)
    persist['entry_time']=pd.to_datetime(persist.entry_time)
    keep=['symbol','entry_time','joint5_persistence','joint1_persistence','price_progress_1m_pct']
    sx=base_cand.merge(persist[keep],on=['symbol','entry_time'],how='inner',validate='one_to_one')
    ext_rows=[]
    for _,r in sx.iterrows():
        ext_rows.append(sab.metric_window(micros[n(r.symbol)],pd.Timestamp(r.entry_time)))
    sx=pd.concat([sx.reset_index(drop=True),pd.DataFrame(ext_rows)],axis=1)
    masks=[]; regs=[]
    for _,r in sx.iterrows():
        ok,rg=classify_slow(r); masks.append(ok); regs.append(rg)
    sx['regime']=regs
    ssel=sx[np.asarray(masks,dtype=bool)].copy()
    sev=szd.event_stream(ssel)
    slow_map={(pd.Timestamp(r.entry_time),n(r.symbol)):r.regime for _,r in ssel.iterrows()}
    for ts,cs in sev.items():
        for c in cs:
            tagged.append(dict(source='SLOW_TURN',symbol=n(c[0]),time=pd.Timestamp(ts),event=c,
                               meta={'regime':slow_map.get((pd.Timestamp(ts),n(c[0])),'UNKNOWN')}))

    # V rebound selected cohort.
    vall=[]; vfeatures={}
    for sym,bars in raw.items():
        pf,m=vold.load_cache(sym,bars,cfg,completed[sym])
        z=vsm.add_features(pf,m,bars).sort_values('time').reset_index(drop=True)
        vfeatures[sym]=z
        c=vsm.state_candidates(sym,z,scored[sym],V_RAW_MIN,V_LEG_MIN)
        if len(c):vall.append(c)
    vcand=pd.concat(vall,ignore_index=True) if vall else pd.DataFrame()
    if len(vcand):
        vcand=vra.add_pullback_reaccel(vcand,vfeatures)
        vcand=vmp.add_preservation(vcand,vfeatures)
        q=vcand[(vcand.stop_dist_pct<=V_STOP_CAP)&vcand.reaccel_pass&
                (pd.to_numeric(vcand.volume_accel,errors='coerce')>=V_VOL_MIN)&
                vcand.rsi_positive_all&
                (pd.to_numeric(vcand.gap_keep_ratio,errors='coerce')>=V_GAP_KEEP_MIN)].copy()
        q['day']=pd.to_datetime(q.time).dt.date
        q=q.sort_values('time').drop_duplicates(['symbol','day'],keep='first')
        for _,r in q.iterrows():
            tagged.append(dict(source='V_REBOUND',symbol=n(r.symbol),time=pd.Timestamp(r.time),event=r.event,
                               meta={'structural_stop':float(r.structural_stop)}))

    return tagged


def simulate(packed,state_events,tagged):
    by_time={}
    for x in tagged: by_time.setdefault(pd.Timestamp(x['time']),[]).append(x)
    positions={}; trades=[]; current_state={}; last_price={}; last_ts=None

    def realize(pos,frac,price):
        frac=min(float(frac),pos['remaining'])
        if frac<=0:return
        pos['realized']+=frac*(float(price)/pos['entry_price']-1.0); pos['remaining']-=frac

    def close_pos(sym,price,ts,reason):
        pos=positions[sym]
        pnl=pos['realized']+pos['remaining']*(float(price)/pos['entry_price']-1.0)
        trades.append(dict(symbol=sym,entry_time=pos['entry_time'],exit_time=pd.Timestamp(ts),
                           entry_price=pos['entry_price'],exit_price=float(price),pnl_pct=pnl*100.0,
                           reason=reason,source=pos['source']))
        del positions[sym]

    for ts,minute,rows in packed:
        last_ts=ts
        if ts in state_events: current_state.update(state_events[ts])
        for sym in list(positions):
            pos=positions.get(sym); rr=rows.get(sym)
            if pos is None or rr is None: continue
            closep,low,high,iu,il,ou,spread1,rsi1=rr
            closep=float(closep); low=float(low); high=float(high); last_price[sym]=closep
            trend_up,outer_expanding,mid_slope8,spread5,rsi5=current_state.get(sym,(False,False,np.nan,np.nan,np.nan))
            fade_votes=int(np.isfinite(mid_slope8) and mid_slope8<=0)+int(np.isfinite(spread5) and spread5<=0)+int(np.isfinite(rsi5) and rsi5<=0)
            clear_5m_collapse=(not trend_up) and fade_votes>=2
            fast_fade=np.isfinite(spread1) and spread1<=0 and np.isfinite(rsi1) and rsi1<=0
            elapsed=(pd.Timestamp(ts)-pos['entry_time']).total_seconds()/60.0
            tight=pos['breakout_entry'] and elapsed<multi.TIGHT_MINUTES
            gross_ret=(closep/pos['entry_price']-1.0)*100.0
            if pos['source']=='V_REBOUND' and not pos['run_mode'] and gross_ret>=RUN_ACTIVATE_PCT:
                pos['run_mode']=True

            if minute>=base.FORCE_FLAT_MINUTE:
                close_pos(sym,closep,ts,'SESSION_FORCE_FLAT')
            elif pos['source']=='V_REBOUND' and (not pos['run_mode']) and low<=pos['v_structural_stop']:
                close_pos(sym,pos['v_structural_stop'],ts,'V_HIGHER_LOW_STRUCTURAL_STOP')
            elif pos['source']!='V_REBOUND' and low<=pos['stop_price']:
                close_pos(sym,pos['stop_price'],ts,'INITIAL_STRUCTURAL_STOP')
            elif pos['source']=='V_REBOUND' and pos['run_mode']:
                # Successful V: ignore fast momentum fade. Current structural evidence does not justify
                # a tighter causal exit than session-end/clear future price-structure failure.
                continue
            elif tight and low<=pos['completed_hwm']*(1.-multi.HWM_DD):
                close_pos(sym,pos['completed_hwm']*(1.-multi.HWM_DD),ts,'BREAKOUT_FIRST10_HWM_1PCT_EXIT')
            elif tight:
                pos['completed_hwm']=max(pos['completed_hwm'],high)
            elif not pos['tp1_done']:
                if high>=pos['tp1_price']:
                    realize(pos,.50,pos['tp1_price']); pos['tp1_done']=True; pos['tp1_bar_high']=high; pos['post_tp1_high']=high; pos['fade_armed']=False; pos['fast_fade_streak']=0
                elif clear_5m_collapse:
                    close_pos(sym,closep,ts,'PRE_TP1_CLEAR_TREND_COLLAPSE')
            else:
                fresh=high>max(pos['tp1_bar_high'],pos['post_tp1_high'])
                outer=trend_up and outer_expanding and np.isfinite(ou) and high>=ou
                if fresh or outer: pos['fade_armed']=True
                pos['post_tp1_high']=max(pos['post_tp1_high'],high)
                pos['fast_fade_streak']=pos['fast_fade_streak']+1 if pos['fade_armed'] and fast_fade else 0
                if pos['fade_armed'] and pos['fast_fade_streak']>=2:
                    close_pos(sym,closep,ts,'FAST_1M_MOMENTUM_FADE_EXIT')
                else:
                    if sym in positions and (not pos['tp2_done']) and outer:
                        realize(pos,pos['remaining']*.50,ou); pos['tp2_done']=True
                    if sym in positions and pos['tp2_done'] and np.isfinite(il) and closep<il:
                        close_pos(sym,closep,ts,'INNER_LOWER_CLOSE_EXIT')

        if minute<base.NO_ENTRY_MINUTE:
            for item in by_time.get(pd.Timestamp(ts),[]):
                sym=item['symbol']; c=item['event']
                if sym in positions or c[2]<float(THRESHOLD): continue
                _,closep,score,msv,rsv,band_r,stop_dist,entry_iu,entry_il,entry_ou,entry_mid,extended,breakout=c
                positions[sym]=dict(symbol=sym,entry_time=pd.Timestamp(ts),entry_price=float(closep),
                    stop_price=float(closep)-float(stop_dist),tp1_price=float(closep)+2.*float(band_r),
                    remaining=1.,realized=0.,tp1_done=False,tp2_done=False,tp1_bar_high=np.nan,
                    post_tp1_high=-np.inf,fade_armed=False,fast_fade_streak=0,breakout_entry=bool(breakout),
                    completed_hwm=float(closep),source=item['source'],
                    v_structural_stop=float(item['meta'].get('structural_stop',np.nan)),run_mode=False)
                last_price[sym]=float(closep)

    if last_ts is not None:
        for sym in list(positions):
            if sym in last_price: close_pos(sym,last_price[sym],last_ts,'END_OF_DATA')
    return pd.DataFrame(trades)


def main():
    OUT_DIR.mkdir(parents=True,exist_ok=True)
    raw={n(k):v for k,v in load_data().items()}
    base_cfg=DoubleBollingerEngine5Config()
    cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    packed=v8.base.pack_exit_events(raw,base_cfg)
    states=base.pack_state_events(base.build_cfg_frames(raw,base_cfg))
    frames0=base.build_cfg_frames(raw,cfg)
    f10={n(s):v10._refine_entry_frame(f) for s,f in frames0.items()}
    scored={n(s):f for s,f in reweight(f10,cfg,0.0).items()}
    strength={s:ms.add_strength(f) for s,f in scored.items()}
    completed={s:rt.add_completed_strength(f) for s,f in scored.items()}
    micros={s:h.build_micro(raw[s],cfg) for s in raw}

    # Baseline V20 untouched for regression reference.
    ev10=sweep.filt_open(v8.pack_entry_events(scored)); ev16,waits=v16.build_wait_events(ev10,raw,cfg,False)
    ev17,_,_=v17b.build_v17b(ev16,scored,waits); ev18,_=h.build_veto_stream(ev17,micros)
    ev20,_=ms.filter_events(ev18,strength,raw_min=V20_RAW,rel_min=V20_REL)
    base_tr=multi.simulate_multi(packed,ev20,states,THRESHOLD)
    sb=stat('V20_BASELINE',base_tr)

    tagged=build_sources(raw,cfg,scored,strength,completed,micros)
    sig=pd.DataFrame([{k:v for k,v in x.items() if k!='event'} for x in tagged])
    if len(sig):
        sig['time']=pd.to_datetime(sig.time)
        sig.to_csv(OUT_SIGNALS,index=False)

    tr=simulate(packed,states,tagged)
    if len(tr): tr.to_csv(OUT_TRADES,index=False)
    si=stat('INTEGRATED',tr)
    summary=pd.DataFrame([sb,si])
    summary.to_csv(OUT_SUMMARY,index=False)

    print('\n=== ENGINE5 INTEGRATED FULL-HISTORY VALIDATION ===')
    print('Source ownership: explicit V20 / SLOW_TURN / V_REBOUND.')
    print(f'V20 extreme-extension guard proxy: dist-to-mid < {V20_EXTREME_CAP:.1f}% (structural proxy, not production frozen).')
    print('V-rebound: Higher-Low defensive stop before RUN; after RUN, fast momentum fade is ignored.')
    print('\n=== SUMMARY ===')
    print(summary.to_string(index=False))
    print('\n=== SIGNAL COUNTS ===')
    print(sig.groupby('source').size().to_string() if len(sig) else 'NONE')
    print('\n=== TRADE COUNTS BY SOURCE ===')
    print(tr.groupby('source').agg(trades=('pnl_pct','size'),gross_sum=('pnl_pct','sum')).to_string() if len(tr) else 'NONE')
    print('\n=== INTEGRATED TRADES ===')
    show=['source','symbol','entry_time','exit_time','pnl_pct','reason']
    print(tr[show].sort_values('entry_time').to_string(index=False) if len(tr) else 'NONE')
    print('\nReading guardrails:')
    print('- This is the first structural integration pass, not final production performance.')
    print('- V20 baseline row must remain 39 trades / 17 wins / +20.012131% before the integrated guard is applied.')
    print('- Slow-turn exact thresholds remain provisional; MID is under-sampled.')
    print('- V-run exit is intentionally permissive because current evidence did not justify a tighter structural failure rule.')
    print('WROTE',OUT_SUMMARY)
    print('WROTE',OUT_TRADES)
    print('WROTE',OUT_SIGNALS)

if __name__=='__main__': main()
