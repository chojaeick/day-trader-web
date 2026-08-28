from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class DoubleBollingerV22Config:
    """DBB V2.2 risk/exit policy for V2.1 structure entries.

    V2.2 deliberately keeps the V2.1 entry filter unchanged. Its purpose is to
    isolate whether adaptive risk and slower structural exits improve results.

    Rules:
      * Structural risk is the distance from ACTUAL fill price to the entry-time
        inner-lower Bollinger band.
      * Clamp that risk to 0.8%..2.0% of actual fill price.
      * The initial hard stop is actual fill - 1R.
      * TP1 sells 50% at exactly +2R from the ACTUAL fill price.
      * A stale completed 1-minute signal must not be used for a new order.
      * Before ordering, reject an entry when the current executable/reference
        price has moved too far from the signal price.
      * After a fill, always rebuild stop/TP1 from the actual fill; never keep a
        stop/TP1 calculated from the earlier signal price.
      * No pre-TP1 momentum/pullback liquidation.
      * No runner mid-touch liquidation.
      * No runner high-water trailing liquidation.
      * No RSI/MACD-only liquidation.
      * The remaining position is structurally liquidated only when an entire
        completed 1-minute candle is below the inner-lower band: candle HIGH <
        inner_lower. A mere wick below the band is ignored.
      * The hard stop always has priority as the absolute loss cap.
      * After an exit, normal V2.1 entry logic may re-enter on a later bar; there
        is no artificial cooldown in this policy.

    Freshness/slippage defaults are live-execution safety guards and must be
    validated separately from historical strategy performance.
    """

    min_risk_pct: float = 0.008
    max_risk_pct: float = 0.020
    tp1_r_multiple: float = 2.0
    partial_fraction: float = 0.5

    # Live execution guards for a 1-minute strategy.
    max_signal_age_seconds: float = 120.0
    max_preorder_drift_pct: float = 0.005


class DoubleBollingerV22ExitPolicy:
    def __init__(self, config: DoubleBollingerV22Config = DoubleBollingerV22Config()):
        self.cfg = config

    def structural_risk_pct(self, fill_price: float, entry_inner_lower: float) -> float:
        fill = float(fill_price)
        lower = float(entry_inner_lower)
        if fill <= 0.0:
            raise ValueError("fill_price must be positive")
        raw = max(0.0, (fill - lower) / fill)
        return max(self.cfg.min_risk_pct, min(self.cfg.max_risk_pct, raw))

    def initial_stop(self, fill_price: float, entry_inner_lower: float) -> float:
        fill = float(fill_price)
        risk_pct = self.structural_risk_pct(fill, entry_inner_lower)
        return fill * (1.0 - risk_pct)

    def tp1_price(self, fill_price: float, entry_inner_lower: float) -> float:
        fill = float(fill_price)
        risk_pct = self.structural_risk_pct(fill, entry_inner_lower)
        return fill * (1.0 + self.cfg.tp1_r_multiple * risk_pct)

    def build_fill_plan(self, fill_price: float, entry_inner_lower: float) -> dict[str, float]:
        """Freeze the live risk plan from the broker-confirmed fill price."""
        fill = float(fill_price)
        risk_pct = self.structural_risk_pct(fill, entry_inner_lower)
        return {
            "fill_price": fill,
            "entry_inner_lower": float(entry_inner_lower),
            "risk_pct": risk_pct,
            "stop_price": fill * (1.0 - risk_pct),
            "tp1_price": fill * (1.0 + self.cfg.tp1_r_multiple * risk_pct),
        }

    def signal_is_fresh(self, signal_time, now=None) -> bool:
        """Reject completed 1m bars that have become stale before evaluation/order."""
        if signal_time is None:
            return False
        try:
            if hasattr(signal_time, "to_pydatetime"):
                signal_time = signal_time.to_pydatetime()
            if not isinstance(signal_time, datetime):
                signal_time = datetime.fromisoformat(str(signal_time).replace("Z", "+00:00"))
            if signal_time.tzinfo is None:
                signal_time = signal_time.replace(tzinfo=timezone.utc)
            else:
                signal_time = signal_time.astimezone(timezone.utc)

            current = now
            if current is None:
                current = datetime.now(timezone.utc)
            elif hasattr(current, "to_pydatetime"):
                current = current.to_pydatetime()
            if not isinstance(current, datetime):
                current = datetime.fromisoformat(str(current).replace("Z", "+00:00"))
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            else:
                current = current.astimezone(timezone.utc)

            age = (current - signal_time).total_seconds()
            return 0.0 <= age <= self.cfg.max_signal_age_seconds
        except Exception:
            return False

    def preorder_price_is_valid(self, signal_price: float, current_price: float) -> bool:
        """Do not chase a signal after price has drifted too far before the order."""
        signal = float(signal_price)
        current = float(current_price)
        if signal <= 0.0 or current <= 0.0:
            return False
        drift = abs(current / signal - 1.0)
        return drift <= self.cfg.max_preorder_drift_pct

    def preorder_drift_pct(self, signal_price: float, current_price: float) -> float:
        signal = float(signal_price)
        current = float(current_price)
        if signal <= 0.0 or current <= 0.0:
            raise ValueError("prices must be positive")
        return abs(current / signal - 1.0)

    @staticmethod
    def candle_fully_below_inner_lower(high: float, inner_lower: float) -> bool:
        """True only when the whole completed 1m candle is below inner-lower."""
        return float(high) < float(inner_lower)
