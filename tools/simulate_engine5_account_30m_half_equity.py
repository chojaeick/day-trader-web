from __future__ import annotations

"""Compound account-level replay for Korean Engine5 integrated validation.

Rules under test
----------------
- Initial equity: KRW 30,000,000.
- At each accepted entry, target stake = 50% of realized account equity immediately before entry.
- No leverage/margin.
- Maximum two concurrent positions.
- If one position is already open, the second order uses min(50% target, free cash).
- If two positions are already open, later signals are skipped until an exit frees a slot.
- Exits are processed before entries at the same timestamp.
- Trade pnl_pct is treated as gross of the validation round-trip fee; subtract 0.25 percentage point.
- Capital stays locked until final exit; partial-TP cash release is not reconstructed.
- Drawdown is realized-equity drawdown (open positions carried at cost), not intratrade mark-to-market MDD.

The exact Slow-turn normalized-slope cutoff remains unfrozen, so OLD and all validated
-0.15/-0.20/-0.30/-0.50 scenarios are replayed side by side.
"""

from dataclasses import dataclass
from pathlib import Path
import math
import numpy as np
import pandas as pd

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
REVISED_TRADES = OUT_DIR / 'integrated_slow_turn_rearm_deep_trades.csv'
OLD_TRADES = OUT_DIR / 'integrated_full_history_trades.csv'
OUT_SUMMARY = OUT_DIR / 'account_30m_half_equity_summary.csv'
OUT_LEDGER = OUT_DIR / 'account_30m_half_equity_ledger.csv'
OUT_EQUITY = OUT_DIR / 'account_30m_half_equity_equity_events.csv'

INITIAL_CASH = 30_000_000.0
STAKE_FRACTION = 0.50
MAX_POSITIONS = 2
FEE_RT_PCT = 0.25
MIN_STAKE_KRW = 1.0


@dataclass
class OpenPos:
    trade_id: int
    symbol: str
    source: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    stake: float
    gross_pct: float
    net_pct: float


def _prepare(df: pd.DataFrame, label: str) -> pd.DataFrame:
    x = df.copy()
    x['entry_time'] = pd.to_datetime(x['entry_time'])
    x['exit_time'] = pd.to_datetime(x['exit_time'])
    x['pnl_pct'] = pd.to_numeric(x['pnl_pct'], errors='coerce')
    x = x.dropna(subset=['entry_time', 'exit_time', 'pnl_pct']).copy()
    x['symbol'] = x['symbol'].astype(str).str.zfill(6)
    if 'source' not in x.columns:
        x['source'] = 'UNKNOWN'
    x['scenario'] = label
    x = x.sort_values(['entry_time', 'exit_time', 'symbol']).reset_index(drop=True)
    x['trade_id'] = np.arange(1, len(x) + 1)
    return x


