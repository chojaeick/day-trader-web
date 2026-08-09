# V1.7.1 Scan Hotfix 3

Confirmed traceback:
AttributeError: 'KiwoomClient' object has no attribute 'active_symbols'

Fix:
- adds KiwoomClient.active_symbols(), returning the current dynamic universe (`self.s.symbols`)
- preserves scan-2 fix: discover_universe runs in asyncio.to_thread
- preserves correct new-symbol priming with quote/daily_metrics/backfill_symbol
- preserves frontend API_URL POST fix
- health marker: `"hotfix":"scan-3"`

No scoring weights changed.
No automatic orders added.
