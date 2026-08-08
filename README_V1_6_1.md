# DAY TRADER WEB v1.6.1 — Robust Validation

Adds validation depth without changing live trading weights.

1. Rolling walk-forward
- 40 completed sessions as context/train window
- next 20 sessions as out-of-sample test
- roll forward by 20 sessions
- reports average OOS Rank Corr / Precision@5 / TOP5 excess
- reports percent of folds with positive TOP5 excess

2. Regime-specific OOS
- BULL / BEAR / MIXED
- compares GLOBAL_CURRENT, GLOBAL_CANDIDATE and regime candidate
- evaluates only regime days inside each chronological OOS fold

3. Relative Strength sensitivity
- RS_OFF
- RS_LOW
- RS_MEDIUM
- RS_HIGH
- all other GLOBAL_CANDIDATE weights held constant
- separates the contribution of RS from other weight changes

No automatic weight changes.
No automatic orders.

UI tab refactor is intentionally deferred to a later version so model validation
and interface restructuring do not change at the same time.
