from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.validate_engine5_v20_regime_transition as rt
import tools.diagnose_v20_transition_structure_targets as st
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_slow_turn_prototype as slow
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
SLOPE_LBS = [3, 5]
SLOPE_RATIOS = [0.67, 0.80]
MICRO_RATIOS = [0.67, 0.80]
MICRO_WINDOW_MIN = 10


def n(x):
    return str(x).zfill(6)


def num(s):
    return pd.to_numeric(s, errors='coerce')


def any_true(q, col):
    return bool(len(q) and q[col].fillna(False).any())


def any_ge(q, col, v):
    return bool(len(q) and (num(q[col]) >= float(v)).any())


def any_gt(q, col, v=0.0):
    return bool(len(q) and (num(q[col]) > float(v)).any())


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = {n(k): v for k, v in load_data().items()}
    cfg0 = DoubleBollingerEngine5Config()
    cfg = replace(cfg0, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    frames0 = base.build_cfg_frames(raw, cfg)
    f10 = {n(s): v10._refine_entry_frame(f) for s, f in frames0.items()}
    scored = {n(s): f for s, f in reweight(f10, cfg, 0.0).items()}
    completed = {s: rt.add_completed_strength(f) for s, f in scored.items()}

    print('=== SLOW TURN STAGE FUNNEL DIAGNOSTIC ===')
    print('No trading rule is changed. No return optimization is performed.')
    print('5m funnel: negative slope -> slope recovery -> recovery continuity -> 5m MACD improvement -> 5m RSI improvement.')
    print('Then inspect the next 10 completed 1m bars for price structure and momentum continuity.\n')

    packed = {}
    for i, s in enumerate(raw, 1):
        print(f'[{i}/{len(raw)}] {s}', flush=True)
        pf, _ = st.load_or_build_cache(s, raw[s], cfg, completed[s])
        m = h.build_micro(raw[s], cfg)
        z, mm = slow.add_slow_turn_features(pf, m)
        packed[s] = (z, mm)

    funnel_rows = []
    detail_rows = []

    for slb in SLOPE_LBS:
        for sr in SLOPE_RATIOS:
            for mr in MICRO_RATIOS:
                counts = dict(
                    total_5m=0,
                    neg_slope=0,
                    slope_gain=0,
                    slope_continuity=0,
                    macd5_up=0,
                    rsi5_up=0,
                    micro_window=0,
                    higher_low=0,
                    prior_high_break=0,
                    hl_and_break=0,
                    gap_continuity=0,
                    rsi_continuity=0,
                    final_same_bar=0,
                )

                for sym, (z0, mm0) in packed.items():
                    z = z0.copy()
                    mm = mm0.copy()
                    mid = num(z['mid_slope8'])
                    gain = num(z[f'slope_gain_{slb}'])
                    posr = num(z[f'slope_pos_ratio_{slb}'])
                    gd5 = num(z.get('gap_delta'))
                    rs5 = num(z.get('rsi_slope'))
                    minute = z.time.dt.hour * 60 + z.time.dt.minute
                    time_ok = (minute >= 9 * 60 + 10) & (minute < base.NO_ENTRY_MINUTE)

                    s1 = time_ok & (mid < 0)
                    s2 = s1 & (gain > 0)
                    s3 = s2 & (posr >= sr)
                    s4 = s3 & (gd5 > 0)
                    s5 = s4 & (rs5 > 0)

                    counts['total_5m'] += int(time_ok.sum())
                    counts['neg_slope'] += int(s1.sum())
                    counts['slope_gain'] += int(s2.sum())
                    counts['slope_continuity'] += int(s3.sum())
                    counts['macd5_up'] += int(s4.sum())
                    counts['rsi5_up'] += int(s5.sum())

                    for _, r in z[s5].iterrows():
                        ts = pd.Timestamp(r.time)
                        q = mm[(mm.time >= ts) & (mm.time < ts + pd.Timedelta(minutes=MICRO_WINDOW_MIN))].copy()
                        if q.empty:
                            continue
                        counts['micro_window'] += 1

                        hl = q['higher_low'].fillna(False)
                        br = q['higher_high_break'].fillna(False)
                        both = hl & br
                        gapok = num(q['gap_pos_ratio_3']) >= mr
                        rsiok = num(q['rsi_pos_ratio_3']) >= mr
                        current_pos = (num(q['macd_gap_delta_1m']) > 0) & (num(q['rsi_slope_1m']) > 0)
                        final = both & gapok & rsiok & current_pos

                        counts['higher_low'] += int(hl.any())
                        counts['prior_high_break'] += int(br.any())
                        counts['hl_and_break'] += int(both.any())
                        counts['gap_continuity'] += int((both & gapok).any())
                        counts['rsi_continuity'] += int((both & gapok & rsiok).any())
                        counts['final_same_bar'] += int(final.any())

                        best = q.copy()
                        best['stage_score'] = (
                            best['higher_low'].fillna(False).astype(int)
                            + best['higher_high_break'].fillna(False).astype(int)
                            + (num(best['gap_pos_ratio_3']) >= mr).astype(int)
                            + (num(best['rsi_pos_ratio_3']) >= mr).astype(int)
                            + (num(best['macd_gap_delta_1m']) > 0).astype(int)
                            + (num(best['rsi_slope_1m']) > 0).astype(int)
                        )
                        b = best.sort_values(['stage_score', 'time'], ascending=[False, True]).iloc[0]
                        detail_rows.append(dict(
                            slope_lb=slb, slope_ratio=sr, micro_ratio=mr,
                            symbol=sym, ready_time=ts, best_time=pd.Timestamp(b.time),
                            mid_slope8=float(r.mid_slope8), slope_gain=float(r[f'slope_gain_{slb}']),
                            slope_pos_ratio=float(r[f'slope_pos_ratio_{slb}']),
                            gap_delta_5m=float(r.get('gap_delta', np.nan)),
                            rsi_slope_5m=float(r.get('rsi_slope', np.nan)),
                            stage_score=int(b.stage_score),
                            higher_low=bool(b.higher_low), high_break=bool(b.higher_high_break),
                            gap_ratio=float(b.gap_pos_ratio_3) if np.isfinite(b.gap_pos_ratio_3) else np.nan,
                            rsi_ratio=float(b.rsi_pos_ratio_3) if np.isfinite(b.rsi_pos_ratio_3) else np.nan,
                            gap_delta_1m=float(b.macd_gap_delta_1m) if np.isfinite(b.macd_gap_delta_1m) else np.nan,
                            rsi_slope_1m=float(b.rsi_slope_1m) if np.isfinite(b.rsi_slope_1m) else np.nan,
                            final_pass=bool(final.any()),
                        ))

                funnel_rows.append(dict(slope_lb=slb, slope_ratio=sr, micro_ratio=mr, **counts))

    funnel = pd.DataFrame(funnel_rows)
    print('\n=== CUMULATIVE STAGE COUNTS ===')
    print(funnel.to_string(index=False))

    details = pd.DataFrame(detail_rows)
    if len(details):
        print('\n=== CLOSEST NON-PASSING READY WINDOWS (top stage scores) ===')
        show = details[~details.final_pass].sort_values(['stage_score','ready_time'], ascending=[False, True]).head(60)
        cols = ['slope_lb','slope_ratio','micro_ratio','symbol','ready_time','best_time','mid_slope8',
                'slope_gain','slope_pos_ratio','gap_delta_5m','rsi_slope_5m','stage_score','higher_low',
                'high_break','gap_ratio','rsi_ratio','gap_delta_1m','rsi_slope_1m']
        print(show[cols].to_string(index=False))

        print('\n=== FINAL PASS WINDOWS, IF ANY ===')
        passed = details[details.final_pass].sort_values(['ready_time','symbol'])
        print(passed[cols].to_string(index=False) if len(passed) else 'NONE')

    p1 = OUT_DIR / 'slow_turn_stage_funnel.csv'
    p2 = OUT_DIR / 'slow_turn_stage_details.csv'
    funnel.to_csv(p1, index=False)
    details.to_csv(p2, index=False)
    print('\nWROTE', p1)
    print('WROTE', p2)


if __name__ == '__main__':
    main()
