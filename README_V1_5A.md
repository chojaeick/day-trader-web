# DAY TRADER WEB v1.5A — Historical Validation Engine

OPEN_V0 reconstructs a historical opening-time ranking using only information available at the opening print:
- five completed prior daily bars
- current-day opening price
- QQQ/SMH opening gap

After the prediction is frozen, the completed day is used only for evaluation:
- Open -> Close return
- MFE / MAE
- QQQ/SMH-adjusted excess return
- predicted rank vs actual rank
- daily rank correlation
- TOP5 average excess return and hit rate

This is not a perfect reconstruction of historical T-30/T-10/T-1 premarket snapshots.
Those will be validated prospectively from live snapshots.

No automatic weight changes and no auto-order functionality.
