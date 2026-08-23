# ETHAN NY BREAKOUT — SOURCE-LOCKED SPEC v1.0

Status: source reconstruction complete enough for replication testing.  
Do **not** treat any failed approximation as rejection of the original strategy.

## 1. Core market / timing
- Primary source market: NQ/MNQ / NAS100.
- Main execution chart: 5-minute; 15-minute may be used for structure/key levels.
- 1-minute is used only for fine rejection/imbalance confirmation when a retest returns with momentum.
- Trading window: New York cash open, approximately 09:30–10:30 ET.
- Typical frequency: 1–2 trades/day.

## 2. Key-level construction
- Start from visually obvious V-shaped reactions / sharp reversals.
- A V-shaped low/high is a candidate key level.
- Repeated V reactions near the same price strengthen the level.
- Source material does **not** provide fixed N-bar pivot rules, exact wick/body boundaries, exact zone thickness, or exact interaction-count thresholds.
- Therefore all numerical V/pivot/cluster thresholds used in code are **ENGINEERING HYPOTHESES**, not claimed original rules.

## 3. Space filter
- Before trading a breakout, there must be enough open space to the next opposing level / major obstacle.
- Source material states that a setup should have room for at least about 1:2 R:R with a roughly 20–25 point stop; other examples mention roughly 20–30 points depending on volatility.
- Generic rule: `available_space >= 2 * intended_risk`.
- Exact NQ point values are not directly transferable to QQQ proxy tests.

## 4. 50 SMA
- 50 SMA is a trend/alignment filter.
- Long bias: price above / aligned with rising 50 SMA.
- Short bias: price below / aligned with falling 50 SMA.
- It is a confluence/filter, not the level generator.

## 5. Breakout
- Wick-only / brief spike does not count.
- A full 5-minute candle must close decisively beyond the key level.
- Source material gives no fixed ATR/body-distance threshold for “decisive”; any such threshold in code is an engineering hypothesis.

## 6. Retest and entry
State sequence:
`LEVEL_FOUND -> BREAK_CLOSED -> WAIT_RETEST -> RETESTED -> ENTRY`

- Do not chase the breakout candle.
- Wait for the broken level to be retested.
- Long: old resistance should behave as support.
- Short: old support should behave as resistance.

### Retest character
- Slow/corrective return: small candles, gradual return -> direct entry on zone/level touch is allowed.
- Momentum return: fast large candles into the level -> do not catch the move blindly; wait for rejection / 1m confirmation.
- Exact candle count/body/speed thresholds are discretionary in source material and must be labeled hypotheses in code.

## 7. Overextension invalidation
- If price travels approximately to the planned 2R objective before producing the retest, cancel the setup.
- This is a pre-entry invalidation rule.

## 8. Stop and target
- Stop: beyond the opposite side of the zone / relevant swing extreme.
- Evaluation-style management: target about 1R.
- Funded-style management: target about 2R; when +1R is reached, move stop to break-even.
- When a lower-timeframe sequence is unavailable and SL/TP are both touched in the same bar, simulations must use a conservative ordering or drill down to 1m data.

## 9. 4H “brick-wall” filter
- 4H is a higher-timeframe obstacle/context filter.
- Avoid trades directly *into* major 4H support/resistance zones.
- Prefer trades moving away from a major 4H wall into open space.
- 4H is **not** the primary 5m entry-level generator in the source-locked baseline.

## 10. Additional confluence material
Miro material separately shows a scoring framework including items such as 50 SMA trend, 4H zones, overextension, S&P500 correlation and imbalance, with liquidity sweep shown as a bonus in screenshots supplied by the user.

These are **not mixed into the core baseline all at once**. They must be tested incrementally after the source-locked breakout/retest baseline is replicated.

## 11. Validation protocol
1. Reproduce source example structure first (level -> body close breakout -> retest -> entry).
2. Measure detector behavior before measuring P/L.
3. Run core baseline with only source-locked core rules.
4. Add one filter at a time: 50 SMA, space, 4H wall, imbalance, correlation, liquidity sweep.
5. Report win rate, 1R hit rate, 2R hit rate, BE rate, expectancy in R, PF, MDD and trade count.
6. Never label the original Ethan strategy REJECTED because a numerical proxy fails.

## 12. Implementation labels
- `SOURCE_CONFIRMED`: directly supported by supplied course/video material.
- `DISCRETIONARY_SOURCE`: source uses visual judgment and gives no numeric definition.
- `ENGINEERING_HYPOTHESIS`: numerical translation introduced only for machine testing.
