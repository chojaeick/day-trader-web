from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.validate_engine5_v20_regime_transition as rt
import tools.validate_engine5_v21_v_rebound_structural_stop as old
import tools.validate_engine5_v21_v_rebound_state_machine as sm
import tools.validate_engine5_v21_v_rebound_reaccel as ra
import tools.validate_engine5_v21_v_rebound_momentum_preservation as mp
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
OUT_CASES = OUT_DIR / 'v21_v_rebound_hold_exit_cases.csv'
OUT_WINNER_PATH = OUT_DIR / 'v21_v_rebound_hold_exit_winner_path.csv'

THRESHOLD = 50
RAW_MIN = 30.0
LEG_MIN = 2.0
STOP_CAP = 2.0
VOL_MIN = 1.0
GAP_KEEP = 0.9
FEE_RT_PCT = 0.25


def n(x):
    return str(x).zfill(6)


def f(x):
    try:
        y = float(x)
        return y if np.isfinite(y) else np.nan
    except Exception:
        return np.nan


def ret_pct(a, b):
    a = f(a); b = f(b)
    if not (np.isfinite(a) and a != 0 and np.isfinite(b)):
        return np.nan
    return (b / a - 1.0) * 100.0


def value_at_or_after(w, ts, col, minutes):
    target = pd.Timestamp(ts) + pd.Timedelta(minutes=minutes)
    q = w[w.time >= target]
    if q.empty:
        return np.nan
    return f(q.iloc[0][col])


