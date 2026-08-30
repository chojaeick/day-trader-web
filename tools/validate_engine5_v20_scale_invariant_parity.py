from __future__ import annotations

"""Diagnose/validate V20 without price-unit-dependent MACD RAW thresholds.

This is a validation-only script. Production strategy files are untouched.

Procedure:
1) Rebuild the frozen KR V18 event stream.
2) Measure legacy V20 selectivity (RAW>=52 and REL>=1.45).
3) On KR only, derive a scale-invariant raw_bps threshold that matches the
   legacy V20 event count among REL>=1.45 candidates.
4) Compare three gates on KR and then apply the exact same gates to US:
   LEGACY      : RAW>=52 and REL>=1.45
   NORM        : RAW/close bps >= KR-derived threshold and REL>=1.45
   NORM_TREND  : NORM plus MACD >= signal (established positive MACD state)

Both gross and fee-0.25 results are printed so fee assumptions cannot hide the
underlying strategy behavior.
"""

import pickle
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_opening_5m_hwm_sweep as sweep
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v17c_multi_symbol as multi
import tools.validate_engine5_v20_macd_strength as ms
import tools.validate_engine5_integrated_full_history as integ
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

ROOT = Path('/home/ubuntu/day-trader-api/engine5_us_kr_mapped_cache')
CORE = ROOT / 'us_kr_mapped_core.pkl'
OUT = ROOT / 'v20_scale_invariant_parity_summary.csv'
EVENTS_OUT = ROOT / 'v20_scale_invariant_parity_events.csv'

RAW_MIN = 52.0
REL_MIN = 1.45
THRESHOLD = 50

US_BUY_START_MINUTE = 9 * 60 + 40
US_OPENING_END_MINUTE = 10 * 60 + 30
US_NO_ENTRY_MINUTE = 15 * 60 + 30
US_FORCE_FLAT_MINUTE = 15 * 60 + 50


def n(x):
    return str(x).zfill(6)


def build_kr():
    raw = {n(k): v for k, v in load_data().items()}
    cfg0 = DoubleBollingerEngine5Config()
    cfg = replace(cfg0, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)
    packed = v8.base.pack_exit_events(raw, cfg0)
    states = base.pack_state_events(base.build_cfg_frames(raw, cfg0))
    frames0 = base.build_cfg_frames(raw, cfg)
    f10 = {n(s): v10._refine_entry_frame(f) for s, f in frames0.items()}
    scored = {n(s): f for s, f in reweight(f10, cfg, 0.0).items()}
    strength = {s: ms.add_strength(f) for s, f in scored.items()}
    raw_entries = v8.pack_entry_events(scored)
    ev10 = sweep.filt_open(raw_entries)
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev17, _, _ = v17b.build_v17b(ev16, scored, waits)
    micros = {n(s): h.build_micro(b, cfg) for s, b in raw.items()}
    ev18, _ = h.build_veto_stream(ev17, micros)
    return dict(raw=raw, cfg=cfg, packed=packed, states=states, scored=scored, strength=strength, ev18=ev18)


def apply_us_clock():
    base.NO_ENTRY_MINUTE = US_NO_ENTRY_MINUTE
    base.FORCE_FLAT_MINUTE = US_FORCE_FLAT_MINUTE
    sweep.OPEN_BUY_MINUTE = US_BUY_START_MINUTE
    sweep.OPENING_ENTRY_END = US_OPENING_END_MINUTE
    multi.OPEN_MINUTE = US_BUY_START_MINUTE


