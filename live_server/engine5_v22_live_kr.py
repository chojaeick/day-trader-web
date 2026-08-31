from __future__ import annotations

"""Live KR adapter for the frozen Engine5 V22 entry-timing policy.

This module is deliberately limited to the entry-timing layer that was actually
validated/frozen for V22. It does not invent a new source generator. Callers feed
causal live-score observations and the source name produced by the existing
Engine5 source pipeline.
"""

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque

from live_server.engine5_v22_kr import early_entry_decision, normal_entry_decision


@dataclass
class _SymbolState:
    scores: Deque[float]
    advanced_at_t_minus_1: bool = False


class KoreaV22LiveEntryGate:
    """Stateful causal V22 entry gate for live KR execution."""

    def __init__(self) -> None:
        self._state = defaultdict(lambda: _SymbolState(deque(maxlen=5)))

    def reset_symbol(self, symbol: str) -> None:
        self._state.pop(str(symbol), None)

    def push_score(self, symbol: str, source: str, live_score: float):
        """Push one completed/provisional 1-minute causal score.

        Returns a dict suitable for logging/execution decisions. Early-entry is
        considered once four score observations are available. If it fires, the
        following normal-T decision must not re-enter the same signal.
        """
        sym = str(symbol)
        st = self._state[sym]
        st.scores.append(float(live_score))
        if len(st.scores) < 4:
            return {"enter": False, "timing": "WAIT", "reason": "INSUFFICIENT_SCORE_PATH"}
        d = early_entry_decision(str(source), list(st.scores)[-4:])
        if d.enter:
            st.advanced_at_t_minus_1 = True
        return {
            "enter": bool(d.enter),
            "timing": d.timing,
            "effective_score": d.effective_score,
            "reason": d.reason,
            "last_step": d.last_step,
        }

    def normal_t(self, symbol: str, live_score_t_minus_1: float, live_score_t: float):
        sym = str(symbol)
        st = self._state[sym]
        if st.advanced_at_t_minus_1:
            st.advanced_at_t_minus_1 = False
            return {"enter": False, "timing": "T", "reason": "ALREADY_ADVANCED_T_MINUS_1"}
        d = normal_entry_decision(float(live_score_t_minus_1), float(live_score_t))
        return {
            "enter": bool(d.enter),
            "timing": d.timing,
            "effective_score": d.effective_score,
            "reason": d.reason,
            "last_step": d.last_step,
        }


KR_V22_LIVE_ENTRY_GATE = KoreaV22LiveEntryGate()
