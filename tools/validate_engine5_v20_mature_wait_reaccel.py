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
import tools.diagnose_engine5_v19_strength_score as v19
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

THRESHOLD = 50
OPEN_MINUTE = 9 * 60 + 10
RSI_MATURE = 70.0
EDGE_WEAK = 0.10
DBB_STRONG = 15.0


def filt_open(ev):
    return {ts: rows for ts, rows in ev.items() if pd.Timestamp(ts).hour * 60 + pd.Timestamp(ts).minute >= OPEN_MINUTE}


def metrics(name, trades):
    if trades is None or trades.empty:
        return dict(name=name,trades=0,wins=0,losses=0,win_rate=0.0,gross_pct=0.0,avg_pct=0.0,pf=np.nan,max_loss_pct=np.nan)
    p = pd.to_numeric(trades.pnl_pct, errors='coerce').dropna()
    gp = float(p[p>0].sum()); gl = float(-p[p<0].sum())
    return dict(name=name,trades=len(p),wins=int((p>0).sum()),losses=int((p<=0).sum()),win_rate=float((p>0).mean()*100),gross_pct=float(p.sum()),avg_pct=float(p.mean()),pf=(gp/gl if gl>0 else np.inf),max_loss_pct=float(p.min()))


def pm(m):
    print(f"{m['name']}: trades={m['trades']} wins={m['wins']} losses={m['losses']} win={m['win_rate']:.2f}% gross={m['gross_pct']:+.4f}% avg={m['avg_pct']:+.4f}% pf={m['pf']:.3f} maxloss={m['max_loss_pct']:+.4f}%")


def v16_delayed_keys(waits):
    if waits is None or waits.empty:
        return set()
    q = waits[waits.status == 'REACCEL_ENTRY'] if 'status' in waits.columns else waits.iloc[0:0]
    return {(str(r.symbol).zfill(6), pd.Timestamp(r.delayed_time)) for r in q.itertuples(index=False) if pd.notna(r.delayed_time)}


def strict_reaccel(prev, row):
    vals = [prev.macd_slope_1m,row.macd_slope_1m,prev.spread_1m,row.spread_1m,prev.rsi_slope_1m,row.rsi_slope_1m]
    if not all(np.isfinite(float(x)) for x in vals): return False
    return bool(float(row.macd_slope_1m)>0 and float(row.macd_slope_1m)>float(prev.macd_slope_1m)
                and float(row.spread_1m)>float(prev.spread_1m)
                and float(row.rsi_slope_1m)>0 and float(row.rsi_slope_1m)>float(prev.rsi_slope_1m))


def delayed_event(event, price):
    x = list(event); x[1] = float(price); return tuple(x)


def build_v20(ev17c_base, diag, raw, cfg, waits):
    rich = {str(sym).zfill(6): v16.build_rich_micro(raw[sym], cfg) for sym in raw}
    exempt_v16 = v16_delayed_keys(waits)
    out = {ts:list(rows) for ts,rows in ev17c_base.items()}
    records=[]
    for ts in sorted(list(ev17c_base.keys())):
        t=pd.Timestamp(ts)
        for e in list(ev17c_base.get(ts, [])):
            sym=str(e[0]).zfill(6)
            if bool(e[-1]) or (sym,t) in exempt_v16: continue
            q=diag[(diag.symbol==sym)&(pd.to_datetime(diag.time)==t)]
            if q.empty: continue
            d=q.iloc[0]
            mature = (str(d.ratio_mode)=='RATIO' and np.isfinite(float(d.ratio_edge))
                      and float(d.ratio_edge)<=EDGE_WEAK
                      and np.isfinite(float(d.rsi)) and float(d.rsi)>=RSI_MATURE
                      and np.isfinite(float(d.dbb_score)) and float(d.dbb_score)>=DBB_STRONG)
            if not mature: continue
            out[t]=[x for x in out.get(t,[]) if x is not e and x!=e]
            if not out[t]: out.pop(t,None)
            m=rich.get(sym)
            chosen=None; prev=None
            if m is not None:
                qq=m[(pd.to_datetime(m.time)>=t)&(pd.to_datetime(m.time)<t+pd.Timedelta(minutes=5))]
                for row in qq.itertuples(index=False):
                    if prev is not None and strict_reaccel(prev,row):
                        chosen=row; break
                    prev=row
            rec=dict(symbol=sym,signal_time=t,signal_price=float(e[1]),rsi=float(d.rsi),dbb_score=float(d.dbb_score),ratio_edge=float(d.ratio_edge),status='NO_REACCEL',delayed_time=pd.NaT,delayed_price=np.nan)
            if chosen is not None:
                dt=pd.Timestamp(chosen.time); px=float(chosen.close)
                out.setdefault(dt,[]).append(delayed_event(e,px))
                rec.update(status='REACCEL_ENTRY',delayed_time=dt,delayed_price=px)
            records.append(rec)
    return out,pd.DataFrame(records)


