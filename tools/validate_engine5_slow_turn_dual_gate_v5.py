from __future__ import annotations

"""V5 diagnostic for the EXISTING Slow-turn path.

Do not force BURST and COHERENCE onto one scalar score axis.
Entry eligibility is instead:
  BURST path: abrupt transition score >= diagnostic threshold
  OR
  COHERENCE path: existing structural confirmation gate passes

This is diagnostic only. It does not change production V21.
"""

from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
V4 = ROOT / 'slow_turn_dual_score_v4_detail.csv'
OUT = ROOT / 'slow_turn_dual_gate_v5_detail.csv'
THRESHOLDS = (50.0, 55.0, 60.0)


def num(x): return pd.to_numeric(x, errors='coerce')


def coherence_gate(r):
    j5=float(r.get('joint5_persistence', np.nan)) if pd.notna(r.get('joint5_persistence', np.nan)) else np.nan
    j1=float(r.get('joint1_persistence', np.nan)) if pd.notna(r.get('joint1_persistence', np.nan)) else np.nan
    px=float(r.get('price_progress_1m_pct', np.nan)) if pd.notna(r.get('price_progress_1m_pct', np.nan)) else np.nan
    return bool(np.isfinite(j5) and j5>=0.80 and np.isfinite(j1) and j1>=0.70 and np.isfinite(px) and px>=1.00)


def stats(q):
    p=num(q.get('net_pct')).dropna() if len(q) else pd.Series(dtype=float)
    gp=float(p[p>0].sum()) if len(p) else 0.0
    gl=float(-p[p<0].sum()) if len(p) else 0.0
    return dict(trades=len(p),wins=int((p>0).sum()),win_pct=float((p>0).mean()*100) if len(p) else 0.0,
                net_sum_pct=float(p.sum()) if len(p) else 0.0,pf=(gp/gl if gl>0 else np.inf),
                max_loss_pct=float(p.min()) if len(p) else np.nan)


def main():
    if not V4.exists(): raise FileNotFoundError(V4)
    x=pd.read_csv(V4)
    x['symbol']=x.symbol.astype(str).str.zfill(6)
    x['entry_time']=pd.to_datetime(x.entry_time)
    x['coherence_gate']=x.apply(coherence_gate,axis=1)
    x['burst_score']=num(x['burst_score'])

    rows=[]
    for th in THRESHOLDS:
        b=x.burst_score>=th
        c=x.coherence_gate
        take=b|c
        q=x[take].copy()
        st=stats(q)
        rows.append(dict(burst_threshold=th,selected=int(take.sum()),burst_only=int((b&~c).sum()),
                         coherence_only=int((c&~b).sum()),both=int((b&c).sum()),**st))
        x[f'enter_burst_{int(th)}']=b
        x[f'enter_v5_{int(th)}']=take
        x[f'mode_v5_{int(th)}']=np.where(b&c,'BOTH',np.where(b,'BURST',np.where(c,'COHERENCE','REJECT')))

    print('=== SLOW-TURN V5 DUAL GATE ===')
    print('Decision = BURST score threshold OR existing COHERENCE structural gate.')
    print(pd.DataFrame(rows).to_string(index=False,float_format=lambda v:f'{v:.4f}'))

    targets=[
        ('058610','2026-08-13 09:25:00+09:00','V_TURN_SUCCESS'),
        ('122630','2026-08-20 13:06:00+09:00','GRADUAL_FAILURE'),
        ('950160','2026-08-14 10:59:00+09:00','VALID_SLOW_SUCCESS'),
    ]
    print('\n=== CANONICAL CASES ===')
    for sym,t,label in targets:
        q=x[(x.symbol==sym)&(x.entry_time==pd.Timestamp(t))]
        print(f'\n[{label}]')
        if q.empty:
            print('NOT FOUND'); continue
        r=q.iloc[0]
        cols=['burst_score','coherence_gate','joint5_persistence','joint1_persistence','price_progress_1m_pct',
              'rsi50_bonus','net_pct','result','mode_v5_50','mode_v5_55','mode_v5_60']
        for c in cols:
            if c in q.columns: print(f'{c:24s} {r[c]}')

    print('\n=== ALL CASES @ BURST 55 ===')
    cols=['symbol','entry_time','regime','burst_score','coherence_gate','mode_v5_55','net_pct','result']
    print(x[cols].sort_values(['mode_v5_55','burst_score'],ascending=[True,False]).to_string(index=False,float_format=lambda v:f'{v:.4f}'))

    x.to_csv(OUT,index=False)
    print('\nPASS CONDITION:')
    print('- 058610 should enter via BURST.')
    print('- 950160 should enter via COHERENCE even with a low burst score.')
    print('- 122630 should be rejected once the BURST threshold is above its weak/gradual score.')
    print('- Do not freeze 50/55/60 from this tiny KR sample; this only tests semantics.')
    print('WROTE',OUT)

if __name__=='__main__': main()
