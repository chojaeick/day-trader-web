from __future__ import annotations

"""Dynamic full-investment KR account replay.

Capital-allocation rule under test
----------------------------------
- Initial cash: KRW 30,000,000.
- Maximum 3 concurrent positions.
- If no position is open, the next BUY uses all available cash.
- If cash exists while 1 or 2 positions are already open, the next BUY uses all free cash.
- If no free cash exists and a new BUY arrives:
    * 1 existing position -> trim it so existing/new are approximately 50/50.
    * 2 existing positions -> trim existing holdings down toward one-third of equity each,
      then fund the third position with the released cash.
- When a position exits normally, proceeds stay as cash until the next BUY; existing
  positions are NOT topped up merely because another position exited.
- If 3 positions are already open, later BUY signals are skipped until a slot opens.

Important accounting note
-------------------------
The integrated trade CSV contains each strategy trade's final total pnl_pct, but not the
full internal partial-TP cashflow path. For forced mid-trade reallocations we therefore use
actual 1-minute market close at the rebalance timestamp to value the trimmed shares, while
scheduled final exits use the trade CSV's exact fee-adjusted total return for the remaining
shares. This preserves each untouched trade's validated economics while making mid-trade
capital transfers price-aware. Extra forced sells incur an additional 0.125% one-way cost.

This is a capital-allocation diagnostic, not a replacement engine backtest.
"""

from dataclasses import dataclass
from pathlib import Path
import math
import numpy as np
import pandas as pd

from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
REVISED_TRADES = OUT_DIR / 'integrated_slow_turn_rearm_deep_trades.csv'
OLD_TRADES = OUT_DIR / 'integrated_full_history_trades.csv'
OUT_SUMMARY = OUT_DIR / 'account_30m_dynamic_full_invest_summary.csv'
OUT_LEDGER = OUT_DIR / 'account_30m_dynamic_full_invest_ledger.csv'
OUT_EVENTS = OUT_DIR / 'account_30m_dynamic_full_invest_events.csv'

INITIAL_CASH = 30_000_000.0
MAX_POSITIONS = 3
ROUND_TRIP_FEE_PCT = 0.25
EXTRA_FORCED_SELL_FEE_PCT = 0.125
CASH_EPS = 1.0


@dataclass
class Pos:
    trade_id: int
    symbol: str
    source: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    shares: float
    original_stake: float
    gross_pct: float
    net_pct: float
    forced_trim_proceeds: float = 0.0
    forced_trim_pnl: float = 0.0


def nt(x):
    t = pd.Timestamp(x)
    if t.tzinfo is not None:
        t = t.tz_localize(None)
    return t


def n(x):
    return str(x).zfill(6)


def prepare(df: pd.DataFrame, label: str) -> pd.DataFrame:
    x = df.copy()
    x['entry_time'] = x['entry_time'].map(nt)
    x['exit_time'] = x['exit_time'].map(nt)
    x['pnl_pct'] = pd.to_numeric(x['pnl_pct'], errors='coerce')
    x = x.dropna(subset=['entry_time', 'exit_time', 'pnl_pct']).copy()
    x['symbol'] = x['symbol'].map(n)
    if 'source' not in x.columns:
        x['source'] = 'UNKNOWN'
    x = x.sort_values(['entry_time', 'exit_time', 'symbol']).reset_index(drop=True)
    x['trade_id'] = np.arange(1, len(x) + 1)
    x['scenario'] = label
    return x


def build_price_lookup():
    raw = {n(k): v.copy() for k, v in load_data().items()}
    out = {}
    for sym, f in raw.items():
        q = f[['time', 'close']].copy()
        q['time'] = q['time'].map(nt)
        q['close'] = pd.to_numeric(q['close'], errors='coerce')
        q = q.dropna().sort_values('time').reset_index(drop=True)
        out[sym] = q
    return out


