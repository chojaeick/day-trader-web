from __future__ import annotations

"""KR V22 timing diagnostic: advance an EXISTING tagged entry by one minute when
causal provisional score has been rising steadily into T-1.

IMPORTANT: this is a timing-opportunity diagnostic, not deployable production logic,
because candidate identity/source is anchored by the later existing tagged event at T.
If this improves results, the next step is to rebuild the same rule causally upstream.

Rule at T-1:
  - scores T-3 <= T-2 <= T-1 (sustained increase)
  - last step T-2 -> T-1 < 20 points (avoid the validated late-spike pattern)
  - effective_score = score(T-1) + min(BONUS_CAP, score(T-1)-score(T-3))
  - if effective_score >= 50, shift the tagged entry from T to T-1

Cases: A baseline, E0/E5/E10/E15. E0 means no bonus: only already >=50 at T-1.
"""

from dataclasses import replace
from pathlib import Path
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_engine5_v17_volume_bypass_tight10 as v17
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_macd_strength as ms
import tools.validate_engine5_v20_regime_transition as rt
import tools.validate_engine5_integrated_full_history as integ
import tools.diagnose_engine5_v22_preentry_minute_scores as diag
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5, DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT = Path('/home/ubuntu/day-trader-api/engine5_v22_sustained_score_early_entry')
FEE = integ.FEE_RT_PCT
BONUS_CAPS = [0.0, 5.0, 10.0, 15.0]
LATE_SPIKE_VETO = 20.0
ENTRY_THRESHOLD = 50.0


def n(x): return str(x).zfill(6)

def finite(x):
    try:
        z=float(x)
        return z if np.isfinite(z) else np.nan
    except Exception:
        return np.nan


def stats(label, tr):
    g=pd.to_numeric(tr.pnl_pct,errors='coerce').dropna() if len(tr) else pd.Series(dtype=float)
    net=g-FEE
    gp=float(net[net>0].sum()) if len(net) else 0.0
    gl=float(-net[net<0].sum()) if len(net) else 0.0
    return dict(case=label,trades=len(net),wins=int((net>0).sum()),
                win_pct=float((net>0).mean()*100) if len(net) else 0.0,
                net_sum_pct=float(net.sum()) if len(net) else 0.0,
                avg_net_pct=float(net.mean()) if len(net) else 0.0,
                pf=(gp/gl if gl>0 else np.inf),
                max_loss_pct=float(net.min()) if len(net) else np.nan,
                max_win_pct=float(net.max()) if len(net) else np.nan)


def provisional_row(bars, ts, cfg):
    p5=diag.provisional_5m(bars,pd.Timestamp(ts))
    if len(p5)<max(30,int(cfg.bb_period)+5): return None
    eng=DoubleBollingerEngine5(cfg)
    f=v10._refine_entry_frame(eng.enrich(p5))
    s=reweight({'X':f},cfg,0.0)['X']
    if s.empty:return None
    return s.iloc[-1]


def event_from_provisional(sym, ts, cfg, bars, effective_score, original_event):
    r=provisional_row(bars,ts,cfg)
    if r is None:return None
    iu=finite(r.get('inner_upper',np.nan)); il=finite(r.get('inner_lower',np.nan))
    ou=finite(r.get('outer_upper',np.nan)); mid=finite(r.get('mid',np.nan)); close=finite(r.get('close',np.nan))
    br=iu-il if np.isfinite(iu) and np.isfinite(il) else np.nan
    if not(np.isfinite(close) and np.isfinite(br) and br>0):return None
    msx=finite(r.get('macd_slope_spread_strength',np.nan)); rsx=finite(r.get('rsi_slope_strength',np.nan))
    extended=bool(np.isfinite(ou) and close>ou)
    breakout=bool(original_event[-1]) if len(original_event)>=13 else False
    return (n(sym),close,float(effective_score),msx,rsx,br,br,iu,il,ou,mid,extended,breakout)


def score_at_cached(cache, raw, sym, ts, cfg):
    key=(sym,pd.Timestamp(ts))
    if key not in cache:
        r=diag.score_at(raw[sym],pd.Timestamp(ts),cfg)
        cache[key]=np.nan if r is None else finite(r.get('live_score',np.nan))
    return cache[key]


