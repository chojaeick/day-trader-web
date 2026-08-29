from __future__ import annotations

from dataclasses import replace

import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data, summary

OPEN_MINUTE = 9 * 60 + 10

# Previously strong V10/V11 neighborhood configurations. This is deliberately
# a small robustness panel, not a new 360-run optimizer.
CASES = [
    ('S_M2.0_R1.5', 50),
    ('S_M1.5_R1.0', 55),
    ('S_M1.0_R1.0', 55),
    ('S_M1.0_R1.5', 50),
    ('C_A10_V10_O10', 55),
    ('C_A10_V10_O10', 50),
    ('C_A5_V10_O10', 50),
    ('W_M30_R30', 55),
    ('S_M3.0_R1.0', 65),
    ('S_M3.0_R3.0', 70),
]


def cfg_by_name(name: str) -> DoubleBollingerEngine5Config:
    b = DoubleBollingerEngine5Config()
    if name.startswith('S_M'):
        a, r = name.replace('S_M', '').split('_R')
        return replace(b, macd_slope_spread_full_ratio=float(a), rsi_slope_full_ratio=float(r))
    if name.startswith('W_M'):
        a, r = name.replace('W_M', '').split('_R')
        return replace(b, w_macd_gap=float(a), w_rsi_state=float(r))
    if name.startswith('C_A'):
        p = name.split('_')
        accel = float(p[1][1:])
        vol = float(p[2][1:])
        outer = float(p[3][1:])
        return replace(b, w_rsi_accel=accel, w_volume=vol, w_outer_expand=outer)
    if name == 'BASE':
        return b
    raise ValueError(name)


def filter_open(ev):
    return {
        ts: rows for ts, rows in ev.items()
        if pd.Timestamp(ts).hour * 60 + pd.Timestamp(ts).minute >= OPEN_MINUTE
    }


def metrics(name, trades, collisions):
    s = summary(name, trades)
    return {
        'trades': len(trades),
        'wins': int((trades.pnl_pct > 0).sum()),
        'losses': int((trades.pnl_pct <= 0).sum()),
        'win': float(s['win_rate']),
        'avg': float(s['avg_pct']),
        'gross': float(s['gross_pct']),
        'pf': float(s['pf']),
        'collisions': int(collisions),
    }


def run_sim(name, packed_exits, state_events, events, th):
    t, c = v8.v7.simulate_v7(packed_exits, events, state_events, th)
    return t, metrics(name, t, c)


def main():
    raw = load_data()
    base_cfg = DoubleBollingerEngine5Config()
    packed_exits = v8.base.pack_exit_events(raw, base_cfg)
    state_events = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))

    rows = []
    all_waits = []
    print('=== V16 ROBUSTNESS PANEL ===')
    print('Compare exact V10+09:10 baseline vs same config with V16 WAIT->1m reacceleration. No parameter sweep.\n')

    for cfg_name, th in CASES:
        cfg = cfg_by_name(cfg_name)
        raw_frames = base.build_cfg_frames(raw, cfg)
        f10 = {sym: v10._refine_entry_frame(f) for sym, f in raw_frames.items()}
        scored = reweight(f10, cfg, 0.0)
        ev10 = filter_open(v8.pack_entry_events(scored))
        ev16, waits = v16.build_wait_events(ev10, raw, cfg, require_better_price=False)

        t10, m10 = run_sim('V10', packed_exits, state_events, ev10, th)
        t16, m16 = run_sim('V16', packed_exits, state_events, ev16, th)

        row = {
            'config': cfg_name,
            'th': th,
            'v10_trades': m10['trades'], 'v16_trades': m16['trades'],
            'v10_win': m10['win'], 'v16_win': m16['win'], 'd_win': m16['win'] - m10['win'],
            'v10_avg': m10['avg'], 'v16_avg': m16['avg'], 'd_avg': m16['avg'] - m10['avg'],
            'v10_gross': m10['gross'], 'v16_gross': m16['gross'], 'd_gross': m16['gross'] - m10['gross'],
            'v10_pf': m10['pf'], 'v16_pf': m16['pf'], 'd_pf': m16['pf'] - m10['pf'],
            'waits': len(waits),
            'reaccel': int((waits.status == 'REACCEL_ENTRY').sum()) if len(waits) else 0,
            'no_reaccel': int((waits.status == 'NO_REACCEL').sum()) if len(waits) else 0,
        }
        rows.append(row)
        if len(waits):
            w = waits.copy()
            w['config'] = cfg_name
            w['th'] = th
            all_waits.append(w)

        print(
            f"{cfg_name:16s} th={th:2d} | "
            f"V10 {m10['trades']:3d}t win={m10['win']:5.2f} avg={m10['avg']:+.4f} gross={m10['gross']:+.4f} pf={m10['pf']:.3f} | "
            f"V16 {m16['trades']:3d}t win={m16['win']:5.2f} avg={m16['avg']:+.4f} gross={m16['gross']:+.4f} pf={m16['pf']:.3f} | "
            f"DELTA win={row['d_win']:+.2f} gross={row['d_gross']:+.4f} pf={row['d_pf']:+.3f} waits={row['waits']} re={row['reaccel']} no={row['no_reaccel']}"
        )

    board = pd.DataFrame(rows)
    print('\n=== ROBUSTNESS SUMMARY ===')
    print('configs=', len(board))
    print('win_improved=', int((board.d_win > 0).sum()), 'same=', int((board.d_win == 0).sum()), 'worse=', int((board.d_win < 0).sum()))
    print('gross_improved=', int((board.d_gross > 0).sum()), 'same=', int((board.d_gross == 0).sum()), 'worse=', int((board.d_gross < 0).sum()))
    print('pf_improved=', int((board.d_pf > 0).sum()), 'same=', int((board.d_pf == 0).sum()), 'worse=', int((board.d_pf < 0).sum()))
    print('median_d_win=', round(float(board.d_win.median()), 4))
    print('median_d_gross=', round(float(board.d_gross.median()), 4))
    print('median_d_pf=', round(float(board.d_pf.median()), 4))

    print('\n=== SORTED BY V16 PF ===')
    cols = ['config','th','v10_trades','v16_trades','v10_win','v16_win','d_win','v10_gross','v16_gross','d_gross','v10_pf','v16_pf','d_pf','waits','reaccel','no_reaccel']
    print(board.sort_values(['v16_pf','v16_win','v16_gross'], ascending=False)[cols].round(4).to_string(index=False))

    target_rows = []
    if all_waits:
        aw = pd.concat(all_waits, ignore_index=True)
        target_rows = aw[(aw.symbol.astype(str).str.zfill(6) == '484810') & (pd.to_datetime(aw.signal_time) == pd.Timestamp('2026-08-11 09:10:00+09:00'))]
    print('\n=== TARGET 484810 2026-08-11 09:10 ACROSS PANEL ===')
    if isinstance(target_rows, pd.DataFrame) and len(target_rows):
        print(target_rows[['config','th','signal_time','signal_price','status','delayed_time','delayed_price']].to_string(index=False))
    else:
        print('target not present in WAIT set for this panel')

    out = '/home/ubuntu/day-trader-api/dbb_engine5_v16_robustness_panel.csv'
    board.to_csv(out, index=False)
    print('\n[CSV]', out)


if __name__ == '__main__':
    main()
