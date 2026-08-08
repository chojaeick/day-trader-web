# DAY TRADER WEB v1.4.2

Discovery sources
- usa20530: volume / dollar-volume ranking
- usa20910: previous-day change-rate ranking (gainers + losers)
- usa20520: volume surge vs 5-day average

Pipeline
1. Merge and deduplicate ranking sources.
2. Price and liquidity screening.
3. Chase-risk penalty for extreme movers.
4. Target ~40 active names plus Core Watchlist.
5. Prime new candidates with quote, daily metrics and 80 one-minute bars.
6. Existing multi-timeframe TOP10 signal engine remains unchanged.

No auto-order functionality is included.
