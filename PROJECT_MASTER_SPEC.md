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

---

## Canonical phrase: `미장 모의투자`

Whenever the user says `미장 모의투자`, treat the following as the authoritative operating contract unless the user explicitly changes it.

### Broker / connection
- U.S. market mock investment only.
- Broker adapter: `live_server/kiwoom_us_mock_broker.py` / `KiwoomUSMockBroker`.
- Kiwoom MOCK REST only: `https://mockapi.kiwoom.com`.
- Preferred credentials: `KIWOOM_US_MOCK_APP_KEY`, `KIWOOM_US_MOCK_APP_SECRET`.
- Legacy credential fallback supported by the existing service: `KIWOOM_MOCK_APP_KEY`, `KIWOOM_MOCK_APP_SECRET`.
- Orders are enabled only when `KIWOOM_MOCK_US_ORDER_ENABLE=1`.
- Never route a U.S. mock order to a non-mock endpoint.
- Do not replace this broker with the internal SQLite `PaperBroker` or a newly invented paper ledger.

### Existing validated U.S. mock REST contract
- Account/balance query: `/api/us/acnt`, API ID `ust21070`.
- Buy: `/api/us/ordr`, API ID `ust20000`.
- Sell: `/api/us/ordr`, API ID `ust20001`.
- Supported exchange codes: `NY`, `ND`, `NA`.
- Kiwoom mock U.S. orders use limit type `00`; do not assume market-order support.

### Engine authority
- U.S. DBB execution engine name: `ENGINE5_V22E_USA`.
- US BUY authority must be `ENGINE5_V22E_USA` once deployed.
- US SELL authority must be `ENGINE5_V22E_USA` once deployed.
- V22E must call the existing `KiwoomUSMockBroker`; it must not use the internal `PaperBroker` for execution.
- Until both BUY and SELL are wired and verified, do not claim V22E is live-connected.

### UI contract
- Trading detail engine list is DBB only.
- KR label: `DBB V22`.
- US label: `DBB V22E`.
- U.S. account/positions/fills shown in V5 must come from the existing Kiwoom U.S. mock path, not an internal simulated ledger.

### Required runtime verification markers
A valid deployed U.S. mock-trading runtime must preserve all of these:

```
US_BUY_AUTHORITY=ENGINE5_V22E_USA
US_SELL_AUTHORITY=ENGINE5_V22E_USA
US_BROKER=KIWOOM_US_MOCK_ONLY
US_REST_BASE=https://mockapi.kiwoom.com
US_ORDER_ENABLE_ENV=KIWOOM_MOCK_US_ORDER_ENABLE
US_ACCOUNT_API=ust21070
US_BUY_API=ust20000
US_SELL_API=ust20001
INTERNAL_PAPER_EXECUTION=DISABLED
```

If any marker/authority is missing, do not assume `미장 모의투자` is correctly configured; inspect and repair it.
