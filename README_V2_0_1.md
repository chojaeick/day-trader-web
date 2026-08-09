# DAY TRADER WEB V2.0.1 — Real Premarket Freshness Gate

This patch fixes the main V2.0 risk: treating a recently fetched REST quote as if the underlying market data itself were current.

## Freshness rule
A symbol is `PREMARKET_LIVE` only when:
- current New York time is in the 04:00-09:29 ET premarket window,
- the latest Kiwoom 1-minute bar belongs to the current ET date,
- the latest bar age is no more than 15 minutes.

Otherwise the report marks the data `LAST_SESSION` / `LAST_SESSION_REFERENCE`
and assigns ZERO premarket momentum/volume weight.

## Real premarket fields
When freshness passes:
- premarket_price
- premarket_change_pct vs stored previous close
- premarket_volume from actual same-day 04:00-09:29 ET minute bars
- premarket_volume_pct_avg_daily = premarket volume / 5-day average FULL-DAY volume

The last item is intentionally not called RVOL.

## UI
The Briefing tab now shows:
- PREMARKET_LIVE or LAST_SESSION_REFERENCE
- Market data as-of timestamp
- QQQ/SMH Premarket only when verified fresh
- per-symbol data mode and age

## Unchanged
- CURRENT Trading Score
- SHADOW operational status
- automatic-order policy (still NO AUTO ORDER)

## Next
News Catalyst / AI news judgement, then Kakao/email delivery after this freshness gate is verified.
