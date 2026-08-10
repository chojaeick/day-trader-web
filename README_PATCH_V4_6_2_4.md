# V4.6.2.4 WARM FAULT ISOLATION

Changed:
- live_server/api.py
- app.py

No Finder formula change.
No Power / READY / ENTRY threshold change.
No Discovery live bridge activation.
No Kiwoom TR/schema change.
No DB migration.
No order behavior change.

Warm stages are now isolated:
1. EXCHANGE
2. QUOTE
3. DAILY
4. MINUTE

DAILY is supporting data only.
A DAILY failure no longer blocks MINUTE backfill.

Statuses:
- READY
- READY_DAILY_WARN
- PARTIAL
- QUOTE_FAILED
- MINUTE_FAILED
- PENDING / RUNNING / OBSERVED

Operational readiness:
- price > 0
- minute bars >= 6

If quote+minute are usable but daily fails:
- status = READY_DAILY_WARN
- symbol is considered warmed for retry suppression
- daily warning remains visible
- no live Finder scoring behavior is changed

UI moves failed_step and error_short next to status.

Apply:
python3 apply_v4_6_2_4.py .
python3 -m py_compile live_server/api.py app.py
