from __future__ import annotations

"""Full KR V21 integration test for revised EXISTING Slow-turn decision logic.

V20, V-rebound, exits, conflict ordering and position ownership remain unchanged.
Only Slow-turn eligibility is compared.

IMPORTANT: OLD baseline is the final pre-score-fix V21, i.e. non-Slow sources plus the
re-armed Slow-turn selection at CUT=-0.15. It is NOT integ.build_sources()'s older
Slow-turn cohort.
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
EXPECTED_OLD=dict(trades=50,wins=27,win_pct=54.0,net_sum_pct=49.4759,pf=3.6605)


def n(x): return str(x).zfill(6)
def num(x): return pd.to_numeric(x,errors='coerce')

def replace_score(event,score):
    e=list(event); e[2]=float(score); return tuple(e)

def stat(label,tr): return integ.stat(label,tr)
def mode_for(r,th):
    b=float(r.burst_score)>=float(th); c=bool(r.coherence_gate)
    if b and c:return 'BOTH'
    if b:return 'BURST'
    if c:return 'COHERENCE'
    return 'REJECT'


def assert_old(st):
    ok=(int(st['trades'])==EXPECTED_OLD['trades'] and int(st['wins'])==EXPECTED_OLD['wins'] and
        abs(float(st['win_pct'])-EXPECTED_OLD['win_pct'])<1e-3 and
        abs(float(st['net_sum_pct'])-EXPECTED_OLD['net_sum_pct'])<1e-3 and
        abs(float(st['pf'])-EXPECTED_OLD['pf'])<1e-3)
    if not ok:
        print('OLD BASELINE REPRO FAILURE')
        print('expected=',EXPECTED_OLD)
        print('actual=',{k:st.get(k) for k in EXPECTED_OLD})
        raise SystemExit(3)


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
    legacy=integ.build_sources(raw,cfg,scored,strength,completed,micros)
    non_slow=[z for z in legacy if z['source']!='SLOW_TURN']

    print('=== REBUILD FINAL V21 RE-ARMED SLOW-TURN ===',flush=True)
    allc=revised.build_all_slow(raw,cfg,completed,micros)
    sel=revised.select_revised(allc,CUT).copy()
    sel['symbol']=sel.symbol.astype(str).str.zfill(6)
    sel['entry_time']=pd.to_datetime(sel.entry_time)

    # Correct OLD V21: final re-armed Slow-turn cohort, including historical transport-score
    # behavior, combined with unchanged V20/V-rebound sources.
    old_slow=revised.slow_tags(sel)
    old=sorted(non_slow+old_slow,key=lambda z:(pd.Timestamp(z['time']),z['symbol'],z['source']))
    old_tr=integ.simulate(packed,states,old)
    old_st=stat('OLD_V21',old_tr)
    assert_old(old_st)

    v=pd.read_csv(V5)
    v['symbol']=v.symbol.astype(str).str.zfill(6)
    v['entry_time']=pd.to_datetime(v.entry_time)
    keep=['symbol','entry_time','burst_score','coherence_gate','joint5_persistence','joint1_persistence','price_progress_1m_pct','rsi50_bonus']
    x=sel.merge(v[keep].drop_duplicates(['symbol','entry_time']),on=['symbol','entry_time'],how='left',validate='one_to_one',suffixes=('','_v5'))
    if x.burst_score.isna().any():
        miss=x[x.burst_score.isna()][['symbol','entry_time']]
        print('V5 SCORE MATCH FAILURE'); print(miss.to_string(index=False)); raise SystemExit(2)

    rows=[]; trade_parts=[]; signal_parts=[]
    rows.append(dict(burst_threshold='OLD',slow_selected=len(old_slow),burst_only=np.nan,coherence_only=np.nan,both=np.nan,**old_st))

    for th in THRESHOLDS:
        tags=[]; diag=[]
        for _,r in x.iterrows():
            mode=mode_for(r,th)
            if mode=='REJECT':
                diag.append(dict(symbol=n(r.symbol),entry_time=pd.Timestamp(r.entry_time),regime=str(r.regime),burst_score=float(r.burst_score),coherence_gate=bool(r.coherence_gate),decision_mode=mode,transport_score=np.nan))
                continue
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

    summary=pd.DataFrame(rows); summary.to_csv(OUT_SUM,index=False)
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

    print('\nREPRO CHECK: OLD final V21 baseline PASS')
    print('55/60 remain diagnostics only; do not freeze from this KR sample.')
    print('COHERENCE transport_score=50 is simulator interface compatibility, not a calculated score.')
    print('WROTE',OUT_SUM);print('WROTE',OUT_TR);print('WROTE',OUT_SIG)

if __name__=='__main__':main()
