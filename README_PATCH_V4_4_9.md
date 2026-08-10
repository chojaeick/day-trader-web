# V4.4.9 VALIDATION / DAILY REPORT

SAFE UI PATCH
- app.py is patched in-place.
- No backend scoring logic changes.
- No DB schema changes.

Uses existing /api/v4/validation/marks data:
- +5 / +15 / +30 / +60 minute return
- MFE / MAE
- state
- Power / Delta Power
- Finder rank
- Setup / Trigger counts
- RVOL / volume ratio
- Floor mode

Adds to Validation tab
- session date selector
- horizon performance summary
- per-symbol aggregated report
- best / weak tracked symbols
- state-by-state performance
- Power bucket performance
- simple daily engine assessment
- raw validation table expander

Important
Validation rows are minute snapshots, NOT independent trades.
The report explicitly warns against treating snapshot count as trade count.

Apply:
python3 apply_v4_4_9.py app.py
python3 -m py_compile app.py

The script aborts without modifying app.py if the Validation Lab block cannot be found.
