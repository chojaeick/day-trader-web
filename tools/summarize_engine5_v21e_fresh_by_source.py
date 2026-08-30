from __future__ import annotations

"""Lightweight attribution summary for the already-completed fresh V21E run.

Reads the saved trade CSV only. No DB rebuild and no backtest rerun.
Reports gross/net(0.25% RT) metrics by V21E internal source and symbol.
"""

from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path('/home/ubuntu/day-trader-api/engine5_v21e_fresh_validation')
TRADES = ROOT / 'v21e_fresh_trades.csv'
OUT_SOURCE = ROOT / 'v21e_fresh_by_source.csv'
OUT_SYMBOL = ROOT / 'v21e_fresh_by_symbol.csv'
FEE_RT_PCT = 0.25


def stats(g: pd.DataFrame) -> dict:
    x = pd.to_numeric(g['pnl_pct'], errors='coerce').dropna()
    net = x - FEE_RT_PCT

    def pf(v: pd.Series) -> float:
        gp = float(v[v > 0].sum()) if len(v) else 0.0
        gl = float(-v[v < 0].sum()) if len(v) else 0.0
        return gp / gl if gl > 0 else np.inf

    return {
        'trades': int(len(x)),
        'gross_wins': int((x > 0).sum()),
        'gross_wr_pct': float((x > 0).mean() * 100.0) if len(x) else 0.0,
        'gross_sum_pct': float(x.sum()) if len(x) else 0.0,
        'gross_avg_pct': float(x.mean()) if len(x) else 0.0,
        'gross_pf': float(pf(x)),
        'net025_wins': int((net > 0).sum()),
        'net025_wr_pct': float((net > 0).mean() * 100.0) if len(net) else 0.0,
        'net025_sum_pct': float(net.sum()) if len(net) else 0.0,
        'net025_avg_pct': float(net.mean()) if len(net) else 0.0,
        'net025_pf': float(pf(net)),
        'max_gross_loss_pct': float(x.min()) if len(x) else np.nan,
        'max_net025_loss_pct': float(net.min()) if len(net) else np.nan,
    }


def summarize(df: pd.DataFrame, key: str) -> pd.DataFrame:
    rows = []
    for value, g in df.groupby(key, dropna=False, sort=False):
        rows.append({key: value, **stats(g)})
    return pd.DataFrame(rows).sort_values('net025_sum_pct', ascending=False).reset_index(drop=True)


def main():
    if not TRADES.exists():
        raise FileNotFoundError(TRADES)
    tr = pd.read_csv(TRADES)
    if 'source' not in tr.columns or 'symbol' not in tr.columns or 'pnl_pct' not in tr.columns:
        raise ValueError(f'Unexpected trade CSV columns: {list(tr.columns)}')

    by_source = summarize(tr, 'source')
    by_symbol = summarize(tr, 'symbol')
    by_source.to_csv(OUT_SOURCE, index=False)
    by_symbol.to_csv(OUT_SYMBOL, index=False)

    show = ['source','trades','gross_wr_pct','gross_sum_pct','gross_pf','net025_wr_pct','net025_sum_pct','net025_pf','max_net025_loss_pct']
    print('=== V21E FRESH ATTRIBUTION BY SOURCE ===')
    print(by_source[show].to_string(index=False, float_format=lambda x: f'{x:.4f}'))

    show2 = ['symbol','trades','gross_wr_pct','gross_sum_pct','net025_wr_pct','net025_sum_pct','net025_pf']
    print('\n=== BY SYMBOL ===')
    print(by_symbol[show2].to_string(index=False, float_format=lambda x: f'{x:.4f}'))
    print('\nWROTE', OUT_SOURCE)
    print('WROTE', OUT_SYMBOL)


if __name__ == '__main__':
    main()
