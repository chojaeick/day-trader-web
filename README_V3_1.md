# DAY TRADER WEB V3.1 — UNIFIED UX

Purpose
- USA and KOREA Trading screens use the same reading order.
- Remove analysis-universe tables from Trading.
- Replace developer terminology with user-facing Korean labels.
- Make button intent explicit.

Unified Trading order
1. Market summary
2. Final Recommendation 1~5
3. Stock detail / chart area
4. Candidate TOP10 (collapsed)
5. Market context
6. Diagnostics / glossary (collapsed)

KOREA chart
- The same chart/detail location exists as USA.
- Until verified domestic minute bars are connected, the panel explicitly says chart data is pending.

Research
- Analysis universe is moved here.
- A glossary explains A / B_EVENT / C_HIGH_RISK / REJECT and source-count meaning.
- 9999 ranking placeholders are rendered as '-'.

Buttons
- Trading: one visible `화면 새로고침`.
- Diagnostics:
  - 시장 후보 다시 찾기
  - 장중 신호 다시 계산 (KOREA)
  - API 연결 확인 (KOREA)
  - 현재 점수 다시 표시 (USA)
- Each diagnostic action has an explanation above it.

No recommendation/scoring engine changes.
No auto-order changes.
