# DAY TRADER WEB v1.5A.1

Historical validation fixes:
- usa06012 retries with official NY / ND / NA exchange codes
- transient request retry/backoff
- reports loaded/failed symbols and failure reasons
- reports average daily universe
- inverse ETF benchmark direction is corrected (SOXS vs -SMH, SQQQ vs -QQQ)
- Precision@5 added

Main-screen validation:
- saves TOP10 snapshots at T-10, T-1, T+7, T+30, T+60
- evaluates +30m, +60m and latest/close returns using stored ticks
- adds Live TOP10 Validation section

UI cleanup:
- CORE discovery placeholder rows display current quote/change when available
- Discovery Score and Trading Score are explicitly separated

No automatic weight changes. No automatic orders.
