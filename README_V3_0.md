# DAY TRADER WEB V3.0 — UI CONSOLIDATION

Goal
- Keep engine logic unchanged.
- Make USA and KOREA Trading screens follow the same reading order.
- The user should answer "what do I trade?" before seeing engine internals.

Unified Trading hierarchy
1. Market summary (4 compact metrics)
2. Final Recommendation 1~5
3. Candidate TOP10 (collapsed)
4. Market Context / selected-stock detail
5. Research data (collapsed)
6. Diagnostics (collapsed)

KOREA
- PREOPEN full table moved behind a collapsed detail section.
- Pulse full table moved behind a collapsed detail section.
- Universe/Quality Gate moved to Research.
- Diagnostics only contains recovery/manual actions.

USA
- Candidate TOP10 collapsed by default.
- CURRENT vs SHADOW moved to Research.
- Universe remains collapsed.
- Final Recommendation remains the first actionable section.
- Detailed selected-stock chart remains available on demand.

No engine/scoring changes.
No automatic orders.
