from __future__ import annotations

"""V4 diagnostic for the EXISTING Slow-turn path.

The existing candidate generation/episode selection remains untouched. This validator
separates two valid Slow-turn shapes instead of forcing one scalar interpretation:
  1) BURST: abrupt V-turn energy, based on V3 transition score including causal RSI-50 bonus.
  2) COHERENCE: slower but persistent transition, using the existing joint5/joint1/price
     confirmation metrics already used by the Slow-turn structure.

This is diagnostic only. It does not change production rules or freeze a threshold.
"""

from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
V3 = ROOT / 'slow_turn_transition_score_v3_rsi50_detail.csv'
OUT = ROOT / 'slow_turn_dual_score_v4_detail.csv'


def num(x): return pd.to_numeric(x, errors='coerce')

def clip01(v):
    try:
        x=float(v)
        return float(np.clip(x,0.0,1.0)) if np.isfinite(x) else 0.0
    except Exception:
        return 0.0


def coherence_score(r):
    """Score a genuinely persistent slow turn from existing structural evidence.

    This branch is intentionally unavailable unless the existing Slow-turn confirmation
    floor is met. No score floor is injected after the fact.
    """
    j5=float(r.get('joint5_persistence', np.nan)) if pd.notna(r.get('joint5_persistence', np.nan)) else np.nan
    j1=float(r.get('joint1_persistence', np.nan)) if pd.notna(r.get('joint1_persistence', np.nan)) else np.nan
    px=float(r.get('price_progress_1m_pct', np.nan)) if pd.notna(r.get('price_progress_1m_pct', np.nan)) else np.nan

    eligible=(np.isfinite(j5) and j5>=0.80 and np.isfinite(j1) and j1>=0.70 and np.isfinite(px) and px>=1.00)
    if not eligible:
        return 0.0, False

    # Map the already-existing structural floors to a continuous 0..100 quality score.
    # 0.80->0 and 1.00->1 for 5m persistence; 0.70->0 and 1.00->1 for 1m persistence;
    # 1%->0 and 2%->1 for price progress. Strong coherence requires all three dimensions.
    s5=clip01((j5-0.80)/0.20)
    s1=clip01((j1-0.70)/0.30)
    sp=clip01((px-1.00)/1.00)
    floor=min(s5,s1)
    score=100.0*(0.40*s5+0.35*s1+0.25*sp)

    # Prevent one strong dimension from carrying two barely-passing dimensions.
    score*=0.50+0.50*floor
    return float(score), True


def main():
    if not V3.exists(): raise FileNotFoundError(V3)
    x=pd.read_csv(V3)

    required={'transition_score_v3','joint5_persistence','joint1_persistence','price_progress_1m_pct'}
    missing=sorted(required-set(x.columns))
    if missing:
        print('MISSING_COLUMNS',missing)
        raise SystemExit(2)

    cs=[]; ce=[]
    for _,r in x.iterrows():
        s,e=coherence_score(r); cs.append(s); ce.append(e)
    x['burst_score']=num(x['transition_score_v3'])
    x['coherence_score']=cs
    x['coherence_eligible']=ce
    x['slow_turn_score_v4']=x[['burst_score','coherence_score']].max(axis=1)
    x['score_mode']=np.where(num(x.coherence_score)>num(x.burst_score),'COHERENCE','BURST')

    targets=[
        ('058610','2026-08-13 09:25:00+09:00','V_TURN_SUCCESS'),
        ('122630','2026-08-20 13:06:00+09:00','GRADUAL_FAILURE'),
        ('950160','2026-08-14 10:59:00+09:00','VALID_SLOW_SUCCESS'),
    ]
    x['entry_time']=pd.to_datetime(x['entry_time'])
    print('=== SLOW-TURN V4 DUAL SCORE | BURST vs COHERENCE ===')
    for sym,t,label in targets:
        q=x[(x.symbol.astype(str).str.zfill(6)==sym)&(x.entry_time==pd.Timestamp(t))]
        print(f'\n[{label}]')
        if q.empty:
            print('NOT FOUND'); continue
        r=q.iloc[0]
        for c in ['burst_score','coherence_score','coherence_eligible','slow_turn_score_v4','score_mode',
                  'joint5_persistence','joint1_persistence','price_progress_1m_pct','rsi50_bonus','net_pct','result']:
            if c in q.columns: print(f'{c:24s} {r[c]}')

    print('\n=== ALL REALIZED/SELECTED SCORE DISTRIBUTION ===')
    cols=[c for c in ['symbol','entry_time','regime','burst_score','coherence_score','slow_turn_score_v4','score_mode','net_pct','result'] if c in x.columns]
    print(x[cols].sort_values('slow_turn_score_v4',ascending=False).to_string(index=False,float_format=lambda v:f'{v:.4f}'))

    x.to_csv(OUT,index=False)
    print('\nPASS CONDITION:')
    print('- 058610 V-turn should be high via BURST.')
    print('- 950160 valid gradual turn should be materially rescued by COHERENCE.')
    print('- 122630 gradual failure should not receive a strong COHERENCE rescue.')
    print('WROTE',OUT)

if __name__=='__main__': main()
