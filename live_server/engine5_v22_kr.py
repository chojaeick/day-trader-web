from __future__ import annotations

"""Frozen Engine5 V22 policy for the Korean market.

This module is intentionally small: it freezes the market-specific entry policy
chosen for V22 while leaving the existing Engine5 source generation, structural
stops, TP1=2.0x band-R, partial exits, runner logic and execution plumbing in
place.

KR V22 entry timing:
- normal threshold: 50
- early entry: three consecutive positive 1-minute causal live-score rises into
  T-1, fixed +5 merit, effective score >= 50
- latest rise into T-1 must be < 20 points
- V_REBOUND is not advanced
- entries not already advanced are vetoed at normal T when T-1 -> T live-score
  jump is >= 15 points
- an entry already taken at T-1 is never cancelled using the future T score
"""

from live_server.engine5_v22_entry_policy import (
    Engine5V22EntryDecision,
    Engine5V22EntryPolicy,
    Engine5V22EntryPolicyConfig,
)

MARKET = "KR"
VERSION = "V22"

KR_V22_CONFIG = Engine5V22EntryPolicyConfig(
    entry_threshold=50.0,
    required_consecutive_rises=3,
    early_entry_merit=5.0,
    early_last_step_max_exclusive=20.0,
    normal_entry_jump_veto=15.0,
    excluded_early_sources=("V_REBOUND", "V_REBOUND_E"),
)

ENTRY_POLICY = Engine5V22EntryPolicy(KR_V22_CONFIG)


def early_entry_decision(source: str, scores_t4_to_t1) -> Engine5V22EntryDecision:
    return ENTRY_POLICY.early_entry_decision(source, scores_t4_to_t1)


def normal_entry_decision(live_score_t_minus_1: float, live_score_t: float) -> Engine5V22EntryDecision:
    return ENTRY_POLICY.normal_entry_decision(live_score_t_minus_1, live_score_t)
