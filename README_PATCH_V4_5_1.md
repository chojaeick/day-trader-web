# V4.5.1 STAGE-ANCHOR VALIDATION

Safe in-place patch. Modifies:
- live_server/v4_engine.py
- live_server/api.py
- app.py

Purpose
V4.5.0 Episode start Power is not necessarily the Power at READY or ENTRY.
V4.5.1 evaluates the first SETUP, first READY, and first ENTRY mark separately.

No DB migration.
Historical V4.5.0 validation marks are usable immediately.

New API
GET /api/v4/validation/stage-anchors?market=USA&limit=5000&bridge_minutes=5

Stage Anchor fields
- episode_id / symbol / stage / stage_ts
- minutes from episode start
- anchor price
- Power / Delta Power
- Finder rank
- Setup / Trigger count
- +5/+15/+30/+60 return
- MFE / MAE

Validation UI adds
- SETUP vs READY vs ENTRY performance
- READY moment Power buckets
- ENTRY anchor individual outcomes
- full Stage Anchor list

Not changed
- Finder/Fresh/Power formulas
- Entry threshold
- Heavy5
- Exit/Floor
- auto orders
- Kiwoom schema

Apply
python3 apply_v4_5_1.py .
python3 -m py_compile live_server/v4_engine.py live_server/api.py app.py
