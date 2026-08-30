from __future__ import annotations

"""Full KR V21 integration test for the revised EXISTING Slow-turn decision logic.

This does not create a new strategy path. V20, V-rebound, exits, conflict ordering and
position ownership remain the existing integrated V21 implementation. Only SLOW_TURN
eligibility is replaced by:

    BURST score >= diagnostic threshold
      OR
    COHERENCE structural gate: joint5>=0.80, joint1>=0.70, price_progress>=1.00%

The integrated simulator still requires event tuple score>=50. For COHERENCE-only events
we therefore use 50 solely as an interface/transport credential after the independent
structural gate has already accepted the entry. It must NOT be interpreted as a computed
Slow-turn score and is recorded separately as decision_mode='COHERENCE'.

Thresholds 50/55/60 are diagnostics only; no production threshold is frozen here.
"""

from dataclasses import replace
from pathlib import Path
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_macd_strength as ms
import tools.validate_engine5_v20_regime_transition as rt
import tools.validate_engine5_integrated_full_history as integ
import tools.validate_engine5_integrated_slow_turn_rearm_deep as revised
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

ROOT=Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
V5=ROOT/'slow_turn_dual_gate_v5_detail.csv'
OUT_SUM=ROOT/'v21_slow_turn_dual_gate_integrated_summary.csv'
OUT_TR=ROOT/'v21_slow_turn_dual_gate_integrated_trades.csv'
OUT_SIG=ROOT/'v21_slow_turn_dual_gate_integrated_signals.csv'
CUT=-0.15
THRESHOLDS=(50.0,55.0,60.0)


def n(x): return str(x).zfill(6)
def num(x): return pd.to_numeric(x,errors='coerce')

def replace_score(event,score):
    e=list(event); e[2]=float(score); return tuple(e)

def stat(label,tr):
    return integ.stat(label,tr)

def mode_for(r,th):
    b=float(r.burst_score)>=float(th)
    c=bool(r.coherence_gate)
    if b and c:return 'BOTH'
    if b:return 'BURST'
    if c:return 'COHERENCE'
    return 'REJECT'


