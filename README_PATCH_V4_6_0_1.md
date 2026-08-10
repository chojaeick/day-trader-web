# V4.6.0.1 COVERAGE AUDIT CORRECTNESS FIX

Changed:
- live_server/scanner.py
- live_server/api.py
- app.py (footer only)

Fixes:
1. Discovery source corruption
   - a pre-normalized string such as "gainer,surge" was being joined again,
     producing character-level sources like a:10, g:10, i:9.
   - sources are now normalized type-safely.

2. EXTREME_WATCH origin loss
   - the final scanner normalization overwrote EXTREME_WATCH as AUTO.
   - EXTREME_WATCH provenance is now preserved.

3. Coverage stage semantics
   - extreme_rows were included in the mover table but stage() ignored them,
     so some extreme movers appeared as NOT_SEEN.
   - stages now include EXTREME / EXTREME_WATCH / QUALITY_RISK.

4. Inverse lookup order
   - prefers Heavy/Finder/Light current data, then Discovery/Extreme/Risk/Screener/Quote.

No Finder/Power/READY/ENTRY scoring change.
No Kiwoom TR/schema change.
No DB migration.
No order behavior change.

Apply:
python3 apply_v4_6_0_1.py .
python3 -m py_compile live_server/scanner.py live_server/api.py app.py
