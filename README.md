# DAY TRADER WEB + LIVE SERVER v1.2

V1.2 upgrades the live signal system with user-requested day-trading selection logic.

## Added in v1.2
- Kiwoom official US daily chart (`usa06012`, `/api/us/chart`) for 5-day metrics
- Current price > 5-day close average screening
- MA5 slope
- 5-day average volume / dollar volume
- time-adjusted intraday RVOL
- 5-day ATR%
- current momentum + QQQ/SMH context
- 30+ liquid US large-cap / leveraged ETF candidate universe
- TOP10 endpoint `/api/screener`
- ranking checkpoints at 09:20 / 09:29 / 09:37 New York time
- combined 1-minute + 5-minute signal confirmation
- position endpoint for HOLD / ADD / TRIM / EXIT support
- no order execution

## AWS update after uploading to GitHub
```bash
cd /home/ubuntu/day-trader-api-repo
git pull
cp -r live_server trader /home/ubuntu/day-trader-api/
cp app.py /home/ubuntu/day-trader-api-repo/app.py
cd /home/ubuntu/day-trader-api
source venv/bin/activate
pip install -r live_server/requirements.txt
sudo systemctl restart day-trader-api
curl http://127.0.0.1:8000/health
```

Or use `live_server/update_server.sh` after the new file is on the server.

## Streamlit
GitHub push triggers Streamlit redeploy automatically. Keep the existing Secret:

```toml
DAYTRADER_API_URL = "http://3.37.169.231:8000"
```

## Important
This is a decision-support tool. It does not place orders. Signal scoring should be forward-tested and tuned before relying on it with meaningful capital.
