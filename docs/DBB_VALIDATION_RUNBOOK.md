# DBB Validation Runbook

This file records the non-negotiable validation workflow for Engines 1, 2, 3 and 5 so expensive work is not repeated accidentally.

## Persistent historical diagnostics cache

- Runtime cache directory: `/home/ubuntu/day-trader-api/.cache/dbb_diagnostics`
- Cache builder: `tools.backtest_dbb_kr_v2_v21_v22_adaptive.build_frames_cached`
- Cache version: `dbb_diag_exact_v1`
- The cache is fingerprinted from symbol + exact historical bars. Reuse it whenever the input bars are identical.
- Engines 1/2/3 must NOT blindly call the old uncached `enrich()` loop during ordinary comparison/tuning.
- Rebuild only when historical input changes or when explicitly requested.
- First historical build was expensive; subsequent cached sweeps were previously observed to complete in seconds for small grids. Preserve that advantage.

## Integrated comparison rule

- Engine 1 = V2 BASE, fixed historical reference.
- Engine 2 = V2.1 STRUCTURE, fixed historical reference.
- Engine 3 = V2.2 adaptive/structural exit, fixed historical reference.
- Engine 5 = active tuning target.
- Every Engine 5 tuning cycle should be interpreted against the fixed cached 1/2/3 references.
- Compare at least: trades, wins, win rate, average return, gross return, PF, maximum loss and partial/scale-out behavior.
- A high win rate achieved by collapsing trade count is not automatically better.

## Engine 5 current intent

- Entry context uses 5-minute bars.
- Overall DBB-mid rising trend is mandatory.
- MACD slope versus signal-line slope spread is a magnitude score; steep RSI upslope is also a magnitude score.
- Volume, Bollinger expansion, band location and recent inner-band traversal are confirmations/scoring inputs, not mandatory gates.
- Win-rate goal (80%+ during current tuning) is a target, never a strategy filter.
- Exit management uses 1-minute bars.
- First profit-taking: a completed 1-minute candle entirely above dynamic outer-upper -> sell 50%.
- After a subsequent inner-band/inner-upper retest, another outer-upper touch can sell half of the remaining position; this may repeat.
- Inner-lower touch exits all remaining position under the current clarified design.
- Initial stop is still a tuning axis and must not be silently hard-coded as final truth.

## Performance discipline

Before a large validation run, inspect CPU count/load, available memory, disk free space, DB size and cache presence. Use cached diagnostics for 1/2/3. Do not introduce a new uncached integrated runner unless there is a documented reason.

Current fast integrated runner: `tools/backtest_dbb_engines_1_2_3_5.py`.
