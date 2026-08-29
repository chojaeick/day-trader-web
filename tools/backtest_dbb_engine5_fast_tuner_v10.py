from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v9 as v9

# Preserve the original indicator-frame builder before monkey-patching base.main().
_ORIGINAL_BUILD_CFG_FRAMES = base.build_cfg_frames

# V10: keep V9 opening filter + V8 strict risk/exit semantics, but tighten ENTRY semantics.
# Goal: distinguish BUY vs WAIT. A brief one-bar positive slope while RSI/MACD are
# still weakening must not count as a fresh entry. We require either:
#   A) continuation/re-acceleration in an existing uptrend, or
#   B) a strong early reversal pulse before the DBB mid slope fully turns positive.
base.CHECKPOINT = Path('/home/ubuntu/day-trader-api/dbb_engine5_exit_v10_checkpoint.csv')


def _refine_entry_frame(f: pd.DataFrame) -> pd.DataFrame:
    z = f.copy()

    prev_spread = pd.to_numeric(z['macd_slope_spread'], errors='coerce').shift(1)
    prev_rsi_slope = pd.to_numeric(z['rsi_slope'], errors='coerce').shift(1)
    prev_mid_slope = pd.to_numeric(z['mid_slope8'], errors='coerce').shift(1)
    spread_strength = pd.to_numeric(z['macd_slope_spread_strength'], errors='coerce').fillna(0.0)
    rsi_strength = pd.to_numeric(z['rsi_slope_strength'], errors='coerce').fillna(0.0)

    # Existing-uptrend continuation/re-acceleration BUY.
    # Positive values alone are insufficient: momentum must be newly turning up,
    # broadly maintained, or objectively strong versus recent history.
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

    # Strong early-reversal BUY.
    # This is the second path found in manual validation: MACD/RSI can turn hard
    # before the slower DBB mid-slope has crossed above zero. Allow that only with
    # strong two-bar MACD+RSI confirmation plus price/DBB confirmation.
    mid_now = pd.to_numeric(z['mid_slope8'], errors='coerce')
    mid_improving = (mid_now.notna() & prev_mid_slope.notna() & (mid_now > prev_mid_slope)).fillna(False)

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

    close = pd.to_numeric(z['close'], errors='coerce')
    iu = pd.to_numeric(z['inner_upper'], errors='coerce')
    price_confirm = (
        z['macd_golden_cross'].fillna(False)
        | z['inner_traverse_up'].fillna(False)
        | (iu.notna() & (close > iu))
    ).fillna(False)

    early_reversal_buy = (
        (~z['trend_up'].fillna(False))
        & mid_improving
        & strong_macd_turn
        & strong_rsi_turn
        & price_confirm
    )

    z['entry_mode_continuation'] = continuation_buy
    z['entry_mode_early_reversal'] = early_reversal_buy
    z['entry_gate_v10'] = continuation_buy | early_reversal_buy
    z['entry_gate'] = z['entry_gate_v10']
    return z


def build_cfg_frames(raw, cfg):
    frames = _ORIGINAL_BUILD_CFG_FRAMES(raw, cfg)
    return {sym: _refine_entry_frame(f) for sym, f in frames.items()}


def pack_entry_events(scored_frames):
    # V10 gate is already in entry_gate. Reuse V9's 09:10 opening filter on top.
    return v9.pack_entry_events(scored_frames)


def main():
    print('[ENGINE5 V10] V9 09:00-09:09 block + V8 strict 1R/2R exits; NEW BUY/WAIT gate: continuation requires maintained/re-accelerating MACD+RSI; strong early reversal may enter before DBB mid-slope turns positive.', flush=True)
    base.CHECKPOINT = Path('/home/ubuntu/day-trader-api/dbb_engine5_exit_v10_checkpoint.csv')
    base.build_cfg_frames = build_cfg_frames
    base.pack_entry_events = pack_entry_events
    base.pack_exit_events = v8.base.pack_exit_events
    base.simulate_v4 = v8.v7.simulate_v7
    base.main()


if __name__ == '__main__':
    main()
