# V4.1.2 MINUTE DATE + EXCHANGE FIX — PATCH

Changed files only:
- live_server/kiwoom.py
- live_server/scanner.py

Fixes:
- usa06011 minute chart now requests current America/New_York date instead of ET date minus 7 days.
- If current date has no minute rows, falls back through prior weekdays up to 7 calendar days.
- AMEX normalization fixed to NA (NY/ND/NA).
- No auto order.

Verify after deploy:
1. restart backend
2. test GDX with minute_chart("GDX","NY",1)
3. LAST should be recent trading-day data, not the old 7-day-offset date
4. confirm backfill inserts new rows
5. confirm Data Integrity Gate only turns normal when quote/bar prices align
