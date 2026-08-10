# V4.3.5 FINDER REGIME + RECOVERY — PATCH

Changed files
- live_server/kiwoom.py
- live_server/analytics.py
- live_server/v4_engine.py

1) Live-bar recovery coverage
- V4.3.3 only refreshed the first 8 symbols in Settings.
- That could leave current Finder/Tracker names stale.
- V4.3.5 refreshes up to 30 symbols from the active USA universe each minute in REGULAR session.
- Existing REST load protection/cadence remains.

2) Market Regime routing
Uses QQQ + SMH current change as a simple routing proxy:
- STRONG_BEAR
- BEAR
- NEUTRAL
- BULL
- STRONG_BULL

This is NOT a buy probability. It controls which candidate direction should receive visibility.

3) Inverse rotation
During BEAR / STRONG_BEAR:
- explicitly evaluates SOXS / SQQQ
- requires positive own-day move, liquidity, and approximately holding MA5
- applies a bounded regime/alignment bonus
- reserves one Finder slot for the best qualified inverse candidate if it would otherwise be crowded out

4) Finder responsiveness
Old Finder recomputed its own score mostly from liquidity/RVOL/ATR and could ignore the
more current screener ranking.
New Finder score:
- 58% live screener score
- 42% quality/liquidity/ATR base
- optional bounded inverse bonus in bearish regimes

Goal
- Avoid a TOP5 made mostly of slow/stagnant names when stronger real-time movers exist.
- Allow the system to surface inverse ETFs in a bearish tape.
- TOP5 still means 'watch closely', NOT automatic buy.

Not changed
- Entry thresholds
- Exit/Floor thresholds
- Validation rules
- Korea logic
- No automatic orders
- No new Kiwoom TR/schema
