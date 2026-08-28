from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class DoubleBollingerEngine5Config:
    """5-minute DBB/MACD/RSI score engine.

    Engine 5 enters only when the directional conditions are all true:
      1) DBB mid is rising,
      2) MACD itself is rising,
      3) MACD is rising faster than its signal line,
      4) RSI is rising.

    Those four conditions are hard gates. Their *magnitude* plus MACD state,
    golden cross, RSI acceleration, volume, band expansion and inner-band
    traversal are then scored. This prevents a positive MACD slope spread from
    authorizing a buy while MACD itself is still falling, and prevents other
    score components from compensating for a flat/falling RSI.
    """

    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bb_period: int = 20
    inner_sigma: float = 0.5
    outer_sigma: float = 3.0
    lookback_bars: int = 8
    entry_score: float = 70.0

    w_trend: float = 20.0
    w_macd_state: float = 10.0
    w_macd_gap: float = 20.0
    w_golden: float = 5.0
    w_rsi_state: float = 20.0
    w_rsi_accel: float = 10.0
    w_volume: float = 5.0
    w_outer_expand: float = 5.0
    w_inner_traverse: float = 5.0

    macd_slope_spread_full_ratio: float = 2.0
    rsi_slope_full_ratio: float = 2.0
    volume_full_ratio: float = 2.0
    outer_expand_full_ratio: float = 0.03


