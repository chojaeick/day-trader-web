# V4.3.6 MANUAL BUY / SELL — PATCH

Changed file only:
- app.py

Fix
- Manual trade entry no longer switches to sell-only when a position is open.
- BUY / ADD-BUY and SELL / PARTIAL-SELL are shown side-by-side.
- When a position is open:
  - additional buys remain available
  - partial/full sells remain available
- When no position is open:
  - buy remains available
  - sell inputs/buttons are disabled
- Sell quantity is still capped at current holdings.
- This does NOT add short-selling or automatic orders.

No backend trading logic changed.
