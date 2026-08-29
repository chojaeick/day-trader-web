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
from live_server.double_bollinger_engine5_v16 import DoubleBollingerEngine5V16
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
THRESHOLD = 50.0
OPEN_MINUTE = 9 * 60 + 10


def pf_from_series(x: pd.Series) -> float:
    a = pd.to_numeric(x, errors='coerce').dropna()
    gains = float(a[a > 0].sum())
    losses = float(-a[a < 0].sum())
    return gains / losses if losses > 0 else np.inf


def metrics(t: pd.DataFrame, cost_bps: float = 0.0) -> dict:
    if t is None or t.empty:
        return {'trades':0,'win':np.nan,'avg':np.nan,'gross':0.0,'pf':np.nan,'maxloss':np.nan,'tp1':np.nan,'tp2':np.nan,'avg_r':np.nan,'mdd':np.nan}
    p = pd.to_numeric(t['pnl_pct'], errors='coerce').fillna(0.0) - float(cost_bps) / 100.0
    eq = p.cumsum()
    dd = eq - eq.cummax()
    return {
        'trades': int(len(t)),
        'win': float((p > 0).mean() * 100.0),
        'avg': float(p.mean()),
        'gross': float(p.sum()),
        'pf': float(pf_from_series(p)),
        'maxloss': float(p.min()),
        'tp1': float(pd.Series(t.get('first_tp_done', False)).astype(bool).mean() * 100.0),
        'tp2': float(pd.Series(t.get('second_tp_done', False)).astype(bool).mean() * 100.0),
        'avg_r': float(pd.to_numeric(t.get('r_pct', np.nan), errors='coerce').mean()),
        'mdd': float(dd.min()) if len(dd) else np.nan,
    }


def fmt(m: dict) -> str:
    return (f"{m['trades']}t win={m['win']:.2f} avg={m['avg']:+.4f} gross={m['gross']:+.4f} "
            f"pf={m['pf']:.3f} max={m['maxloss']:+.4f} tp1={m['tp1']:.2f} tp2={m['tp2']:.2f} "
            f"avgR={m['avg_r']:.3f} mdd={m['mdd']:+.4f}")


def filter_open(ev):
    return {ts:list(rows) for ts,rows in ev.items() if pd.Timestamp(ts).hour*60 + pd.Timestamp(ts).minute >= OPEN_MINUTE}


def sim(packed_exits, state_events, ev, th=THRESHOLD):
    return v8.v7.simulate_v7(packed_exits, ev, state_events, th)[0]


def keys(t):
    if t is None or t.empty:
        return set()
    return set(zip(t.symbol.astype(str).str.zfill(6), pd.to_datetime(t.entry_time).astype(str)))


def event_filter(ev, keep_fn):
    out={}
    for ts, rows in ev.items():
        q=[r for r in rows if keep_fn(pd.Timestamp(ts), r)]
        if q: out[ts]=q
    return out


def trade_rows_for_keys(t, ks):
    if t.empty or not ks:
        return t.iloc[0:0].copy()
    k = list(zip(t.symbol.astype(str).str.zfill(6), pd.to_datetime(t.entry_time).astype(str)))
    return t[[x in ks for x in k]].copy()


def classify_path_changes(t10, t16, waits):
    k10, k16 = keys(t10), keys(t16)
    removed = k10-k16
    added = k16-k10
    wait_orig=set()
    delayed=set()
    no_re=set()
    for r in waits.itertuples(index=False):
        orig=(str(r.symbol).zfill(6), str(pd.Timestamp(r.signal_time)))
        wait_orig.add(orig)
        if str(r.status)=='REACCEL_ENTRY' and not pd.isna(r.delayed_time):
            delayed.add((str(r.symbol).zfill(6), str(pd.Timestamp(r.delayed_time))))
        else:
            no_re.add(orig)
    direct_removed=removed & wait_orig
    direct_added=added & delayed
    path_removed=removed-direct_removed
    path_added=added-direct_added
    rows=[]
    for typ, group in [('DIRECT_REMOVED',direct_removed),('DIRECT_DELAYED_ADDED',direct_added),('PATH_REMOVED',path_removed),('PATH_ADDED',path_added)]:
        for sym,ts in sorted(group): rows.append({'type':typ,'symbol':sym,'entry_time':ts})
    return pd.DataFrame(rows), {'removed':removed,'added':added,'direct_removed':direct_removed,'direct_added':direct_added,'path_removed':path_removed,'path_added':path_added,'no_reaccel_signals':no_re}


def by_symbol(t, label):
    rows=[]
    for sym,g in t.groupby(t.symbol.astype(str).str.zfill(6)):
        m=metrics(g); rows.append({'engine':label,'symbol':sym,**m})
    return pd.DataFrame(rows)


def by_date(t, label):
    z=t.copy(); z['date']=pd.to_datetime(z.entry_time).dt.date.astype(str)
    rows=[]
    for day,g in z.groupby('date'):
        m=metrics(g); rows.append({'engine':label,'date':day,**m})
    return pd.DataFrame(rows)


