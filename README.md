# DAY TRADER WEB + LIVE SERVER v1.3

V1.3 tunes the user-requested day-trading screener and adds a live selected-symbol signal/position engine.

## Added / changed in v1.3

### TOP10 tuning
- Current price > 5-day average is a strong requirement
- Positive MA5 slope gets more weight; negative slope is penalized
- Time-adjusted RVOL tiers: 1.5x / 2x / 3x
- ATR day-trading sweet spot (roughly 3-8%)
- Dollar-volume / liquidity scoring
- Liquid leveraged ETF bonus (SOXL/SOXS/TQQQ/SQQQ)
- +3% to +10% momentum sweet spot; extreme chasing is penalized
- QQQ / SMH alignment
- Near-high price-action score

### Selected-symbol live signal
- 1-minute + 5-minute confirmation
- VWAP, EMA9, EMA20, EMA50, RSI, bar RVOL
- 20-bar breakout / breakdown
- LONG and SHORT scores are calculated separately
- WAIT / WATCH / SETUP / TRIGGER
- Shows reason, risks, invalidation, T1, T2
- Market/sector context comes from QQQ/SMH, not from the selected stock itself

### Position mode
- User enters the real fill price manually
- LONG or SHORT side
- HOLD / HOLD_CAUTION / ADD / TRIM_30 / TRIM_30_RUNNER / TRIM_MORE / EXIT
- Hard loss cap remains -2% reference; technical stop can be tighter
- No averaging down signal
- No automated order execution

## Deployment update
Upload the entire package contents to the existing GitHub repository, then on Lightsail:

```bash
cd ~/day-trader-api-repo
git pull
cp -r live_server trader /home/ubuntu/day-trader-api/
cd /home/ubuntu/day-trader-api
sudo systemctl restart day-trader-api
curl http://127.0.0.1:8000/health
```

Expected health version: `1.3`.