def key_status(waitmap,sym,ts):
    if waitmap.empty: return 'NOT_WAITED'
    q=waitmap[(waitmap.symbol==sym)&(pd.to_datetime(waitmap.signal_time)==pd.Timestamp(ts))]
    return 'NOT_WAITED' if q.empty else str(q.iloc[0].status)


def main():
    raw=load_data(); base_cfg=DoubleBollingerEngine5Config(); cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    packed_exits=v8.base.pack_exit_events(raw,base_cfg)
    state_events=base.pack_state_events(base.build_cfg_frames(raw,base_cfg))
    raw_frames=base.build_cfg_frames(raw,cfg)
    f10={s:v10._refine_entry_frame(f) for s,f in raw_frames.items()}; scored=reweight(f10,cfg,0.0)
    frames=v19.build_score_frames(scored)
    ev10=filt_open(v8.pack_entry_events(scored)); ev16,waits=v16.build_wait_events(ev10,raw,cfg,False)
    ev17b,added,skipped=v17b.build_v17b(ev16,scored,waits)
    _,diag=v19.rescore_events(ev17b,frames)
    ev20,waitmap=build_v20(ev17b,diag,raw,cfg,waits)

    print('=== ENGINE5 V20 MATURE-TREND WAIT -> STRICT 1M REACCEL ===')
    print('Baseline is V17C only. No score cutoff change, no live-engine change.')
    print(f'WAIT trigger: ordinary RATIO-mode + RSI>={RSI_MATURE:.0f} + DBB strength>={DBB_STRONG:.0f}/20 + ratio_edge<={EDGE_WEAK:.2f}.')
    print('WAIT is not a permanent veto: same 5m signal may re-enter only after strict 1m MACD slope/spread + RSI slope reacceleration. New 5m signals are independent. Breakouts and existing V16 delayed entries are exempt.')

    cases=[('FAIL_950260_0821_1000','950260','2026-08-21 10:00:00+09:00'),('FAIL_080220_0811_0955','080220','2026-08-11 09:55:00+09:00'),('FAIL_257720_0818_1430','257720','2026-08-18 14:30:00+09:00'),('GOOD_257720_0812_0910','257720','2026-08-12 09:10:00+09:00'),('BREAKOUT_257720_0818_1420','257720','2026-08-18 14:20:00+09:00')]
    print('\n=== MANUAL REGRESSION ===')
    for label,sym,ts in cases: print(label,'=>',key_status(waitmap,sym,ts))

    print('\n=== WAIT MAP ===')
    print(waitmap.to_string(index=False) if len(waitmap) else 'none')
    if len(waitmap):
        print(f"WAIT_TOTAL={len(waitmap)} REACCEL={(waitmap.status=='REACCEL_ENTRY').sum()} NO_REACCEL={(waitmap.status=='NO_REACCEL').sum()}")

    ta=v17c.simulate_unconditional_hwm(packed_exits,ev17b,state_events,THRESHOLD)
    tb=v17c.simulate_unconditional_hwm(packed_exits,ev20,state_events,THRESHOLD)
    ma=metrics('A_V17C_BASELINE',ta); mb=metrics('B_V20_MATURE_WAIT_REACCEL',tb)
    print('\n=== FULL PATH ==='); pm(ma); pm(mb)
    print(f"DELTA: trades={mb['trades']-ma['trades']:+d} win={mb['win_rate']-ma['win_rate']:+.2f}pp gross={mb['gross_pct']-ma['gross_pct']:+.4f}%p avg={mb['avg_pct']-ma['avg_pct']:+.4f}%p pf={mb['pf']-ma['pf']:+.3f}")

    print('\n=== WAITED CANDIDATES: ORIGINAL VS DELAYED INDEPENDENT ===')
    pairs=[]
    for r in waitmap.itertuples(index=False):
        t=pd.Timestamp(r.signal_time); sym=str(r.symbol).zfill(6)
        orig=[e for e in ev17b.get(t,[]) if str(e[0]).zfill(6)==sym]
        if not orig: continue
        a=v17c.simulate_unconditional_hwm(packed_exits,{t:[orig[0]]},state_events,THRESHOLD)
        old=float(a.iloc[0].pnl_pct) if len(a) else np.nan
        new=np.nan
        if r.status=='REACCEL_ENTRY':
            dt=pd.Timestamp(r.delayed_time); de=[e for e in ev20.get(dt,[]) if str(e[0]).zfill(6)==sym]
            if de:
                b=v17c.simulate_unconditional_hwm(packed_exits,{dt:[de[0]]},state_events,THRESHOLD)
                if len(b): new=float(b.iloc[0].pnl_pct)
        pairs.append((sym,t,r.status,old,new))
    for x in pairs: print(x)

    out='/home/ubuntu/day-trader-api/engine5_v16_full_validation/v20_mature_wait_reaccel.csv'
    waitmap.to_csv(out,index=False); print('\n[CSV]',out)

if __name__=='__main__': main()
