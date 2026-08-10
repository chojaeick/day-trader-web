# V4.4.3 EXTREME PIPELINE BRIDGE — PATCH

Requires V4.4.2.

Changed files only:
- live_server/analytics.py
- live_server/v4_engine.py

Root cause:
Extreme movers were already discovered and present in the active universe, but
analytics._score_row() forced `eligible=False` whenever `extreme=True`.
Therefore they never reached V4 Light Tracker.

Fix:
- Keep regular eligibility unchanged.
- Add `extreme_watch_eligible` for positive, liquid, price>=5 extreme movers.
- Reserve a bounded slice of screener candidate capacity for them.
- Preserve high-risk/extreme identity.
- V4.4.2 Heavy5 continuation gate remains unchanged.
- Existing Entry chase guard still blocks EXTREME from ENTRY.

Goal:
SEE the major mover, track it, then decide whether it is still continuing.
Do not treat a +30%/+40% move by itself as a buy signal.
