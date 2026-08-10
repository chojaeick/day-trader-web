# V4.3.7 FINDER V2 PIPELINE — PATCH

Changed files
- live_server/api.py
- live_server/analytics.py
- live_server/v4_engine.py

Observed live-session problems
- Finder was rebuilt only every 300 seconds, too slow for day-trading rotation.
- Large falling common stocks could rank above actionable long candidates.
- Actual rising leaders could remain outside TOP5.
- Inverse rotation was not consistently visible to the user.

Changes
1. Finder rotation cadence
- USA Finder rebuild: 300s -> 60s.
- Tracker still refreshes every 5s.

2. Long/manual-order actionable ranking
- Negative common stocks receive a bounded ranking penalty.
- Rising names with RVOL >= 1 receive a bounded live-leadership bonus.
- This does NOT remove DOWN names from context; it stops them crowding the actionable TOP5.

3. Finder V2 weighting
- 72% current live screener score
- 28% quality/liquidity/ATR base
- bounded negative-common-stock penalty
- stronger bounded SOXS/SQQQ bonus in BEAR/STRONG_BEAR

4. Finder metadata
- market_regime preserved
- preferred_direction = INVERSE in BEAR/STRONG_BEAR, otherwise LONG

Important
- TOP5 remains a watch list, not an automatic buy signal.
- No auto orders.
- Entry/Exit/Floor thresholds unchanged.
- No Kiwoom TR/schema changes.
- Universe discovery is unchanged in this patch; this patch fixes ranking/rotation first.

Post-deploy checks
- /api/v4/USA/finder should change at most ~1 minute after market leadership changes.
- market_regime should no longer be None when new finder data is built.
- Large negative common stocks should fall in actionable ranking.
- If SOXS/SQQQ qualify during a BEAR regime, one can surface in TOP5.
