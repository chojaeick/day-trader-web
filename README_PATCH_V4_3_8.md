# V4.3.8 TRACKER WARM-UP + CORE WATCH — PATCH

Changed files
- live_server/api.py
- live_server/kiwoom.py

Why
1. Finder now rotates every ~60s, but a newly selected TOP5 symbol could enter Tracker
   before its 1m/5m bars were warmed, causing temporary DATA_INVALID rows.
2. PLTR could disappear from discovery even on a strong day because it was not guaranteed
   in the always-evaluate core set.
3. SOXS/SQQQ are already configured core instruments; this patch keeps that behavior.

Changes
A. Dynamic Tracker warm-up
- Whenever USA Finder TOP5 changes, newly entering symbols are immediately primed:
  quote -> daily_metrics -> minute backfill.
- Tracker refresh happens after that warm-up.
- Only newly entering names are primed, capped to 5.
- Warmed-symbol cache is bounded to current Finder names + open positions.

B. Core Watch
- Adds PLTR to the always-evaluate discovery core at runtime.
- Existing configured core remains intact, including:
  SOXL, SOXS, TQQQ, SQQQ, QQQ, SPY, SMH.
- This does not guarantee PLTR/SOXS a TOP5 rank; it guarantees they are actually evaluated.

Not changed
- Finder V2 scoring/weights from V4.3.7
- Entry/Exit/Floor thresholds
- Market-regime thresholds
- Korea logic
- No automatic orders
- No new Kiwoom TR/schema

Post-deploy expectations
- New Finder names should show fewer transient DATA_INVALID rows.
- PLTR should no longer disappear from the evaluation universe.
- SOXS/SQQQ remain always evaluated.
- Whether PLTR/SOXS reach TOP5 should now be determined by live score/regime, not absence.