class DoubleBollingerEngine5:
    def __init__(self, config: DoubleBollingerEngine5Config = DoubleBollingerEngine5Config()):
        self.cfg = config

    def with_entry_score(self, score: float) -> "DoubleBollingerEngine5":
        return DoubleBollingerEngine5(replace(self.cfg, entry_score=float(score)))

    @staticmethod
    def _clip01(x):
        return np.clip(x, 0.0, 1.0)

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> pd.Series:
        c = pd.to_numeric(close, errors='coerce').astype(float)
        d = c.diff()
        gain = d.clip(lower=0.0)
        loss = -d.clip(upper=0.0)
        ag = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
        al = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
        rs = ag / al.mask(al == 0.0, np.nan)
        return (100.0 - 100.0 / (1.0 + rs)).astype(float)

    def _macd(self, close: pd.Series):
        c = pd.to_numeric(close, errors='coerce').astype(float)
        macd = c.ewm(span=self.cfg.macd_fast, adjust=False).mean() - c.ewm(span=self.cfg.macd_slow, adjust=False).mean()
        signal = macd.ewm(span=self.cfg.macd_signal, adjust=False).mean()
        return macd, signal

    def _bands(self, close: pd.Series):
        c = pd.to_numeric(close, errors='coerce').astype(float)
        mid = c.rolling(self.cfg.bb_period).mean()
        std = c.rolling(self.cfg.bb_period).std(ddof=0)
        return (
            mid,
            mid + self.cfg.inner_sigma * std,
            mid - self.cfg.inner_sigma * std,
            mid + self.cfg.outer_sigma * std,
            mid - self.cfg.outer_sigma * std,
        )

    @staticmethod
    def _rolling_slope(s: pd.Series, n: int) -> pd.Series:
        x = np.arange(n, dtype=float)
        xc = x - x.mean()
        denom = float(np.dot(xc, xc))

        def f(a):
            a = np.asarray(a, dtype=float)
            if len(a) != n or not np.isfinite(a).all():
                return np.nan
            return float(np.dot(xc, a - a.mean()) / denom)

        return s.rolling(n, min_periods=n).apply(f, raw=True)

    @staticmethod
    def _relative_positive_strength(x: pd.Series, n: int, full_ratio: float) -> pd.Series:
        baseline = x.abs().shift(1).rolling(n, min_periods=max(3, n // 2)).median()
        ratio = x.clip(lower=0.0) / baseline.replace(0.0, np.nan)
        return np.clip(ratio / max(float(full_ratio), 1e-9), 0.0, 1.0).fillna(0.0)

    def enrich(self, bars_5m: pd.DataFrame) -> pd.DataFrame:
        z = bars_5m.copy().sort_values('time').reset_index(drop=True)
        close = pd.to_numeric(z['close'], errors='coerce').astype(float)
        volume = pd.to_numeric(z['volume'], errors='coerce').fillna(0.0).astype(float)
        n = self.cfg.lookback_bars

        rsi = self._rsi(close, self.cfg.rsi_period)
        macd, signal = self._macd(close)
        gap = macd - signal
        mid, iu, il, ou, ol = self._bands(close)
        width = ou - ol

        z['rsi'] = rsi
        z['rsi_slope'] = rsi.diff()
        z['rsi_accel'] = z['rsi_slope'] - z['rsi_slope'].shift(1)
        z['rsi_accelerating'] = (z['rsi_slope'] > 0) & (z['rsi_accel'] > 0)
        z['rsi_slope_strength'] = self._relative_positive_strength(z['rsi_slope'], n, self.cfg.rsi_slope_full_ratio)

        z['macd'] = macd
        z['macd_signal'] = signal
        z['macd_gap'] = gap
        z['macd_gap_delta'] = gap.diff()
        z['macd_slope'] = macd.diff()
        z['macd_signal_slope'] = signal.diff()
        z['macd_slope_spread'] = z['macd_slope'] - z['macd_signal_slope']
        z['macd_slope_spread_strength'] = self._relative_positive_strength(
            z['macd_slope_spread'], n, self.cfg.macd_slope_spread_full_ratio
        )
        z['macd_above_signal'] = macd > signal
        z['macd_gap_widening'] = z['macd_slope_spread'] > 0
        z['macd_golden_cross'] = (macd.shift(1) <= signal.shift(1)) & (macd > signal)

        z['mid'] = mid
        z['inner_upper'] = iu
        z['inner_lower'] = il
        z['outer_upper'] = ou
        z['outer_lower'] = ol
        z['outer_width'] = width
        z['outer_width_ratio'] = width / width.shift(1).replace(0.0, np.nan) - 1.0
        z['outer_expanding'] = z['outer_width_ratio'] > 0
        z['mid_slope8'] = self._rolling_slope(mid, n)
        z['trend_up'] = z['mid_slope8'] > 0

        prior_vol = volume.shift(1).rolling(n, min_periods=n).mean()
        z['volume_ratio'] = volume / prior_vol.replace(0.0, np.nan)
        z['volume_surge'] = z['volume_ratio'] >= self.cfg.volume_full_ratio

        touched_lower_recently = (close.shift(1) <= il.shift(1)).rolling(n, min_periods=1).max().fillna(0).astype(bool)
        cross_inner_upper_now = (close.shift(1) <= iu.shift(1)) & (close > iu)
        z['inner_traverse_up'] = touched_lower_recently & cross_inner_upper_now

        z['score_trend'] = np.where(z['trend_up'], self.cfg.w_trend, 0.0)
        z['score_macd_state'] = np.where(z['macd_above_signal'], self.cfg.w_macd_state, 0.0)
        z['score_macd_gap'] = self.cfg.w_macd_gap * z['macd_slope_spread_strength']
        z['score_golden'] = np.where(z['macd_golden_cross'], self.cfg.w_golden, 0.0)
        z['score_rsi_state'] = self.cfg.w_rsi_state * z['rsi_slope_strength']
        z['score_rsi_accel'] = np.where(z['rsi_accelerating'], self.cfg.w_rsi_accel, 0.0)

        vol_strength = self._clip01((z['volume_ratio'].fillna(0.0) - 1.0) / max(self.cfg.volume_full_ratio - 1.0, 1e-9))
        z['score_volume'] = self.cfg.w_volume * vol_strength
        width_strength = self._clip01(z['outer_width_ratio'].fillna(0.0) / max(self.cfg.outer_expand_full_ratio, 1e-9))
        z['score_outer_expand'] = self.cfg.w_outer_expand * width_strength
        z['score_inner_traverse'] = np.where(z['inner_traverse_up'], self.cfg.w_inner_traverse, 0.0)

        score_cols = [
            'score_trend', 'score_macd_state', 'score_macd_gap', 'score_golden',
            'score_rsi_state', 'score_rsi_accel', 'score_volume',
            'score_outer_expand', 'score_inner_traverse',
        ]
        z['entry_score'] = z[score_cols].sum(axis=1).clip(0.0, 100.0)

        # Hard directional gates: score can measure strength, but it cannot
        # compensate for a missing directional condition.
        z['gate_trend_up'] = z['trend_up'].fillna(False)
        z['gate_macd_rising'] = (z['macd_slope'] > 0).fillna(False)
        z['gate_macd_accel'] = (z['macd_slope_spread'] > 0).fillna(False)
        z['gate_rsi_rising'] = (z['rsi_slope'] > 0).fillna(False)
        z['entry_gate'] = (
            z['gate_trend_up']
            & z['gate_macd_rising']
            & z['gate_macd_accel']
            & z['gate_rsi_rising']
        )
        z['entry_signal'] = z['entry_gate'] & (z['entry_score'] >= self.cfg.entry_score)
        return z
