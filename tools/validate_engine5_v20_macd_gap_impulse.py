from __future__ import annotations

import numpy as np
import pandas as pd

import tools.validate_engine5_v20_macd_real_cross as core

# Do not require a golden-cross sign pattern.  The signal is the SPEED at which
# MACD-signal separation becomes strongly positive.
# All values are normalized by price (% points), so symbols are comparable.
TOTAL_SWING_LEVELS = [0.05, 0.08, 0.10, 0.15, 0.20, 0.25, 0.30]
MIN_STEP_LEVELS = [0.010, 0.020, 0.030, 0.040, 0.050, 0.060, 0.065]


def gap_impulse(m, row, total_swing_pct, min_step_pct):
    """Strong positive MACD-signal separation impulse, not 'golden cross'.

    Last four completed 1m gaps are g0..g3.  We require:
      * current/final gap g3 > 0
      * gaps rise on every step: g0 < g1 < g2 < g3
      * every step is at least min_step_pct
      * total rise g3-g0 is at least total_swing_pct
      * current MACD slope, gap delta and RSI slope are positive

    There is intentionally NO requirement such as g0<0,g1<0,g2>0.
    Therefore a violent widening that starts before or after the literal
    MACD/signal crossing can qualify, while weak rubbing around zero cannot.
    """
    if row is None or not core.base_direction_ok(row):
        return False, 'BASE_DIRECTION_FAIL', None

    q = core.last4(m, row.time)
    if q.empty:
        return False, 'NO_LAST4', None

    gaps = np.array([core.gap_pct(r) for _, r in q.iterrows()], dtype=float)
    if not np.all(np.isfinite(gaps)):
        return False, 'NONFINITE_GAPS', None

    g0, g1, g2, g3 = gaps
    steps = np.diff(gaps)
    total = g3 - g0
    metrics = {
        'g0': g0, 'g1': g1, 'g2': g2, 'g3': g3,
        'step1': steps[0], 'step2': steps[1], 'step3': steps[2],
        'total_swing': total,
    }

    if g3 <= 0:
        return False, 'FINAL_GAP_NOT_POSITIVE', metrics
    if not np.all(steps > 0):
        return False, 'GAP_NOT_MONOTONIC_UP', metrics
    if not np.all(steps >= min_step_pct):
        return False, 'GAP_STEPS_TOO_WEAK', metrics
    if total < total_swing_pct:
        return False, 'GAP_TOTAL_SWING_TOO_WEAK', metrics
    return True, 'STRONG_POSITIVE_GAP_IMPULSE', metrics


def main():
    # Reuse the frozen V18/V19 reconstruction and simulator, replacing only the
    # MACD trigger semantics and sweep levels.
    core.strict_cross = gap_impulse
    core.TOTAL_SWING_LEVELS = TOTAL_SWING_LEVELS
    core.MIN_STEP_LEVELS = MIN_STEP_LEVELS

    print('=== V20 MACD-SIGNAL POSITIVE GAP IMPULSE ===')
    print('Golden-cross shape is NOT required.')
    print('Signal = MACD-signal gap rapidly expanding toward/into positive territory.')
    print('Weak rubbing around zero is discarded; fee remains 0.25% round-trip.')
    core.main()


if __name__ == '__main__':
    main()
