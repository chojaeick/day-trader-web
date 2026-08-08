# DAY TRADER WEB v1.7.1 — Manual Market Rescan

Adds two user-triggered controls to the live trading screen.

1) Score Refresh
- Keeps the current Universe.
- Re-renders the latest TOP10 using currently available quote/daily data.

2) Market Rescan
- Forces a fresh market-wide discovery immediately.
- Rebuilds the active Universe.
- Detects added / removed symbols.
- Primes newly added symbols with quote, daily metrics, and up to 80 one-minute bars.
- Recalculates TOP10 after the scan.
- Uses a 45-second cooldown to prevent rapid repeated API calls.

Archive integration
- Every successful manual market rescan is stored automatically as:
  MANUAL_SCAN_HHMM
- Stores the resulting CURRENT TOP10 in Daily Ranking Archive.
- UI shows Universe before/after, new candidates, removed candidates, and new TOP10 entries.

API
- POST /api/scan/market
- GET  /api/scan/status

Safety
- Production score logic is unchanged.
- No automatic orders.
- Auto discovery on the normal schedule remains enabled.
