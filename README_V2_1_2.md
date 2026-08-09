# DAY TRADER WEB V2.1.2 — ENV FINAL HOTFIX

## Why this hotfix exists
Observed:
- manual venv test: `KEY LOADED = True`
- direct OpenAI Responses API + web_search: `OPENAI CALL = OK`
- systemd/FastAPI generated reports: `NEWS AI = False`

This proves the secret key and OpenAI API are valid.
The remaining issue is backend process environment precedence.

## Fix
`live_server/api.py` now loads:

```python
load_dotenv(_BACKEND_ENV, override=True)
```

This makes `/home/ubuntu/day-trader-api/.env` authoritative even if systemd already
contains a stale or empty `OPENAI_API_KEY` variable.

## Safe health diagnostics
`/health` now exposes only:
- `news_ai_configured: true/false`
- `news_ai_model: ...`

The API key value is NEVER returned.

## Expected after deploy
1. `/health`:
   - `version: 2.1.2`
   - `news_ai_configured: true`
   - `news_ai_model: gpt-5`
2. Generate a new USA briefing.
3. Latest briefing:
   - `news_ai_enabled: true`
   - `news_ai_error: null`
   - Catalyst / Bias / AI Confidence populated where material news exists.

## Unchanged
- CURRENT Trading Score
- SHADOW research behavior
- PREMARKET freshness gate
- Archive behavior
- NO AUTO ORDER
