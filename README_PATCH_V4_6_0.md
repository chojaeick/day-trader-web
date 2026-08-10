# V4.6.0 SCANNER / COVERAGE AUDIT

Changed:
- live_server/api.py
- app.py

No Finder score change.
No Power/READY/ENTRY threshold change.
No Kiwoom TR/schema change.
No DB migration.
No order behavior change.

New diagnostic API:
GET /api/v4/coverage-audit?market=USA

Audit shows:
- quote / Screener40 / Discovery / Light / Finder / Heavy5 counts
- extreme / quality-risk / reject counts
- discovery source counts
- strongest absolute movers and their current pipeline stage
- reason text for Discovery-only / Light-only / Finder / Heavy
- SOXS / SQQQ / SOXL / TQQQ pipeline position
- quote rows older than 180 seconds
- current Light/Finder/Heavy symbol sets

Purpose:
Answer “did the system see this mover, and if so where was it filtered out?”
without changing selection or entry logic.

Apply:
python3 apply_v4_6_0.py .
python3 -m py_compile live_server/api.py app.py
