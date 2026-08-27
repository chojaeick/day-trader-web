from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from live_server.strategy_core_v1 import Action, PositionPhase, PositionState, SignalResult, enforce_long_stop


@dataclass(frozen=True)
class WilliamsConfig:
    k: float = 0.5
    rsi_period: int = 2
    rsi_min: float = 50.0
    fallback_risk_pct: float = 0.015
    max_swing_risk_pct: float = 0.025
    swing_left: int = 2
    swing_right: int = 2
    partial_at_r: Optional[float] = None
    partial_fraction: float = 0.5


class CleanWilliamsV1:
    """Frozen Williams core for SOXL/SOXS.

    Entry is ONLY a fresh current-bar volatility breakout plus RSI(2).
    No finder, ranking, STRUCT5, stale/recovered signal, MACD, CCI, Bollinger or MTF entry path.
    Backtest/replay/live must call this same evaluator.
    """

    ALLOWED_SYMBOLS = {"SOXL", "SOXS"}

    def __init__(self, config: WilliamsConfig = WilliamsConfig()):
        self.cfg = config

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> pd.Series:
        d = pd.to_numeric(close, errors="coerce").astype(float).diff()
        gain = d.clip(lower=0)
        loss = -d.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0, pd.NA)
        out = 100 - 100 / (1 + rs)
        return out.fillna(100.0).astype(float)

    def _confirmed_swing_low(self, bars: pd.DataFrame, before_index: Optional[int] = None) -> Optional[float]:
        if len(bars) < self.cfg.swing_left + self.cfg.swing_right + 1:
            return None
        low = pd.to_numeric(bars["low"], errors="coerce").astype(float).reset_index(drop=True)
        end = len(low) - self.cfg.swing_right
        if before_index is not None:
            end = min(end, int(before_index))
        found = None
        for i in range(self.cfg.swing_left, max(self.cfg.swing_left, end)):
            v = low.iloc[i]
            if pd.isna(v):
                continue
            left = low.iloc[i - self.cfg.swing_left:i]
            right = low.iloc[i + 1:i + 1 + self.cfg.swing_right]
            if len(right) < self.cfg.swing_right:
                continue
            if v < left.min() and v <= right.min():
                found = float(v)
        return found

    def _entry_stop(self, entry: float, bars: pd.DataFrame) -> float:
        swing = self._confirmed_swing_low(bars)
        if swing is not None:
            risk_pct = (entry - swing) / entry
            if 0 < risk_pct <= self.cfg.max_swing_risk_pct:
                return enforce_long_stop(entry, swing, self.cfg.fallback_risk_pct)
        return enforce_long_stop(entry, None, self.cfg.fallback_risk_pct)

    def evaluate_flat(
        self,
        symbol: str,
        bars_1m: pd.DataFrame,
        session_open: float,
        prev_high: float,
        prev_low: float,
    ) -> SignalResult:
        symbol = symbol.upper()
        if symbol not in self.ALLOWED_SYMBOLS:
            raise ValueError(f"unsupported symbol: {symbol}")
        if len(bars_1m) < max(5, self.cfg.rsi_period + 2):
            return SignalResult(symbol, Action.HOLD, "INSUFFICIENT_BARS", 0.0)

        close = pd.to_numeric(bars_1m["close"], errors="coerce").astype(float)
        prev_close = float(close.iloc[-2])
        price = float(close.iloc[-1])
        trigger = float(session_open) + self.cfg.k * (float(prev_high) - float(prev_low))
        rsi = self._rsi(close, self.cfg.rsi_period)
        rsi_now = float(rsi.iloc[-1])

        fresh_cross = prev_close <= trigger < price
        entry_ok = bool(fresh_cross and rsi_now > self.cfg.rsi_min)
        diag = {
            "trigger": trigger,
            "prev_close": prev_close,
            "price": price,
            "rsi2": rsi_now,
            "fresh_cross": fresh_cross,
        }
        if not entry_ok:
            return SignalResult(symbol, Action.HOLD, "WATCH", price, diagnostics=diag)

        stop = self._entry_stop(price, bars_1m.iloc[:-1])
        if not (0 < stop < price):
            return SignalResult(symbol, Action.HOLD, "INVALID_STOP_BLOCK", price, diagnostics=diag)
        diag["risk_pct"] = (price - stop) / price
        return SignalResult(symbol, Action.ENTER, "FRESH_WILLIAMS_BREAKOUT", price, score=1.0, stop=stop, diagnostics=diag)

    def evaluate_open(self, state: PositionState, bars_1m: pd.DataFrame) -> SignalResult:
        if state.entry_price is None or state.stop_price is None or state.phase not in {PositionPhase.OPEN, PositionPhase.RUNNER}:
            raise ValueError("open evaluation requires a valid open/runner position")
        price = float(pd.to_numeric(bars_1m["close"], errors="coerce").iloc[-1])
        state.update_high(price)

        if price <= state.stop_price:
            return SignalResult(state.symbol, Action.FULL_EXIT, "STOP_OR_TRAILING_SUPPORT_BREAK", price, stop=state.stop_price, exit_fraction=1.0)

        # Trail only with a newly CONFIRMED swing low formed after entry. Stop may move upward only.
        swing = self._confirmed_swing_low(bars_1m)
        if swing is not None and state.entry_price < swing < price and swing > state.stop_price:
            state.stop_price = float(swing)

        risk = state.entry_price - state.stop_price if state.stop_price < state.entry_price else None
        if self.cfg.partial_at_r and not state.partial_exit_done and risk and risk > 0:
            target = state.entry_price + self.cfg.partial_at_r * risk
            if price >= target:
                return SignalResult(
                    state.symbol,
                    Action.PARTIAL_EXIT,
                    "OPTIONAL_R_PARTIAL",
                    price,
                    stop=state.stop_price,
                    exit_fraction=self.cfg.partial_fraction,
                    diagnostics={"target": target},
                )

        return SignalResult(state.symbol, Action.HOLD, "RUN_TREND", price, stop=state.stop_price)