def replay(df: pd.DataFrame, label: str):
    x = _prepare(df, label)
    events = []
    rows = {int(r.trade_id): r for _, r in x.iterrows()}
    for _, r in x.iterrows():
        events.append((pd.Timestamp(r.exit_time), 0, int(r.trade_id)))
        events.append((pd.Timestamp(r.entry_time), 1, int(r.trade_id)))
    events.sort(key=lambda z: (z[0], z[1], z[2]))

    cash = INITIAL_CASH
    open_pos: dict[int, OpenPos] = {}
    accepted: set[int] = set()
    ledger = []
    equity_events = []
    peak_realized_equity = INITIAL_CASH
    max_realized_dd = 0.0
    max_concurrent = 0
    gross_profit_krw = 0.0
    gross_loss_krw = 0.0
    total_stake_krw = 0.0
    min_accepted_stake = math.inf
    max_accepted_stake = 0.0

    def realized_equity() -> float:
        return cash + sum(p.stake for p in open_pos.values())

    def record_equity(ts, kind):
        nonlocal peak_realized_equity, max_realized_dd
        eq = realized_equity()
        peak_realized_equity = max(peak_realized_equity, eq)
        dd = (eq / peak_realized_equity - 1.0) * 100.0 if peak_realized_equity > 0 else 0.0
        max_realized_dd = min(max_realized_dd, dd)
        equity_events.append(dict(
            scenario=label, time=ts, event=kind, cash_krw=cash,
            locked_cost_krw=sum(p.stake for p in open_pos.values()),
            realized_equity_krw=eq, open_positions=len(open_pos),
            realized_drawdown_pct=dd,
        ))

    record_equity(x.entry_time.min() if len(x) else pd.Timestamp('1970-01-01'), 'START')

    for ts, priority, tid in events:
        r = rows[tid]
        if priority == 0:
            if tid not in accepted or tid not in open_pos:
                continue
            p = open_pos.pop(tid)
            pnl_krw = p.stake * p.net_pct / 100.0
            proceeds = p.stake + pnl_krw
            cash += proceeds
            if pnl_krw >= 0:
                gross_profit_krw += pnl_krw
            else:
                gross_loss_krw += -pnl_krw
            ledger.append(dict(
                scenario=label, trade_id=tid, status='ACCEPTED', symbol=p.symbol, source=p.source,
                entry_time=p.entry_time, exit_time=p.exit_time, stake_krw=p.stake,
                gross_pct=p.gross_pct, net_pct=p.net_pct, pnl_krw=pnl_krw,
                cash_after_exit_krw=cash, skip_reason='',
            ))
            record_equity(ts, 'EXIT')
            continue

        gross_pct = float(r.pnl_pct)
        net_pct = gross_pct - FEE_RT_PCT

        if len(open_pos) >= MAX_POSITIONS:
            ledger.append(dict(
                scenario=label, trade_id=tid, status='SKIPPED', symbol=str(r.symbol), source=str(r.source),
                entry_time=pd.Timestamp(r.entry_time), exit_time=pd.Timestamp(r.exit_time), stake_krw=0.0,
                gross_pct=gross_pct, net_pct=net_pct, pnl_krw=0.0, cash_after_exit_krw=np.nan,
                skip_reason='MAX_CONCURRENT_POSITIONS',
            ))
            record_equity(ts, 'SKIP_MAX_POSITIONS')
            continue

        eq_before = realized_equity()
        target_stake = eq_before * STAKE_FRACTION
        stake = min(target_stake, cash)
        if stake < MIN_STAKE_KRW:
            ledger.append(dict(
                scenario=label, trade_id=tid, status='SKIPPED', symbol=str(r.symbol), source=str(r.source),
                entry_time=pd.Timestamp(r.entry_time), exit_time=pd.Timestamp(r.exit_time), stake_krw=0.0,
                gross_pct=gross_pct, net_pct=net_pct, pnl_krw=0.0, cash_after_exit_krw=np.nan,
                skip_reason='INSUFFICIENT_FREE_CASH',
            ))
            record_equity(ts, 'SKIP_CASH')
            continue

        cash -= stake
        accepted.add(tid)
        open_pos[tid] = OpenPos(
            tid, str(r.symbol), str(r.source), pd.Timestamp(r.entry_time), pd.Timestamp(r.exit_time),
            stake, gross_pct, net_pct,
        )
        total_stake_krw += stake
        min_accepted_stake = min(min_accepted_stake, stake)
        max_accepted_stake = max(max_accepted_stake, stake)
        max_concurrent = max(max_concurrent, len(open_pos))
        record_equity(ts, 'ENTRY')

    if open_pos:
        raise RuntimeError(f'{label}: {len(open_pos)} positions remained open after replay')

    led = pd.DataFrame(ledger)
    eq = pd.DataFrame(equity_events)
    acc = led[led.status == 'ACCEPTED'].copy() if len(led) else pd.DataFrame()
    skipped = led[led.status == 'SKIPPED'].copy() if len(led) else pd.DataFrame()
    final_balance = float(cash)
    ret_pct = (final_balance / INITIAL_CASH - 1.0) * 100.0
    wins = int((pd.to_numeric(acc.net_pct, errors='coerce') > 0).sum()) if len(acc) else 0
    losses = int((pd.to_numeric(acc.net_pct, errors='coerce') <= 0).sum()) if len(acc) else 0
    pf_cash = gross_profit_krw / gross_loss_krw if gross_loss_krw > 0 else math.inf

    skip_pos = int((skipped.skip_reason == 'MAX_CONCURRENT_POSITIONS').sum()) if len(skipped) else 0
    skip_cash = int((skipped.skip_reason == 'INSUFFICIENT_FREE_CASH').sum()) if len(skipped) else 0

    summary = dict(
        scenario=label,
        available_signals=len(x),
        accepted_trades=len(acc),
        skipped_total=len(skipped),
        skipped_max_positions=skip_pos,
        skipped_cash=skip_cash,
        wins=wins,
        losses=losses,
        win_pct=(wins / len(acc) * 100.0 if len(acc) else 0.0),
        initial_balance_krw=INITIAL_CASH,
        final_balance_krw=final_balance,
        account_profit_krw=final_balance - INITIAL_CASH,
        account_return_pct=ret_pct,
        max_concurrent_positions=max_concurrent,
        realized_equity_mdd_pct=max_realized_dd,
        cash_pf=pf_cash,
        avg_stake_krw=(total_stake_krw / len(acc) if len(acc) else 0.0),
        min_stake_krw=(min_accepted_stake if len(acc) else 0.0),
        max_stake_krw=(max_accepted_stake if len(acc) else 0.0),
        first_entry=(x.entry_time.min() if len(x) else pd.NaT),
        last_exit=(x.exit_time.max() if len(x) else pd.NaT),
    )
    return summary, led, eq