def main():
    if not V5.exists(): raise FileNotFoundError(V5)
    raw={n(k):v for k,v in load_data().items()}
    cfg0=DoubleBollingerEngine5Config()
    cfg=replace(cfg0,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    packed=v8.base.pack_exit_events(raw,cfg0)
    states=base.pack_state_events(base.build_cfg_frames(raw,cfg0))
    frames0=base.build_cfg_frames(raw,cfg)
    f10={n(s):v10._refine_entry_frame(f) for s,f in frames0.items()}
    scored={n(s):f for s,f in reweight(f10,cfg,0.0).items()}
    strength={s:ms.add_strength(f) for s,f in scored.items()}
    completed={s:rt.add_completed_strength(f) for s,f in scored.items()}
    micros={s:h.build_micro(raw[s],cfg) for s in raw}

    print('=== BUILD UNCHANGED V20 + V_REBOUND ===',flush=True)
    old=integ.build_sources(raw,cfg,scored,strength,completed,micros)
    non_slow=[x for x in old if x['source']!='SLOW_TURN']

    print('=== REBUILD EXISTING RE-ARMED SLOW-TURN CANDIDATES ===',flush=True)
    allc=revised.build_all_slow(raw,cfg,completed,micros)
    sel=revised.select_revised(allc,CUT).copy()
    sel['symbol']=sel.symbol.astype(str).str.zfill(6)
    sel['entry_time']=pd.to_datetime(sel.entry_time)

    v=pd.read_csv(V5)
    v['symbol']=v.symbol.astype(str).str.zfill(6)
    v['entry_time']=pd.to_datetime(v.entry_time)
    keep=['symbol','entry_time','burst_score','coherence_gate','joint5_persistence','joint1_persistence','price_progress_1m_pct','rsi50_bonus']
    x=sel.merge(v[keep].drop_duplicates(['symbol','entry_time']),on=['symbol','entry_time'],how='left',validate='one_to_one',suffixes=('','_v5'))
    if x.burst_score.isna().any():
        miss=x[x.burst_score.isna()][['symbol','entry_time']]
        print('V5 SCORE MATCH FAILURE')
        print(miss.to_string(index=False)); raise SystemExit(2)

    rows=[]; trade_parts=[]; signal_parts=[]
    old_tr=integ.simulate(packed,states,old)
    old_st=stat('OLD_V21',old_tr)
    rows.append(dict(burst_threshold='OLD',slow_selected=sum(z['source']=='SLOW_TURN' for z in old),burst_only=np.nan,coherence_only=np.nan,both=np.nan,**old_st))

    for th in THRESHOLDS:
        tags=[]; diag=[]
        for _,r in x.iterrows():
            mode=mode_for(r,th)
            if mode=='REJECT':
                diag.append(dict(symbol=n(r.symbol),entry_time=pd.Timestamp(r.entry_time),regime=str(r.regime),burst_score=float(r.burst_score),coherence_gate=bool(r.coherence_gate),decision_mode=mode,transport_score=np.nan))
                continue
            # BURST keeps its computed score. COHERENCE-only uses 50 solely because the
            # existing simulator's tuple interface re-checks score>=50 after the structural gate.
            transport=float(r.burst_score) if mode in ('BURST','BOTH') and float(r.burst_score)>=50 else 50.0
            ev=replace_score(r.event,transport)
            tags.append(dict(source='SLOW_TURN',symbol=n(r.symbol),time=pd.Timestamp(r.entry_time),event=ev,
                             meta={'regime':str(r.regime),'decision_mode':mode,'burst_score':float(r.burst_score),'coherence_gate':bool(r.coherence_gate)}))
            diag.append(dict(symbol=n(r.symbol),entry_time=pd.Timestamp(r.entry_time),regime=str(r.regime),burst_score=float(r.burst_score),coherence_gate=bool(r.coherence_gate),decision_mode=mode,transport_score=transport))

        tagged=sorted(non_slow+tags,key=lambda z:(pd.Timestamp(z['time']),z['symbol'],z['source']))
        tr=integ.simulate(packed,states,tagged)
        st=stat(f'V21_DUAL_{int(th)}',tr)
        modes=pd.Series([z['meta']['decision_mode'] for z in tags],dtype=str)
        rows.append(dict(burst_threshold=th,slow_selected=len(tags),burst_only=int((modes=='BURST').sum()),coherence_only=int((modes=='COHERENCE').sum()),both=int((modes=='BOTH').sum()),**st))
        q=tr.copy();q['burst_threshold']=th;trade_parts.append(q)
        q2=pd.DataFrame(diag);q2['burst_threshold']=th;signal_parts.append(q2)

    summary=pd.DataFrame(rows)
    summary.to_csv(OUT_SUM,index=False)
    if trade_parts:pd.concat(trade_parts,ignore_index=True).to_csv(OUT_TR,index=False)
    if signal_parts:pd.concat(signal_parts,ignore_index=True).to_csv(OUT_SIG,index=False)

    print('\n=== KR V21 FULL INTEGRATION | SLOW-TURN DUAL GATE ===')
    show=['burst_threshold','slow_selected','burst_only','coherence_only','both','trades','wins','win_pct','net_sum_pct','pf','max_loss_pct']
    print(summary[[c for c in show if c in summary.columns]].to_string(index=False,float_format=lambda v:f'{v:.4f}'))

    print('\n=== CANONICAL DECISIONS ===')
    targets=[('058610','2026-08-13 09:25:00+09:00'),('122630','2026-08-20 13:06:00+09:00'),('950160','2026-08-14 10:59:00+09:00')]
    d=pd.concat(signal_parts,ignore_index=True) if signal_parts else pd.DataFrame()
    for sym,t in targets:
        q=d[(d.symbol==sym)&(pd.to_datetime(d.entry_time)==pd.Timestamp(t))]
        print(f'-- {sym} {t} --')
        print(q[['burst_threshold','burst_score','coherence_gate','decision_mode','transport_score']].to_string(index=False,float_format=lambda v:f'{v:.4f}') if len(q) else 'NOT FOUND')

    print('\nREADING:')
    print('- OLD must reproduce the prior V21 integrated baseline before comparing revised rows.')
    print('- 55/60 are not frozen; this checks full-simulator conflicts/ownership and semantics only.')
    print('- COHERENCE transport_score=50 is interface compatibility, not a calculated score or score-floor rule.')
    print('WROTE',OUT_SUM);print('WROTE',OUT_TR);print('WROTE',OUT_SIG)

if __name__=='__main__':main()
