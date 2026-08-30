from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import pickle

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_regime_transition as rt
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
CACHE_DIR = OUT_DIR / 'v20_transition_cache'
REL_MIN = 1.45
RAW_MINS = [20.0, 30.0, 40.0]
SLOPE_LOOKBACKS = [3, 5]
TARGETS = [
    ('950160', pd.Timestamp('2026-08-14').date(), '10:30', '11:40'),
    ('950260', pd.Timestamp('2026-08-19').date(), '13:00', '13:55'),
]


def norm_sym(x):
    return str(x).zfill(6)


def with_tz(ts_text: str, series: pd.Series) -> pd.Timestamp:
    ts = pd.Timestamp(ts_text)
    tz = getattr(series.dt, 'tz', None)
    return ts.tz_localize(tz) if tz is not None and ts.tzinfo is None else ts


def add_structure_features(pf: pd.DataFrame, m: pd.DataFrame) -> pd.DataFrame:
    z = pf.copy().sort_values('time').reset_index(drop=True)
    z['time'] = pd.to_datetime(z['time'])

    mm = m.copy().sort_values('time').reset_index(drop=True)
    mm['time'] = pd.to_datetime(mm['time'])
    close = pd.to_numeric(mm['close'], errors='coerce')
    high = pd.to_numeric(mm['high'], errors='coerce') if 'high' in mm else close
    low = pd.to_numeric(mm['low'], errors='coerce') if 'low' in mm else close

    # Box structure: prior 10 one-minute bars only, so the current bar cannot define its own breakout.
    mm['box_high_10'] = high.shift(1).rolling(10, min_periods=6).max()
    mm['box_low_10'] = low.shift(1).rolling(10, min_periods=6).min()
    mm['box_width_pct'] = (mm['box_high_10'] / mm['box_low_10'] - 1.0) * 100.0
    mm['box_break'] = close > mm['box_high_10']

    # V/pullback structure: a local low exists, price has already rebounded, then a shallow pullback
    # holds above that low and the current one-minute close turns upward again.
    mm['local_low_8'] = low.shift(1).rolling(8, min_periods=5).min()
    mm['recent_high_4'] = high.shift(1).rolling(4, min_periods=3).max()
    mm['pullback_low_3'] = low.shift(1).rolling(3, min_periods=2).min()
    mm['rise_from_low_pct'] = (close / mm['local_low_8'] - 1.0) * 100.0
    mm['pullback_holds'] = mm['pullback_low_3'] > mm['local_low_8']
    mm['price_turn_up'] = (close > close.shift(1)) & (close.shift(1) <= close.shift(2))
    mm['v_reclaim'] = (mm['rise_from_low_pct'] >= 0.50) & mm['pullback_holds'] & mm['price_turn_up']

    # Structural stop references. These are diagnostics, not frozen production values.
    mm['break_stop'] = mm['box_high_10']
    mm['v_stop'] = mm['pullback_low_3']

    cols = ['time','box_high_10','box_low_10','box_width_pct','box_break',
            'local_low_8','pullback_low_3','rise_from_low_pct','pullback_holds','price_turn_up','v_reclaim',
            'break_stop','v_stop']
    z = z.merge(mm[cols], on='time', how='left')
    return z


