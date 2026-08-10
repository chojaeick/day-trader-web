# V4.2 ENTRY TRIGGER V1 — PATCH

Changed files only:
- app.py
- live_server/v4_engine.py

USA Entry Trigger V1
- LONG entry only. No automatic order.
- Data Integrity Gate must pass.
- Regular session only.

5-minute Setup (3 of 4 required)
1. price > VWAP
2. EMA9 > EMA20
3. latest 5m close > previous 5m close
4. rising 5m structure / higher low

1-minute Trigger
- 1m green candle
- close breaks previous 1m high
- volume ratio >= 1.5x
- 1m impulse >= +0.15% (supporting check)
- Power >= 60 and ΔPower >= +4

State
- SETUP: 5m setup established, wait for 1m wave
- READY: setup + at least 3 trigger checks + Power >=55 + chase guard
- ENTRY: setup + green/breakout/volume/power acceleration + Power >=68 + chase guard
- WATCH: otherwise

Chase guard
- risk must be NORMAL
- RSI < 74
- price less than 2.5% above VWAP

Important
- Thresholds are baseline hypotheses, not probabilities.
- Historical/shadow validation should calibrate them.
- Korea logic unchanged; ENTRY remains blocked until verified 1m/5m bars are connected.
