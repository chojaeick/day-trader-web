# DAY TRADER WEB V2.6.1 — KOREA PREOPEN VALIDATION

Fixes the important trust issue found in V2.6.

Rules:
- ka10029 data is scored only during the real pre-open window: 08:20~08:59 KST on weekdays.
- Outside that window, ka10029 can still be queried for diagnostics but does NOT alter PREOPEN score or direction.
- TOP10 coverage is explicit:
  - PREOPEN_EXPECTED_LIVE: 10/10 covered
  - PREOPEN_EXPECTED_PARTIAL: at least half (and at least 3) covered
  - GAMMA_FALLBACK: lower coverage
- UI shows matched TOP10 count, coverage %, raw ka10029 count, and window-valid flag.
- Market LONG/SHORT is based on final trusted rows only.
- Existing 08:30 scheduler and archive remain.
- NO AUTO ORDER.
