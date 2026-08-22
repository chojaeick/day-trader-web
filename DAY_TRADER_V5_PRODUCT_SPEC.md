# DAY TRADER V5 — Product Baseline

Status: DESIGN BASELINE
Date: 2026-08-23

## Philosophy
DAY TRADER V5 is a trading decision terminal, not an engine-debug dashboard.

The main screen must quickly answer: what to buy, how much, at what price, where risk limits are, and what to do with positions already owned.

## Primary navigation
1. Trading
2. Portfolio
3. Market Briefing
4. Settings

Existing Validation / Archive / Shadow diagnostics are preserved under Legacy/Debug rather than dominating the normal workflow.

## Trading
Global market switch: KOREA / USA.

### Short-term recommendations
Show symbol/name, BUY/ADD/HOLD/WAIT/REDUCE/EXIT/AVOID action, current price, entry zone, recommended allocation, confidence, Power, stop/hard floor, dynamic floor, ceiling, T1/T2, chase warning, and a concise reason.

Detailed drill-down may expose VO, MFI, MACD, Dynamic RSI, VWAP, volume, trend, 1m trigger, 5m setup and other engine evidence.

### Long-term recommendations
Separate from day trades. Show rating/action, accumulation range, target portfolio weight, thesis/trend and risk.

### Manual trade registration
User can register purchase price and amount or quantity from a recommendation. Registration creates an active short-term position.

### Active short-term position management
Use one unified card for quantity, average cost, current price, position value, P&L, action, hard stop, dynamic floor, ceiling, T1/T2, add/reduce/exit recommendation and concise reason.

Manual order only until brokerage automation is explicitly enabled after validation.

## Portfolio
Maintain actual asset ledger: market, symbol/name, asset class, quantity, average purchase price, current price, market value, cost basis and P&L.

Summary includes total assets, cash/equities, ETF/long-term/short-term allocation, Korea/USA allocation and daily/weekly/monthly/cumulative performance.

Persist immutable daily portfolio snapshots for asset curve, returns, MDD and allocation history.

## Market Briefing
Purpose: answer “What should I know about the market today?” in about three minutes.

Generate a canonical report every morning at 07:00 user-local schedule and reuse it in both App and Kakao.

Cover KOSPI/KOSDAQ, S&P 500/Nasdaq/Dow, USD/KRW and relevant macro indicators, Korea/USA summaries, important news, currently popular themes, watchlist/holding news, risks and watch points.

Pipeline: market/news data -> AI briefing -> DB -> App -> optional Kakao summary.

## Settings
Persist capital/risk settings including target allocation, max position amount, staged-entry percentages and daily loss/risk limits.

Kakao notification toggles are independent for morning brief, day-trade BUY, ADD, profit-taking, urgent EXIT/stop, long-term changes, important holding alerts and daily asset summary. Support Korea/USA enablement where applicable and ALL/IMPORTANT/URGENT ONLY intensity.

Message categories use distinct headers/emoji rather than assuming arbitrary Kakao chat-bubble background styling: 📰 MORNING BRIEF, ⚡ DAY TRADE, 📈 LONG TERM, 🛡 POSITION, 🚨 URGENT EXIT.

## Architecture principles
- Reuse existing Finder, Tracker, Kiwoom integration, DB and validated engine components.
- Do not rebuild backend functionality merely because UI is redesigned.
- Trading UI consumes final decision outputs instead of exposing every internal diagnostic.
- Settings are persisted and consumed by engine/notification services; avoid hard-coded user policy.
- One generated briefing is reused across App and Kakao.
- Portfolio daily snapshots are immutable historical records.
- Validation and Shadow information remain available but outside the primary workflow.

## Implementation phases
1. V5 shell and clean Trading decision screen using existing status APIs.
2. Portfolio ledger + daily snapshot DB/API.
3. Settings DB/API + Kakao notification preferences.
4. 07:00 Market Briefing generation/storage/App/Kakao pipeline.
5. Integrate validated short-term and long-term engines into decision cards.
6. Only after sufficient validation, optional Kiwoom order execution.

## Non-negotiable
Profitability is the optimization objective, but UI confidence must never substitute for validated engine evidence. Production BUY/ADD/EXIT decisions remain distinguishable from research/shadow signals until validation promotes them.
