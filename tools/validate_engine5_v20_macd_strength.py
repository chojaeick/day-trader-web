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
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
THRESHOLD = 50
FEE_RT_PCT = 0.25
RAW_LEVELS = [45.0, 47.5, 50.0, 52.5, 55.0, 57.5, 60.0]
REL_LEVELS = [1.0, 1.25, 1.5, 1.75, 2.0]
REL_LOOKBACK = 8
TARGET_SYM = '950260'
TARGET_DAY = pd.Timestamp('2026-08-21').date()


def finite(x):
    return h.finite(x)


def add_strength(frame: pd.DataFrame) -> pd.DataFrame:
    f = frame.copy().sort_values('time').reset_index(drop=True)
    d = pd.to_numeric(f['macd_gap_delta'], errors='coerce')
    baseline = d.abs().shift(1).rolling(REL_LOOKBACK, min_periods=4).median()
    f['macd_strength_raw'] = d
    f['macd_strength_rel'] = d.clip(lower=0.0) / baseline.replace(0.0, np.nan)
    f['macd_strength_baseline'] = baseline
    return f


def row_at(frames, sym, ts):
    f = frames[str(sym).zfill(6)]
    q = f[f.time <= pd.Timestamp(ts)]
    return None if q.empty else q.iloc[-1]


def filter_events(events, frames, raw_min=None, rel_min=None):
    out = {}
    diag = []
    for ts in sorted(events):
        for c in events[ts]:
            sym = str(c[0]).zfill(6)
            r = row_at(frames, sym, ts)
            raw = finite(r.macd_strength_raw) if r is not None else np.nan
            rel = finite(r.macd_strength_rel) if r is not None else np.nan
            ok = True
            if raw_min is not None:
                ok = ok and np.isfinite(raw) and raw >= float(raw_min)
            if rel_min is not None:
                ok = ok and np.isfinite(rel) and rel >= float(rel_min)
            diag.append(dict(symbol=sym, time=pd.Timestamp(ts), raw=raw, rel=rel,
                             baseline=finite(r.macd_strength_baseline) if r is not None else np.nan,
                             keep=bool(ok)))
            if ok:
                out.setdefault(pd.Timestamp(ts), []).append(c)
    return out, pd.DataFrame(diag)


def stats(label, t):
    g = pd.to_numeric(t.pnl_pct, errors='coerce').dropna() if len(t) else pd.Series(dtype=float)
    n = g - FEE_RT_PCT
    gp = float(n[n > 0].sum()) if len(n) else 0.0
    gl = float(-n[n < 0].sum()) if len(n) else 0.0
    return dict(label=label, trades=len(n), net_wins=int((n > 0).sum()),
                net_losses=int((n <= 0).sum()),
                net_win_pct=float((n > 0).mean()*100) if len(n) else 0.0,
                net_sum_pct=float(n.sum()) if len(n) else 0.0,
                net_avg_pct=float(n.mean()) if len(n) else 0.0,
                net_pf=gp/gl if gl > 0 else np.inf,
                gross_sum_pct=float(g.sum()) if len(g) else 0.0,
                max_net_loss_pct=float(n.min()) if len(n) else np.nan)


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
    strength_frames = {str(s).zfill(6): add_strength(f) for s, f in scored.items()}

    # Frozen V18 stream only. No reversal logic, no 1m trigger changes here.
    raw_entries = v8.pack_entry_events(scored)
    ev10 = sweep.filt_open(raw_entries)
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev17, _, _ = v17b.build_v17b(ev16, scored, waits)
    micros = {str(s).zfill(6): h.build_micro(b, cfg) for s, b in raw.items()}
    ev18, _ = h.build_veto_stream(ev17, micros)
    t18 = multi.simulate_multi(packed, ev18, states, THRESHOLD)

    rows = [stats('V18_REFERENCE', t18)]
    diags = []

    for lv in RAW_LEVELS:
        ev, d = filter_events(ev18, strength_frames, raw_min=lv)
        t = multi.simulate_multi(packed, ev, states, THRESHOLD)
        s = stats(f'RAW_{lv:g}', t); s.update(raw_min=lv, rel_min=np.nan, kept_events=int(d.keep.sum()), total_events=len(d))
        rows.append(s); d['label'] = f'RAW_{lv:g}'; diags.append(d)

    for lv in REL_LEVELS:
        ev, d = filter_events(ev18, strength_frames, rel_min=lv)
        t = multi.simulate_multi(packed, ev, states, THRESHOLD)
        s = stats(f'REL_{lv:g}X', t); s.update(raw_min=np.nan, rel_min=lv, kept_events=int(d.keep.sum()), total_events=len(d))
        rows.append(s); d['label'] = f'REL_{lv:g}X'; diags.append(d)

    # Combined values near the observed 950260 separation.
    for raw_lv in [47.5, 50.0, 52.5, 55.0]:
        for rel_lv in [1.0, 1.25, 1.5]:
            ev, d = filter_events(ev18, strength_frames, raw_min=raw_lv, rel_min=rel_lv)
            t = multi.simulate_multi(packed, ev, states, THRESHOLD)
            label = f'RAW{raw_lv:g}_REL{rel_lv:g}X'
            s = stats(label, t); s.update(raw_min=raw_lv, rel_min=rel_lv, kept_events=int(d.keep.sum()), total_events=len(d))
            rows.append(s); d['label'] = label; diags.append(d)

    summary = pd.DataFrame(rows)
    print('=== V20 MACD STRENGTH ONLY ===')
    print('Only V18 entries are filtered by 5m MACD oscillator strength. No reversal/V-shape/1m logic is changed.')
    print(summary.to_string(index=False))

    all_diag = pd.concat(diags, ignore_index=True) if diags else pd.DataFrame()
    target = []
    f = strength_frames[TARGET_SYM]
    q = f[(pd.to_datetime(f.time).dt.date == TARGET_DAY) & (f.time.dt.strftime('%H:%M') >= '09:30') & (f.time.dt.strftime('%H:%M') <= '12:30')]
    print('\n=== 950260 5M MACD STRENGTH 09:30-12:30 ===')
    cols = ['time','close','macd','macd_signal','macd_gap','macd_gap_delta','macd_strength_baseline','macd_strength_rel','macd_golden_cross']
    print(q[[c for c in cols if c in q.columns]].to_string(index=False))

    if len(all_diag):
        td = all_diag[(all_diag.symbol == TARGET_SYM) & (pd.to_datetime(all_diag.time).dt.date == TARGET_DAY)]
        print('\n=== 950260 V18 EVENT KEEP/DROP BY STRENGTH ===')
        print(td.sort_values(['time','label']).to_string(index=False))
        td.to_csv(OUT_DIR/'v20_950260_macd_strength_diag.csv', index=False)

    summary.to_csv(OUT_DIR/'v20_macd_strength_summary.csv', index=False)
    print('\nWROTE', OUT_DIR/'v20_macd_strength_summary.csv')
    print('WROTE', OUT_DIR/'v20_950260_macd_strength_diag.csv')

if __name__ == '__main__':
    main()
