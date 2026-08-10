# V4.5.5 Multi-session Shadow Stability

Changed at deploy time:
- live_server/v4_engine.py
- app.py

No live ENTRY threshold change. No DB migration. No order behavior change.

Adds per-market-date Shadow session stats, sample-shrunk Confidence, stability labels (1일 우수 / 반복 우수 / 불안정 / 관찰 / 표본부족), and ignores Delta-Power duplicate warnings below 3 Episodes.

Confidence shrinkage by completed 60m samples:
- 0 => 25%
- 1-2 => 50%
- 3-4 => 75%
- 5+ => 100%

A real recommendation candidate additionally requires multi-session `반복 우수`. One strong day cannot promote a live threshold.

Apply:
```bash
python3 apply_v4_5_5.py .
python3 -m py_compile live_server/v4_engine.py app.py
```
