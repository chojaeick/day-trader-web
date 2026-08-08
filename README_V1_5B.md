# DAY TRADER WEB v1.5B — Regime Validation

This version keeps the current OPEN_V0 weights unchanged and tests stability.

Time windows
- RECENT_20
- PRIOR_20
- OLDER_20

Market regimes
- BULL
- BEAR
- MIXED

Semiconductor regimes
- SEMI_STRONG
- SEMI_WEAK
- SEMI_MIXED

Regime classification is ex-ante:
- only completed bars before the target trading day
- prior QQQ close vs prior 20-day average
- recent 5-day average vs preceding 5-day average
- same logic for SMH

Each slice reports:
- Rank Corr
- Precision@5
- TOP5 market-adjusted excess return
- sample size / validated days

Also includes regime-specific TRUE_POSITIVE vs FALSE_POSITIVE
score-component comparison.

No automatic weight changes.
No automatic orders.
