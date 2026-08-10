# V4.6.2.1 CANDIDATE DATA WARM / FAIR BRIDGE AUDIT

Changed:
- live_server/v4_engine.py
- live_server/api.py
- app.py

No live Finder formula change.
No Power / READY / ENTRY threshold change.
No Kiwoom TR/schema change.
No DB migration.
No order behavior change.

Candidate data warm:
- async; never blocks live Finder/Tracker loop
- highest Screener-eligible names missing Discovery: top 8
- always audits SOXS / SQQQ / SOXL / TQQQ
- quote + daily metrics + minute backfill
- failed symbols are retried later
- warming does NOT add a symbol to live Finder

Fair Bridge Guard:
- SHADOW_UNKNOWN still gets quality bonus 0
- SHADOW_UNKNOWN needs recent_bars >= 6 to enter Shadow Finder
- data-poor names may remain visible in Shadow Light diagnostics
- API exposes price, recent_bars, data_ready, fair_status
- core ETF readiness exposes price + minute bar count + ready

Why:
V4.6.2 showed strong Discovery-miss candidates but some lacked enough minute data.
This patch makes Live-vs-Bridge comparison use a minimum common data condition.

Apply:
python3 apply_v4_6_2_1.py .
python3 -m py_compile live_server/v4_engine.py live_server/api.py app.py
