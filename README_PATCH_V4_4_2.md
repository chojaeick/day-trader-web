# V4.4.2 EXTREME MOVER VISIBILITY — PATCH

Requires
- V4.4.0 Broad Market Finder
- V4.4.1 Light Tracker 20 + Live Rotation

Changed files only
- live_server/scanner.py
- live_server/v4_engine.py

Observed live-session root cause
- Broad scan DID find major movers such as HZO / MarineMax at roughly +45%.
- They were classified C_HIGH_RISK / EXTREME_MOVE.
- Those rows lived only in `extreme_rows`, so they were not part of the active universe
  and could not reach Light Tracker / Heavy Tracker.
- This made the app look as if it had failed to discover them.

Changes

1) Extreme Watch universe
- Up to 10 verified extreme movers are added to the active USA universe.
- Their risk labels are preserved:
  quality_grade = C_HIGH_RISK
  chase_risk = EXTREME
- They are not reclassified as normal candidates.

2) Light Tracker visibility
- C_HIGH_RISK names may now enter Light Tracker 20.
- This allows the app to show that a major mover exists instead of silently hiding it.

3) Heavy Tracker qualification
A C_HIGH_RISK mover may enter Heavy TOP5 only if:
- at least 6 one-minute bars are available
- recent ~5m return >= +0.25%
- recent volume acceleration >= 1.10x
- fade penalty <= 3

These are initial hypotheses for Validation, not probabilities.

4) Entry safety
- Finder risk remains EXTREME.
- Existing Entry chase guard requires NORMAL risk, so an EXTREME mover is NOT promoted
  to automatic ENTRY logic.
- The app is still manual-order only.

Goal
Find the mover first, then distinguish:
- EXTREME WATCH-ONLY: big daily move but current momentum not confirmed
- EXTREME CONTINUING: still moving now, eligible for Heavy visibility
Neither state is an automatic buy.

No changes
- No new Kiwoom TR/schema
- Exit/Floor thresholds unchanged
- Korea unchanged
- No auto orders
