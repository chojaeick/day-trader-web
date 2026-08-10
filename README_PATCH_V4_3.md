# V4.3 EXIT + VALIDATION — PATCH

Changed files only:
- app.py
- live_server/v4_engine.py
- live_server/api.py

Adds:
- Position states: HOLD / PARTIAL_EXIT / EXIT_READY / HARD_EXIT.
- Adaptive Floor lifecycle: INITIAL -> PROTECT (~0.8R) -> TRAILING (~1.5R).
- Floor never loosens while a manual position remains open.
- Position risk fields persist in SQLite with safe schema migration.
- One validation mark per tracked USA symbol per minute.
- Forward returns: +5m / +15m / +30m / +60m, plus MFE / MAE.
- New endpoint: /api/v4/validation/marks.
- Validation UI shows sample count, 60m completed count, mean 60m return, positive-return ratio.
- EMA9/EMA20 are thinner and dashed; Price/VWAP remain solid.
- Thresholds are hypotheses for Historical/Shadow calibration, not probabilities.
- Korea minute-bar ENTRY/Exit logic remains gated.
- NO AUTO ORDER.
