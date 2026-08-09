# V2.1.1 ENV HOTFIX

Observed symptom:
- V2.1 report model loaded correctly
- NEWS AI = False
- ERROR = None
- OPENAI key had been saved to `/home/ubuntu/day-trader-api/.env`

Root cause:
- Streamlit `app.py` called `load_dotenv()`
- FastAPI/systemd backend did not load `.env`
- `live_server/news_ai.py` therefore saw no `OPENAI_API_KEY`

Fix:
- `live_server/api.py` explicitly loads `<project_root>/.env` at process startup
  before any API client reads environment variables.
- No key is logged or returned.
- Version bumped to 2.1.1.

After deployment/restart, regenerate the briefing and verify:
NEWS AI = True
ERROR = None
