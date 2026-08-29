from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_multi_symbol as multi
import tools.validate_engine5_v17c_opening_5m_hwm_sweep as sweep
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v19_prebuy_5m_1m_confirm as v19
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

FEE_RT_PCT = 0.25
DELAY = 2
TOP_N = 12


def finite(x):
    return h.finite(x)


def main():
    raw = load_data()
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    packed = v8.base.pack_exit_events(raw, base_cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    frames = base.build_cfg_frames(raw, cfg)
    f10 = {s: v10._refine_entry_frame(f) for s, f in frames.items()}
    scored = reweight(f10, cfg, 0.0)

    raw_entries = v8.pack_entry_events(scored)
    ev10 = sweep.filt_open(raw_entries)
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev_v17c, _, _ = v17b.build_v17b(ev16, scored, waits)
    micros = {str(sym).zfill(6): h.build_micro(bars, cfg) for sym, bars in raw.items()}
    ev_v18, _ = h.build_veto_stream(ev_v17c, micros)

    fast, diag = v19.build_v19_events(scored, micros, raw, DELAY)
    merged, _ = v19.merge_additive(ev_v18, fast)
    trades = multi.simulate_multi(packed, merged, states, v19.THRESHOLD).copy()
    trades['symbol'] = trades.symbol.astype(str).str.zfill(6)
    trades['entry_time'] = pd.to_datetime(trades.entry_time)
    trades['exit_time'] = pd.to_datetime(trades.exit_time)
    trades['gross_pnl_pct'] = pd.to_numeric(trades.pnl_pct, errors='coerce')
    trades['net_pnl_pct'] = trades['gross_pnl_pct'] - FEE_RT_PCT

    fast_keys = {
        (str(c[0]).zfill(6), pd.Timestamp(ts))
        for ts, cs in fast.items() for c in cs
    }
    trades['source'] = [
        'V19_FAST_ADD' if (s, pd.Timestamp(t)) in fast_keys else 'V18_BASE'
        for s, t in zip(trades.symbol, trades.entry_time)
    ]

    losers = trades[trades.net_pnl_pct <= 0].sort_values('net_pnl_pct').head(TOP_N).copy()
    print('=== V19 2M WORST NET LOSSES: ENTRY LATENCY DIAGNOSTIC ===')
    print('Fee assumption: 0.25% round-trip. V19_FAST_ADD and V18_BASE are separated.')
    cols = ['symbol','source','entry_time','exit_time','entry_price','gross_pnl_pct','net_pnl_pct','reason']
    print('\n=== WORST LOSSES ===')
    print(losers[cols].to_string(index=False))

    diag2 = diag.copy()
    if len(diag2):
        diag2['symbol'] = diag2.symbol.astype(str).str.zfill(6)
        diag2['ready_time'] = pd.to_datetime(diag2.ready_time)
        diag2['trigger_time'] = pd.to_datetime(diag2.trigger_time)

    for _, tr in losers.iterrows():
        sym = tr.symbol
        et = pd.Timestamp(tr.entry_time)
        print('\n' + '=' * 120)
        print(f"CASE {sym} source={tr.source} entry={et} price={tr.entry_price:.4f} gross={tr.gross_pnl_pct:+.4f}% net={tr.net_pnl_pct:+.4f}% reason={tr.reason}")

        ready_time = pd.NaT
        if tr.source == 'V19_FAST_ADD' and len(diag2):
            qd = diag2[(diag2.symbol == sym) & (diag2.trigger_time == et)]
            if len(qd):
                r = qd.iloc[0]
                ready_time = pd.Timestamp(r.ready_time)
                print(f"FAST_TIMING ready={ready_time} entry={et} delay_min={(et-ready_time).total_seconds()/60.0:.1f}")
                print(
                    'READY_5M '
                    f"score={finite(r.score):.3f} trend_up={bool(r.trend_up)} "
                    f"macd_ctx={bool(r.gate_macd_context)} macd_rising={bool(r.gate_macd_rising)} "
                    f"rsi_persist={bool(r.gate_rsi_persistent)} spread={finite(r.macd_spread_5m):.6f} "
                    f"gap_delta={finite(r.macd_gap_delta_5m):.6f} rsi_slope={finite(r.rsi_slope_5m):.6f}"
                )
            else:
                print('FAST_TIMING diagnostic row not found')
        else:
            print('FAST_TIMING N/A (V18 base entry)')

        f5 = scored[sym].copy()
        f5['time'] = pd.to_datetime(f5.time)
        q5 = f5[(f5.time >= et - pd.Timedelta(minutes=20)) & (f5.time <= et + pd.Timedelta(minutes=5))].copy()
        rows5 = []
        for _, r in q5.iterrows():
            rows5.append({
                'time': r.time,
                'close': finite(r.get('close')),
                'score': finite(r.get('entry_score')),
                'trend': bool(r.get('trend_up', False)),
                'mctx': bool(r.get('gate_macd_context', False)),
                'mrise': bool(r.get('gate_macd_rising', False)),
                'rpersist': bool(r.get('gate_rsi_persistent', False)),
                'mspread': finite(r.get('macd_slope_spread')),
                'gap_d': finite(r.get('macd_gap_delta')),
                'rsi_sl': finite(r.get('rsi_slope')),
                'full_buy': bool(r.get('entry_gate', False)),
                'prebuy': bool(v19.prebuy_5m(r)),
            })
        d5 = pd.DataFrame(rows5)
        print('\n-- 5M BEFORE/AROUND ENTRY --')
        print(d5.to_string(index=False) if len(d5) else 'NONE')

        if len(d5):
            prior = d5[d5.time <= et]
            momentum = prior[(prior.mspread > 0) & (prior.gap_d > 0) & (prior.rsi_sl > 0)]
            if len(momentum):
                first_m = pd.Timestamp(momentum.iloc[0].time)
                print(f"FIRST_5M_ALL_POSITIVE_IN_WINDOW={first_m} -> entry_lag={(et-first_m).total_seconds()/60.0:.1f}m")
            pre = prior[prior.prebuy]
            if len(pre):
                first_p = pd.Timestamp(pre.iloc[0].time)
                print(f"FIRST_5M_PREBUY_IN_WINDOW={first_p} -> entry_lag={(et-first_p).total_seconds()/60.0:.1f}m")

        m = micros[sym].copy()
        m['time'] = pd.to_datetime(m.time)
        q1 = m[(m.time >= et - pd.Timedelta(minutes=8)) & (m.time <= et + pd.Timedelta(minutes=2))].copy()
        rows1 = []
        for _, r in q1.iterrows():
            rows1.append({
                'time': r.time,
                'close': finite(r.get('close')),
                'macd': finite(r.get('macd_1m')),
                'signal': finite(r.get('signal_1m')),
                'gap': finite(r.get('macd_gap_1m')),
                'macd_sl': finite(r.get('macd_slope_1m')),
                'gap_d': finite(r.get('macd_gap_delta_1m')),
                'rsi': finite(r.get('rsi_1m')),
                'rsi_sl': finite(r.get('rsi_slope_1m')),
                'vol20': finite(r.get('vol_ratio20')),
                'confirm': bool(v19.final_1m_confirm(r)),
            })
        d1 = pd.DataFrame(rows1)
        print('\n-- 1M BEFORE/AROUND ENTRY --')
        print(d1.to_string(index=False) if len(d1) else 'NONE')

        if len(d1):
            prior1 = d1[d1.time <= et]
            confirms = prior1[prior1.confirm]
            if len(confirms):
                first_c = pd.Timestamp(confirms.iloc[0].time)
                print(f"FIRST_1M_CONFIRM_IN_8M_WINDOW={first_c} -> entry_lag={(et-first_c).total_seconds()/60.0:.1f}m")

    print('\n=== INTERPRETATION GUIDE ===')
    print('1) FAST delay 0-2m인데 FIRST_5M_ALL_POSITIVE가 훨씬 이전이면 5m PREBUY가 늦은 것.')
    print('2) READY는 이른데 entry가 2m 뒤이고 그 사이 가격/RSI/MACD가 과열되면 1m confirm이 늦은 것.')
    print('3) READY/entry 모두 이른데 손실이면 latency보다 후보 품질 또는 exit 문제.')
    print('4) V18_BASE 실패는 V19 fast-path 문제로 계산하지 말 것.')


if __name__ == '__main__':
    main()
