from __future__ import annotations

import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_macd_strength as ms
import tools.validate_engine5_v20_regime_transition as rt
import tools.validate_engine5_integrated_full_history as integ
from dataclasses import replace
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

TARGETS = {
    ('466100', pd.Timestamp('2026-08-14 09:15:00', tz='Asia/Seoul')),
    ('122630', pd.Timestamp('2026-08-21 09:40:00', tz='Asia/Seoul')),
}


def n(x): return str(x).zfill(6)


def main():
    raw = {n(k): v for k, v in load_data().items()}
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)
    packed = v8.base.pack_exit_events(raw, base_cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    frames0 = base.build_cfg_frames(raw, cfg)
    f10 = {n(s): v10._refine_entry_frame(f) for s, f in frames0.items()}
    scored = {n(s): f for s, f in reweight(f10, cfg, 0.0).items()}
    strength = {s: ms.add_strength(f) for s, f in scored.items()}
    completed = {s: rt.add_completed_strength(f) for s, f in scored.items()}
    micros = {s: h.build_micro(raw[s], cfg) for s in raw}
    tagged = integ.build_sources(raw, cfg, scored, strength, completed, micros)
    trades = integ.simulate(packed, states, tagged).copy()
    trades['symbol'] = trades.symbol.astype(str).str.zfill(6)
    trades['entry_time'] = pd.to_datetime(trades.entry_time)

    rows = []
    for sym, ts in TARGETS:
        q = trades[(trades.symbol == sym) & (trades.entry_time == ts)]
        if q.empty:
            print('NOT FOUND', sym, ts)
            continue
        r = q.iloc[0]
        print('\n===', sym, ts, '===')
        print(r.to_string())
        rows.append(r)

    if rows:
        out = pd.DataFrame(rows)
        cols = [c for c in ['symbol','source','entry_time','entry_price','exit_time','exit_price','pnl_pct','reason','tp1_done','tp1_price','stop_price','remaining'] if c in out.columns]
        print('\n=== COMPACT ===')
        print(out[cols].to_string(index=False))

if __name__ == '__main__':
    main()
