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
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5, DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight, to_5m
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
THRESHOLD = 50
FEE_RT_PCT = 0.25
TARGET_SYM = '950260'
TARGET_DAY = pd.Timestamp('2026-08-21').date()

# Causal intrabar experiment:
# prior COMPLETE 5m bars + current 1m close as provisional close of the forming 5m bar.
# Detect two valid regimes:
#   A) established/upward structure
#   B) falling structure whose slope is clearly improving toward flat/up (inflection)
# In both cases 1m continuity must confirm before entry.
PROV_DELTA_LEVELS = [25.0, 35.0, 45.0, 55.0]
ONE_M_LOOKBACKS = [3, 4]
ONE_M_POS_RATIOS = [0.67, 0.75]
MAX_WAIT_MIN = 4


def finite(x): return h.finite(x)


def build_provisional_5m(raw_bars, cfg):
    b = raw_bars.copy().sort_values('time').reset_index(drop=True)
    b['time'] = pd.to_datetime(b['time'])
    complete = to_5m(b)
    complete['time'] = pd.to_datetime(complete['time'])
    eng = DoubleBollingerEngine5(cfg)
    out=[]
    for _, r1 in b.iterrows():
        ts = pd.Timestamp(r1.time)
        bucket_end = ts.floor('5min') + pd.Timedelta(minutes=5)
        hist = complete[complete.time <= ts.floor('5min')].copy()
        bucket_start = ts.floor('5min')
        cur = b[(b.time >= bucket_start) & (b.time <= ts)]
        if cur.empty:
            continue
        prov = pd.DataFrame([dict(time=bucket_end, open=float(cur.iloc[0].open),
                                  high=float(pd.to_numeric(cur.high).max()),
                                  low=float(pd.to_numeric(cur.low).min()),
                                  close=float(r1.close), volume=float(pd.to_numeric(cur.volume).sum()))])
        z = pd.concat([hist, prov], ignore_index=True).drop_duplicates('time', keep='last').sort_values('time')
        e = eng.enrich(z)
        if len(e) < 2:
            continue
        x=e.iloc[-1]; prev=e.iloc[-2]
        cur_mid_slope=finite(x.mid_slope8); prev_mid_slope=finite(prev.mid_slope8)
        slope_improve=(cur_mid_slope-prev_mid_slope) if np.isfinite(cur_mid_slope) and np.isfinite(prev_mid_slope) else np.nan
        out.append(dict(time=ts, bucket_end=bucket_end, close=float(r1.close),
                        macd=finite(x.macd), signal=finite(x.macd_signal), gap=finite(x.macd_gap),
                        gap_delta=finite(x.macd_gap_delta), golden=bool(x.macd_golden_cross),
                        macd_slope=finite(x.macd_slope), rsi=finite(x.rsi), rsi_slope=finite(x.rsi_slope),
                        mid_slope8=cur_mid_slope, prev_mid_slope8=prev_mid_slope,
                        mid_slope_improve=slope_improve,
                        trend_up=bool(x.trend_up), entry_score=finite(x.entry_score)))
    return pd.DataFrame(out)


def one_m_continuity(m, ts, lookback, min_pos_ratio, reversal_mode=False):
    q=m[m.time <= pd.Timestamp(ts)].tail(lookback)
    if len(q)<lookback: return False, {}
    gaps=pd.to_numeric(q.macd_gap_1m,errors='coerce').to_numpy(float)
    if not np.isfinite(gaps).all(): return False, {}
    d=np.diff(gaps)
    pos_ratio=float((d>0).mean())
    total=float(gaps[-1]-gaps[0])
    neg=float(-d[d<0].sum()) if np.any(d<0) else 0.0
    pos=float(d[d>0].sum()) if np.any(d>0) else 0.0
    retrace=neg/max(pos,1e-9)
    # Reversal/inflection entries need cleaner 1m persistence than already-uptrend entries.
    req_ratio=min(1.0, min_pos_ratio + (0.08 if reversal_mode else 0.0))
    req_retrace=0.25 if reversal_mode else 0.35
    last_macd_slope=finite(q.iloc[-1].macd_slope_1m)
    last_gap_delta=finite(q.iloc[-1].macd_gap_delta_1m)
    ok=bool(total>0 and pos_ratio>=req_ratio and retrace<=req_retrace
            and last_macd_slope>0 and last_gap_delta>0)
    return ok, dict(one_m_start_gap=gaps[0],one_m_end_gap=gaps[-1],one_m_rise=total,
                    one_m_pos_ratio=pos_ratio,one_m_retrace=retrace,
                    one_m_above=bool(gaps[-1]>0), reversal_mode=bool(reversal_mode))


