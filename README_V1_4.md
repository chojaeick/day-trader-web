# DAY TRADER WEB v1.4

- Kiwoom official `usa20530` ranking API used for market-wide discovery.
- Combines volume-top and dollar-volume-top lists.
- Applies minimum price and liquidity filters before expensive analysis.
- Keeps SOXL/SOXS/TQQQ/SQQQ/QQQ/SPY/SMH as Core Watchlist.
- Refreshes the discovered universe every 10 minutes.
- New candidates receive quote + daily metrics + 80-bar minute backfill.
- WebSocket subscription refreshes when the universe changes.
- `/api/universe` exposes current discovery status.
- No auto-order functionality is added.
