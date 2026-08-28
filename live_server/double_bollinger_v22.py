from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DoubleBollingerV22Config:
    """DBB V2.2 risk/exit policy for V2.1 structure entries.

    V2.2 deliberately keeps the V2.1 entry filter unchanged.  Its purpose is to
    isolate whether adaptive risk and slower structural exits improve results.

    Rules:
      * Structural risk is the distance from entry to the entry-time inner-lower
        Bollinger band.
      * Clamp that risk to 0.8%..2.0% of entry.  Narrow bands therefore get at
        least 0.8% room; extremely wide bands can never risk more than 2.0%.
      * The initial hard stop is entry - 1R using that clamped distance.
      * TP1 sells 50% at exactly +2R.
      * No pre-TP1 momentum/pullback liquidation.
      * No runner mid-touch liquidation.
      * No runner high-water trailing liquidation.
      * No RSI/MACD-only liquidation.
      * The remaining position is structurally liquidated only when an entire
        completed 1-minute candle is below the inner-lower band.  In code this
        means candle HIGH < inner_lower, so neither body nor wick remains inside
        the band.  A mere wick below the band is ignored.
      * The hard stop always has priority as the absolute loss cap.
      * After an exit, normal V2.1 entry logic may re-enter on a later bar; there
        is no artificial cooldown in this policy.
    """

    min_risk_pct: float = 0.008
    max_risk_pct: float = 0.020
    tp1_r_multiple: float = 2.0
    partial_fraction: float = 0.5


class DoubleBollingerV22ExitPolicy:
    def __init__(self, config: DoubleBollingerV22Config = DoubleBollingerV22Config()):
        self.cfg = config

    def structural_risk_pct(self, entry_price: float, entry_inner_lower: float) -> float:
        entry = float(entry_price)
        lower = float(entry_inner_lower)
        if entry <= 0.0:
            raise ValueError("entry_price must be positive")
        raw = max(0.0, (entry - lower) / entry)
        return max(self.cfg.min_risk_pct, min(self.cfg.max_risk_pct, raw))

    def initial_stop(self, entry_price: float, entry_inner_lower: float) -> float:
        entry = float(entry_price)
        risk_pct = self.structural_risk_pct(entry, entry_inner_lower)
        return entry * (1.0 - risk_pct)

    def tp1_price(self, entry_price: float, entry_inner_lower: float) -> float:
        entry = float(entry_price)
        risk_pct = self.structural_risk_pct(entry, entry_inner_lower)
        return entry * (1.0 + self.cfg.tp1_r_multiple * risk_pct)

    @staticmethod
    def candle_fully_below_inner_lower(high: float, inner_lower: float) -> bool:
        """True only when the whole completed 1m candle is below inner-lower."""
        return float(high) < float(inner_lower)
