# V4.4.8 FINDER SCORE CALIBRATION

Requires
- V4.4.6 engine
- V4.4.7 UI is compatible (no app.py change required)

Changed file only
- live_server/v4_engine.py

Live reason for patch
The Finder could saturate at 100 because:
  0.65*Live + 0.25*Base + Recent(up to 40) + Fresh(up to 40)
allowed multiple momentum terms to stack.

Calibration
- Live component: 0.58 * live_score
- Base component: 0.22 * base
- Recent component: 0.72 * recent_score
- True Fresh bonus: min(18, 0.65 * raw_fresh)
- WATCH Fresh bonus: min(3, 0.15 * raw_fresh)

High-end soft compression
- raw <= 85 : unchanged
- raw > 85  : 85 + (raw-85)*0.35
- displayed Finder score capped at 96

Why soft compression
- It is monotonic, so it does not randomly reshuffle strong names.
- It avoids misleading 100-point saturation.
- It restores useful separation among high-ranked names.

New diagnostics
- finder_raw_score
- score_components:
  live / base / recent / fresh / fade / down / inverse

NOT changed
- Broad Discovery
- Light20 / Heavy5 architecture
- Fresh detection thresholds
- Entry Trigger / Position / Exit / Floor
- inverse reserve logic
- automatic orders (still none)
- Korea
- Kiwoom TR/schema

These weights remain validation hypotheses and should be adjusted from
+5/+15/+30/+60 minute outcome data, not treated as probabilities.
