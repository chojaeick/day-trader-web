from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from live_server.double_bollinger_engine5 import DoubleBollingerEngine5, DoubleBollingerEngine5Config


@dataclass(frozen=True)
class Engine5V16Policy:
    """Runtime-safe Engine5 entry policy validated by the V16 robustness panel.

    The 5-minute Engine5 signal remains the primary BUY source. During the
    opening hour, a large gap-up signal with severe 1-minute MACD-slope decay is
    downgraded from BUY to WAIT. The signal may enter later inside its natural
    five-minute lifetime only after 1-minute momentum reaccelerates.
    """

    open_entry_minute: int = 9 * 60 + 10
    opening_wait_end_minute: int = 10 * 60
    gap_wait_pct: float = 4.0
    fade_ratio_max: float = 0.25
    step_ratio_max: float = 0.35
    down_steps_min: int = 2
    signal_lifetime_minutes: int = 5


class DoubleBollingerEngine5V16(DoubleBollingerEngine5):
    def __init__(
        self,
        config: DoubleBollingerEngine5Config = DoubleBollingerEngine5Config(),
        policy: Engine5V16Policy = Engine5V16Policy(),
    ):
        super().__init__(config)
        self.policy = policy

    @staticmethod
    def _num(s: pd.Series) -> pd.Series:
        return pd.to_numeric(s, errors="coerce").astype(float)

    def refine_v10_entry_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Canonical V10 BUY/WAIT semantics used by V16 before micro timing.

        This is intentionally colocated with the live engine so backtest and
        runtime do not carry separate copies of the same entry logic.
        """
        z = frame.copy()
        prev_spread = self._num(z["macd_slope_spread"]).shift(1)
        prev_rsi_slope = self._num(z["rsi_slope"]).shift(1)
        prev_mid_slope = self._num(z["mid_slope8"]).shift(1)
        spread_strength = self._num(z["macd_slope_spread_strength"]).fillna(0.0)
        rsi_strength = self._num(z["rsi_slope_strength"]).fillna(0.0)

        macd_reaccel = (
            (z["macd_slope_spread"] > 0)
            & (
                (prev_spread <= 0)
                | (z["macd_slope_spread"] >= prev_spread * 0.85)
                | (spread_strength >= 0.75)
            )
        ).fillna(False)
        rsi_reaccel = (
            (z["rsi_slope"] > 0)
            & (
                (prev_rsi_slope <= 0)
                | (z["rsi_slope"] >= prev_rsi_slope * 0.70)
                | (rsi_strength >= 0.75)
            )
        ).fillna(False)

        continuation_buy = (
            z["trend_up"].fillna(False)
            & z["gate_macd_context"].fillna(False)
            & z["gate_macd_rising"].fillna(False)
            & macd_reaccel
            & rsi_reaccel
            & z["gate_rsi_persistent"].fillna(False)
        )

        mid_now = self._num(z["mid_slope8"])
        mid_improving = (
            mid_now.notna() & prev_mid_slope.notna() & (mid_now > prev_mid_slope)
        ).fillna(False)
        strong_macd_turn = (
            (z["macd_slope_spread"] > 0)
            & (z["macd_gap_delta"] > 0)
            & (z["macd_gap_delta"].shift(1) > 0)
            & (spread_strength >= 0.80)
        ).fillna(False)
        strong_rsi_turn = (
            (z["rsi_slope"] > 0)
            & (z["rsi_slope"].shift(1) > 0)
            & (rsi_strength >= 0.80)
        ).fillna(False)

        close = self._num(z["close"])
        iu = self._num(z["inner_upper"])
        price_confirm = (
            z["macd_golden_cross"].fillna(False)
            | z["inner_traverse_up"].fillna(False)
            | (iu.notna() & (close > iu))
        ).fillna(False)
        early_reversal_buy = (
            (~z["trend_up"].fillna(False))
            & mid_improving
            & strong_macd_turn
            & strong_rsi_turn
            & price_confirm
        )

        z["entry_mode_continuation"] = continuation_buy
        z["entry_mode_early_reversal"] = early_reversal_buy
        z["entry_gate_v10"] = continuation_buy | early_reversal_buy
        z["entry_gate"] = z["entry_gate_v10"]
        return z

    def build_rich_micro(self, bars_1m: pd.DataFrame) -> pd.DataFrame:
        f = bars_1m.copy().sort_values("time").reset_index(drop=True)
        f["time"] = pd.to_datetime(f["time"])
        close = self._num(f["close"])
        macd, signal = self._macd(close)
        rsi = self._rsi(close, self.cfg.rsi_period)
        f["macd_1m"] = macd
        f["signal_1m"] = signal
        f["macd_slope_1m"] = macd.diff()
        f["signal_slope_1m"] = signal.diff()
        f["spread_1m"] = f["macd_slope_1m"] - f["signal_slope_1m"]
        f["rsi_1m"] = rsi
        f["rsi_slope_1m"] = rsi.diff()
        return f

    def slope_decay_state(self, micro: pd.DataFrame, signal_time: pd.Timestamp) -> dict:
        """Measure only completed 1m bars before a completed 5m signal stamp."""
        t = pd.Timestamp(signal_time)
        q = micro[
            (pd.to_datetime(micro["time"]) >= t - pd.Timedelta(minutes=5))
            & (pd.to_datetime(micro["time"]) < t)
        ].copy()
        s = self._num(q["macd_slope_1m"]).dropna().to_numpy(dtype=float)
        if len(s) < 4:
            return {
                "wait": False,
                "down_steps": 0,
                "fade_ratio": np.nan,
                "step_ratio": np.nan,
                "peak": np.nan,
                "last": np.nan,
            }
        last4 = s[-4:]
        down_steps = int((np.diff(last4) < 0).sum())
        peak = float(np.max(last4))
        last = float(last4[-1])
        prev = float(last4[-2])
        fade_ratio = last / peak if peak > 0 else np.nan
        step_ratio = last / prev if prev > 0 else np.nan
        wait = bool(
            down_steps >= self.policy.down_steps_min
            and np.isfinite(fade_ratio)
            and fade_ratio <= self.policy.fade_ratio_max
            and np.isfinite(step_ratio)
            and step_ratio <= self.policy.step_ratio_max
        )
        return {
            "wait": wait,
            "down_steps": down_steps,
            "fade_ratio": fade_ratio,
            "step_ratio": step_ratio,
            "peak": peak,
            "last": last,
        }

    def should_wait_opening_signal(
        self,
        bars_1m: pd.DataFrame,
        signal_time: pd.Timestamp,
        gap_pct: float,
    ) -> tuple[bool, dict]:
        t = pd.Timestamp(signal_time)
        minute = t.hour * 60 + t.minute
        sensitive = bool(
            np.isfinite(float(gap_pct))
            and float(gap_pct) >= self.policy.gap_wait_pct
            and self.policy.open_entry_minute <= minute < self.policy.opening_wait_end_minute
        )
        micro = self.build_rich_micro(bars_1m)
        state = self.slope_decay_state(micro, t) if sensitive else {
            "wait": False,
            "down_steps": 0,
            "fade_ratio": np.nan,
            "step_ratio": np.nan,
            "peak": np.nan,
            "last": np.nan,
        }
        return bool(sensitive and state["wait"]), {"sensitive": sensitive, **state}

    @staticmethod
    def is_reaccel(prev_row, row) -> bool:
        vals = [
            prev_row.macd_slope_1m,
            row.macd_slope_1m,
            prev_row.spread_1m,
            row.spread_1m,
            row.rsi_slope_1m,
        ]
        try:
            if not all(np.isfinite(float(x)) for x in vals):
                return False
        except Exception:
            return False
        return bool(
            float(row.macd_slope_1m) > 0
            and float(row.macd_slope_1m) > float(prev_row.macd_slope_1m)
            and float(row.spread_1m) > float(prev_row.spread_1m)
            and float(row.rsi_slope_1m) > 0
        )

    def first_reaccel(
        self,
        bars_1m: pd.DataFrame,
        signal_time: pd.Timestamp,
    ) -> dict | None:
        """Find first V16 reacceleration inside the signal's 5m lifetime.

        No fixed cooldown and no better-price requirement are imposed.
        """
        t = pd.Timestamp(signal_time)
        micro = self.build_rich_micro(bars_1m)
        q = micro[
            (micro["time"] >= t)
            & (micro["time"] < t + pd.Timedelta(minutes=self.policy.signal_lifetime_minutes))
        ].copy()
        q = q[
            q["time"].dt.hour * 60 + q["time"].dt.minute
            < self.policy.opening_wait_end_minute
        ]
        prev = None
        for row in q.itertuples(index=False):
            if prev is not None and self.is_reaccel(prev, row):
                return {
                    "time": pd.Timestamp(row.time),
                    "price": float(row.close),
                    "macd_slope_1m": float(row.macd_slope_1m),
                    "spread_1m": float(row.spread_1m),
                    "rsi_slope_1m": float(row.rsi_slope_1m),
                }
            prev = row
        return None
