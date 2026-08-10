# V4.3.2 US WEBSOCKET REGISTRATION FIX — PATCH

Changed file only
- live_server/kiwoom.py

Why
- Running server receives successful REG acknowledgements but raw_ws has F5 TOTAL = 0.
- Kiwoom's official USA WebSocket documentation defines each REG item as a map containing
  symbol + exchange, e.g. {"jmcode":"NVDA","stex_tp":"ND"}.
- The current code was sending only a list of symbol strings.

Fix
- Build each USA WebSocket subscription item as:
  {"jmcode":"<SYMBOL>","stex_tp":"ND|NY|NA"}
- Use the already-discovered exchange via active_exchange().
- Preserve AM -> NA AMEX normalization.
- Skip invalid/unknown exchange codes instead of inventing one.
- Apply the same format on the initial REG and every dynamic universe refresh.
- Keep F5 as the subscribed real-time type.
- Store the initial REG acknowledgement in raw_ws for easier diagnostics.
- Log subscriptions as SYMBOL/EXCHANGE.

Not changed
- No Entry/Exit/Floor thresholds.
- No Finder scoring.
- No automatic orders.
- No invented Kiwoom TR/type code.
- Backfill behavior is unchanged.

Post-deploy validation
1. raw_ws should begin receiving trnm=REAL messages.
2. F5 TOTAL should become > 0 during regular trading.
3. ticks latest timestamp should approach current UTC.
4. /api/bars/<symbol> latest 1m bar should approach current market time.
5. V4 Data Integrity should recover from DATA_INVALID once price/bar freshness matches.
