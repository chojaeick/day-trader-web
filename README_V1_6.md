# DAY TRADER WEB v1.6 — Weight Study

This version does not change the live trading weights.

New research features
- Relative Strength feature:
  - stocks vs QQQ
  - semiconductor names vs SMH
  - inverse ETFs vs inverse benchmark
- Candidate weight sets:
  - GLOBAL_CURRENT
  - GLOBAL_CANDIDATE
  - BULL_CANDIDATE
  - BEAR_CANDIDATE
  - MIXED_CANDIDATE
  - REGIME_CANDIDATE
- Same 120-session historical sample used for every model.
- 80-session / 40-session walk-forward split.
- Reports Rank Corr, Precision@5, TOP5 excess return.
- Regime-specific current-vs-candidate comparison.

Candidate-weight philosophy from V1.5 baseline
- increase MA5 slope / Open>MA5 importance
- reduce ranking contribution of pure liquidity
- reduce gap-chasing contribution
- add Relative Strength
- increase long leveraged ETF bonus in BULL
- strengthen relative/market context in BEAR and MIXED

No automatic weight changes.
No automatic orders.
