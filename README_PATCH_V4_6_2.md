# V4.6.2 DISCOVERY BRIDGE SHADOW AUDIT

Changed:
- live_server/v4_engine.py
- live_server/api.py
- app.py

Purpose
Test whether Screener candidates missed by Discovery would have reached
Light20 / Finder5 if they had been allowed into the Finder evaluation path.

Safety
- NO live Finder mutation in Shadow mode.
- NO TOP5_IN / TOP5_OUT events in Shadow mode.
- NO ENTRY / Power / READY threshold change.
- NO order behavior change.
- NO Kiwoom TR/schema change.
- NO DB migration.

Implementation
- build_usa_finder gains:
  commit=True (default, production behavior unchanged)
  shadow_allow_unknown_quality=False (default)
- Shadow call uses commit=False.
- Screener eligible rows missing verified discovery quality are evaluated as:
  quality = SHADOW_UNKNOWN
  quality bonus = 0
- This is intentionally conservative.
- recent_bars is exposed so missing minute history is visible.

New API
GET /api/v4/discovery-bridge-shadow?market=USA

Shows
- current live Finder5
- bridge-shadow Finder5
- new shadow entrants
- displaced live names
- each Discovery Miss:
  Screener score / change
  Shadow Light rank / Finder rank / Finder score
  recent bars / Fresh / 1m/3m/5m/15m / volume accel
  whether it would reach Light/Finder

Apply
python3 apply_v4_6_2.py .
python3 -m py_compile live_server/v4_engine.py live_server/api.py app.py
