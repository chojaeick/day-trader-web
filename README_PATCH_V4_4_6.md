# V4.4.6 FRESH SCORE CALIBRATION

Requires V4.4.5.

Changed file only:
- live_server/v4_engine.py

Live diagnosis
- No Light20 name was actually fresh=True.
- PATH had freshScore=18 but fresh=False, yet the full +18 was added to Finder score.
- PATH/HZO/ONDS showed volx=12, indicating a near-empty/sparse prior-volume denominator.

Fixes

1. Fresh bonus calibration
- fresh_mover=True  -> full positive freshScore is added.
- fresh_mover=False -> only 20% is added, capped at +4.
This prevents WATCH-only names from dominating Finder.

2. Robust volume acceleration
- Prior 10m uses only positive-volume bars.
- Baseline = max(median_positive, 40% of positive mean, 1).
- If fewer than 4 positive prior bars exist, volx defaults to 1.0.
- If prior-volume coverage <50%, volx is capped at 1.25.
- Final volx hard cap reduced from 12 to 6.

3. Fresh data-quality gate
Both CONTINUATION and BREAKOUT require:
- prior 10m positive-volume coverage >= 50%.

4. New diagnostic
- volume_coverage_10m

No changes to Entry/Exit/Floor.
No auto orders.
No new Kiwoom TR/schema.
Thresholds remain validation hypotheses.
