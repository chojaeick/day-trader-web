# V4.0 CLEAN ENGINE ALPHA — PATCH ZIP

Included files only:
- app.py
- live_server/api.py
- live_server/v4_engine.py

Core reset:
- Finder selects up to 5 candidates.
- Heavy Tracker is capped at 5 total symbols.
- Open positions have slot priority, remaining slots use Finder rank.
- TOP5 does not equal BUY.
- Power = -100..+100 from structure / volume / momentum / market-sector / risk penalty.
- State machine: WATCH -> SETUP -> READY -> ENTRY; position states HOLD / TAKE_PROFIT / REDUCE / EXIT / STOP.
- Manual buy/sell quantity and price are stored in SQLite.
- Full exit automatically releases the priority slot.
- USA has adaptive Warning Floor / Hard Floor / T1 / T2.
- Korea does not emit ENTRY until verified domestic 1m/5m bars are connected.
- Feature snapshots are stored once per minute for later Historical/Shadow calibration.
- Existing historical DB is preserved; no DB or historical data is included in this ZIP.
- NO AUTO ORDER.

The current weights are baseline hypotheses, not learned probabilities.