def time_buckets(t, label):
    z=t.copy(); dt=pd.to_datetime(z.entry_time)
    mins=dt.dt.hour*60+dt.dt.minute
    bins=[9*60+10,9*60+30,10*60,11*60,13*60,14*60,15*60+1]
    labs=['09:10-09:29','09:30-09:59','10:00-10:59','11:00-12:59','13:00-13:59','14:00-15:00']
    z['bucket']=pd.cut(mins,bins=bins,labels=labs,right=False,include_lowest=True)
    rows=[]
    for b,g in z.dropna(subset=['bucket']).groupby('bucket', observed=True):
        rows.append({'engine':label,'bucket':str(b),**metrics(g)})
    return pd.DataFrame(rows)


def leave_one_out(packed_exits,state_events,ev10,ev16,t10,t16):
    rows=[]
    syms=sorted(set(t10.symbol.astype(str).str.zfill(6)) | set(t16.symbol.astype(str).str.zfill(6)))
    for sym in syms:
        a=sim(packed_exits,state_events,event_filter(ev10,lambda ts,r: str(r[0]).zfill(6)!=sym))
        b=sim(packed_exits,state_events,event_filter(ev16,lambda ts,r: str(r[0]).zfill(6)!=sym))
        ma,mb=metrics(a),metrics(b)
        rows.append({'kind':'SYMBOL','excluded':sym,'v10_trades':ma['trades'],'v16_trades':mb['trades'],'d_win':mb['win']-ma['win'],'d_gross':mb['gross']-ma['gross'],'d_pf':mb['pf']-ma['pf']})
    days=sorted(set(pd.to_datetime(t10.entry_time).dt.date) | set(pd.to_datetime(t16.entry_time).dt.date))
    for day in days:
        a=sim(packed_exits,state_events,event_filter(ev10,lambda ts,r: ts.date()!=day))
        b=sim(packed_exits,state_events,event_filter(ev16,lambda ts,r: ts.date()!=day))
        ma,mb=metrics(a),metrics(b)
        rows.append({'kind':'DATE','excluded':str(day),'v10_trades':ma['trades'],'v16_trades':mb['trades'],'d_win':mb['win']-ma['win'],'d_gross':mb['gross']-ma['gross'],'d_pf':mb['pf']-ma['pf']})
    return pd.DataFrame(rows)


