# V1.7.1 Scan Hotfix 2

Fixes manual market rescan HTTP 500.

Root cause:
- manual_discover_now() used `await self.discover_universe()` even though
  discover_universe() is synchronous and returns a dict.
- This raised TypeError after discovery ran.

Fix:
- run discovery with `await asyncio.to_thread(self.discover_universe)`
- prime new symbols using existing quote(), daily_metrics(), backfill_symbol()
- allow POST in CORS
- health includes `"hotfix":"scan-2"` for deployment verification
- keeps frontend API_URL POST fix

No scoring weights changed.
No automatic orders added.
