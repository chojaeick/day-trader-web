# DAY TRADER WEB V3.2 — ONE TRADING UI

USA and KOREA now use the same shared Trading UI renderer.

Common order:
1. Market summary
2. Final Recommendation 1~5
3. Stock Detail
4. Candidate TOP10
5. Market Context
6. Diagnostics
7. Glossary

Both markets use the same stock-detail metrics:
- 상태
- 방향
- 신호점수
- 5분 확인
- 거래량강도
- 현재가

Both markets reserve identical chart positions:
- 1-minute chart
- 5-minute chart

KOREA shows same-size placeholders until verified minute bars are connected.

Final Engine debug details are moved to Research.
Engine/scoring logic is unchanged.
No auto orders.
