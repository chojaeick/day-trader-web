# DAY TRADER WEB v1.5B.2 — Latest Historical Anchor Fix

Critical correction:
- usa06012 history is requested with strt_dt anchored at the latest/current date.
- continuation pages then move backward in time.
- v1.5B.1 incorrectly anchored strt_dt in the past, so a 120-session test could end months before the latest market date.

Diagnostics now include per-symbol:
- first historical date
- last historical date
- page count
- raw rows
- usable daily bars

The validation result is considered a baseline only when:
- requested sessions == candidate sessions == validated sessions
- the historical end date is the latest completed U.S. trading session available from Kiwoom.

No automatic weight changes.
No automatic orders.
