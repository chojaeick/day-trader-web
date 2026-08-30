from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
IN_CSV = OUT_DIR / 'v20_entry_extension_cohort.csv'
OUT_SUMMARY = OUT_DIR / 'v20_extreme_extension_ablation_summary.csv'
OUT_REMOVED = OUT_DIR / 'v20_extreme_extension_ablation_removed.csv'
CAPS = [6.0, 7.0, 8.0, 9.0, 10.0]


def main():
    if not IN_CSV.exists():
        raise FileNotFoundError(f'Missing {IN_CSV}. Run diagnose_engine5_v20_entry_extension_cohort first.')

    df = pd.read_csv(IN_CSV)
    if 'dist_5m_mean_pct' not in df.columns or 'net_pct' not in df.columns:
        raise RuntimeError('Required columns missing: dist_5m_mean_pct / net_pct')

    df['dist_5m_mean_pct'] = pd.to_numeric(df['dist_5m_mean_pct'], errors='coerce')
    df['net_pct'] = pd.to_numeric(df['net_pct'], errors='coerce')
    if 'result' not in df.columns:
        df['result'] = np.where(df['net_pct'] > 0, 'WIN', 'LOSS')

    baseline_net = float(df['net_pct'].sum())
    baseline_trades = int(len(df))
    baseline_wins = int((df['net_pct'] > 0).sum())

    summaries = []
    removed_rows = []

    for cap in CAPS:
        # Interpretation: reject a NEW V20 entry only when its distance from the 5m mean
        # is at or above the cap. This is a diagnostic ablation, not a production rule.
        removed = df[df['dist_5m_mean_pct'] >= cap].copy()
        kept = df[df['dist_5m_mean_pct'] < cap].copy()

        summaries.append({
            'cap_pct': cap,
            'trades_kept': int(len(kept)),
            'trades_removed': int(len(removed)),
            'wins_removed': int((removed['net_pct'] > 0).sum()),
            'losses_removed': int((removed['net_pct'] <= 0).sum()),
            'removed_net_pct': float(removed['net_pct'].sum()),
            'remaining_net_pct': float(kept['net_pct'].sum()),
            'delta_vs_baseline_pct': float(kept['net_pct'].sum() - baseline_net),
            'remaining_wins': int((kept['net_pct'] > 0).sum()),
            'remaining_win_pct': float((kept['net_pct'] > 0).mean() * 100.0) if len(kept) else np.nan,
            'max_removed_winner_pct': float(removed.loc[removed['net_pct'] > 0, 'net_pct'].max()) if (removed['net_pct'] > 0).any() else np.nan,
            'worst_removed_loss_pct': float(removed.loc[removed['net_pct'] <= 0, 'net_pct'].min()) if (removed['net_pct'] <= 0).any() else np.nan,
        })

        if len(removed):
            z = removed.copy()
            z['cap_pct'] = cap
            removed_rows.append(z)

    s = pd.DataFrame(summaries)
    r = pd.concat(removed_rows, ignore_index=True) if removed_rows else pd.DataFrame()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    s.to_csv(OUT_SUMMARY, index=False)
    r.to_csv(OUT_REMOVED, index=False)

    print('\n=== V20 EXTREME-EXTENSION ONE-AXIS ABLATION ===')
    print('Diagnostic only. No V20 rule changed.')
    print('Axis: dist_5m_mean_pct only. Reject NEW entries at/above each cap.')
    print(f'BASELINE: trades={baseline_trades} wins={baseline_wins} net={baseline_net:+.6f}%')

    print('\n=== SUMMARY ===')
    show = [
        'cap_pct','trades_kept','trades_removed','wins_removed','losses_removed',
        'removed_net_pct','remaining_net_pct','delta_vs_baseline_pct',
        'remaining_wins','remaining_win_pct','max_removed_winner_pct','worst_removed_loss_pct'
    ]
    print(s[show].to_string(index=False))

    print('\n=== REMOVED CASES BY CAP ===')
    wanted = [c for c in [
        'cap_pct','result','symbol','entry_time','net_pct','reason','dist_5m_mean_pct',
        'runup_3x5m_pct','rsi','macd_strength_raw','macd_strength_rel'
    ] if c in r.columns]
    if r.empty:
        print('NONE')
    else:
        for cap in CAPS:
            q = r[r['cap_pct'] == cap].sort_values('net_pct')
            print(f'\nCAP >= {cap:.1f}% :')
            print(q[wanted].to_string(index=False) if len(q) else 'NONE')

    print('\nReading target:')
    print('- Do not choose the numerically best cap.')
    print('- Keep this axis only if several adjacent caps remove major late-entry losses with little winner damage.')
    print('- If good winners are repeatedly removed, reject a global extension veto and return to missed-transition/source-state logic.')
    print('WROTE', OUT_SUMMARY)
    print('WROTE', OUT_REMOVED)


if __name__ == '__main__':
    main()
