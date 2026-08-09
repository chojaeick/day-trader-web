# V4.1 POWER ENGINE V1 — PATCH
Changed files only:
- app.py
- live_server/v4_engine.py

- Finder rank != real-time Tracker rank.
- Open positions keep slot priority.
- Remaining slots are ordered by State, |Power|, |ΔPower|, then Finder rank.
- USA Power V1: 5m structure + VWAP/EMA + higher-low/lower-high + 1m participation + weighted 1/3/5m momentum + QQQ/SPY/SMH alignment + chase penalty.
- Alerts only for state changes, Power jump >=12, or rank movement >=2.
- Closed-market data is labeled as reference, not live.
- Korea ENTRY remains blocked until verified domestic 1m/5m bars are connected.
- Weights are baseline hypotheses for Historical/Shadow validation.
- NO AUTO ORDER.
