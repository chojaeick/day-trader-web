from __future__ import annotations

from typing import Iterable, Optional

from live_server.strategy_core_v1 import Action, SignalResult


ALLOWED = {"SOXL", "SOXS"}


def choose_single_entry(results: Iterable[SignalResult]) -> Optional[SignalResult]:
    """Choose at most one SOXL/SOXS entry. No finder, no ranking side effects.

    Highest engine score wins. Exact ties are rejected instead of guessing direction.
    """
    entries = [r for r in results if r.symbol in ALLOWED and r.action == Action.ENTER]
    if not entries:
        return None
    entries.sort(key=lambda r: float(r.score), reverse=True)
    if len(entries) > 1 and float(entries[0].score) == float(entries[1].score):
        return None
    return entries[0]
