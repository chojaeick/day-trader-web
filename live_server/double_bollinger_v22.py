from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class DoubleBollingerV22Config:
    """DBB V2.2 risk/exit policy for V2.1 structure entries.

    V2.2 deliberately keeps the V2.1 entry filter unchanged. Its purpose is to
    isolate whether adaptive risk and slower structural exits improve results.

    Strategy rules:
      * Structural risk is the distance from ACTUAL fill price to the entry-time
        inner-lower Bollinger band.
      * Clamp that risk to 0.8%..2.0% of actual fill price.
      * The initial hard stop is actual fill - 1R.
      * TP1 sells 50% at exactly +2R from the ACTUAL fill price.
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

    Live-execution safety rules:
      * A stale completed 1-minute signal must never open a new position.
      * Re-check a fresh current/reference price immediately before ordering.
      * Reject the order if price has drifted too far from the signal.
      * Do NOT use the legacy 1% marketable-limit cross. V2.2 caps the order
        cross to a small configurable amount around the fresh current price.
      * After the broker confirms the fill, rebuild stop/TP1 from the ACTUAL
        fill price; never keep levels derived from the signal price.

    Freshness/drift/cross defaults are execution-safety candidates and should be
    validated with live/mock logs separately from historical strategy results.
    """

    min_risk_pct: float = 0.008
    max_risk_pct: float = 0.020
    tp1_r_multiple: float = 2.0
    partial_fraction: float = 0.5

    # Live guards for a 1-minute strategy. These intentionally prevent the
    # several-minute-old / ~1% chase behavior observed in the legacy runner.
    max_signal_age_seconds: float = 90.0
    max_preorder_drift_pct: float = 0.003      # 0.30% signal -> fresh price
    max_order_cross_pct: float = 0.0015        # 0.15% fresh price -> limit
    max_signal_to_fill_pct: float = 0.005      # diagnostic ceiling, 0.50%


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
        """Freeze the risk plan only after the broker-confirmed fill exists."""
        fill = float(fill_price)
        risk_pct = self.structural_risk_pct(fill, entry_inner_lower)
        return {
            "fill_price": fill,
            "entry_inner_lower": float(entry_inner_lower),
            "risk_pct": risk_pct,
            "stop_price": fill * (1.0 - risk_pct),
            "tp1_price": fill * (1.0 + self.cfg.tp1_r_multiple * risk_pct),
        }

    @staticmethod
    def _utc_datetime(value):
        if hasattr(value, "to_pydatetime"):
            value = value.to_pydatetime()
        if not isinstance(value, datetime):
            value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        return value

    def signal_age_seconds(self, signal_time, now=None) -> float:
        if signal_time is None:
            return float("inf")
        try:
            signal_dt = self._utc_datetime(signal_time)
            current = datetime.now(timezone.utc) if now is None else self._utc_datetime(now)
            return (current - signal_dt).total_seconds()
        except Exception:
            return float("inf")

    def signal_is_fresh(self, signal_time, now=None) -> bool:
        """A 1m signal older than the configured live window is not tradable."""
        age = self.signal_age_seconds(signal_time, now)
        return 0.0 <= age <= self.cfg.max_signal_age_seconds

    def preorder_drift_pct(self, signal_price: float, current_price: float) -> float:
        signal = float(signal_price)
        current = float(current_price)
        if signal <= 0.0 or current <= 0.0:
            raise ValueError("prices must be positive")
        return abs(current / signal - 1.0)

    def preorder_price_is_valid(self, signal_price: float, current_price: float) -> bool:
        """Reject a fresh signal if the market has already run away from it."""
        try:
            return self.preorder_drift_pct(signal_price, current_price) <= self.cfg.max_preorder_drift_pct
        except Exception:
            return False

    def marketable_limit(self, current_price: float, side: str) -> float:
        """Small-cross limit around a FRESH current price; never the legacy 1%."""
        current = float(current_price)
        if current <= 0.0:
            raise ValueError("current_price must be positive")
        cross = max(0.0, float(self.cfg.max_order_cross_pct))
        s = str(side).upper().strip()
        if s == "BUY":
            px = current * (1.0 + cross)
        elif s == "SELL":
            px = current * (1.0 - cross)
        else:
            raise ValueError("side must be BUY or SELL")
        return round(px, 2 if px >= 1.0 else 4)

    def signal_to_fill_drift_pct(self, signal_price: float, fill_price: float) -> float:
        signal = float(signal_price)
        fill = float(fill_price)
        if signal <= 0.0 or fill <= 0.0:
            raise ValueError("prices must be positive")
        return abs(fill / signal - 1.0)

    def fill_within_diagnostic_ceiling(self, signal_price: float, fill_price: float) -> bool:
        """Post-fill diagnostic. A breach must be logged/reviewed, not ignored."""
        try:
            return self.signal_to_fill_drift_pct(signal_price, fill_price) <= self.cfg.max_signal_to_fill_pct
        except Exception:
            return False

    def validate_live_entry(self, signal_time, signal_price: float, current_price: float, now=None) -> tuple[bool, str, dict[str, float]]:
        """Single pre-order gate for V2.2 live execution."""
        age = self.signal_age_seconds(signal_time, now)
        try:
            drift = self.preorder_drift_pct(signal_price, current_price)
        except Exception:
            return False, "INVALID_PRICE", {"signal_age_sec": age}
        diag = {"signal_age_sec": age, "preorder_drift_pct": drift}
        if not (0.0 <= age <= self.cfg.max_signal_age_seconds):
            return False, "STALE_1M_SIGNAL", diag
        if drift > self.cfg.max_preorder_drift_pct:
            return False, "PREORDER_PRICE_DRIFT", diag
        return True, "OK", diag

    @staticmethod
    def candle_fully_below_inner_lower(high: float, inner_lower: float) -> bool:
        """True only when the whole completed 1m candle is below inner-lower."""
        return float(high) < float(inner_lower)
