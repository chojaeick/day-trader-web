# V4.1.1 DATA INTEGRITY GATE — PATCH
Changed files only:
- app.py
- live_server/v4_engine.py

Core: BAD DATA => NO SIGNAL.

USA
- minimum bars: 1m >= 8, 5m >= 4
- regular-session freshness: 1m <=180 sec, 5m <=480 sec
- future timestamps rejected
- quote vs 1m close gap >2.5% => INVALID
- quote vs 5m close gap >4.0% => INVALID
- INVALID => DATA_INVALID, actionable Power 0, Floor/T1/T2 disabled
- raw Power retained for diagnostics
- outside regular session Floor/T1/T2 disabled

Korea ENTRY remains blocked until verified domestic 1m/5m bars exist.
NO AUTO ORDER.