def build_us():
    if not CORE.exists():
        raise FileNotFoundError(CORE)
    with CORE.open('rb') as fh:
        d = pickle.load(fh)
    if int(d.get('time_shift_minutes', 999)) != 0:
        raise RuntimeError('US cache must preserve original ET (time_shift_minutes=0)')
    apply_us_clock()
    raw = d['raw']; cfg = d['cfg']; packed = d['packed']; states = d['states']
    scored = d['scored']; strength = d['strength']; micros = d['micros']
    raw_entries = v8.pack_entry_events(scored)
    ev10 = {ts: rows for ts, rows in raw_entries.items()
            if pd.Timestamp(ts).hour * 60 + pd.Timestamp(ts).minute >= US_BUY_START_MINUTE}
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev17, _, _ = v17b.build_v17b(ev16, scored, waits)
    ev18, _ = h.build_veto_stream(ev17, micros)
    return dict(raw=raw, cfg=cfg, packed=packed, states=states, scored=scored, strength=strength, ev18=ev18)


def event_table(ctx, market):
    rows = []
    for ts, cs in ctx['ev18'].items():
        for c in cs:
            sym = n(c[0])
            sf = ctx['strength'][sym]
            q = sf[sf.time <= pd.Timestamp(ts)]
            if q.empty:
                continue
            r = q.iloc[-1]
            raw = float(r.macd_strength_raw) if pd.notna(r.macd_strength_raw) else np.nan
            rel = float(r.macd_strength_rel) if pd.notna(r.macd_strength_rel) else np.nan
            close = float(r.close) if pd.notna(r.close) else np.nan
            gap = float(r.macd_gap) if pd.notna(r.macd_gap) else np.nan
            signal = float(r.macd_signal) if pd.notna(r.macd_signal) else np.nan
            macd = float(r.macd) if pd.notna(r.macd) else np.nan
            raw_bps = raw / close * 10000.0 if np.isfinite(raw) and np.isfinite(close) and close != 0 else np.nan
            rows.append(dict(market=market, symbol=sym, time=pd.Timestamp(ts), raw=raw, rel=rel,
                             close=close, raw_bps=raw_bps, macd=macd, signal=signal, gap=gap,
                             macd_above_signal=bool(np.isfinite(gap) and gap >= 0)))
    return pd.DataFrame(rows)


def derive_kr_bps_threshold(k):
    legacy = k[(k.raw >= RAW_MIN) & (k.rel >= REL_MIN)].copy()
    eligible = k[(k.rel >= REL_MIN) & np.isfinite(k.raw_bps)].copy().sort_values('raw_bps', ascending=False)
    target = len(legacy)
    if target == 0 or len(eligible) < target:
        raise RuntimeError(f'cannot derive KR bps threshold target={target} eligible={len(eligible)}')
    # Threshold determined only from KR and only to preserve legacy selectivity.
    return float(eligible.iloc[target - 1].raw_bps), target


def gate_events(ctx, rows, mode, bps_min):
    if mode == 'LEGACY':
        keep = (rows.raw >= RAW_MIN) & (rows.rel >= REL_MIN)
    elif mode == 'NORM':
        keep = (rows.raw_bps >= bps_min) & (rows.rel >= REL_MIN)
    elif mode == 'NORM_TREND':
        keep = (rows.raw_bps >= bps_min) & (rows.rel >= REL_MIN) & rows.macd_above_signal
    else:
        raise ValueError(mode)

    keys = set((n(r.symbol), pd.Timestamp(r.time)) for _, r in rows[keep].iterrows())
    out = {}
    for ts, cs in ctx['ev18'].items():
        kept = [c for c in cs if (n(c[0]), pd.Timestamp(ts)) in keys]
        if kept:
            out[pd.Timestamp(ts)] = kept
    return out, rows.assign(mode=mode, keep=keep)


def tagged_v20(ctx, ev):
    out = []
    for ts, cs in ev.items():
        for c in cs:
            ext = integ.entry_extension_5m(ctx['scored'], c[0], ts)
            if pd.notna(ext) and ext >= integ.V20_EXTREME_CAP:
                continue
            out.append(dict(source='V20', symbol=n(c[0]), time=pd.Timestamp(ts), event=c, meta={}))
    return out


