from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
SRC = OUT_DIR / 'slow_turn_structure_ablation_cases.csv'
OUT_CASES = OUT_DIR / 'slow_turn_structure_freeze_review_cases.csv'
OUT_SUMMARY = OUT_DIR / 'slow_turn_structure_freeze_review_summary.csv'


def num(x):
    return pd.to_numeric(x, errors='coerce')


def finite(x):
    try:
        v = float(x)
        return v if np.isfinite(v) else np.nan
    except Exception:
        return np.nan


def proposed_state(r):
    rg = str(r.regime)
    px = finite(r.price_progress_1m_pct)
    ext = finite(r.close_progress_6m_pct)
    p5 = finite(r.joint5_persistence)
    p1 = finite(r.joint1_persistence)
    gd = finite(r.gap_delta_5m)
    rs = finite(r.rsi_slope_5m)

    if rg == 'NEAR_LE1_5':
        # Structural hypothesis only: near transition, require actual price confirmation
        # and reject clearly already-extended entries. Exact extension cutoff is NOT frozen.
        return bool(px >= 0.75 and ext < 3.0), 'NEAR_PRICE_CONFIRM_NOT_EXTENDED'
    if rg == 'MID_1_5_8':
        # Evidence is only one case. Keep as provisional/unfrozen.
        return bool(p5 >= 0.60 and p1 >= 0.60 and px >= 1.0), 'MID_PROVISIONAL_LOW_N'
    if rg == 'BOUNDARY_8_12':
        # Treat MACD+RSI+price as a joint propulsion state, not three independently proven cutoffs.
        return bool(gd >= 30.0 and rs >= 10.0 and px >= 1.5), 'BOUNDARY_JOINT_PROPULSION'
    if rg == 'DEEP_GT12':
        return False, 'DEEP_SEPARATE_REVERSAL_PATH'
    return False, 'INVALID'


def stats(label, g):
    net = num(g.net_pct).dropna()
    gp = float(net[net > 0].sum()) if len(net) else 0.0
    gl = float(-net[net < 0].sum()) if len(net) else 0.0
    return dict(label=label, trades=len(net), wins=int((net > 0).sum()),
                win_pct=float((net > 0).mean()*100) if len(net) else 0.0,
                net_sum=float(net.sum()) if len(net) else 0.0,
                pf=(gp/gl if gl > 0 else np.inf),
                max_loss=float(net.min()) if len(net) else np.nan)


def main():
    if not SRC.exists():
        raise FileNotFoundError(SRC)
    x = pd.read_csv(SRC)
    x['symbol'] = x['symbol'].astype(str).str.zfill(6)
    x['entry_time'] = pd.to_datetime(x['entry_time'])

    selected = []
    states = []
    for _, r in x.iterrows():
        ok, state = proposed_state(r)
        selected.append(ok)
        states.append(state)
    x['freeze_candidate'] = selected
    x['structure_state'] = states
    x['result'] = np.where(num(x.net_pct) > 0, 'WIN', 'LOSS')

    # Only gradual-turn families. Deep is intentionally excluded from this freeze review.
    grad = x[x.regime.isin(['NEAR_LE1_5','MID_1_5_8','BOUNDARY_8_12'])].copy()
    chosen = grad[grad.freeze_candidate].copy()

    rows = []
    for rg in ['NEAR_LE1_5','MID_1_5_8','BOUNDARY_8_12']:
        q = grad[grad.regime.eq(rg)]
        rows.append(stats(rg + '_ALL', q))
        rows.append(stats(rg + '_SELECTED', q[q.freeze_candidate]))
    rows.append(stats('GRADUAL_SELECTED_TOTAL', chosen))
    summary = pd.DataFrame(rows)

    keep = ['result','symbol','entry_time','net_pct','regime','structure_state','freeze_candidate',
            'zero_cross_bars','gap_delta_5m','rsi_slope_5m','joint5_persistence','joint1_persistence',
            'price_progress_1m_pct','close_progress_6m_pct','rise_from_6m_low_pct','entry_vs_6m_high_pct']
    grad[keep].sort_values(['regime','freeze_candidate','net_pct'], ascending=[True,False,False]).to_csv(OUT_CASES, index=False)
    summary.to_csv(OUT_SUMMARY, index=False)

    print('\n=== SLOW TURN STRUCTURE FREEZE REVIEW ===')
    print('No engine rule changed. This is a final in-sample structure review before provisional freeze.')
    print('DEEP >12 is excluded and remains a separate reversal family.')
    print('\n=== SUMMARY ===')
    print(summary.to_string(index=False))
    print('\n=== SELECTED GRADUAL-TURN CASES ===')
    print(chosen[keep].sort_values('entry_time').to_string(index=False))
    print('\nFreeze interpretation:')
    print('- NEAR: structure may freeze as price-confirm + not-already-extended; exact extension cutoff remains provisional.')
    print('- MID: DO NOT freeze exact rule; sample is insufficient.')
    print('- BOUNDARY: freeze the joint-propulsion concept, NOT the exact 30/10/1.5 numbers.')
    print('- DEEP: separate reversal path, not gradual-turn.')
    print('WROTE', OUT_CASES)
    print('WROTE', OUT_SUMMARY)


if __name__ == '__main__':
    main()