def regime_ok(p):
    cur=finite(p.mid_slope8); prev=finite(p.prev_mid_slope8); improve=finite(p.mid_slope_improve)
    if np.isfinite(cur) and cur > 0:
        return True, 'UPTREND'
    # Downtrend is allowed only when its slope is visibly bending upward toward flat/up.
    # Require current slope less negative than prior slope, positive MACD slope, and positive oscillator expansion.
    if (np.isfinite(cur) and np.isfinite(prev) and np.isfinite(improve)
            and cur <= 0 and improve > 0
            and finite(p.macd_slope) > 0 and finite(p.gap_delta) > 0):
        return True, 'INFLECTION'
    return False, 'REJECT_TREND'


def build_events(scored, micros, provisional, level, lookback, min_pos_ratio):
    events={}; diag=[]; seen=set()
    for sym, pf in provisional.items():
        m=micros[sym]
        armed_until=None
        arm_meta=None
        for _, p in pf.iterrows():
            ts=pd.Timestamp(p.time)
            minute=ts.hour*60+ts.minute
            if minute<9*60+10 or minute>=base.NO_ENTRY_MINUTE: continue
            rg_ok, regime=regime_ok(p)
            strong=bool(rg_ok and np.isfinite(p.gap_delta) and p.gap_delta>=level and np.isfinite(p.macd_slope) and p.macd_slope>0)
            cross=bool(rg_ok and p.golden and np.isfinite(p.gap_delta) and p.gap_delta>=level*0.60)
            if strong or cross:
                armed_until=ts+pd.Timedelta(minutes=MAX_WAIT_MIN)
                arm_meta=dict(arm_reason='CROSS' if cross else 'IMPULSE', arm_ts=ts,
                              arm_gap_delta=finite(p.gap_delta), arm_regime=regime,
                              arm_mid_slope8=finite(p.mid_slope8),
                              arm_prev_mid_slope8=finite(p.prev_mid_slope8),
                              arm_mid_slope_improve=finite(p.mid_slope_improve))
            if armed_until is None or ts>armed_until: continue
            reversal_mode=bool(arm_meta and arm_meta['arm_regime']=='INFLECTION')
            ok, mm=one_m_continuity(m,ts,lookback,min_pos_ratio,reversal_mode=reversal_mode)
            if not ok: continue
            sf=scored[sym]
            q5=sf[sf.time<=ts.floor('5min')]
            if q5.empty: continue
            row5=q5.iloc[-1]
            ev=h.event_from_5m_row(sym,row5,ts,finite(p.close))
            key=(sym,ts)
            if ev is not None and key not in seen:
                seen.add(key); events.setdefault(ts,[]).append(ev)
                diag.append(dict(symbol=sym,arm_time=arm_meta['arm_ts'],trigger_time=ts,
                                 arm_reason=arm_meta['arm_reason'],arm_regime=arm_meta['arm_regime'],
                                 arm_mid_slope8=arm_meta['arm_mid_slope8'],
                                 arm_prev_mid_slope8=arm_meta['arm_prev_mid_slope8'],
                                 arm_mid_slope_improve=arm_meta['arm_mid_slope_improve'],
                                 prov_gap=finite(p.gap),prov_gap_delta=arm_meta['arm_gap_delta'],
                                 prov_golden=bool(p.golden),**mm))
                armed_until=None; arm_meta=None
    return events,pd.DataFrame(diag)


def stats(label,t):
    g=pd.to_numeric(t.pnl_pct,errors='coerce').dropna() if len(t) else pd.Series(dtype=float)
    n=g-FEE_RT_PCT
    gp=float(n[n>0].sum()) if len(n) else 0.0; gl=float(-n[n<0].sum()) if len(n) else 0.0
    return dict(label=label,trades=len(n),net_wins=int((n>0).sum()),net_win_pct=float((n>0).mean()*100) if len(n) else 0.0,
                net_sum_pct=float(n.sum()) if len(n) else 0.0,net_pf=gp/gl if gl>0 else np.inf,
                gross_sum_pct=float(g.sum()) if len(g) else 0.0)