def price_at(prices, sym: str, ts: pd.Timestamp) -> float:
    f = prices.get(sym)
    if f is None or f.empty:
        raise KeyError(f'no minute bars for {sym}')
    t = nt(ts)
    a = f['time'].values
    i = int(np.searchsorted(a, np.datetime64(t), side='right') - 1)
    if i < 0:
        raise KeyError(f'no bar <= {t} for {sym}')
    row = f.iloc[i]
    bt = nt(row.time)
    if bt.date() != t.date():
        raise KeyError(f'no same-day bar for {sym} at {t}; last={bt}')
    return float(row.close)


def replay(df: pd.DataFrame, label: str, prices):
    x = prepare(df, label)
    rows = {int(r.trade_id): r for _, r in x.iterrows()}
    events = []
    for _, r in x.iterrows():
        events.append((nt(r.exit_time), 0, int(r.trade_id)))
        events.append((nt(r.entry_time), 1, int(r.trade_id)))
    events.sort(key=lambda z: (z[0], z[1], z[2]))

    cash = INITIAL_CASH
    open_pos: dict[int, Pos] = {}
    accepted: set[int] = set()
    skipped = 0
    forced_trim_events = 0
    forced_trim_value = 0.0
    ledger = []
    evrows = []
    peak = INITIAL_CASH
    max_dd = 0.0
    max_concurrent = 0

    def market_value(p: Pos, ts: pd.Timestamp) -> float:
        return p.shares * price_at(prices, p.symbol, ts)

    def equity(ts: pd.Timestamp) -> float:
        return cash + sum(market_value(p, ts) for p in open_pos.values())

    def record(ts, kind, note=''):
        nonlocal peak, max_dd
        eq = equity(ts) if open_pos else cash
        peak = max(peak, eq)
        dd = (eq / peak - 1.0) * 100.0 if peak > 0 else 0.0
        max_dd = min(max_dd, dd)
        evrows.append(dict(scenario=label, time=ts, event=kind, note=note,
                           cash_krw=cash, market_equity_krw=eq,
                           open_positions=len(open_pos), drawdown_pct=dd))

    def trim_position(p: Pos, sell_value_gross: float, ts: pd.Timestamp):
        nonlocal cash, forced_trim_events, forced_trim_value
        px = price_at(prices, p.symbol, ts)
        cur_value = p.shares * px
        gross = min(max(float(sell_value_gross), 0.0), cur_value)
        if gross <= CASH_EPS:
            return 0.0
        sell_shares = gross / px
        fee = gross * EXTRA_FORCED_SELL_FEE_PCT / 100.0
        net_proceeds = gross - fee
        sold_cost = sell_shares * p.entry_price
        pnl = net_proceeds - sold_cost
        p.shares -= sell_shares
        p.forced_trim_proceeds += net_proceeds
        p.forced_trim_pnl += pnl
        cash += net_proceeds
        forced_trim_events += 1
        forced_trim_value += gross
        evrows.append(dict(scenario=label, time=ts, event='FORCED_TRIM',
                           note=f'{p.symbol} gross={gross:.0f} fee={fee:.0f}',
                           cash_krw=cash, market_equity_krw=np.nan,
                           open_positions=len(open_pos), drawdown_pct=np.nan))
        return net_proceeds

    if len(x):
        record(nt(x.entry_time.min()), 'START')

    for ts, priority, tid in events:
        r = rows[tid]

        if priority == 0:
            if tid not in accepted or tid not in open_pos:
                continue
            p = open_pos.pop(tid)
            # Preserve exact validated economics for the remaining shares.
            final_factor = 1.0 + p.net_pct / 100.0
            proceeds = p.shares * p.entry_price * final_factor
            cash += proceeds
            total_pnl = p.forced_trim_pnl + (proceeds - p.shares * p.entry_price)
            ledger.append(dict(
                scenario=label, trade_id=tid, status='ACCEPTED', symbol=p.symbol, source=p.source,
                entry_time=p.entry_time, exit_time=p.exit_time, original_stake_krw=p.original_stake,
                remaining_final_proceeds_krw=proceeds, forced_trim_proceeds_krw=p.forced_trim_proceeds,
                forced_trim_pnl_krw=p.forced_trim_pnl, total_realized_pnl_krw=total_pnl,
                gross_pct=p.gross_pct, net_pct=p.net_pct, cash_after_exit_krw=cash, skip_reason=''))
            record(ts, 'EXIT', p.symbol)
            continue

        gross_pct = float(r.pnl_pct)
        net_pct = gross_pct - ROUND_TRIP_FEE_PCT
        if len(open_pos) >= MAX_POSITIONS:
            skipped += 1
            ledger.append(dict(
                scenario=label, trade_id=tid, status='SKIPPED', symbol=str(r.symbol), source=str(r.source),
                entry_time=nt(r.entry_time), exit_time=nt(r.exit_time), original_stake_krw=0.0,
                remaining_final_proceeds_krw=0.0, forced_trim_proceeds_krw=0.0,
                forced_trim_pnl_krw=0.0, total_realized_pnl_krw=0.0,
                gross_pct=gross_pct, net_pct=net_pct, cash_after_exit_krw=np.nan,
                skip_reason='MAX_CONCURRENT_POSITIONS'))
            record(ts, 'SKIP', str(r.symbol))
            continue

        # If meaningful free cash exists, the new signal gets all of it.
        if cash <= CASH_EPS:
            eq = equity(ts)
            if len(open_pos) == 1:
                # 100% -> approximately 50/50.
                p = next(iter(open_pos.values()))
                desired_existing = eq * 0.50
                cur = market_value(p, ts)
                trim_position(p, max(0.0, cur - desired_existing), ts)
            elif len(open_pos) == 2:
                # Existing positions -> one-third each; released cash funds third.
                target = eq / 3.0
                # Trim only overweight holdings; never top up an underweight holding.
                for p in list(open_pos.values()):
                    cur = market_value(p, ts)
                    if cur > target + CASH_EPS:
                        trim_position(p, cur - target, ts)

        stake = cash
        if stake <= CASH_EPS:
            skipped += 1
            ledger.append(dict(
                scenario=label, trade_id=tid, status='SKIPPED', symbol=str(r.symbol), source=str(r.source),
                entry_time=nt(r.entry_time), exit_time=nt(r.exit_time), original_stake_krw=0.0,
                remaining_final_proceeds_krw=0.0, forced_trim_proceeds_krw=0.0,
                forced_trim_pnl_krw=0.0, total_realized_pnl_krw=0.0,
                gross_pct=gross_pct, net_pct=net_pct, cash_after_exit_krw=np.nan,
                skip_reason='NO_FREE_CASH_AFTER_REBALANCE'))
            record(ts, 'SKIP_NO_CASH', str(r.symbol))
            continue

        sym = n(r.symbol)
        entry_px = price_at(prices, sym, ts)
        shares = stake / entry_px
        cash = 0.0
        accepted.add(tid)
        open_pos[tid] = Pos(
            trade_id=tid, symbol=sym, source=str(r.source), entry_time=nt(r.entry_time),
            exit_time=nt(r.exit_time), entry_price=entry_px, shares=shares,
            original_stake=stake, gross_pct=gross_pct, net_pct=net_pct)
        max_concurrent = max(max_concurrent, len(open_pos))
        record(ts, 'ENTRY', f'{sym} stake={stake:.0f}')

    if open_pos:
        raise RuntimeError(f'{label}: {len(open_pos)} positions remained open')

    led = pd.DataFrame(ledger)
    acc = led[led.status == 'ACCEPTED'].copy() if len(led) else pd.DataFrame()
    wins = int((pd.to_numeric(acc.total_realized_pnl_krw, errors='coerce') > 0).sum()) if len(acc) else 0
    losses = int((pd.to_numeric(acc.total_realized_pnl_krw, errors='coerce') <= 0).sum()) if len(acc) else 0
    gp = float(pd.to_numeric(acc.total_realized_pnl_krw, errors='coerce').clip(lower=0).sum()) if len(acc) else 0.0
    gl = float((-pd.to_numeric(acc.total_realized_pnl_krw, errors='coerce').clip(upper=0)).sum()) if len(acc) else 0.0
    final = float(cash)
    summary = dict(
        scenario=label,
        available_signals=len(x),
        accepted_trades=len(acc),
        skipped_total=skipped,
        forced_trim_events=forced_trim_events,
        forced_trim_gross_krw=forced_trim_value,
        wins=wins,
        losses=losses,
        win_pct=(wins / len(acc) * 100.0 if len(acc) else 0.0),
        initial_balance_krw=INITIAL_CASH,
        final_balance_krw=final,
        account_profit_krw=final - INITIAL_CASH,
        account_return_pct=(final / INITIAL_CASH - 1.0) * 100.0,
        max_concurrent_positions=max_concurrent,
        mark_to_market_mdd_pct=max_dd,
        cash_pf=(gp / gl if gl > 0 else math.inf),
        first_entry=(x.entry_time.min() if len(x) else pd.NaT),
        last_exit=(x.exit_time.max() if len(x) else pd.NaT),
    )
    return summary, led, pd.DataFrame(evrows)