def metrics(market, mode, ctx, ev, bps_min):
    tags = tagged_v20(ctx, ev)
    tr = integ.simulate(ctx['packed'], ctx['states'], tags)
    g = pd.to_numeric(tr.pnl_pct, errors='coerce').dropna() if len(tr) else pd.Series(dtype=float)
    net = g - 0.25
    gp_g = float(g[g > 0].sum()) if len(g) else 0.0
    gl_g = float(-g[g < 0].sum()) if len(g) else 0.0
    gp_n = float(net[net > 0].sum()) if len(net) else 0.0
    gl_n = float(-net[net < 0].sum()) if len(net) else 0.0
    return dict(
        market=market, mode=mode, bps_min=bps_min, signals=len(tags), trades=len(g),
        gross_wins=int((g > 0).sum()), gross_wr=float((g > 0).mean() * 100) if len(g) else 0.0,
        gross_sum=float(g.sum()) if len(g) else 0.0, gross_avg=float(g.mean()) if len(g) else 0.0,
        gross_pf=gp_g / gl_g if gl_g > 0 else np.inf,
        net025_wins=int((net > 0).sum()), net025_wr=float((net > 0).mean() * 100) if len(net) else 0.0,
        net025_sum=float(net.sum()) if len(net) else 0.0,
        net025_pf=gp_n / gl_n if gl_n > 0 else np.inf,
    )


def main():
    print('=== V20 SCALE-INVARIANT PARITY VALIDATION ===', flush=True)
    print('Build KR reference first; US is not used to derive thresholds.', flush=True)
    kr = build_kr()
    krows = event_table(kr, 'KR')
    bps_min, target = derive_kr_bps_threshold(krows)
    print(f'KR-derived normalized threshold: raw_bps >= {bps_min:.6f} with REL >= {REL_MIN}', flush=True)
    print(f'KR legacy target events before extreme guard: {target}', flush=True)

    summaries = []
    diagnostics = []
    for mode in ('LEGACY', 'NORM', 'NORM_TREND'):
        ev, dg = gate_events(kr, krows, mode, bps_min)
        s = metrics('KR', mode, kr, ev, bps_min)
        summaries.append(s); diagnostics.append(dg)
        print(f"KR {mode}: signals={s['signals']} trades={s['trades']} grossWR={s['gross_wr']:.2f}% gross={s['gross_sum']:+.4f}% PF={s['gross_pf']:.3f} | net025={s['net025_sum']:+.4f}%", flush=True)

    us = build_us()
    urows = event_table(us, 'US')
    for mode in ('LEGACY', 'NORM', 'NORM_TREND'):
        ev, dg = gate_events(us, urows, mode, bps_min)
        s = metrics('US', mode, us, ev, bps_min)
        summaries.append(s); diagnostics.append(dg)
        print(f"US {mode}: signals={s['signals']} trades={s['trades']} grossWR={s['gross_wr']:.2f}% gross={s['gross_sum']:+.4f}% PF={s['gross_pf']:.3f} | net025={s['net025_sum']:+.4f}%", flush=True)

    print('\n=== EVENT STATE SPLIT ===')
    for market, x in [('KR', krows), ('US', urows)]:
        legacy = x[(x.raw >= RAW_MIN) & (x.rel >= REL_MIN)]
        norm = x[(x.raw_bps >= bps_min) & (x.rel >= REL_MIN)]
        print(f"{market}: legacy={len(legacy)} legacy_above_signal={legacy.macd_above_signal.mean()*100 if len(legacy) else 0:.2f}% | norm={len(norm)} norm_above_signal={norm.macd_above_signal.mean()*100 if len(norm) else 0:.2f}%")

    pd.DataFrame(summaries).to_csv(OUT, index=False)
    pd.concat(diagnostics, ignore_index=True).to_csv(EVENTS_OUT, index=False)
    print('WROTE', OUT)
    print('WROTE', EVENTS_OUT)
    print('VALIDATION ONLY. Production engine unchanged.')


if __name__ == '__main__':
    main()
