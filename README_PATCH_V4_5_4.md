# V4.5.4 SHADOW CONFIDENCE RANKING + DEDUP

Changed file:
- app.py only

No live ENTRY rule change.
No backend change.
No DB migration.
No order behavior change.

Adds:
- Confidence diagnostic score using:
  sample size / 15m / 30m / 60m expectancy / MFE-MAE / Core pass
- Classification:
  추천 후보 / 관찰 / 표본부족
- Minimum recommendation requirements:
  60m complete >= 5
  15m > 0
  30m > 0
  60m > 0
  MAE > -0.50%
  Core pass >= 60%
- Exact-result deduplication:
  threshold profiles producing the same Episodes and outcomes are grouped.
- Delta-Power redundancy warning when D0/D2/D4 give exactly the same result.

Important:
Confidence is NOT a probability and is not used by live trading logic.

Apply:
python3 apply_v4_5_4.py app.py
python3 -m py_compile app.py