def build_case(tagged, raw, cfg, bonus_cap, score_cache):
    out=[]; changes=[]
    for item in tagged:
        sym=n(item['symbol']); t=pd.Timestamp(item['time'])
        s3=score_at_cached(score_cache,raw,sym,t-pd.Timedelta(minutes=3),cfg)
        s2=score_at_cached(score_cache,raw,sym,t-pd.Timedelta(minutes=2),cfg)
        s1=score_at_cached(score_cache,raw,sym,t-pd.Timedelta(minutes=1),cfg)
        valid=all(np.isfinite(z) for z in (s3,s2,s1))
        monotonic=bool(valid and s3<=s2<=s1)
        last_step=(s1-s2) if valid else np.nan
        rise2=(s1-s3) if valid else np.nan
        bonus=min(float(bonus_cap),max(0.0,rise2)) if valid and monotonic else 0.0
        effective=s1+bonus if valid else np.nan
        eligible=bool(valid and monotonic and last_step < LATE_SPIKE_VETO and effective>=ENTRY_THRESHOLD)

        if eligible:
            nt=t-pd.Timedelta(minutes=1)
            ev=event_from_provisional(sym,nt,cfg,raw[sym],effective,item['event'])
            if ev is not None:
                x=dict(item); x['time']=nt; x['event']=ev
                # V_REBOUND structural stop is source-specific and belongs to the original later state.
                # Do not advance it in this timing diagnostic; leave V_REBOUND unchanged.
                if item['source']=='V_REBOUND':
                    out.append(item)
                    continue
                out.append(x)
                changes.append(dict(symbol=sym,source=item['source'],original_time=t,early_time=nt,
                                    score_t_3=s3,score_t_2=s2,score_t_1=s1,
                                    rise_2m=rise2,last_step=last_step,bonus=bonus,
                                    effective_score=effective,original_event_score=float(item['event'][2]),
                                    original_price=float(item['event'][1]),early_price=float(ev[1])))
                continue
        out.append(item)
    out=sorted(out,key=lambda z:(pd.Timestamp(z['time']),z['symbol'],z['source']))
    return out,pd.DataFrame(changes)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    print('=== V22 KR SUSTAINED-SCORE EARLY-ENTRY TIMING DIAGNOSTIC ===',flush=True)
    print('Advance existing tagged signal by exactly 1 minute only when T-3<=T-2<=T-1 and T-2->T-1 <20.',flush=True)
    print('effective_score = score(T-1) + min(bonus_cap, score(T-1)-score(T-3)); threshold=50.',flush=True)
    print('NOTE: anchored to future existing tag identity; timing diagnostic only, not deployable yet.',flush=True)

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
    tagged=integ.build_sources(raw,cfg,scored,strength,completed,micros)

    baseline=integ.simulate(packed,states,tagged)
    b=stats('A',baseline)
    guard=int(b['trades'])==44 and abs(float(b['net_sum_pct'])-46.35511700526944)<1e-6
    print('BASELINE',b)
    print('BASELINE REPRO:','PASS' if guard else 'FAIL')
    if not guard:raise SystemExit('Baseline mismatch; early-entry diagnostic invalid.')

    score_cache={}; rows=[b]; trade_parts=[]; change_parts=[]
    xb=baseline.copy(); xb['case']='A'; trade_parts.append(xb)

    for cap in BONUS_CAPS:
        name=f'E1_BONUS_{int(cap):02d}'
        tags,changes=build_case(tagged,raw,cfg,cap,score_cache)
        tr=integ.simulate(packed,states,tags)
        st=stats(name,tr); st['advanced_tags']=len(changes); rows.append(st)
        q=tr.copy(); q['case']=name; trade_parts.append(q)
        if len(changes):
            changes['case']=name; change_parts.append(changes)
        print(name,st,flush=True)

    summary=pd.DataFrame(rows)
    trades=pd.concat(trade_parts,ignore_index=True,sort=False)
    changes=pd.concat(change_parts,ignore_index=True,sort=False) if change_parts else pd.DataFrame()

    print('\n=== SUMMARY ===')
    print(summary.to_string(index=False))
    print('\n=== ADVANCED TAGS ===')
    print(changes.sort_values(['case','early_time','symbol']).to_string(index=False) if len(changes) else 'NONE')

    # Match advanced tags to baseline realized trades and new realized entry times.
    if len(changes):
        be=baseline[['symbol','entry_time','entry_price','pnl_pct','reason','source']].copy(); be['symbol']=be.symbol.astype(str).str.zfill(6)
        new_rows=[]
        for case,q in changes.groupby('case'):
            ct=trades[trades.case==case].copy(); ct['symbol']=ct.symbol.astype(str).str.zfill(6)
            z=q.merge(be,left_on=['symbol','original_time'],right_on=['symbol','entry_time'],how='left',suffixes=('','_baseline'))
            z=z.merge(ct[['symbol','entry_time','entry_price','pnl_pct','reason']],left_on=['symbol','early_time'],right_on=['symbol','entry_time'],how='left',suffixes=('_baseline','_early'))
            new_rows.append(z)
        matched=pd.concat(new_rows,ignore_index=True,sort=False)
        print('\n=== ADVANCED SIGNAL OUTCOME MATCH ===')
        print(matched.to_string(index=False))
        matched.to_csv(OUT/'advanced_signal_outcomes.csv',index=False)

    summary.to_csv(OUT/'summary.csv',index=False)
    trades.to_csv(OUT/'trades.csv',index=False)
    changes.to_csv(OUT/'advanced_tags.csv',index=False)
    print('\nWROTE',OUT/'summary.csv')
    print('WROTE',OUT/'trades.csv')
    print('WROTE',OUT/'advanced_tags.csv')

if __name__=='__main__':main()
