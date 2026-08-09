# DAY TRADER WEB V2.2 — NEWS CATALYST QUALITY

Purpose:
Make the news layer auditable before adding messaging/automation.

New per-symbol fields:
- catalyst_type
- source_quality
- event_recency
- impact_horizon
- event_time_utc
- source_title / source_url
- confidence_score (0-100)
- news_headline_ko
- news_why_now_ko
- news_weight_pct
- news_delta_long

Catalyst types:
EARNINGS_GUIDANCE, CONTRACT_CUSTOMER, MNA, FDA_REGULATORY, ANALYST,
FINANCING_DILUTION, PRODUCT, LITIGATION, POLICY_MACRO, SECTOR, OTHER, NONE.

Important:
- CURRENT Trading Score is unchanged.
- Existing limited News AI weighting formula is unchanged.
- NO AUTO ORDER is unchanged.
- Web search remains TOP5 first for latency/cost control.
- If News AI fails, the technical/pre-market briefing still completes.
