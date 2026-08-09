# DAY TRADER WEB V1.9 — Shadow Model

Purpose
- Run CURRENT production ranking and SHADOW experimental ranking side-by-side.
- Do not change production Trading Score.
- Do not place automatic orders.

SHADOW model
- Name: LIVE_CANDIDATE_V1
- Uses the same live feature inputs as CURRENT.
- Candidate-style weighting:
  - stronger MA5 trend / price action
  - reduced liquidity and raw momentum dominance
  - modest sector / ATR / RVOL weighting
- This is a live proxy model for forward testing, not a claim that the historical OPEN_V0 candidate model is identical.

New API
- GET /api/screener/shadow
- GET /api/screener/compare

Archive
- Scheduled T-10 / T-1 / T+7 / T+30 / T+60 / CLOSE saves both CURRENT and SHADOW.
- Manual save and MANUAL_SCAN also save both models.
- Existing archive model selector can compare the same date/label across CURRENT and SHADOW.

Trading UI
- CURRENT TOP10 remains the operational list.
- Compact expander shows:
  - TOP10 overlap
  - CURRENT-only names
  - SHADOW-only names
  - rank and score comparison

Safety
- CURRENT score logic unchanged.
- Signal / position logic unchanged.
- No automatic orders.
