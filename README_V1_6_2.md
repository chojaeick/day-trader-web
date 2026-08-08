# DAY TRADER WEB v1.6.2 — Robustness Study

- Validation UI supports 20 / 40 / 60 / 90 / 120 / 180 / 240 / 250 sessions.
- API accepts up to 260 sessions.
- Rolling walk-forward stays 40 -> 20, step 20.
- Adds worst fold, standard deviation, median, positive-fold rate.
- Adds the same robustness fields to Regime OOS.
- Adds a research-only Robustness Ranking that penalizes dispersion and bad worst folds.
- Time-window stability expands in 20-session blocks up to 260 sessions.

The score is research-only and never changes production weights automatically.
No automatic orders.

UI tab refactor remains planned for the next interface-focused version.
