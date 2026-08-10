# V4.6.2.3 SESSION-AWARE FRESHNESS + WARM DIAGNOSTICS

Changed:
- live_server/api.py
- app.py

No Finder formula change.
No Power / READY / ENTRY threshold change.
No Kiwoom TR/schema change.
No DB migration.
No order behavior change.

Session-aware stale:
- REGULAR: weekday 09:30 <= ET < 16:00
  current decision-universe quotes >180s are CRITICAL
- Outside regular session:
  the same rows are REFERENCE, not a live-data failure
- Inactive cache remains separate

Bridge Warm Diagnostics:
- per symbol in-memory state
- last_attempt
- exchange
- quote_ok
- daily_ok
- minute_bars
- inserted
- status: PENDING / RUNNING / READY / PARTIAL / FAILED
- error
- current price / quote age / ready_now

The warm diagnostics are operational only and not persisted.
They do not affect Finder scoring.

Apply:
python3 apply_v4_6_2_3.py .
python3 -m py_compile live_server/api.py app.py
