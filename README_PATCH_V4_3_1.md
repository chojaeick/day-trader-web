# V4.3.1 VALIDATION SESSION GATE — PATCH

Changed file only:
- live_server/v4_engine.py

Fix:
- Validation performance marks are created ONLY for USA REGULAR session with valid Data Integrity.
- +5/+15/+30/+60m and MFE/MAE updates use the same gate.
- Tracker snapshots still continue outside regular session for reference/debugging.
- Existing contaminated REFERENCE_ONLY validation rows are removed automatically on backend startup.

No Entry/Exit/Floor thresholds changed.
No Kiwoom TR schema changed.
NO AUTO ORDER.