def main():
    if not REVISED_TRADES.exists():
        raise FileNotFoundError(f'{REVISED_TRADES} missing; run integrated Slow-turn validation first')

    revised = pd.read_csv(REVISED_TRADES)
    if 'cut' not in revised.columns:
        raise ValueError(f'{REVISED_TRADES} has no cut column')

    scenarios: list[tuple[str, pd.DataFrame]] = []
    if OLD_TRADES.exists():
        scenarios.append(('OLD', pd.read_csv(OLD_TRADES)))

    revised['cut_num'] = pd.to_numeric(revised['cut'], errors='coerce')
    for cut in (-0.15, -0.20, -0.30, -0.50):
        q = revised[np.isclose(revised.cut_num, cut, atol=1e-9)].copy()
        if len(q):
            scenarios.append((f'SLOW_{cut:.2f}', q))

    summaries, ledgers, equities = [], [], []
    for label, df in scenarios:
        s, l, e = replay(df, label)
        summaries.append(s)
        ledgers.append(l)
        equities.append(e)

    summary = pd.DataFrame(summaries)
    summary.to_csv(OUT_SUMMARY, index=False)
    if ledgers:
        pd.concat(ledgers, ignore_index=True).to_csv(OUT_LEDGER, index=False)
    if equities:
        pd.concat(equities, ignore_index=True).to_csv(OUT_EQUITY, index=False)

    print('\n=== KR ACCOUNT REPLAY: 30M INITIAL / 50% EQUITY PER ENTRY / MAX 2 POSITIONS ===')
    print('Target stake = 50% of realized account equity immediately before each entry.')
    print('No leverage; second concurrent order is capped by remaining free cash.')
    print('Exits before entries at same timestamp; fee = 0.25 percentage point per completed trade.')
    print('Capital locked until final exit; partial-TP cash release not reconstructed.')
    print('MDD is realized-equity MDD, not intratrade mark-to-market MDD.\n')
    cols = [
        'scenario','available_signals','accepted_trades','skipped_total','skipped_max_positions','skipped_cash',
        'wins','losses','win_pct','final_balance_krw','account_profit_krw','account_return_pct',
        'max_concurrent_positions','realized_equity_mdd_pct','cash_pf','avg_stake_krw','min_stake_krw',
        'max_stake_krw','first_entry','last_exit',
    ]
    print(summary[cols].to_string(index=False, float_format=lambda v: f'{v:,.4f}'))

    print('\nWROTE', OUT_SUMMARY)
    print('WROTE', OUT_LEDGER)
    print('WROTE', OUT_EQUITY)


if __name__ == '__main__':
    main()
