from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
CASES_SRC = OUT_DIR / 'v21_v_rebound_post_entry_failure_cases.csv'
TRIG_SRC = OUT_DIR / 'v21_v_rebound_post_entry_failure_triggers.csv'
OUT_CASES = OUT_DIR / 'v21_v_rebound_early_failure_ablation_cases.csv'
OUT_SUMMARY = OUT_DIR / 'v21_v_rebound_early_failure_ablation_summary.csv'
FEE_RT_PCT = 0.25
TARGET_TRIGGER = 'BELOW_ENTRY_BOTH_WEAKER_2M'


def num(x):
    return pd.to_numeric(x, errors='coerce')


def stats(label: str, net: pd.Series):
    n = num(net).dropna()
    gp = float(n[n > 0].sum()) if len(n) else 0.0
    gl = float(-n[n < 0].sum()) if len(n) else 0.0
    return dict(
        variant=label,
        trades=len(n),
        wins=int((n > 0).sum()),
        win_pct=float((n > 0).mean() * 100.0) if len(n) else 0.0,
        net_sum_pct=float(n.sum()) if len(n) else 0.0,
        avg_net_pct=float(n.mean()) if len(n) else 0.0,
        pf=(gp / gl if gl > 0 else np.inf),
        max_loss_pct=float(n.min()) if len(n) else np.nan,
        gross_profit_pct=gp,
        gross_loss_pct=gl,
    )


def main():
    if not CASES_SRC.exists():
        raise FileNotFoundError(CASES_SRC)
    if not TRIG_SRC.exists():
        raise FileNotFoundError(TRIG_SRC)

    cases = pd.read_csv(CASES_SRC)
    trig = pd.read_csv(TRIG_SRC)
    cases['symbol'] = cases['symbol'].astype(str).str.zfill(6)
    trig['symbol'] = trig['symbol'].astype(str).str.zfill(6)
    cases['entry_time'] = pd.to_datetime(cases['entry_time'])
    trig['entry_time'] = pd.to_datetime(trig['entry_time'])
    trig['trigger_time'] = pd.to_datetime(trig['trigger_time'], errors='coerce')

    t = trig[trig['trigger'].eq(TARGET_TRIGGER)].copy()
    keep = ['symbol','entry_time','triggered','trigger_time','minutes_after_entry','trigger_ret_pct']
    x = cases.merge(t[keep], on=['symbol','entry_time'], how='left', validate='one_to_one')

    x['baseline_net_pct'] = num(x['net_pct'])
    x['early_exit_net_pct'] = x['baseline_net_pct']
    hit = x['triggered'].fillna(False).astype(bool) & num(x['trigger_ret_pct']).notna()
    # Same round-trip fee convention used throughout the V validation series.
    x.loc[hit, 'early_exit_net_pct'] = num(x.loc[hit, 'trigger_ret_pct']) - FEE_RT_PCT
    x['net_change_pct'] = x['early_exit_net_pct'] - x['baseline_net_pct']
    x['baseline_result'] = np.where(x['baseline_net_pct'] > 0, 'WIN', 'LOSS')
    x['early_result'] = np.where(x['early_exit_net_pct'] > 0, 'WIN', 'LOSS')
    x['winner_damaged'] = (x['baseline_net_pct'] > 0) & (x['net_change_pct'] < -1e-12)
    x['loser_improved'] = (x['baseline_net_pct'] <= 0) & (x['net_change_pct'] > 1e-12)

    summary = pd.DataFrame([
        stats('BASELINE_EXIT', x['baseline_net_pct']),
        stats('ADD_BELOW_ENTRY_BOTH_WEAKER_2M', x['early_exit_net_pct']),
    ])
    summary['triggered_trades'] = [0, int(hit.sum())]
    summary['winner_damaged_count'] = [0, int(x['winner_damaged'].sum())]
    summary['loser_improved_count'] = [0, int(x['loser_improved'].sum())]

    cols = [
        'symbol','entry_time','baseline_result','baseline_net_pct','reason',
        'triggered','trigger_time','minutes_after_entry','trigger_ret_pct',
        'early_exit_net_pct','net_change_pct','early_result','loser_improved','winner_damaged',
        'mfe_10m_pct','mae_10m_pct'
    ]
    x[cols].sort_values('entry_time').to_csv(OUT_CASES, index=False)
    summary.to_csv(OUT_SUMMARY, index=False)

    print('\n=== V-REBOUND EARLY FAILURE EXIT ABLATION ===')
    print('Entry rules unchanged. Cohort = every trade admitted by the current selected V entry configuration.')
    print('Only exit change: while price is below entry, both MACD gap-delta and RSI slope are weaker than at entry for 2 consecutive 1m observations.')
    print('Trigger exit is valued at the observed trigger-minute close; round-trip fee 0.25% is applied.')
    print('\n=== SUMMARY ===')
    print(summary.to_string(index=False))
    print('\n=== CASE CHANGES ===')
    print(x[cols].sort_values('entry_time').to_string(index=False))
    print('\nDecision guide:')
    print('- Good: both existing losers improve, large winner is untouched, total net/PF improve, max loss does not worsen.')
    print('- If a winner is damaged, do NOT freeze this rule; inspect whether the 2-minute state needs price-structure context.')
    print('- This ablation tests failure handling only. Winner hold/exit remains a separate next step.')
    print('WROTE', OUT_CASES)
    print('WROTE', OUT_SUMMARY)


if __name__ == '__main__':
    main()
