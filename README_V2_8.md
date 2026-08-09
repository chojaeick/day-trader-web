# DAY TRADER WEB V2.8 — QUALITY_GATE_V1 (USA + KOREA)

Purpose: aggressively reduce the universe before expensive chart/news/pulse analysis.

Grades
- A: preferred normal universe
- B_EVENT: event/exception candidate, allowed but not equivalent to final recommendation
- C_HIGH_RISK: monitor only; excluded from normal expensive-analysis universe
- REJECT: excluded

USA V1
- AUTO price < $5 rejected
- low-liquidity names rejected
- extreme/high chase names moved to C_HIGH_RISK
- leveraged ETFs classified B_EVENT
- QQQ/SPY/SMH core liquid ETFs A
- market-cap rank deliberately remains pending until a verified source is connected

KOREA V1
- official Kiwoom ka10099 security metadata
- preferred shares rejected
- explicit management/liquidation/halt/delisting state rejected
- price < KRW 2,000 rejected
- estimated market cap = listed shares × previous close
- when ka10099 snapshot is sufficiently broad:
  - top 500 preferred
  - 501~800 B_EVENT
  - >800 rejected unless a strong multi-source event exception
- investment-warning or high chase names -> C_HIGH_RISK
- new listing <90 days: A downgraded to B_EVENT
- if metadata is incomplete, cap-rank enforcement is disabled and UI says PENDING

QUALITY_GATE is not the final recommendation engine.
V2.9 is planned for FINAL_RECOMMENDATION_V1 (actionable 1~5).
NO AUTO ORDER.
