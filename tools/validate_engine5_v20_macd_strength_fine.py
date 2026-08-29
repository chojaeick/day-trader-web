from __future__ import annotations

from dataclasses import replace
from pathlib import Path

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
import tools.validate_engine5_v20_macd_strength as ms
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
THRESHOLD = 50
FEE_RT_PCT = 0.25
RAW_LEVELS = np.arange(50.0, 55.0001, 0.5)
REL_LEVELS = np.arange(1.20, 1.6001, 0.05)
TARGET_SYM = '950260'
TARGET_DAY = pd.Timestamp('2026-08-21').date()
BAD_TIME = pd.Timestamp('2026-08-21 10:00:00+09:00')
GOOD_TIME = pd.Timestamp('2026-08-21 12:20:00+09:00')


def target_strength(frame, ts):
    q = frame[frame.time == ts]
    if q.empty:
        q = frame[frame.time <= ts].tail(1)
    if q.empty:
        return np.nan, np.nan
    r = q.iloc[-1]
    return ms.finite(r.macd_strength_raw), ms.finite(r.macd_strength_rel)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = load_data()
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    packed = v8.base.pack_exit_events(raw, base_cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    frames0 = base.build_cfg_frames(raw, cfg)
    f10 = {s: v10._refine_entry_frame(f) for s, f in frames0.items()}
    scored = reweight(f10, cfg, 0.0)
    strength_frames = {str(s).zfill(6): ms.add_strength(f) for s, f in scored.items()}

    raw_entries = v8.pack_entry_events(scored)
    ev10 = sweep.filt_open(raw_entries)
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev17, _, _ = v17b.build_v17b(ev16, scored, waits)
    micros = {str(s).zfill(6): h.build_micro(b, cfg) for s, b in raw.items()}
    ev18, _ = h.build_veto_stream(ev17, micros)
    t18 = multi.simulate_multi(packed, ev18, states, THRESHOLD)

    target_frame = strength_frames[TARGET_SYM]
    bad_raw, bad_rel = target_strength(target_frame, BAD_TIME)
    good_raw, good_rel = target_strength(target_frame, GOOD_TIME)

    rows = []
    ref = ms.stats('V18_REFERENCE', t18)
    ref.update(raw_min=np.nan, rel_min=np.nan, kept_events=np.nan, total_events=np.nan,
               bad10_pass=np.nan, good1220_pass=np.nan, separation_ok=np.nan)
    rows.append(ref)

    for raw_lv in RAW_LEVELS:
        for rel_lv in REL_LEVELS:
            raw_lv = round(float(raw_lv), 2)
            rel_lv = round(float(rel_lv), 2)
            ev, d = ms.filter_events(ev18, strength_frames, raw_min=raw_lv, rel_min=rel_lv)
            t = multi.simulate_multi(packed, ev, states, THRESHOLD)
            label = f'RAW{raw_lv:g}_REL{rel_lv:g}X'
            s = ms.stats(label, t)
            bad_pass = bool(np.isfinite(bad_raw) and np.isfinite(bad_rel) and bad_raw >= raw_lv and bad_rel >= rel_lv)
            good_pass = bool(np.isfinite(good_raw) and np.isfinite(good_rel) and good_raw >= raw_lv and good_rel >= rel_lv)
            s.update(raw_min=raw_lv, rel_min=rel_lv,
                     kept_events=int(d.keep.sum()), total_events=len(d),
                     bad10_pass=bad_pass, good1220_pass=good_pass,
                     separation_ok=bool((not bad_pass) and good_pass))
            rows.append(s)

    summary = pd.DataFrame(rows)
    grid = summary[summary.label != 'V18_REFERENCE'].copy()
    separated = grid[grid.separation_ok == True].copy()
    separated['trade_retention_pct'] = separated.trades / max(len(t18), 1) * 100.0

    # Balanced ranking: first require known-case separation, then prefer robust PF/net while retaining trades.
    separated['balance_score'] = (
        separated.net_sum_pct
        + 8.0 * (separated.net_pf - 1.0)
        + 0.05 * separated.trades
    )
    ranked = separated.sort_values(
        ['balance_score', 'net_sum_pct', 'net_pf', 'trades'],
        ascending=False,
    )

    print('=== V20 MACD STRENGTH FINE TUNE ===')
    print('Grid: RAW 50.0..55.0 step 0.5; REL 1.20..1.60 step 0.05')
    print('Only V18 5m MACD strength gate is changed. No reversal/V-shape/1m logic change.')
    print('\nV18 REFERENCE')
    print(pd.DataFrame([ref]).to_string(index=False))
    print(f'\n950260 BAD 10:00 strength: raw={bad_raw:.6f} rel={bad_rel:.6f}')
    print(f'950260 GOOD 12:20 strength: raw={good_raw:.6f} rel={good_rel:.6f}')
    print('\n=== TOP 25: KNOWN-CASE SEPARATION ONLY ===')
    cols = ['label','trades','net_wins','net_losses','net_win_pct','net_sum_pct','net_avg_pct',
            'net_pf','gross_sum_pct','max_net_loss_pct','raw_min','rel_min','trade_retention_pct',
            'kept_events','balance_score']
    print(ranked[cols].head(25).to_string(index=False) if len(ranked) else 'NONE')

    # Also show the local neighborhood around the previous center 52.5 / 1.50.
    local = grid[(grid.raw_min.between(51.5,53.5)) & (grid.rel_min.between(1.35,1.60))].copy()
    local = local.sort_values(['raw_min','rel_min'])
    print('\n=== LOCAL 51.5..53.5 / 1.35..1.60 ===')
    print(local[['label','trades','net_win_pct','net_sum_pct','net_avg_pct','net_pf','max_net_loss_pct',
                 'raw_min','rel_min','bad10_pass','good1220_pass','separation_ok']].to_string(index=False))

    summary.to_csv(OUT_DIR/'v20_macd_strength_fine_all.csv', index=False)
    ranked.to_csv(OUT_DIR/'v20_macd_strength_fine_ranked.csv', index=False)
    print('\nWROTE', OUT_DIR/'v20_macd_strength_fine_all.csv')
    print('WROTE', OUT_DIR/'v20_macd_strength_fine_ranked.csv')


if __name__ == '__main__':
    main()
