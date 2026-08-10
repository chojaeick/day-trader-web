# V4.4.7 SCORE EXPLAINABILITY + LIGHT20 UI

This is a SAFE SOURCE PATCH, not a full app.py replacement.

Why patcher format
- The current Streamlit app contains newer manual buy/sell and chart work.
- This patch changes only the Finder UI block and avoids overwriting unrelated working UI.

Changes
- Market regime
- Preferred direction
- Light Tracker count
- Finder rotation seconds
- TOP5 adds 1m/3m/5m, Fresh mode, Fresh score, volume acceleration,
  observed Power and fade penalty
- Expandable Light Tracker table with:
  1m/3m/5m/15m momentum, volume coverage, breakout, Fresh, Fade,
  quality/risk and finder_reason
- Fresh/Fade/Extreme counts

NO scoring changes.
NO Entry/Exit/Floor changes.
NO auto orders.
NO Kiwoom changes.

Apply
python apply_v4_4_7.py app.py

The script aborts without modifying app.py if the expected current Finder block is not found.
