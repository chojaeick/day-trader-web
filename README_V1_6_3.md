# DAY TRADER WEB v1.6.3 — Paired Evidence Study

Purpose
Compare models on exactly the same rolling out-of-sample folds.

Paired Fold Comparison
- baseline: GLOBAL_CURRENT
- candidates:
  - GLOBAL_CANDIDATE
  - BULL_CANDIDATE
  - BEAR_CANDIDATE
  - MIXED_CANDIDATE
  - REGIME_CANDIDATE
- reports wins / losses / ties
- win rate
- average and median TOP5 excess improvement
- worst and best relative fold
- average Rank Corr improvement
- average Precision@5 improvement
- deterministic bootstrap 95% confidence interval of mean TOP5 improvement

RS Paired Study
- baseline: RS_OFF
- compares RS_LOW / RS_MEDIUM / RS_HIGH on identical OOS folds

Evidence labels are research-only:
- STRONG
- PROMISING
- INCONCLUSIVE
- WEAK

No evidence label changes production weights automatically.
No automatic orders.

Next candidate step, only after evidence is good enough:
run GLOBAL_CANDIDATE + preferred RS strength as a live Shadow Model beside Current.
