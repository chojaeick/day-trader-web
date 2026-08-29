from __future__ import annotations

from dataclasses import replace
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_breakout_first10_hwm1pct as v17c
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

START_CAPITAL = 10_000_000.0
POSITION_FRACTION = 0.50
THRESHOLD = 50
OPEN_MINUTE = 9 * 60 + 10


def filt_open(ev):
    return {
        ts: rows for ts, rows in ev.items()
        if pd.Timestamp(ts).hour * 60 + pd.Timestamp(ts).minute >= OPEN_MINUTE
    }


def main():
    raw = load_data()
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    packed_exits = v8.base.pack_exit_events(raw, base_cfg)
    state_events = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    raw_frames = base.build_cfg_frames(raw, cfg)
    f10 = {s: v10._refine_entry_frame(f) for s, f in raw_frames.items()}
    scored = reweight(f10, cfg, 0.0)
    ev10 = filt_open(v8.pack_entry_events(scored))
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev17b, _, _ = v17b.build_v17b(ev16, scored, waits)
    trades = v17c.simulate_unconditional_hwm(packed_exits, ev17b, state_events, THRESHOLD).copy()

    if trades.empty:
        print('NO_TRADES')
        return

    time_col = next((c for c in ['entry_time', 'time', 'buy_time'] if c in trades.columns), None)
    if time_col is not None:
        trades = trades.sort_values(time_col).reset_index(drop=True)
    else:
        trades = trades.reset_index(drop=True)

    equity = START_CAPITAL
    peak = equity
    max_dd = 0.0
    max_dd_krw = 0.0
    rows = []

    for i, r in trades.iterrows():
        pnl_pct = float(r['pnl_pct'])
        before = equity
        invested = before * POSITION_FRACTION
        pnl_krw = invested * pnl_pct / 100.0
        equity = before + pnl_krw
        peak = max(peak, equity)
        dd = (equity / peak - 1.0) * 100.0
        dd_krw = equity - peak
        if dd < max_dd:
            max_dd = dd
            max_dd_krw = dd_krw
        rows.append({
            'trade_no': i + 1,
            'entry_time': r[time_col] if time_col is not None else pd.NaT,
            'symbol': str(r.get('symbol', r.get('code', r.get('ticker', '')))).zfill(6),
            'pnl_pct_trade': pnl_pct,
            'equity_before': before,
            'invested_50pct': invested,
            'pnl_krw': pnl_krw,
            'equity_after': equity,
            'drawdown_pct': dd,
        })

    curve = pd.DataFrame(rows)
    wins = int((pd.to_numeric(trades['pnl_pct']) > 0).sum())
    losses = int((pd.to_numeric(trades['pnl_pct']) <= 0).sum())
    final_return = (equity / START_CAPITAL - 1.0) * 100.0
    profit = equity - START_CAPITAL
    hit_12m = bool(equity >= 12_000_000)
    first_12m = curve[curve['equity_after'] >= 12_000_000].head(1)

    print('=== ENGINE5 V17C EQUITY SIMULATION : 50% POSITION ===')
    print(f'START_CAPITAL={START_CAPITAL:,.0f} KRW')
    print(f'POSITION_FRACTION={POSITION_FRACTION:.0%} of current equity per trade')
    print(f'TRADES={len(trades)} WINS={wins} LOSSES={losses}')
    if time_col is not None:
        print(f'PERIOD={pd.Timestamp(trades[time_col].min())} -> {pd.Timestamp(trades[time_col].max())}')
    print(f'FINAL_EQUITY={equity:,.0f} KRW')
    print(f'PROFIT={profit:+,.0f} KRW')
    print(f'CUM_RETURN={final_return:+.2f}%')
    print(f'MAX_DRAWDOWN={max_dd:.2f}% ({max_dd_krw:,.0f} KRW from peak)')
    print(f'REACHED_12M={hit_12m}')
    if not first_12m.empty:
        x = first_12m.iloc[0]
        print(f'FIRST_12M_AT_TRADE={int(x.trade_no)} TIME={x.entry_time} EQUITY={x.equity_after:,.0f} KRW')
    print('\n--- LAST 10 TRADES ---')
    print(curve.tail(10).to_string(index=False))

    out = '/home/ubuntu/day-trader-api/engine5_v16_full_validation/v17c_equity_50pct.csv'
    curve.to_csv(out, index=False)
    print(f'\n[CSV] {out}')


if __name__ == '__main__':
    main()
