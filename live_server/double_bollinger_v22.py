from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DoubleBollingerV22Config:
    """Exit-policy extension for DBB V2.1 structure entries.

    V2.2 intentionally keeps the V2.1 entry filter unchanged so the backtest can
    isolate the effect of exit management.

    Rules:
      * Catastrophic initial stop stays at 1.2% below entry.
      * TP1 sells 50% at exactly 2R (2 x initial risk distance).
      * No pre-TP1 momentum/pullback liquidation.
      * No runner mid-touch liquidation.
      * No runner high-water trailing liquidation.
      * No RSI/MACD-only liquidation.
      * A candle wick below inner-lower is ignored; structural exit requires the
        1-minute close to finish below the inner-lower band.
      * After any exit, the normal V2.1 entry logic may re-enter on a later bar;
        there is no artificial cooldown in this policy.
    """

    initial_risk_pct: float = 0.012
    tp1_r_multiple: float = 2.0
    partial_fraction: float = 0.5
    inner_lower_confirm_bars: int = 1


class DoubleBollingerV22ExitPolicy:
    def __init__(self, config: DoubleBollingerV22Config = DoubleBollingerV22Config()):
        self.cfg = config

    def initial_stop(self, entry_price: float) -> float:
        return float(entry_price) * (1.0 - self.cfg.initial_risk_pct)

    def tp1_price(self, entry_price: float) -> float:
        risk = float(entry_price) * self.cfg.initial_risk_pct
        return float(entry_price) + self.cfg.tp1_r_multiple * risk

    @staticmethod
    def candle_closed_below_inner_lower(close: float, inner_lower: float) -> bool:
        return float(close) < float(inner_lower)
