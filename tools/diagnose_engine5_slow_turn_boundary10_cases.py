from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
SRC = OUT_DIR / 'slow_turn_structure_ablation_cases.csv'
OUT = OUT_DIR / 'slow_turn_boundary10_cases.csv'
OUT_GROUP = OUT_DIR / 'slow_turn_boundary10_group_summary.csv'


def num(s):
    return pd.to_numeric(s, errors='coerce')


def main():
    if not SRC.exists():
        raise FileNotFoundError(f'{SRC} not found. Run tools.validate_engine5_slow_turn_structure_ablation first.')

    x = pd.read_csv(SRC)
    x['symbol'] = x['symbol'].astype(str).str.zfill(6)
    x['entry_time'] = pd.to_datetime(x['entry_time'])
    q = x[x['regime'].eq('BOUNDARY_8_12')].copy()
    if q.empty:
        print('NO BOUNDARY CASES')
        return

    q['result'] = np.where(num(q['net_pct']) > 0, 'WIN', 'LOSS')
    q['strength_joint_min'] = np.minimum(num(q['gap_delta_5m']), num(q['rsi_slope_5m']))
    q['strength_pass_macd30'] = num(q['gap_delta_5m']) >= 30.0
    q['strength_pass_rsi10'] = num(q['rsi_slope_5m']) >= 10.0
    q['price_pass_1_5'] = num(q['price_progress_1m_pct']) >= 1.5
    q['base_combo_pass'] = q['strength_pass_macd30'] & q['strength_pass_rsi10'] & q['price_pass_1_5']

    metrics = [
        'zero_cross_bars','gap_delta_5m','rsi_slope_5m',
        'joint5_persistence','joint1_persistence','price_progress_1m_pct',
        'close_progress_6m_pct','rise_from_6m_low_pct','entry_vs_6m_high_pct',
        'last1m_return_pct','last2m_return_pct','net_pct'
    ]

    rows = []
    for result, g in q.groupby('result', sort=False):
        row = {'result': result, 'trades': len(g), 'wins': int((num(g['net_pct']) > 0).sum()), 'net_sum': float(num(g['net_pct']).sum())}
        for c in metrics[:-1]:
            row[f'{c}_mean'] = float(num(g[c]).mean()) if c in g.columns else np.nan
            row[f'{c}_median'] = float(num(g[c]).median()) if c in g.columns else np.nan
        rows.append(row)
    group = pd.DataFrame(rows)

    cols = [
        'result','symbol','entry_time','net_pct','zero_cross_bars',
        'gap_delta_5m','rsi_slope_5m','joint5_persistence','joint1_persistence',
        'price_progress_1m_pct','close_progress_6m_pct','rise_from_6m_low_pct','entry_vs_6m_high_pct',
        'last1m_return_pct','last2m_return_pct',
        'strength_pass_macd30','strength_pass_rsi10','price_pass_1_5','base_combo_pass'
    ]
    cols = [c for c in cols if c in q.columns]
    q = q.sort_values(['result','net_pct'], ascending=[False, False]).reset_index(drop=True)
    q.to_csv(OUT, index=False)
    group.to_csv(OUT_GROUP, index=False)

    print('\n=== SLOW TURN BOUNDARY 8-12 : 10 CASE COMPARISON ===')
    print('Descriptive only. No rule/threshold changed.')
    print(f'CASES={len(q)} WINS={(num(q.net_pct) > 0).sum()} LOSSES={(num(q.net_pct) <= 0).sum()} NET={num(q.net_pct).sum():.6f}%')
    print('\n=== INDIVIDUAL 10 CASES ===')
    print(q[cols].to_string(index=False))

    show_group = ['result','trades','net_sum']
    for c in [
        'gap_delta_5m_mean','rsi_slope_5m_mean','joint5_persistence_mean','joint1_persistence_mean',
        'price_progress_1m_pct_mean','close_progress_6m_pct_mean','rise_from_6m_low_pct_mean',
        'zero_cross_bars_mean'
    ]:
        if c in group.columns:
            show_group.append(c)
    print('\n=== WIN vs LOSS GROUP MEANS ===')
    print(group[show_group].to_string(index=False))

    print('\n=== BASE COMBO CHECK ===')
    combo = q.groupby('base_combo_pass')['net_pct'].agg(['count', lambda s: int((num(s) > 0).sum()), 'sum', 'mean']).reset_index()
    combo.columns = ['base_combo_pass','trades','wins','net_sum','avg_net']
    print(combo.to_string(index=False))

    print('\nReading target:')
    print('- Determine whether winners differ by absolute 5m propulsion, persistence, actual 1m price progress, or pre-entry extension.')
    print('- If MACD/RSI/price pass together in exactly the same cases, treat them as a joint propulsion state rather than claiming independent thresholds.')
    print('WROTE', OUT)
    print('WROTE', OUT_GROUP)


if __name__ == '__main__':
    main()
