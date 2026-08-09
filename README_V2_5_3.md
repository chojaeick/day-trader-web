# DAY TRADER WEB V2.5.3 — KOREA RISK + NORMALIZATION

Adds:
- canonical Korean security code normalization
  - e.g. `005930_AL -> 005930`
  - preserves `raw_symbol`
- `KOREA_CURRENT_V1_GAMMA`
- `raw_score` vs final `score`
- CHASE_RISK:
  - NORMAL: abs(change) < 12%
  - MEDIUM: 12%+
  - HIGH: 20%+
  - EXTREME: 25%+
- extreme volume-surge risk flag
- explicit risk penalty
- EXTREME names are NOT silently excluded; they remain visible with penalty

Not yet enabled:
- ka10029 expected execution ranking.
  We intentionally do not guess its request body. It remains PREOPEN_NEXT until
  the official request schema is verified.

NO AUTO ORDER.
