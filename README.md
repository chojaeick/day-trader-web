# DAY TRADER WEB + LIVE SERVER v1.3.1

V1.3.1 is a reliability/visualization update to the live signal system.

## Added / fixed

- Kiwoom official US minute chart (`usa06011`, `/api/us/chart`) startup backfill
- Recent 1-minute bars are seeded after service restart so indicators do not need ~20 live minutes to warm up
- REST quote snapshots are no longer written as fake intraday ticks while markets are closed
- Warmup state renamed from `WARNING` to `DATA WARMUP`
- 1-minute and 5-minute charts use dynamic non-zero price scaling
- Close + EMA9 + EMA20 + VWAP overlays on charts
- SMH exchange mapping corrected to NASDAQ (`ND`)
- ORCL exchange mapping corrected to NYSE (`NY`)
- API/health version: `1.3.1`

## Architecture

Kiwoom REST/WebSocket -> AWS Lightsail live server -> SQLite/Signal Engine -> FastAPI -> Streamlit Web.

No automatic order execution is included.
