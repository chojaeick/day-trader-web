# DAY TRADER WEB v1.7 — Daily Ranking Archive

New persistent archive
- Saves CURRENT TOP10 ranking snapshots into SQLite.
- Automatic labels:
  - T-10
  - T-1
  - T+7
  - T+30
  - T+60
  - CLOSE (15:59 New York time)
- Existing current ranking-history checkpoint remains intact.
- Manual archive save is available from the UI.

Stored for every ranked symbol
- rank
- symbol
- score
- bias
- price
- day %
- MA5
- MA5 slope
- RVOL
- ATR %
- dollar volume
- exchange
- remaining row fields as JSON

Archive UI
- list saved trading dates
- select a date
- see all snapshots for that date
- select snapshot label/model
- inspect exact saved ranking table

API
- /api/archive/dates
- /api/archive/snapshots?trade_date=YYYY-MM-DD
- /api/archive/ranking?trade_date=...&label=...&model=CURRENT
- /api/archive/recent
- /api/archive/save-now?label=MANUAL

This version intentionally keeps production scoring unchanged.
No automatic orders.

UI roadmap
- v1.7 starts the UI separation.
- Next interface pass can fully split:
  Trading / Research / Archive / Live Validation
  after archive behavior is verified in production.
