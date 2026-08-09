# DAY TRADER WEB V2.8.1 — QUALITY GATE CONSISTENCY

Fixes:
1. Korea market-cap grade/reason consistency
   - <=500: A / MARKET_CAP_TOP500
   - 501~800: B_EVENT / MARKET_CAP_RANK_501_800
   - >800: REJECT unless EVENT_EXCEPTION
2. Korea ETFs are separated from corporate market-cap logic
   - normal ETF: A / ETF_CORE
   - leveraged/inverse ETF: B_EVENT / LEVERAGED_OR_INVERSE_ETF
   - ETF market_cap_rank shown as None
3. TOP10 naming clarified
   - Candidate TOP10 = analysis priority, NOT final recommendation
   - SHORT/WAIT can appear here without implying a trade recommendation

Next:
FINAL_RECOMMENDATION_V1 for actionable 1~5 names.
