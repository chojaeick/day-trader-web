from __future__ import annotations

from pathlib import Path

import numpy as np

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v6 as v6
import tools.backtest_dbb_engine5_fast_tuner_v7 as v7

# V8 restores the strict risk rule requested for Engine 5:
# stop distance is always exactly one full 5m inner-band width at entry.
# Extended entries above outer-upper no longer widen the initial stop.
base.CHECKPOINT = Path('/home/ubuntu/day-trader-api/dbb_engine5_exit_v8_checkpoint.csv')


def _finite(v):
    try:
        x = float(v)
        return x if np.isfinite(x) else np.nan
    except Exception:
        return np.nan


def pack_entry_events(scored_frames):
    """Strict Engine5 risk geometry.

    R = full 5m inner-band width at entry.
    Initial stop = entry - 1R for every entry, including close > outer-upper.
    TP1 = entry + 2R (handled by V7 simulator).
    """
    ev = {}
    for sym, f in scored_frames.items():
        if 'entry_gate' not in f.columns:
            raise RuntimeError('Engine 5 frame missing entry_gate; corrected persistence gate is not deployed')
        q = f[f['entry_gate']].copy()
        cols = [
            'time', 'close', 'entry_score', 'macd_slope_spread_strength',
            'rsi_slope_strength', 'inner_upper', 'inner_lower', 'outer_upper', 'mid',
        ]
        for r in q[cols].itertuples(index=False, name=None):
            ts = r[0]
            close = float(r[1])
            iu, il, ou, mid = _finite(r[5]), _finite(r[6]), _finite(r[7]), _finite(r[8])
            band_r = iu - il if np.isfinite(iu) and np.isfinite(il) else np.nan
            if not np.isfinite(band_r) or band_r <= 0:
                continue
            extended_entry = bool(np.isfinite(ou) and close > ou)
            stop_dist = band_r
            ev.setdefault(ts, []).append((
                sym, close, float(r[2]), _finite(r[3]), _finite(r[4]),
                band_r, stop_dist, iu, il, ou, mid, extended_entry,
            ))
    return ev


# Keep V6 1m packing and V7 exit state machine; replace only initial risk geometry.
base.pack_exit_events = v6.pack_exit_events
base.pack_entry_events = pack_entry_events
base.simulate_v4 = v7.simulate_v7


def main():
    print('[ENGINE5 EXIT V8] STRICT 1R STOP: R=raw 5m inner-band width for every entry, including extended entries; TP1=+2R 50%; V7 post-TP1 continuation-arm/fade logic retained.', flush=True)
    base.main()


if __name__ == '__main__':
    main()
