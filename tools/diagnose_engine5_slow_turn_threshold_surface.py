from pathlib import Path

import numpy as np
import pandas as pd

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
SRC = OUT_DIR / 'slow_turn_zero_cross_candidates.csv'
OUT = OUT_DIR / 'slow_turn_threshold_surface.csv'
FEE_RT_PCT = 0.25
ZERO_THRESHOLDS = [1.5, 3.0, 5.0, 8.0, 12.0]


def num(x):
    return pd.to_numeric(x, errors='coerce')


def stats(g):
    pnl = num(g['pnl_pct']).dropna()
    net = pnl - FEE_RT_PCT
    gp = float(net[net > 0].sum()) if len(net) else 0.0
    gl = float(-net[net < 0].sum()) if len(net) else 0.0
    return dict(
        trades=int(len(net)),
        wins=int((net > 0).sum()),
        win_pct=float((net > 0).mean() * 100.0) if len(net) else 0.0,
        net_sum=float(net.sum()) if len(net) else 0.0,
        avg_net=float(net.mean()) if len(net) else 0.0,
        pf=(gp / gl if gl > 0 else np.inf),
        max_loss=float(net.min()) if len(net) else np.nan,
    )


def main():
    if not SRC.exists():
        raise SystemExit(f'MISSING SOURCE: {SRC}')

    df = pd.read_csv(SRC)
    if df.empty:
        raise SystemExit('SOURCE CSV IS EMPTY')

    df['zero_cross_bars'] = num(df['zero_cross_bars'])
    df['gap_delta_5m'] = num(df['gap_delta_5m'])
    df['pnl_pct'] = num(df['pnl_pct'])
    df = df[np.isfinite(df['zero_cross_bars']) & np.isfinite(df['gap_delta_5m']) & np.isfinite(df['pnl_pct'])].copy()

    macd_filters = [
        ('ALL', lambda x: pd.Series(True, index=x.index)),
        ('<=10', lambda x: x <= 10),
        ('<=20', lambda x: x <= 20),
        ('20-40', lambda x: (x > 20) & (x <= 40)),
        ('<=40', lambda x: x <= 40),
        ('40-80', lambda x: (x > 40) & (x <= 80)),
        ('>80', lambda x: x > 80),
    ]

    rows = []
    for zt in ZERO_THRESHOLDS:
        zmask = df['zero_cross_bars'] <= zt
        for label, fn in macd_filters:
            mmask = fn(df['gap_delta_5m'])
            g = df[zmask & mmask]
            rows.append(dict(
                zero_cross_max=zt,
                macd_filter=label,
                **stats(g),
                median_zero_cross=float(num(g['zero_cross_bars']).median()) if len(g) else np.nan,
                median_macd=float(num(g['gap_delta_5m']).median()) if len(g) else np.nan,
            ))

    out = pd.DataFrame(rows)
    out.to_csv(OUT, index=False)

    print('=== SLOW TURN CUMULATIVE THRESHOLD SURFACE ===')
    print('Source candidates only. No trading rule changed. No threshold selected automatically.')
    print(f'SOURCE_TRADES={len(df)}')
    print()
    print(out.to_string(index=False))
    print()
    print('WROTE', OUT)


if __name__ == '__main__':
    main()
