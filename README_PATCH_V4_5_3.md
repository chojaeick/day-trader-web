# V4.5.3 ENTRY THRESHOLD SHADOW TEST

Changed:
- live_server/v4_engine.py
- live_server/api.py
- app.py

NO live ENTRY rule change.
NO order behavior change.
NO DB migration.

Purpose
Compare hypothetical ENTRY thresholds without changing the production signal.

Grid
- Power >= 55 / 60 / 65 / 68
- Trigger >= 3 / 4 / 5
- Delta Power >= 0 / 2 / 4

Method
- Only marks with 5m setup_ok and chase_ok are eligible.
- Trigger is recomputed for each profile:
  green 1m
  + break previous high
  + volume expansion
  + 1m impulse
  + that profile's dynamic Power acceleration.
- FIRST qualifying mark per Episode only.
- This avoids minute-snapshot duplication.
- CURRENT_READY and CURRENT_CORE are also reported from the stored live gate.
- Core pass % shows green+breakout+volume simultaneously at the shadow anchor.

New API
GET /api/v4/validation/entry-shadow?market=USA&limit=5000&bridge_minutes=5

UI
Validation -> Stage Anchor -> Entry Threshold Shadow Test

Apply
python3 apply_v4_5_3.py .
python3 -m py_compile live_server/v4_engine.py live_server/api.py app.py
