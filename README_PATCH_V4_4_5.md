# V4.4.5 FRESH MOMENTUM V2 + VOLUME ACCEL FIX

Requires
- V4.4.4 Fresh Breakout Detector

Changed file only
- live_server/v4_engine.py

Why
Live diagnostics showed the Fresh detector was too strict:
- ACHR had ~3m +0.34% and ~5m +0.42%
- but Fresh=False because the last 1m bar was flat,
  break3=False, and the old volume acceleration ratio was too weak.

Changes

1. Volume acceleration V2
Old:
  recent 3m average volume / prior 10m average volume

New:
  recent 3m average volume / prior 10m MEDIAN volume

Why:
A single giant earlier volume bar can make the old denominator too large and
suppress a new burst. Median is more robust.

The ratio is bounded to 0..12 for ranking stability.

2. Fresh Momentum now has TWO paths

A) CONTINUATION
- 3m return >= +0.25%
- 5m return >= +0.30%
- volume acceleration >= 1.10x
- fresh score >= 15

This path does NOT require a 3-minute high breakout.

B) BREAKOUT
- 1m return >= +0.12%
- 3m return >= +0.18%
- break above prior 3 one-minute highs
- volume acceleration >= 1.15x
- fresh score >= 15

3. New diagnostics
Finder/Light rows now expose:
- fresh_mode = WATCH / CONTINUATION / BREAKOUT
- recent_vol_3m
- prior_vol_median_10m

4. Safety
- Fresh detection is ranking/observation logic only.
- Existing Entry Trigger remains separate.
- EXTREME risk still fails the existing chase guard for ENTRY.
- No automatic orders.
- No Kiwoom TR/schema changes.
- Korea unchanged.

Thresholds are validation hypotheses, not probabilities.
