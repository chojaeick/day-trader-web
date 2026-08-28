from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class DoubleBollingerEngine5Config:
    """User-specified 5-minute DBB/MACD/RSI logic.

    Engine 5 rules:
      - 5-minute candles.
      - Overall trend is rising.
      - MACD is above its signal line.
      - RSI slope is rising.
      - The outer Bollinger range is expanding.
      - Inner-band upward traversal and approximately 2x volume are confirmation
        diagnostics only; they are NOT mandatory entry gates.
      - Entry may occur at any Bollinger-band position when the mandatory trend,
        MACD, RSI and volatility-expansion conditions are satisfied.
      - TP1 is NOT frozen. After price breaks the dynamically rising outer-upper
        band and later comes back below the then-current outer-upper band, sell 50%.
      - Hold the remaining 50% through an inner-upper touch.
      - Sell the remainder only when MACD is trending down, RSI slope is down,
        and price reaches the inner-lower band.

    The existing DBB family parameters are preserved: 20-period center,
    inner +/-0.5 sigma, outer +/-3 sigma.
    """

    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bb_period: int = 20
    inner_sigma: float = 0.5
    outer_sigma: float = 3.0
    setup_lookback_bars: int = 8
    volume_multiple: float = 2.0


class DoubleBollingerEngine5:
    def __init__(self, config: DoubleBollingerEngine5Config = DoubleBollingerEngine5Config()):
        self.cfg = config

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
        inner_upper = mid + self.cfg.inner_sigma * std
        inner_lower = mid - self.cfg.inner_sigma * std
        outer_upper = mid + self.cfg.outer_sigma * std
        outer_lower = mid - self.cfg.outer_sigma * std
        return mid, inner_upper, inner_lower, outer_upper, outer_lower

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

    def enrich(self, bars_5m: pd.DataFrame) -> pd.DataFrame:
        """Attach causal Engine-5 diagnostics to completed 5-minute bars."""
        z = bars_5m.copy().sort_values('time').reset_index(drop=True)
        close = pd.to_numeric(z['close'], errors='coerce').astype(float)
        volume = pd.to_numeric(z['volume'], errors='coerce').fillna(0.0).astype(float)

        rsi = self._rsi(close, self.cfg.rsi_period)
        macd, signal = self._macd(close)
        mid, iu, il, ou, ol = self._bands(close)
        width = ou - ol
        n = self.cfg.setup_lookback_bars

        z['rsi'] = rsi
        z['rsi_slope'] = rsi.diff()
        z['macd'] = macd
        z['macd_signal'] = signal
        z['macd_slope'] = macd.diff()
        z['macd_above_signal'] = macd > signal
        z['macd_golden_cross'] = (macd.shift(1) <= signal.shift(1)) & (macd > signal)
        z['mid'] = mid
        z['inner_upper'] = iu
        z['inner_lower'] = il
        z['outer_upper'] = ou
        z['outer_lower'] = ol
        z['outer_width'] = width
        z['outer_expanding'] = width > width.shift(1)
        z['mid_slope8'] = self._rolling_slope(mid, n)
        z['trend_up'] = z['mid_slope8'] > 0

        # Confirmation diagnostics only. Neither 2x volume nor inner-band
        # traversal is required for an Engine-5 entry.
        prior_vol = volume.shift(1).rolling(n, min_periods=n).mean()
        z['volume_ratio'] = volume / prior_vol.replace(0.0, np.nan)
        z['volume_surge'] = z['volume_ratio'] >= self.cfg.volume_multiple

        touched_lower_recently = (close.shift(1) <= il.shift(1)).rolling(n, min_periods=1).max().fillna(0).astype(bool)
        cross_inner_upper_now = (close.shift(1) <= iu.shift(1)) & (close > iu)
        z['inner_traverse_up'] = touched_lower_recently & cross_inner_upper_now
        z['confirmation_inner_and_volume'] = z['inner_traverse_up'] & z['volume_surge']

        # Mandatory entry conditions only. Bollinger position is intentionally
        # unrestricted: the signal may occur below, inside, or above the inner
        # band. Inner traversal and volume surge remain available for reporting.
        z['entry_signal'] = (
            z['trend_up']
            & z['macd_above_signal']
            & (z['rsi_slope'] > 0)
            & z['outer_expanding']
        )
        return z
