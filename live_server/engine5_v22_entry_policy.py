from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Engine5V22EntryPolicyConfig:
    """Frozen V22 entry-timing policy.

    Finalized rule:
      - keep the normal entry threshold unchanged
      - allow a one-minute-early entry when the causal live score has risen for
        three consecutive 1-minute steps into T-1
      - grant a fixed +5 merit points for that early-entry check
      - do not early-advance V_REBOUND/V_REBOUND_E
      - the most recent rise used for early entry must be < 20 points
      - if the signal is not already advanced, veto the normal T entry when the
        last 1-minute live-score jump is >= 15 points

    The normal-time veto never retroactively cancels an entry already made at
    T-1. This preserves causal ordering and avoids lookahead.
    """

    entry_threshold: float = 50.0
    required_consecutive_rises: int = 3
    early_entry_merit: float = 5.0
    early_last_step_max_exclusive: float = 20.0
    normal_entry_jump_veto: float = 15.0
    excluded_early_sources: tuple[str, ...] = ("V_REBOUND", "V_REBOUND_E")


@dataclass(frozen=True)
class Engine5V22EntryDecision:
    enter: bool
    timing: str
    effective_score: float
    reason: str
    last_step: float | None = None


class Engine5V22EntryPolicy:
    def __init__(self, config: Engine5V22EntryPolicyConfig):
        self.cfg = config

    @staticmethod
    def _scores(values: Iterable[float]) -> list[float]:
        return [float(v) for v in values]

    def early_entry_decision(self, source: str, scores_ending_t_minus_1: Sequence[float]) -> Engine5V22EntryDecision:
        """Evaluate the T-1 early-entry path using only information known at T-1.

        For V22, three consecutive rises require four score observations:
        [T-4, T-3, T-2, T-1].
        """
        source_name = str(source)
        if source_name in self.cfg.excluded_early_sources:
            return Engine5V22EntryDecision(False, "T-1", float("nan"), "EARLY_SOURCE_EXCLUDED")

        scores = self._scores(scores_ending_t_minus_1)
        need = self.cfg.required_consecutive_rises + 1
        if len(scores) != need:
            return Engine5V22EntryDecision(False, "T-1", float("nan"), "INSUFFICIENT_SCORE_PATH")

        deltas = [scores[i + 1] - scores[i] for i in range(len(scores) - 1)]
        if not all(d > 0.0 for d in deltas):
            return Engine5V22EntryDecision(False, "T-1", scores[-1], "NOT_3_CONSECUTIVE_RISES", deltas[-1])

        last_step = deltas[-1]
        if last_step >= self.cfg.early_last_step_max_exclusive:
            return Engine5V22EntryDecision(False, "T-1", scores[-1], "EARLY_LAST_STEP_SPIKE", last_step)

        effective = scores[-1] + self.cfg.early_entry_merit
        if effective < self.cfg.entry_threshold:
            return Engine5V22EntryDecision(False, "T-1", effective, "EARLY_EFFECTIVE_SCORE_BELOW_THRESHOLD", last_step)

        return Engine5V22EntryDecision(True, "T-1", effective, "R3_PLUS_5_EARLY_ENTRY", last_step)

    def normal_entry_decision(self, live_score_t_minus_1: float, live_score_t: float) -> Engine5V22EntryDecision:
        """Evaluate an entry that was not already advanced at T-1."""
        s1 = float(live_score_t_minus_1)
        s0 = float(live_score_t)
        jump = s0 - s1
        if jump >= self.cfg.normal_entry_jump_veto:
            return Engine5V22EntryDecision(False, "T", s0, "LAST_1M_JUMP_VETO_15", jump)
        if s0 < self.cfg.entry_threshold:
            return Engine5V22EntryDecision(False, "T", s0, "NORMAL_SCORE_BELOW_THRESHOLD", jump)
        return Engine5V22EntryDecision(True, "T", s0, "NORMAL_ENTRY", jump)
