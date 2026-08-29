from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v9 as v9

# V10: keep V9 time filter + V8 strict risk/exit semantics, but tighten ENTRY semantics.
# Goal: distinguish BUY vs WAIT. A brief one-bar positive slope while RSI/MACD are
# still weakening must not count as a fresh entry. We require either:
#   A) continuation/re-acceleration in an existing uptrend, or
#   B) a strong early reversal pulse before the DBB mid slope fully turns positive.
base.CHECKPOINT = Path('/home/ubuntu/day-trader-api/dbb_engine5_exit_v10_checkpoint.csv')


def _finite(v):
    try:
        x = float(v)
        return x if np.isfinite(x) else np.nan
    except Exception:
        return np.nan


def _refine_entry_frame(f: pd.DataFrame) -> pd.DataFrame:
    z = f.copy()

    # Current/previous momentum context.
    prev_spread = pd.to_numeric(z['macd_slope_spread'], errors='coerce').shift(1)
    prev_rsi_slope = pd.to_numeric(z['rsi_slope'], errors='coerce').shift(1)
    prev_mid_slope = pd.to_numeric(z['mid_slope8'], errors='coerce').shift(1)
    spread_strength = pd.to_numeric(z['macd_slope_spread_strength'], errors='coerce').fillna(0.0)
    rsi_strength = pd.to_numeric(z['rsi_slope_strength'], errors='coerce').fillna(0.0)

    # Existing-trend continuation path.
    # BUY only if momentum is not merely positive, but is being maintained/re-accelerated.
    macd_reaccel = (
        (z['macd_slope_spread'] > 0)
        & (
            (prev_spread <= 0)
            | (z['macd_slope_spread'] >= prev_spread * 0.85)
            | (spread_strength >= 0.75)
        )
    ).fillna(False)
    rsi_reaccel = (
        (z['rsi_slope'] > 0)
        & (
            (prev_rsi_slope <= 0)
            | (z['rsi_slope'] >= prev_rsi_slope * 0.70)
            | (rsi_strength >= 0.75)
        )
    ).fillna(False)

    continuation_buy = (
        z['trend_up'].fillna(False)
        & z['gate_macd_context'].fillna(False)
        & z['gate_macd_rising'].fillna(False)
        & macd_reaccel
        & rsi_reaccel
        & z['gate_rsi_persistent'].fillna(False)
    )

    # Early-reversal path.
    # Permit entry before mid_slope8 becomes positive only when MACD+RSI reversal is
    # unusually strong and DBB/price confirms an upward transition.
    mid_improving = (
        np.isfinite(pd.to_numeric(z['mid_slope8'], errors='coerce'))
        & np.isfinite(prev_mid_slope)
        & (z['mid_slope8'] > prev_mid_slope)
    )
    strong_macd_turn = (
        (z['macd_slope_spread'] > 0)
        & (z['macd_gap_delta'] > 0)
        & (z['macd_gap_delta'].shift(1) > 0)
        & (spread_strength >= 0.80)
    ).fillna(False)
    strong_rsi_turn = (
        (z['rsi_slope'] > 0)
        & (z['rsi_slope'].shift(1) > 0)
        & (rsi_strength >= 0.80)
    ).fillna(False)
    price_confirm = (
        z['macd_golden_cross'].fillna(False)
        | z['inner_traverse_up'].fillna(False)
        | (
            np.isfinite(pd.to_numeric(z['inner_upper'], errors='coerce'))
            & (pd.to_numeric(z['close'], errors='coerce') > pd.to_numeric(z['inner_upper'], errors='coerce'))
        )
    )
    early_reversal_buy = (
        (~z['trend_up'].fillna(False))
        & mid_improving.fillna(False)
        & strong_macd_turn
        & strong_rsi_turn
        & price_confirm.fillna(False)
    )

    z['entry_mode_continuation'] = continuation_buy
    z['entry_mode_early_reversal'] = early_reversal_buy
    z['entry_gate_v10'] = continuation_buy | early_reversal_buy
    z['entry_gate'] = z['entry_gate_v10']
    z['entry_signal'] = z['entry_gate'] & (z['entry_score'] >= z.get('entry_score_threshold', 0))
    return z


def build_cfg_frames(raw, cfg):
    # Reuse V4's indicator construction, then apply V10 BUY/WAIT/BLOCK gate.
    frames = base.build_cfg_frames(raw, cfg)
    return {sym: _refine_entry_frame(f) for sym, f in frames.items()}


def main():
    print('[ENGINE5 V10] V9 09:00-09:09 entry block + V8 strict 1R/2R exits; NEW BUY/WAIT entry gate: continuation requires maintained/re-accelerating MACD+RSI, and strong early-reversal path can enter before DBB mid slope turns positive.', flush=True)

    # Patch the base runner so every candidate uses V10-refined frames.
    base.build_cfg_frames = build_cfg_frames
    base.pack_entry_events = v8.pack_entry_events
    base.pack_exit_events = v8.base.pack_exit_events
    base.simulate_v4 = v9.simulate_v9
    base.main()


if __name__ == '__main__':
    main()
