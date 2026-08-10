# V4.6.1 LIGHT -> FINDER CUTLINE + DISCOVERY MISS AUDIT

Changed:
- live_server/api.py
- app.py

No Finder formula change.
No Power/READY/ENTRY change.
No Kiwoom TR/schema change.
No DB migration.
No order behavior change.

Adds Light->Finder audit:
- current Finder 5th-place cut score
- each Light row score and gap to cut
- Fresh mode/score
- 1m/3m/5m/15m returns
- volume acceleration
- 3m breakout
- fade penalty
- extreme continuation status
- explicit diagnostic reasons

Adds Discovery Miss Audit:
- Screener rows absent from Discovery, Extreme, and Quality-Risk snapshots
- screener score/change/RVOL/ATR/dollar volume/penalties
- does NOT invent upstream Kiwoom ranking/TR reasons when data is unavailable

Apply:
python3 apply_v4_6_1.py .
python3 -m py_compile live_server/api.py app.py
