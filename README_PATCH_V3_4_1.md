# V3.4.1 PATCH — Briefing result hotfix

Included files:
- app.py

Fix:
- KOREA `/api/korea/preopen/generate` returns the saved report with `id`.
- V3.4 UI incorrectly checked only `ok` / `report_id`, so a successful generation could be shown as failure.
- V3.4.1 accepts `id` and shows backend error detail when generation truly fails.

No backend or engine change.
