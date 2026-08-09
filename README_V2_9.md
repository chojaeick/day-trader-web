# DAY TRADER WEB V2.9 — FINAL_RECOMMENDATION_V1

Purpose
Candidate score is NOT a recommendation. This layer asks whether a position is actually actionable now.

USA V1
- Final candidates are evaluated from Candidate TOP10.
- Quality A is preferred; B_EVENT is only allowed for ETF-type exceptions.
- BUY_NOW/WATCH requires LONG bias, SETUP/TRIGGER, 5m confirmation, price>VWAP, EMA9>EMA20, RVOL>=1.
- chase / ATR / overbought risk reduces the final score.
- SHORT candidates are not actionable recommendations in V1.
- bearish exposure should use approved inverse ETFs rather than treating ordinary SHORT-ranked stocks as buy recommendations.
- 0 recommendations is valid.

KOREA V1
- Quality + GAMMA + live execution strength + VI are evaluated.
- BUY_NOW is deliberately DISABLED until a verified domestic minute-chart adapter provides 1m/5m VWAP/EMA confirmation.
- During live market hours, strong A-grade LONG + execution-strength names may become WATCH.
- Outside live market hours, NO TRADE / WAIT is normal.

UI
- Final Recommendation 1~5 appears before Candidate TOP10.
- Candidate TOP10 remains research priority only.
- detailed final-engine blocks are collapsed.

Next
- Connect verified KOREA minute charts.
- persist recommendation snapshots and validate 5/15/30/60-minute and close outcomes.
- then add News AI as a separately measurable incremental factor.
