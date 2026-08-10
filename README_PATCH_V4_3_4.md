# V4.3.4 US MINUTE TIMEZONE FIX — PATCH

Changed file only:
- live_server/kiwoom.py

Observed live-session evidence
- NOW / Tracker: ~14:42 UTC
- Current tracker price: ~135.26
- REST-recovered tick/bar price: ~135.27
- But recovered tick/bar timestamps were ~01:42 UTC
- Exact 13-hour offset caused Data Integrity to remain DATA_INVALID.

Root cause
- usa06011 minute-chart `bus_dt + cntr_tm` was being localized as Asia/Seoul.
- For the US chart data, the trading clock must be interpreted as America/New_York
  before conversion to UTC.

Fix
- Change minute_chart timestamp localization:
  Asia/Seoul -> America/New_York

Expected result during EDT
- 10:42 ET -> 14:42 UTC
- Periodic V4.3.3 live-bar recovery should insert fresh timestamps.
- Data Integrity should recover automatically when quote/bar freshness agrees.

Not changed
- Finder scoring
- Entry/Exit/Floor thresholds
- Validation logic
- WebSocket subscription logic
- Korea logic
- NO AUTO ORDER