def lookahead_audit(waits, raw):
    rows=[]
    for r in waits.itertuples(index=False):
        t=pd.Timestamp(r.signal_time)
        d=pd.Timestamp(r.delayed_time) if not pd.isna(r.delayed_time) else pd.NaT
        valid_lifetime = pd.isna(d) or (d>=t and d<t+pd.Timedelta(minutes=5))
        valid_end = pd.isna(d) or (d.hour*60+d.minute < 10*60)
        rows.append({'symbol':str(r.symbol).zfill(6),'signal_time':t,'status':r.status,'delayed_time':d,'within_5m':bool(valid_lifetime),'before_10':bool(valid_end),'pass':bool(valid_lifetime and valid_end)})
    return pd.DataFrame(rows)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    print('=== ENGINE5 V16 FULL ENGINE VALIDATION ===', flush=True)
    print('Scope: current cached 10-day/13-symbol dataset; no Kiwoom/network; no 1/2/3 rerun.', flush=True)

    raw=load_data()
    base_cfg=DoubleBollingerEngine5Config()
    cfg=replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    packed_exits=v8.base.pack_exit_events(raw,base_cfg)
    state_events=base.pack_state_events(base.build_cfg_frames(raw,base_cfg))
    raw_frames=base.build_cfg_frames(raw,cfg)
    f10={sym:v10._refine_entry_frame(f) for sym,f in raw_frames.items()}
    scored=reweight(f10,cfg,0.0)
    ev10=filter_open(v8.pack_entry_events(scored))
    ev16,waits=v16.build_wait_events(ev10,raw,cfg,require_better_price=False)

    t10=sim(packed_exits,state_events,ev10)
    t16=sim(packed_exits,state_events,ev16)
    m10,m16=metrics(t10),metrics(t16)
    print('\n[1] BASELINE / V16')
    print('V10',fmt(m10)); print('V16',fmt(m16))
    print(f"DELTA win={m16['win']-m10['win']:+.2f} gross={m16['gross']-m10['gross']:+.4f} pf={m16['pf']-m10['pf']:+.3f} mdd={m16['mdd']-m10['mdd']:+.4f}")

    changes,parts=classify_path_changes(t10,t16,waits)
    print('\n[2] DIRECT vs SINGLE-POSITION PATH EFFECT')
    print(changes.to_string(index=False) if len(changes) else 'none')
    print('counts=',{k:len(v) for k,v in parts.items() if isinstance(v,set)})

    print('\n[3] WAIT SIGNALS')
    print(waits.to_string(index=False) if len(waits) else 'none')

    sym=pd.concat([by_symbol(t10,'V10'),by_symbol(t16,'V16')],ignore_index=True)
    day=pd.concat([by_date(t10,'V10'),by_date(t16,'V16')],ignore_index=True)
    tb=pd.concat([time_buckets(t10,'V10'),time_buckets(t16,'V16')],ignore_index=True)
    print('\n[4] SYMBOL BREAKDOWN'); print(sym.to_string(index=False))
    print('\n[5] DATE BREAKDOWN'); print(day.to_string(index=False))
    print('\n[6] TIME-OF-DAY BREAKDOWN'); print(tb.to_string(index=False))

    print('\n[7] EXIT BEHAVIOR')
    exit_rows=[]
    for label,t in [('V10',t10),('V16',t16)]:
        for reason,g in t.groupby('reason'):
            exit_rows.append({'engine':label,'reason':reason,'count':len(g),'win':(g.pnl_pct>0).mean()*100,'gross':g.pnl_pct.sum(),'avg':g.pnl_pct.mean()})
    exits=pd.DataFrame(exit_rows)
    print(exits.to_string(index=False))

    print('\n[8] COST / SLIPPAGE STRESS')
    stress=[]
    for bps in [0,5,10,20,30]:
        a,b=metrics(t10,bps),metrics(t16,bps)
        stress.append({'roundtrip_bps':bps,'v10_win':a['win'],'v16_win':b['win'],'v10_gross':a['gross'],'v16_gross':b['gross'],'d_gross':b['gross']-a['gross'],'v10_pf':a['pf'],'v16_pf':b['pf'],'d_pf':b['pf']-a['pf']})
    stress=pd.DataFrame(stress); print(stress.to_string(index=False))

    print('\n[9] LEAVE-ONE-SYMBOL / LEAVE-ONE-DATE OUT')
    loo=leave_one_out(packed_exits,state_events,ev10,ev16,t10,t16)
    print(loo.to_string(index=False))
    print('LOO pf delta worse count=',int((loo.d_pf<0).sum()),'of',len(loo))
    print('LOO gross delta worse count=',int((loo.d_gross<0).sum()),'of',len(loo))

    print('\n[10] LOOKAHEAD / SIGNAL-LIFETIME AUDIT')
    la=lookahead_audit(waits,raw); print(la.to_string(index=False) if len(la) else 'none')
    print('LOOKAHEAD_LIFETIME_PASS=',bool(la['pass'].all()) if len(la) else True)

    print('\n[11] WORST / BEST V16 TRADES')
    cols=['symbol','entry_time','exit_time','entry_price','exit_price','pnl_pct','r_pct','first_tp_done','second_tp_done','reason']
    print('WORST')
    print(t16.nsmallest(min(15,len(t16)),'pnl_pct')[cols].to_string(index=False))
    print('BEST')
    print(t16.nlargest(min(15,len(t16)),'pnl_pct')[cols].to_string(index=False))

    # Save every audit table for manual chart review without rerunning.
    t10.to_csv(OUT/'v10_trades.csv',index=False)
    t16.to_csv(OUT/'v16_trades.csv',index=False)
    waits.to_csv(OUT/'v16_wait_signals.csv',index=False)
    changes.to_csv(OUT/'v10_v16_path_changes.csv',index=False)
    sym.to_csv(OUT/'symbol_breakdown.csv',index=False)
    day.to_csv(OUT/'date_breakdown.csv',index=False)
    tb.to_csv(OUT/'time_breakdown.csv',index=False)
    exits.to_csv(OUT/'exit_breakdown.csv',index=False)
    stress.to_csv(OUT/'cost_stress.csv',index=False)
    loo.to_csv(OUT/'leave_one_out.csv',index=False)
    la.to_csv(OUT/'lookahead_audit.csv',index=False)

    # Conservative stage gate: this is NOT final production approval. It only
    # decides whether V16 survives the current full-dataset structural audit.
    finite_loo=loo[np.isfinite(pd.to_numeric(loo.d_pf,errors='coerce'))]
    structural_pass=(
        m16['gross']>m10['gross'] and m16['pf']>m10['pf'] and m16['win']>=m10['win']
        and (len(la)==0 or bool(la['pass'].all()))
        and (len(finite_loo)==0 or float((finite_loo.d_pf>=0).mean())>=0.80)
        and float((loo.d_gross>=0).mean())>=0.80
        and metrics(t16,20)['gross']>metrics(t10,20)['gross']
    )
    print('\n=== CURRENT-DATASET STRUCTURAL GATE ===')
    print('STRUCTURAL_VALIDATION_PASS=',bool(structural_pass))
    print('NOTE=PASS means V16 survives current dataset audit; it does NOT mean Engine5 production validation is complete.')
    print('[CSV_DIR]',OUT)


if __name__=='__main__':
    main()
