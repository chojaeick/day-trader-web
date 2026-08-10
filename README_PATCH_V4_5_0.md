# V4.5.0 EPISODE VALIDATION

Safe in-place patch. Modifies:
- live_server/v4_engine.py
- live_server/api.py
- app.py

Purpose:
Raw Validation marks are minute snapshots, not independent trades.
This patch derives signal Episodes from existing marks without a DB migration.

Episode:
- Active states: SETUP, READY, ENTRY, HOLD, PARTIAL_EXIT, EXIT_READY, HARD_EXIT
- Brief inactive/WATCH flickers are bridged up to 5 minutes.
- Longer breaks create a new Episode.
- Existing historical marks are usable immediately.

Adds API:
GET /api/v4/validation/episodes?market=USA&limit=5000&bridge_minutes=5

Adds UI:
- Episode count / 60m complete
- READY+ / ENTRY reached
- max-state performance
- start-Power performance
- episode list

No scoring, Fresh, Heavy5, Entry, Exit, Floor, order, or Kiwoom changes.

Apply:
python3 apply_v4_5_0.py .
python3 -m py_compile live_server/v4_engine.py live_server/api.py app.py
