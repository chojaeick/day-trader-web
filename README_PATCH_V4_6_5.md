# V4.6.5 KR SESSION SNAPSHOT AUDIT

Changed:
- live_server/v4_engine.py
- live_server/api.py
- app.py

Purpose:
Use existing KOREA Heavy Tracker minute snapshots as observational validation.

Important:
- Does NOT add or invent a Korea minute-chart TR.
- Does NOT enable KR ENTRY.
- Does NOT create KR Setup/Trigger counts.
- Does NOT change Finder/Power scoring.
- No order behavior change.

Live from next regular session:
- one KR validation mark per tracked symbol per minute
- +5/+15/+30/+60 outcome settlement from subsequent tracker prices
- MFE / MAE
- Power bucket and symbol-level report

Retroactive:
POST /api/v4/korea/session-audit/backfill?date=YYYY-MM-DD
converts existing v4_tracker_snapshots into KR validation marks and calculates
future returns from later snapshots of the same symbol.

Report:
GET /api/v4/korea/session-audit?date=YYYY-MM-DD

Recommended first backfill:
2026-08-11

Apply:
python3 apply_v4_6_5.py .
python3 -m py_compile live_server/v4_engine.py live_server/api.py app.py
