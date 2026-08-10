# V4.4.0 BROAD MARKET FINDER — PATCH

Changed files
- live_server/scanner.py
- live_server/kiwoom.py
- live_server/v4_engine.py
- live_server/api.py

Why this patch
The live session showed that Finder could improve a named/core watchlist but still miss broader
market leaders. The root structural problems were:
1. CORE symbols were inserted at the FRONT of discovery, biasing downstream first-N processing.
2. Broad discovery refreshed only every 10 minutes by default.
3. Finder emphasized daily move/RVOL but did not explicitly reward the last 5m/15m move.
4. PLTR had been hard-coded after user discussion, which made the system less market-neutral.

What changes

A) MARKET-FIRST universe
- Auto-discovered leaders remain at the front.
- Structural core ETFs remain guaranteed but are APPENDED, not forced to the top.
- Removes the PLTR hard-code. PLTR must now earn its place through market discovery like any stock.

B) BROADER discovery
- Auto discovery target: at least 80 names.
- Sources remain verified existing Kiwoom ranking APIs:
  volume, dollar-volume, gainers, losers, 5-minute volume surge.
- No new Kiwoom TR/schema is invented.

C) FASTER broad refresh
- During USA REGULAR session, broad discovery refreshes at most every ~120 seconds.
- Outside regular session, existing configured cadence remains.

D) 5m / 15m leadership
Finder now reads actual stored 1-minute bars for candidates and computes:
- return over ~5 minutes
- return over ~15 minutes
- recent volume acceleration

A fresh mover gets a bounded leadership bonus.
A stock that rose earlier but is now fading gets a bounded penalty.
These values are also included in Finder rows:
- ret_5m
- ret_15m
- volume_accel
- recent_score

E) Candidate depth
- Finder scores top 40 screener names instead of 30.

Safety / unchanged
- TOP5 is still a watch list, NOT a buy order.
- Entry Trigger remains separate.
- Exit/Floor logic unchanged.
- No automatic orders.
- Korea logic unchanged.
- Existing quality/liquidity gates remain.

Expected live behavior
- More newly moving market-wide stocks should rotate into consideration.
- User-mentioned stocks should no longer appear simply because they were mentioned.
- Strong 5-minute movers can outrank stale daily winners.
- Core inverse ETFs remain available without crowding out broad-market leaders.
