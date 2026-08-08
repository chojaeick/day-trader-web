# DAY TRADER WEB + LIVE SERVER v1.1

V1.1 adds an AWS Lightsail backend for Kiwoom live market data.

## What is included
- Kiwoom OAuth token handling
- US quote snapshots (verified request: `usa20100`)
- US WebSocket LOGIN + F5 subscription
- SQLite tick/raw-message persistence
- 1-minute / 5-minute candle aggregation
- FastAPI endpoints: health, quotes, bars, signal
- Streamlit app auto-switches from DEMO to LIVE when `DAYTRADER_API_URL` is configured
- No order endpoint exists in this package

## AWS install (after this repository is updated)
```bash
cd ~
rm -rf day-trader-api-repo
git clone https://github.com/chojaeick/day-trader-web.git day-trader-api-repo
cd day-trader-api-repo
cp -r live_server trader /home/ubuntu/day-trader-api/
cd /home/ubuntu/day-trader-api
./live_server/install_server.sh
```

Open Lightsail firewall TCP 8000 before testing from Streamlit.

## Streamlit Secret
In Streamlit Community Cloud > App settings > Secrets add:
```toml
DAYTRADER_API_URL = "http://3.37.169.231:8000"
```

The existing Kiwoom `.env` stays only on the AWS server. Never commit it.
