# V2.2.4b — PREOPEN ROW DECODE FLATTEN HOTFIX

Observed:
- report_text correctly showed Catalyst / Evidence / AI confidence
- Evidence Audit showed N/A / None for the same symbols
- saved PREOPEN rows stored all enriched fields in `extra_json`

Root cause:
`PreopenStore._decode()` restored `extra_json` under `row['extra']`,
while the UI expects fields like `catalyst_strength`, `evidence_check`,
`news_symbol_status`, `news_elapsed_sec`, etc. at the row top level.

Fix:
- decode `extra_json`
- merge it back into each row with `d.update(extra)`

No changes to:
- Trading Score
- News weighting
- OpenAI request logic
- scheduler
- archive generation
- NO AUTO ORDER
