# DAY TRADER WEB V2.1.3 — NEWS AI TIMEOUT/RETRY HOTFIX

Confirmed before this fix:
- OpenAI API key loads correctly.
- Direct Responses API + web_search call completes successfully.
- Briefing News AI is enabled.
- Briefing failed with: `The read operation timed out`.

Changes:
- News AI first attempt timeout: 90 seconds.
- One retry using 150 seconds.
- News web-search scope reduced from TOP10 to TOP5.
- Existing technical briefing remains available if News AI fails.
- CURRENT Trading Score and NO AUTO ORDER behavior unchanged.

Optional `.env` overrides:
DAYTRADER_NEWS_AI_TIMEOUT_SECONDS=90
DAYTRADER_NEWS_AI_RETRY_TIMEOUT_SECONDS=150
DAYTRADER_NEWS_AI_MAX_ATTEMPTS=2