def main():
    if not REVISED_TRADES.exists():
        raise FileNotFoundError(f'{REVISED_TRADES} missing; run integrated Slow-turn validation first')

    print('Loading KR 1-minute prices for price-aware reallocation...', flush=True)
    prices = build_price_lookup()

    revised = pd.read_csv(REVISED_TRADES)
    if 'cut' not in revised.columns:
        raise ValueError(f'{REVISED_TRADES} has no cut column')
    revised['cut_num'] = pd.to_numeric(revised['cut'], errors='coerce')

    scenarios = []
    if OLD_TRADES.exists():
        scenarios.append(('OLD', pd.read_csv(OLD_TRADES)))
    for cut in (-0.15, -0.20, -0.30, -0.50):
        q = revised[np.isclose(revised.cut_num, cut, atol=1e-9)].copy()
        if len(q):
            scenarios.append((f'SLOW_{cut:.2f}', q))

    sums, leds, evs = [], [], []
    for label, df in scenarios:
        s, l, e = replay(df, label, prices)
        sums.append(s); leds.append(l); evs.append(e)

    summary = pd.DataFrame(sums)
    summary.to_csv(OUT_SUMMARY, index=False)
    if leds:
        pd.concat(leds, ignore_index=True).to_csv(OUT_LEDGER, index=False)
    if evs:
        pd.concat(evs, ignore_index=True).to_csv(OUT_EVENTS, index=False)

    print('\n=== KR DYNAMIC FULL-INVEST ACCOUNT REPLAY ===')
    print('Rule: cash -> next BUY 100%; if no cash, rebalance 1->2 near 50/50, 2->3 near thirds.')
    print('Normal exits create cash only; no automatic top-up of remaining positions.')
    print('Mid-trade forced trims use actual 1m close; untouched final economics use validated trade net_pct.')
    print('Extra forced-sell cost = 0.125%; MDD is minute-price mark-to-market at account events.\n')
    cols = ['scenario','available_signals','accepted_trades','skipped_total','forced_trim_events',
            'forced_trim_gross_krw','wins','losses','win_pct','final_balance_krw','account_profit_krw',
            'account_return_pct','max_concurrent_positions','mark_to_market_mdd_pct','cash_pf',
            'first_entry','last_exit']
    print(summary[cols].to_string(index=False, float_format=lambda v: f'{v:,.4f}'))
    print('\nWROTE', OUT_SUMMARY)
    print('WROTE', OUT_LEDGER)
    print('WROTE', OUT_EVENTS)


if __name__ == '__main__':
    main()
