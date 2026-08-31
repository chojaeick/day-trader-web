# Day Trader Project Master Spec

## Canonical phrase: `국장 모의투자`

Whenever the user says `국장 모의투자`, treat the following as the authoritative operating contract unless the user explicitly changes it.

### Broker / account
- Korean market only.
- Kiwoom MOCK only (`https://mockapi.kiwoom.com`).
- Mock account: `8133230011`.
- Never imply real-money trading.
- Never route an order to a non-mock endpoint.

### Engine authority
- KR BUY authority: `ENGINE5_V22_KR_LIVE`.
- KR SELL authority: `ENGINE5_V22_KR_LIVE`.
- Williams logic is telemetry/diagnostics only and must never submit KR broker BUY/SELL orders.
- Legacy Williams `exit_ready` and legacy fixed -1.5% hard-stop must not have broker order authority.
- Do not describe the runtime as V22 unless both BUY and SELL authorities are V22.

### Capital / position sizing
- Use essentially all available trading capital; minimize idle cash.
- Sizing must use the live Kiwoom mock account (`kt00004`) immediately before the order.
- No fixed 1-share sizing.
- No fixed KRW-per-position sizing.
- No old 1,000,000 KRW / 5-slot Williams allocator.
- Current allocator target: up to 4 simultaneous holdings.
- With free cash, deploy approximately 99.5% of available cash into the next valid V22 entry, whole shares only.
- Profits/losses compound automatically because every subsequent order is sized from the current live account.
- If capital is fully invested and another valid strong entry appears, rotate capital: sell approximately 50% of the largest other holding, wait until the next account refresh confirms released cash, then enter the new signal.
- A rebalance sell is portfolio allocation (`V22_KR_REBALANCE_SELL`), not a V22 strategy exit.

### Order safety
- Never auto-retry a BUY or SELL order on the same engine bar.
- Read current account state before sizing.
- Confirm sell quantity does not exceed the live holding quantity.
- After a funding/rebalance SELL, never chain a BUY before released cash is visible in a later account read.

### V22 position lifecycle
- Entry levels are stored with the position, including structural stop and TP1.
- Structural stop is based on the V22/Engine5 band-R semantics, not the legacy Williams stop.
- TP1 uses +2R and partial exit semantics.
- Runner management remains under V22/Engine5 authority.

### Runtime / deploy
- Repo: `chojaeick/day-trader-web`, branch `v22`.
- Repo checkout: `/home/ubuntu/day-trader-api-repo`.
- Runtime: `/home/ubuntu/day-trader-api`.
- Service: `day-trader-api`.
- Health: `http://127.0.0.1:8000/health`.
- KR console: `/korea-live`.
- Runtime files may be root-owned; deploy scripts must write to a temporary file, compile it, then use `sudo install` rather than direct `Path.write_text()` into runtime.

### Required runtime verification markers
A valid deployed KR mock-trading runtime must preserve all of these:

```
KR_BUY_AUTHORITY=ENGINE5_V22_KR_LIVE
KR_SELL_AUTHORITY=ENGINE5_V22_KR_LIVE
ORDER_SIZING=LIVE_ACCOUNT_99_5PCT_CASH
MAX_POSITIONS=4
ROTATION=SELL_50PCT_LARGEST_OTHER_THEN_WAIT_FOR_CASH
AUTO_ORDER_RETRY=DISABLED_PER_ENGINE_BAR
BROKER=KIWOOM_MOCK_ONLY
```

If any marker/authority is missing, do not assume the system is correctly configured; inspect and repair it.
