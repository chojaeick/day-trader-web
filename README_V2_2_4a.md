# V2.2.4a — TIME IMPORT HOTFIX

Symptom:
`브리핑 생성 실패: name 'time' is not defined`

Cause:
V2.2.4 added per-symbol elapsed timing using `time.monotonic()` in
`live_server/news_ai.py`, but the module did not import `time`.

Fix:
- add `time` to the news_ai.py imports
- no scoring, News AI weighting, scheduler, archive, or order logic changes