def main():
    raw = {n(k): v for k, v in load_data().items()}
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2., rsi_slope_full_ratio=1.5)

    packed = v8.base.pack_exit_events(raw, base_cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    frames = base.build_cfg_frames(raw, cfg)
    f10 = {n(s): v10._refine_entry_frame(x) for s, x in frames.items()}
    scored = {n(s): x for s, x in reweight(f10, cfg, 0.).items()}
    completed = {s: rt.add_completed_strength(x) for s, x in scored.items()}

    allc = []
    features = {}
    for sym, bars in raw.items():
        pf, m = old.load_cache(sym, bars, cfg, completed[sym])
        z = sm.add_features(pf, m, bars).sort_values('time').reset_index(drop=True)
        z['time'] = pd.to_datetime(z.time)
        features[sym] = z
        c = sm.state_candidates(sym, z, scored[sym], RAW_MIN, LEG_MIN)
        if len(c):
            allc.append(c)

    cand = pd.concat(allc, ignore_index=True) if allc else pd.DataFrame()
    if cand.empty:
        print('NO CANDIDATES')
        return
    cand = ra.add_pullback_reaccel(cand, features)
    cand = mp.add_preservation(cand, features)
    q = cand[(cand.stop_dist_pct <= STOP_CAP) & cand.reaccel_pass &
             (pd.to_numeric(cand.volume_accel, errors='coerce') >= VOL_MIN) &
             cand.rsi_positive_all &
             (pd.to_numeric(cand.gap_keep_ratio, errors='coerce') >= GAP_KEEP)].copy()
    q['day'] = pd.to_datetime(q.time).dt.date
    q = q.sort_values('time').drop_duplicates(['symbol', 'day'], keep='first').reset_index(drop=True)

    vev, meta, _ = sm.select(q, RAW_MIN, LEG_MIN, STOP_CAP, None)
    tr = old.simulate_with_v_stop(packed, vev, states, THRESHOLD, meta)
    if tr.empty:
        print('NO TRADES')
        return
    tr['net_pct'] = pd.to_numeric(tr.pnl_pct, errors='coerce') - FEE_RT_PCT

    rows = []
    winner_paths = []
    for _, t in tr.iterrows():
        sym = n(t.symbol)
        et = pd.Timestamp(t.entry_time)
        xt = pd.Timestamp(t.exit_time)
        ep = f(t.entry_price)
        xp = f(t.exit_price)
        z = features[sym].copy().sort_values('time')
        px_col = 'px' if 'px' in z.columns else 'close'
        z['px_use'] = pd.to_numeric(z[px_col], errors='coerce')
        z['gap_delta_use'] = pd.to_numeric(z.get('gap_delta'), errors='coerce')
        z['rsi_slope_use'] = pd.to_numeric(z.get('rsi_slope'), errors='coerce')

        day = et.date()
        w = z[(z.time >= et) & (z.time.dt.date == day)].copy()
        if w.empty:
            continue
        pre = w[w.time <= xt].copy()
        post = w[w.time >= xt].copy()
        if pre.empty:
            continue

        pre['ret'] = [ret_pct(ep, v) for v in pre.px_use]
        post['ret'] = [ret_pct(ep, v) for v in post.px_use]
        pre['hwm'] = pre.ret.cummax()
        pre['dd_from_hwm'] = pre.ret - pre.hwm

        exit_row = pre.iloc[-1]
        mfe_before_exit = f(pre.ret.max())
        exit_ret = ret_pct(ep, xp)
        exit_dd = f(exit_row.dd_from_hwm)
        post_best = f(post.ret.max()) if len(post) else np.nan
        post_worst = f(post.ret.min()) if len(post) else np.nan
        session_end = f(w.iloc[-1].px_use)
        session_end_ret = ret_pct(ep, session_end)

        p1 = value_at_or_after(w, xt, 'px_use', 1)
        p3 = value_at_or_after(w, xt, 'px_use', 3)
        p5 = value_at_or_after(w, xt, 'px_use', 5)
        p10 = value_at_or_after(w, xt, 'px_use', 10)

        rows.append(dict(
            result='WIN' if f(t.net_pct) > 0 else 'LOSS',
            symbol=sym, entry_time=et, exit_time=xt,
            entry_price=ep, exit_price=xp, net_pct=f(t.net_pct), reason=t.get('reason', ''),
            hold_minutes=(xt-et).total_seconds()/60.0,
            mfe_before_exit_pct=mfe_before_exit,
            exit_gross_ret_pct=exit_ret,
            exit_drawdown_from_hwm_pct=exit_dd,
            exit_gap_delta=f(exit_row.gap_delta_use),
            exit_rsi_slope=f(exit_row.rsi_slope_use),
            post_exit_best_pct=post_best,
            post_exit_worst_pct=post_worst,
            post_exit_1m_pct=ret_pct(ep, p1),
            post_exit_3m_pct=ret_pct(ep, p3),
            post_exit_5m_pct=ret_pct(ep, p5),
            post_exit_10m_pct=ret_pct(ep, p10),
            session_end_ret_pct=session_end_ret,
            extra_upside_after_exit_pct=(post_best-exit_ret) if np.isfinite(post_best) and np.isfinite(exit_ret) else np.nan,
        ))

        if f(t.net_pct) > 0:
            ww = w.copy()
            ww['ret_from_entry_pct'] = [ret_pct(ep, v) for v in ww.px_use]
            ww['hwm_ret_pct'] = ww.ret_from_entry_pct.cummax()
            ww['dd_from_hwm_pct'] = ww.ret_from_entry_pct - ww.hwm_ret_pct
            ww['is_after_exit'] = ww.time > xt
            ww['symbol'] = sym
            ww['entry_time'] = et
            ww['exit_time'] = xt
            winner_paths.append(ww[['symbol','entry_time','exit_time','time','px_use','ret_from_entry_pct','hwm_ret_pct','dd_from_hwm_pct','gap_delta_use','rsi_slope_use','is_after_exit']])

    cases = pd.DataFrame(rows).sort_values('entry_time')
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cases.to_csv(OUT_CASES, index=False)
    if winner_paths:
        pd.concat(winner_paths, ignore_index=True).to_csv(OUT_WINNER_PATH, index=False)

    print('\n=== V-REBOUND HOLD / EXIT DIAGNOSTIC ===')
    print('Diagnostic only. Entry and exit rules unchanged.')
    print('Current selected V cohort: STOP<=2, REACCEL, VOL>=1.0, RSI positive, GAP_KEEP>=0.9.')
    print('\n=== CASES ===')
    cols = ['result','symbol','entry_time','exit_time','net_pct','reason','hold_minutes',
            'mfe_before_exit_pct','exit_gross_ret_pct','exit_drawdown_from_hwm_pct',
            'exit_gap_delta','exit_rsi_slope','post_exit_best_pct','extra_upside_after_exit_pct',
            'post_exit_3m_pct','post_exit_5m_pct','post_exit_10m_pct','session_end_ret_pct']
    print(cases[cols].to_string(index=False))

    wins = cases[cases.net_pct > 0]
    print('\n=== WINNER HOLD CHECK ===')
    if wins.empty:
        print('NO WINNERS')
    else:
        print(wins[['symbol','entry_time','exit_time','net_pct','mfe_before_exit_pct','exit_gross_ret_pct',
                    'exit_drawdown_from_hwm_pct','post_exit_best_pct','extra_upside_after_exit_pct',
                    'post_exit_3m_pct','post_exit_5m_pct','post_exit_10m_pct','session_end_ret_pct']].to_string(index=False))

    print('\nReading target:')
    print('- If the big winner has substantial upside after the current exit, inspect whether the exit is too sensitive.')
    print('- If price gives back materially from HWM and post-exit upside is small, current exit is probably doing its job.')
    print('- Do not replace the V structural stop; this diagnostic is only about holding successful rebounds.')
    print('WROTE', OUT_CASES)
    if winner_paths:
        print('WROTE', OUT_WINNER_PATH)

if __name__ == '__main__':
    main()