def main():
    OUT_DIR.mkdir(parents=True,exist_ok=True)
    raw=load_data(); base_cfg=DoubleBollingerEngine5Config(); cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    packed=v8.base.pack_exit_events(raw,base_cfg)
    states=base.pack_state_events(base.build_cfg_frames(raw,base_cfg))
    frames=base.build_cfg_frames(raw,cfg); f10={s:v10._refine_entry_frame(f) for s,f in frames.items()}; scored=reweight(f10,cfg,0.0)
    micros={str(s).zfill(6):h.build_micro(b,cfg) for s,b in raw.items()}
    provisional={str(s).zfill(6):build_provisional_5m(b,cfg) for s,b in raw.items()}

    raw_entries=v8.pack_entry_events(scored); ev10=sweep.filt_open(raw_entries); ev16,waits=v16.build_wait_events(ev10,raw,cfg,False)
    ev17,_,_=v17b.build_v17b(ev16,scored,waits); ev18,_=h.build_veto_stream(ev17,micros)
    t18=multi.simulate_multi(packed,ev18,states,THRESHOLD)
    print('=== LIVE FORMING-5M -> 1M CONTINUITY + SLOPE INFLECTION VALIDATION ===')
    print('No future 5m bar. Downtrend is not an automatic veto: improving 5m slope + strong oscillator + clean 1m continuity can enter.')
    print(pd.DataFrame([stats('V18_REFERENCE',t18)]).to_string(index=False))

    tp=provisional[TARGET_SYM]
    tp=tp[(pd.to_datetime(tp.time).dt.date==TARGET_DAY)&(tp.time.dt.strftime('%H:%M')>='09:45')&(tp.time.dt.strftime('%H:%M')<='12:30')].copy()
    if len(tp):
        rg=tp.apply(lambda r: regime_ok(r)[1],axis=1)
        tp['regime']=rg
    print('\n=== 950260 PROVISIONAL 5M DIAG 09:45-12:30 ===')
    cols=['time','close','gap','gap_delta','golden','macd_slope','mid_slope8','prev_mid_slope8','mid_slope_improve','regime','rsi','rsi_slope']
    print(tp[[c for c in cols if c in tp.columns]].to_string(index=False))

    rows=[]; targets=[]
    for level in PROV_DELTA_LEVELS:
        for lb in ONE_M_LOOKBACKS:
            for pr in ONE_M_POS_RATIOS:
                ev,d=build_events(scored,micros,provisional,level,lb,pr)
                t=multi.simulate_multi(packed,ev,states,THRESHOLD)
                label=f'P5D{level:.0f}_1MLB{lb}_POS{pr:.2f}'
                s=stats(label,t); s.update(prov_delta=level,lookback=lb,min_pos_ratio=pr,triggered=len(d)); rows.append(s)
                if len(d):
                    q=d[(d.symbol==TARGET_SYM)&(pd.to_datetime(d.trigger_time).dt.date==TARGET_DAY)].copy()
                    if len(q): q.insert(0,'label',label); targets.append(q)
    summary=pd.DataFrame(rows).sort_values(['net_sum_pct','net_pf','net_win_pct'],ascending=False)
    print('\n=== SWEEP SUMMARY ==='); print(summary.to_string(index=False))
    target=pd.concat(targets,ignore_index=True) if targets else pd.DataFrame()
    print('\n=== 950260 TRIGGERS 2026-08-21 ==='); print(target.to_string(index=False) if len(target) else 'NONE')
    summary.to_csv(OUT_DIR/'v20_live_5m_1m_summary.csv',index=False)
    target.to_csv(OUT_DIR/'v20_950260_live_5m_1m.csv',index=False)
    print('\nWROTE',OUT_DIR/'v20_live_5m_1m_summary.csv')
    print('WROTE',OUT_DIR/'v20_950260_live_5m_1m.csv')

if __name__=='__main__': main()
