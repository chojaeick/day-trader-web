# DAY TRADER WEB V2.2.2 — SOURCE / EVIDENCE QUALITY

Goal:
Improve trustworthiness of the News Catalyst layer before automated delivery.

Adds:
- evidence_check: PASS / WARN / FAIL
- evidence_warning
- conflict_ko for materially conflicting credible news
- consistency rule: catalyst type must match the actual event
- PRIMARY source without a real URL is automatically downgraded to TIER1
- NONE catalyst forces type NONE + NEUTRAL bias
- TOP5 Evidence Audit UI
- source URL/title, recency, impact horizon, confidence and News ΔLONG remain visible

Unchanged:
- asynchronous manual briefing jobs
- server-side 09:00 ET scheduled PREOPEN generation
- CURRENT Trading Score
- News weighting formula
- NO AUTO ORDER
