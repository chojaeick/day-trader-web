# DAY TRADER WEB V2.2.1 — ASYNC BRIEFING JOB

Problem confirmed in V2.2:
- News AI may legitimately need several minutes.
- Streamlit POST timed out at 180 seconds while the backend was still working.

V2.2.1 architecture:
1. POST `/api/briefing/generate?market=USA`
   - returns immediately with `job_id`
2. Server background task continues:
   - SCANNING
   - NEWS_SEARCH_AI
   - SAVING
   - COMPLETE / FAILED
3. UI polls:
   - GET `/api/briefing/job/{job_id}`
4. Browser refresh can recover:
   - GET `/api/briefing/job-active/USA`

Important:
- Scheduled 09:00 ET PREOPEN_30 remains fully server-side.
- A browser is NOT required for scheduled generation.
- Existing News Catalyst V2.2 fields are preserved.
- Duplicate expensive manual News AI jobs are prevented while one is active.
- CURRENT Trading Score and NO AUTO ORDER behavior are unchanged.

This is the base architecture for later email/Kakao delivery because the saved
report can be delivered after the background job reaches COMPLETE.
