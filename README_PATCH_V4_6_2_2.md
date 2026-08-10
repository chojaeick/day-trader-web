# V4.6.2.2 DATA CONSISTENCY FIX

Changed:
- live_server/api.py
- app.py

No Finder formula change.
No Power / READY / ENTRY threshold change.
No Kiwoom TR/schema change.
No DB migration.
No order behavior change.

Fix 1: Inverse / Leveraged ETF display consistency
- Discovery metadata still determines pipeline stage/reason.
- Latest DB quote wins for price/change_pct when Discovery has 0/None placeholders.
- Adds quote_age_sec.
- Prevents SOXS/SQQQ price=0 display when a valid warmed quote exists.

Fix 2: Stale severity
CRITICAL:
- current Finder
- current Heavy Tracker
- top 8 Screener-eligible Discovery misses used as Bridge warm targets
- SOXS/SQQQ/SOXL/TQQQ

INACTIVE_CACHE:
- old DB quotes outside the current decision universe

UI:
- only CRITICAL stale appears as front-line error
- INACTIVE_CACHE is moved into an expander
- Fair Bridge READY / INSUFFICIENT status is emphasized

Apply:
python3 apply_v4_6_2_2.py .
python3 -m py_compile live_server/api.py app.py
