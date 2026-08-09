# DAY TRADER WEB V2.0 — Scheduled Pre-Open Intelligence Engine

## What V2.0 does first
The first priority is not Kakao or email. It is a reliable immutable reference snapshot.

Every US trading weekday at **09:00 America/New_York (regular open -30 minutes)** the backend:
1. forces a fresh market-wide discovery,
2. rebuilds the active Universe,
3. calculates CURRENT TOP10,
4. calculates SHADOW TOP10,
5. creates transparent Pre-Open Intelligence LONG/SHORT power,
6. saves a permanent PREOPEN_30 report,
7. also freezes CURRENT and SHADOW rankings in the existing Ranking Archive.

This happens on the AWS backend even if nobody opens Streamlit.

## New database records
- preopen_report_meta
- preopen_report_rows

The stored record includes:
- exact generation timestamp
- CURRENT/SHADOW ranks and scores
- quote/change%, MA5 slope, RVOL, ATR
- market QQQ/SMH context
- LONG/SHORT power
- recommendation and rationale
- model_version

Past records are not recomputed when future model logic changes.

## New endpoints
- POST /api/briefing/generate?market=USA
- GET /api/briefing/latest?market=USA
- GET /api/briefing/history?market=USA
- GET /api/briefing/{report_id}

## UI
Adds a `🗞️ Briefing` tab with:
- latest saved USA report
- manual Generate Now test button
- report history
- per-symbol LONG/SHORT power and rationale

## Important scope
V2.0 deliberately creates the reliable scheduling + storage foundation first.
- News Catalyst: V2.1
- external AI news judgement: V2.1
- Kakao/email delivery: after report generation is verified
- Korean PREOPEN_30: same schema, enabled after Korean-market data adapter exists

## Safety
- CURRENT Trading Score unchanged
- SHADOW remains observation-only
- NO AUTO ORDER
