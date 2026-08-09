# DAY TRADER WEB V2.2.4 — UI / EVIDENCE POLISH

Fixes:
- Evidence Audit now reads enriched saved PREOPEN report rows rather than raw screener rows.
- TOP5 audit shows per-symbol News AI status and elapsed seconds.
- Per-symbol News AI errors are persisted into report rows.
- Manual briefing completion shows total elapsed time.
- Retry button appears when one or more TOP5 News AI symbols failed.
- V2.2.3 resilient per-symbol analysis and async background jobs are preserved.

Notes:
- retry-failed currently starts a fresh manual briefing job after detecting failed symbols.
  This preserves data integrity and avoids mutating a completed archived report in-place.
- CURRENT Trading Score / news weighting / NO AUTO ORDER remain unchanged.
