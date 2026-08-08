# DAY TRADER WEB v1.4.3

Purpose: keep broad discovery while improving tradeable-candidate quality.

Changes
- AUTO quality gate:
  - pass with >= $5M reported dollar volume, or
  - >= 1M shares when supported by volume/event ranking.
- ±30% movers are separated into an EXTREME pool instead of regular Universe.
- Adds STOCK / ETF / LEVERAGED_ETF classification to discovery UI.
- Final TOP10 scoring gives more weight to dollar liquidity.
- Final TOP10 excludes ±30% extreme movers.
- Core watchlist remains always available in the focus selector.
- Existing V1.3.2 timestamp fix and V1.4.x dynamic discovery/WebSocket refresh are preserved.
- No auto-order functionality.
