# DAY TRADER WEB v1.5A.2

Validation logic changes:
- SOXS/SQQQ prediction scoring is now direction-normalized, not only the benchmark.
- Asset groups:
  - STOCK
  - LEVERAGED_LONG
  - INVERSE_ETF
- Group-level average excess return, score, and hit rate.
- Validation tags:
  - TRUE_POSITIVE: predicted top5 and actual top5
  - FALSE_POSITIVE: predicted top5 but actual rank > 10
  - MISSED_WINNER: predicted rank > 10 but actual top5
- OPEN_V0 score components are stored for each historical row.
- Dashboard compares average score components for TRUE_POSITIVE vs FALSE_POSITIVE samples.

No automatic weight changes.
No automatic orders.
