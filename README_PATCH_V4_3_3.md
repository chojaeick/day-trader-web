# V4.3.3 LIVE BAR RECOVERY — PATCH

Changed file only:
- live_server/kiwoom.py

Purpose
- Recover current USA 1m/5m bars during REGULAR session while F5 real-time messages remain unavailable.

Behavior
- Uses the existing verified minute_chart() -> backfill_symbol() path.
- During USA REGULAR session, refreshes up to 8 active symbols about once per minute.
- Outside REGULAR session, performs only a lightweight bootstrap backfill.
- Keeps existing WebSocket F5 path untouched; if F5 starts arriving later it can coexist.
- Data Integrity Gate remains unchanged and should recover automatically once bars are fresh.

Not changed
- Finder scoring
- Entry/Exit/Floor thresholds
- Validation rules
- Korea logic
- No automatic orders
- No new Kiwoom TR/type/schema

Post-deploy check
- journalctl should show: live minute recovery SYMBOL: bars=... inserted=...
- latest tick and /api/bars timestamps should approach current market time
- V4 should leave DATA_INVALID when freshness and price integrity pass
