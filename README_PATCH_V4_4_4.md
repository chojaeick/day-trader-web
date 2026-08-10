# V4.4.4 FRESH BREAKOUT DETECTOR — PATCH

Requires V4.4.3.

Changed file only:
- live_server/v4_engine.py

Goal
Catch a stock while it is starting to move NOW, instead of ranking mainly from
the full-day percentage move.

New Light Tracker features
- ret_1m
- ret_3m
- ret_5m
- ret_15m
- volume_accel
- break_3m_high
- fresh_score
- fresh_mover

Fresh Breakout concept
A strong fresh candidate needs:
- positive 1-minute impulse
- positive 3-minute acceleration
- close above the prior 3 one-minute highs
- recent volume acceleration
- sufficient combined fresh score

The thresholds are initial validation hypotheses, NOT probabilities.

Ranking behavior
- Fresh score is added separately from the existing 5m/15m leadership score.
- Therefore a stock up only +1~3% today can outrank an older +10% mover if the
  newer stock has just started accelerating.
- Equal-score ties prefer fresh movers.

Extreme movers
- C_HIGH_RISK still keeps EXTREME risk.
- Existing continuation test remains.
- A genuine fresh breakout can also make an extreme mover visible in Heavy5.
- Entry chase guard still blocks EXTREME from ENTRY.

Not changed
- Broad discovery
- Screener/quality bridge
- Entry thresholds
- Position/Exit/Floor
- Korea
- No automatic orders
- No new Kiwoom TR/schema