def load_or_build_cache(sym: str, raw_bars: pd.DataFrame, cfg, completed: pd.DataFrame):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f'{sym}_provisional_micro.pkl'
    if path.exists():
        with path.open('rb') as f:
            obj = pickle.load(f)
        print(f'CACHE HIT {sym}: {path}', flush=True)
        return obj['provisional'], obj['micro']

    print(f'CACHE BUILD {sym}: provisional 5m + 1m micro', flush=True)
    pf = rt.build_provisional_5m(raw_bars, cfg)
    pf = rt.add_provisional_strength(pf, completed)
    m = h.build_micro(raw_bars, cfg)
    with path.open('wb') as f:
        pickle.dump({'provisional': pf, 'micro': m}, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f'CACHE WROTE {sym}: {path}', flush=True)
    return pf, m


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = {norm_sym(k): v for k, v in load_data().items()}
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    frames0 = base.build_cfg_frames(raw, cfg)
    f10 = {norm_sym(s): v10._refine_entry_frame(f) for s, f in frames0.items()}
    scored0 = reweight(f10, cfg, 0.0)
    scored = {norm_sym(s): f for s, f in scored0.items()}
    completed = {s: rt.add_completed_strength(f) for s, f in scored.items()}

    print('=== V20 TRANSITION STRUCTURE TARGET DIAGNOSTIC ===', flush=True)
    print('Established uptrend V20 is unchanged.', flush=True)
    print('Transition READY uses relaxed RAW only for down/flat regimes; BUY requires BOX_BREAK or V_PULLBACK_RECLAIM.', flush=True)
    print('Structural stops are printed for later short-stop validation.', flush=True)

    rows = []
    for sym, day, t0, t1 in TARGETS:
        print(f'\n===== {sym} {day} {t0}-{t1} =====', flush=True)
        if sym not in raw or sym not in completed:
            print('MISSING SYMBOL', flush=True)
            continue

        b = raw[sym].copy()
        b['time'] = pd.to_datetime(b['time'])
        pf, m = load_or_build_cache(sym, b, cfg, completed[sym])
        z = add_structure_features(pf, m)

        start = with_tz(f'{day} {t0}', z['time'])
        end = with_tz(f'{day} {t1}', z['time'])
        z = z[(z.time >= start) & (z.time <= end)].copy().reset_index(drop=True)
        if z.empty:
            print('NO ROWS IN WINDOW', flush=True)
            continue

        mid = pd.to_numeric(z['mid_slope8'], errors='coerce')
        z['mid_non_up'] = mid <= 0
        z['momentum_up'] = ((pd.to_numeric(z['macd_slope'], errors='coerce') > 0) &
                            (pd.to_numeric(z['rsi_slope'], errors='coerce') > 0))
        z['rel_ok'] = pd.to_numeric(z['strength_rel'], errors='coerce') >= REL_MIN

        for lb in SLOPE_LOOKBACKS:
            d = mid.diff()
            z[f'slope_gain_{lb}'] = mid - mid.shift(lb)
            z[f'slope_pos_ratio_{lb}'] = (d > 0).rolling(lb, min_periods=lb).mean()

        for raw_min in RAW_MINS:
            for lb in SLOPE_LOOKBACKS:
                slope_gain = z[f'slope_gain_{lb}']
                slope_ratio = z[f'slope_pos_ratio_{lb}']
                # Multi-minute recovery, not one-bar second derivative. Allow modest pullback/noise.
                recovery = (slope_gain > 0) & (slope_ratio >= 0.50)
                ready = (z['mid_non_up'] & recovery & z['momentum_up'] & z['rel_ok'] &
                         (pd.to_numeric(z['gap_delta'], errors='coerce') >= raw_min))
                box_buy = ready & z['box_break'].fillna(False)
                v_buy = ready & z['v_reclaim'].fillna(False)
                any_buy = box_buy | v_buy

                hit = z[any_buy].copy()
                if hit.empty:
                    print(f'RAW>={raw_min:g} LB{lb}: NONE', flush=True)
                    continue

                first = hit.iloc[0]
                mode = 'BOX_BREAK' if bool(first.box_break) else 'V_PULLBACK_RECLAIM'
                stop = float(first.break_stop) if mode == 'BOX_BREAK' else float(first.v_stop)
                px = float(first.close)
                stop_pct = ((stop / px) - 1.0) * 100.0 if np.isfinite(stop) and px > 0 else np.nan
                print(
                    f'RAW>={raw_min:g} LB{lb}: {pd.Timestamp(first.time)} px={px:.2f} mode={mode} '
                    f'mid={float(first.mid_slope8):.3f} gain={float(first[f"slope_gain_{lb}"]):.3f} '
                    f'pos={float(first[f"slope_pos_ratio_{lb}"]):.2f} raw={float(first.gap_delta):.3f} '
                    f'rel={float(first.strength_rel):.3f} rsi={float(first.rsi):.2f} stop={stop:.2f} stop_pct={stop_pct:.3f}',
                    flush=True,
                )
                rows.append(dict(symbol=sym, day=str(day), raw_min=raw_min, slope_lb=lb,
                                 trigger_time=first.time, trigger_price=px, mode=mode,
                                 mid_slope8=first.mid_slope8, slope_gain=first[f'slope_gain_{lb}'],
                                 slope_pos_ratio=first[f'slope_pos_ratio_{lb}'], raw=first.gap_delta,
                                 rel=first.strength_rel, rsi=first.rsi, structural_stop=stop,
                                 structural_stop_pct=stop_pct))

        show_cols = ['time','close','mid_slope8','gap_delta','strength_rel','rsi','rsi_slope',
                     'box_high_10','box_low_10','box_width_pct','box_break','local_low_8','pullback_low_3',
                     'rise_from_low_pct','v_reclaim']
        print('\nSTRUCTURE WINDOW (candidate neighborhood):', flush=True)
        cand = z[(pd.to_numeric(z.gap_delta, errors='coerce') >= 20) & z.rel_ok & z.momentum_up]
        print(cand[[c for c in show_cols if c in cand.columns]].to_string(index=False) if len(cand) else 'NONE', flush=True)

    out = pd.DataFrame(rows)
    path = OUT_DIR / 'v20_transition_structure_targets.csv'
    out.to_csv(path, index=False)
    print(f'\nWROTE {path}', flush=True)


if __name__ == '__main__':
    main()
