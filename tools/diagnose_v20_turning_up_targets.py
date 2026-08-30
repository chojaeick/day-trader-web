from __future__ import annotations

from dataclasses import replace
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_regime_transition as rt
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

TARGETS = [
    ('950160', pd.Timestamp('2026-08-14').date(), '10:30', '11:40'),
    ('950260', pd.Timestamp('2026-08-19').date(), '13:00', '13:55'),
]


def norm_sym(x):
    return str(x).zfill(6)


def main():
    raw = {norm_sym(k): v for k, v in load_data().items()}
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    # Completed 5m frames are cheap and give the strength baseline used by V20.
    frames0 = base.build_cfg_frames(raw, cfg)
    f10 = {norm_sym(s): v10._refine_entry_frame(f) for s, f in frames0.items()}
    scored0 = reweight(f10, cfg, 0.0)
    scored = {norm_sym(s): f for s, f in scored0.items()}
    completed = {s: rt.add_completed_strength(f) for s, f in scored.items()}

    print('=== V20 TURNING_UP TARGET DIAGNOSTIC ===', flush=True)
    print('Goal: detect slope recovery while mid_slope8 is still <= 0; no zero-cross requirement.', flush=True)

    for sym, day, t0, t1 in TARGETS:
        print(f'\n===== {sym} {day} {t0}-{t1} =====', flush=True)
        if sym not in raw or sym not in completed:
            print('MISSING SYMBOL', flush=True)
            continue

        b = raw[sym].copy()
        b['time'] = pd.to_datetime(b['time'])
        day_start = pd.Timestamp(f'{day} 00:00:00')
        day_end = day_start + pd.Timedelta(days=1)
        # Keep historical bars for indicators, but only evaluate provisional bars in the target window.
        target_mask = (b.time >= pd.Timestamp(f'{day} {t0}')) & (b.time <= pd.Timestamp(f'{day} {t1}'))
        target_times = set(pd.to_datetime(b.loc[target_mask, 'time']))
        if not target_times:
            print('NO RAW BARS IN WINDOW', flush=True)
            continue

        # Reuse the exact provisional builder, then narrow immediately. This is intentionally
        # a focused diagnostic; the next full validator should persist/cache these features.
        pf = rt.build_provisional_5m(b, cfg)
        pf = rt.add_provisional_strength(pf, completed[sym])
        pf = pf[pf.time.isin(target_times)].copy().sort_values('time').reset_index(drop=True)
        if pf.empty:
            print('NO PROVISIONAL ROWS', flush=True)
            continue

        # What matters now is acceleration of the slope, not whether the slope has crossed zero.
        pf['slope_d1'] = pd.to_numeric(pf.mid_slope8, errors='coerce').diff()
        pf['slope_d2'] = pf['slope_d1'].diff()
        pf['gap_d1'] = pd.to_numeric(pf.gap, errors='coerce').diff()
        pf['raw_pass'] = pd.to_numeric(pf.gap_delta, errors='coerce') >= rt.RAW_MIN
        pf['rel_pass'] = pd.to_numeric(pf.strength_rel, errors='coerce') >= rt.REL_MIN
        pf['momentum_up'] = ((pd.to_numeric(pf.macd_slope, errors='coerce') > 0) &
                             (pd.to_numeric(pf.rsi_slope, errors='coerce') > 0))
        pf['turning_early'] = ((pd.to_numeric(pf.mid_slope8, errors='coerce') <= 0) &
                               (pf['slope_d1'] > 0) & (pf['slope_d2'] >= 0) &
                               pf['raw_pass'] & pf['rel_pass'] & pf['momentum_up'])

        cols = ['time','close','mid_slope8','slope_d1','slope_d2','gap','gap_delta','strength_rel',
                'macd_slope','rsi','rsi_slope','golden','raw_pass','rel_pass','momentum_up','turning_early']
        print(pf[cols].to_string(index=False), flush=True)

        hits = pf[pf.turning_early]
        print('\nEARLY TURNING HITS:', flush=True)
        if hits.empty:
            print('NONE', flush=True)
        else:
            print(hits[cols].to_string(index=False), flush=True)

        m = h.build_micro(b, cfg)
        if not hits.empty:
            first = pd.Timestamp(hits.iloc[0].time)
            q = m[(m.time >= first - pd.Timedelta(minutes=5)) &
                  (m.time <= first + pd.Timedelta(minutes=3))].copy()
            mcols = ['time','close','macd_gap_1m','macd_gap_delta_1m','macd_slope_1m','rsi_1m','rsi_slope_1m']
            print('\n1M AROUND FIRST EARLY HIT:', flush=True)
            print(q[[c for c in mcols if c in q.columns]].to_string(index=False), flush=True)


if __name__ == '__main__':
    main()
