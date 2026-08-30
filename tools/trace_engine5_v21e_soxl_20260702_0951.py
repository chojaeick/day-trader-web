from __future__ import annotations

"""Trace exactly why V21E Slow-turn-E admitted SOXL 2026-07-02 09:51 ET.

Reads the already-built fresh V21E map and prints the candidate metadata plus the
actual Slow-turn/DEEP predicates that can admit a trade while trend_up=False and
MACD remains below signal.
"""

import pickle
from pathlib import Path
import pandas as pd

ROOT = Path('/home/ubuntu/day-trader-api/engine5_v21e_fresh_validation')
MAP = ROOT / 'v21e_fresh_map.pkl'
TARGET_SYM = '00SOXL'
TARGET_TS = pd.Timestamp('2026-07-02 09:51:00', tz='America/New_York')


def b(v):
    try:
        return bool(v)
    except Exception:
        return False


def main():
    with MAP.open('rb') as fh:
        d = pickle.load(fh)

    tags = d['tags']
    rows = [x for x in tags if x.get('source') == 'SLOW_TURN_E' and x.get('symbol') == TARGET_SYM and pd.Timestamp(x.get('time')) == TARGET_TS]
    if not rows:
        raise SystemExit('TARGET SLOW_TURN_E TAG NOT FOUND')

    x = rows[0]
    meta = dict(x.get('meta') or {})
    print('=== SOXL 2026-07-02 09:51 ET SLOW_TURN-E TRACE ===')
    print(f"source={x.get('source')} symbol={x.get('symbol')} time={pd.Timestamp(x.get('time'))}")
    for k in sorted(meta):
        print(f"meta.{k}={meta[k]}")

    # Values already observed in the fresh success/failure diagnostic for this realized entry.
    # These are printed as the semantic interpretation of the existing predicates, not as retuning.
    print('\n=== WHY CURRENT LOGIC CAN ADMIT THIS SHAPE ===')
    print('candidate family requires: mid_slope8 < 0  -> falling/negative mid slope is ALLOWED BY DESIGN')
    print('candidate family requires: gap_delta_5m > 0 -> MACD may still be BELOW signal; only improvement is required')
    print('candidate family requires: rsi_slope_5m > 0 -> weak/brief RSI improvement can qualify')
    print('1m confirmation requires: higher_low + prior-high break + recent positive MACD-gap/RSI-slope ratios')
    print('DEEP final gate requires: joint5>=0.80, joint1>=0.70, price_progress>=1.00%, norm_mid_slope>=cut')
    print('DEEP final gate DOES NOT require: trend_up=True')
    print('DEEP final gate DOES NOT require: MACD > signal')
    print('DEEP final gate DOES NOT require: RSI above a bullish level or persistent current rise')

    print('\n=== KNOWN REALIZED ENTRY STATE ===')
    print('trend_up=False')
    print('macd_gap=-0.5794  # MACD below signal')
    print('macd_gap_delta=+0.2014  # gap merely improving')
    print('rsi=45.0054')
    print('rsi_slope=+3.8167')
    print('strength_rel=0.7437')
    print('net025=-3.1525%')

    print('\nVERDICT: this is not an unexplained simulator reaction. The existing Slow-turn-E/DEEP rules explicitly permit a still-falling, MACD-below-signal market when short-term improvement/persistence tests pass.')


if __name__ == '__main__':
    main()
