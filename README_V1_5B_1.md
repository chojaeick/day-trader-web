# DAY TRADER WEB v1.5B.1 — Historical Pagination Fix

Fixes the historical daily-data range used by validation.

Kiwoom continuation
- Reads response headers: cont-yn / next-key
- Sends those headers on the next request
- Supports up to 10 pages per symbol
- Keeps 0.22s delay between continuation requests

Validation diagnostics
- requested sessions
- candidate sessions loaded
- validated sessions
- historical start/end date
- unknown-regime day count
- per-symbol page count / raw rows / usable daily bars

Time stability
- RECENT_20
- 21_40_DAYS_AGO
- 41_60_DAYS_AGO
- 61_80_DAYS_AGO
- 81_100_DAYS_AGO
- 101_120_DAYS_AGO

No automatic weight changes.
No automatic orders.
