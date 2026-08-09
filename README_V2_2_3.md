# DAY TRADER WEB V2.2.3 — PROGRESS + RESILIENT NEWS

Observed in V2.2.2:
- progress stayed at 35% for ~4–5 minutes
- one large TOP5 OpenAI web-search request could time out
- timeout caused all five news results to be lost

V2.2.3:
- TOP5 News AI is analyzed one symbol at a time.
- progress advances after each symbol instead of remaining at 35%.
- a timeout/error for one symbol does not discard successful news results for the other symbols.
- job status exposes detail text such as `ONDS OK (3/5)`.
- browser remains asynchronous; no long POST is held open.
- scheduled 09:00 ET report uses the same resilient news path.
- Evidence/source checks from V2.2.2 remain.
- CURRENT Trading Score / News weighting / NO AUTO ORDER unchanged.
