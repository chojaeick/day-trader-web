from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class Action(str, Enum):
    HOLD = "HOLD"
    ENTER = "ENTER"
    PARTIAL_EXIT = "PARTIAL_EXIT"
    FULL_EXIT = "FULL_EXIT"


class PositionPhase(str, Enum):
    FLAT = "FLAT"
    OPEN = "OPEN"
    RUNNER = "RUNNER"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class SignalResult:
    symbol: str
    action: Action
    reason: str
    price: float
    score: float = 0.0
    stop: Optional[float] = None
    exit_fraction: float = 0.0
    diagnostics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PositionState:
    symbol: str
    phase: PositionPhase = PositionPhase.FLAT
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    qty_fraction: float = 0.0
    high_watermark: Optional[float] = None
    partial_exit_done: bool = False
    opened_at: Optional[str] = None

    def open(self, price: float, stop: float, opened_at: Optional[str] = None) -> None:
        if not (price > 0 and stop > 0 and stop < price):
            raise ValueError(f"invalid long invariant: stop={stop} entry={price}")
        self.phase = PositionPhase.OPEN
        self.entry_price = float(price)
        self.stop_price = float(stop)
        self.qty_fraction = 1.0
        self.high_watermark = float(price)
        self.partial_exit_done = False
        self.opened_at = opened_at

    def update_high(self, price: float) -> None:
        if self.high_watermark is None or price > self.high_watermark:
            self.high_watermark = float(price)

    def partial_exit(self, fraction: float = 0.5) -> None:
        fraction = max(0.0, min(1.0, float(fraction)))
        self.qty_fraction = max(0.0, self.qty_fraction - fraction)
        self.partial_exit_done = True
        if self.qty_fraction <= 0:
            self.close()
        else:
            self.phase = PositionPhase.RUNNER

    def close(self) -> None:
        self.phase = PositionPhase.CLOSED
        self.qty_fraction = 0.0


def enforce_long_stop(entry_price: float, candidate_stop: Optional[float], fallback_risk_pct: float) -> float:
    """Return a valid long stop. Never allows stop >= entry or stop <= 0."""
    entry = float(entry_price)
    if entry <= 0:
        raise ValueError("entry must be positive")
    if candidate_stop is not None:
        try:
            stop = float(candidate_stop)
            if 0 < stop < entry:
                return stop
        except Exception:
            pass
    risk = max(0.001, min(0.20, float(fallback_risk_pct)))
    return entry * (1.0 - risk)
