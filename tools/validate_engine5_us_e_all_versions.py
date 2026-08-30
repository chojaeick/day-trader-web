from __future__ import annotations

"""US E-series validators and adapters.

This module adapts Engine5 semantics to the US regular session while preserving native USD
and original ET timestamps. It intentionally keeps KR production code untouched.
"""

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_opening_5m_hwm_sweep as sweep
import tools.validate_engine5_v17c_multi_symbol as multi
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_macd_strength as ms
import tools.validate_engine5_integrated_full_history as integ
import tools.validate_engine5_v21_v_rebound_state_machine as vsm
import tools.validate_engine5_v21_v_rebound_reaccel as vra
import tools.validate_engine5_v21_v_rebound_momentum_preservation as vmp

US_OPEN_MINUTE = 9 * 60 + 30
US_BUY_START_MINUTE = 9 * 60 + 40
US_OPENING_END_MINUTE = 10 * 60 + 30
US_NO_ENTRY_MINUTE = 15 * 60 + 30
US_FORCE_FLAT_MINUTE = 15 * 60 + 50

# KR V20 RAW52 translated to price-relative bps from the KR reference distribution.
V20E_RAW_BPS = 11.166071
V20E_REL_MIN = 1.45
V20E_REQUIRE_ABOVE_SIGNAL = True
# KR RAW30 equivalent used by the V21 Slow-turn boundary and V-rebound READY gate.
V21E_RAW30_BPS = V20E_RAW_BPS * (30.0 / 52.0)


def n(x):
    return str(x).zfill(6)


def minute_of(ts):
    t = pd.Timestamp(ts)
    return t.hour * 60 + t.minute


def apply_us_session_clock():
    """Patch only runtime constants used by imported KR validation helpers."""
    base.OPEN_MINUTE = US_OPEN_MINUTE
    base.NO_ENTRY_MINUTE = US_NO_ENTRY_MINUTE
    base.FORCE_FLAT_MINUTE = US_FORCE_FLAT_MINUTE
    sweep.OPEN_MINUTE = US_BUY_START_MINUTE
    sweep.OPEN_BUY_MINUTE = US_BUY_START_MINUTE
    sweep.OPENING_ENTRY_END = US_OPENING_END_MINUTE
    multi.OPEN_MINUTE = US_BUY_START_MINUTE
    multi.NO_ENTRY_MINUTE = US_NO_ENTRY_MINUTE
    multi.FORCE_FLAT_MINUTE = US_FORCE_FLAT_MINUTE


def clip_us_entry_window(ev):
    return {
        pd.Timestamp(ts): cs
        for ts, cs in ev.items()
        if US_BUY_START_MINUTE <= minute_of(ts) < US_NO_ENTRY_MINUTE
    }


def ev_keys(ev):
    return {(pd.Timestamp(ts), n(c[0])) for ts, cs in ev.items() for c in cs}


def _event_price(c):
    try:
        return float(c[2])
    except Exception:
        return np.nan


def build_v20e(ev18, strength):
    tags = []
    for ts, cs in ev18.items():
        for c in cs:
            sym = n(c[0])
            f = strength.get(sym)
            if f is None or f.empty:
                continue
            q = f[f.time <= pd.Timestamp(ts)]
            if q.empty:
                continue
            r = q.iloc[-1]
            px = float(r.close) if pd.notna(r.close) else np.nan
            raw = float(r.macd_strength_raw) if pd.notna(r.macd_strength_raw) else np.nan
            rel = float(r.macd_strength_rel) if pd.notna(r.macd_strength_rel) else np.nan
            if not np.isfinite(px) or px == 0 or not np.isfinite(raw) or not np.isfinite(rel):
                continue
            bps = raw / px * 10000.0
            gap = float(r.macd_gap) if 'macd_gap' in r and pd.notna(r.macd_gap) else np.nan
            if bps < V20E_RAW_BPS or rel < V20E_REL_MIN:
                continue
            if V20E_REQUIRE_ABOVE_SIGNAL and np.isfinite(gap) and gap <= 0:
                continue
            ext = integ.entry_extension_5m(strength, c[0], ts)
            if pd.notna(ext) and ext >= integ.V20_EXTREME_CAP:
                continue
            tags.append(dict(source='V20E', symbol=sym, time=pd.Timestamp(ts), event=c, meta={}))
    return tags

# Backward-compatible name used by fresh remap script.
def build_v20e_tags(ev18, strength, scored):
    tags = []
    for ts, cs in ev18.items():
        for c in cs:
            sym = n(c[0])
            f = strength.get(sym)
            if f is None or f.empty:
                continue
            q = f[f.time <= pd.Timestamp(ts)]
            if q.empty:
                continue
            r = q.iloc[-1]
            px = float(r.close) if pd.notna(r.close) else np.nan
            raw = float(r.macd_strength_raw) if pd.notna(r.macd_strength_raw) else np.nan
            rel = float(r.macd_strength_rel) if pd.notna(r.macd_strength_rel) else np.nan
            if not np.isfinite(px) or px == 0 or not np.isfinite(raw) or not np.isfinite(rel):
                continue
            bps = raw / px * 10000.0
            gap = float(r.macd_gap) if 'macd_gap' in r and pd.notna(r.macd_gap) else np.nan
            if bps < V20E_RAW_BPS or rel < V20E_REL_MIN:
                continue
            if V20E_REQUIRE_ABOVE_SIGNAL and np.isfinite(gap) and gap <= 0:
                continue
            ext = integ.entry_extension_5m(scored, c[0], ts)
            if pd.notna(ext) and ext >= integ.V20_EXTREME_CAP:
                continue
            tags.append(dict(source='V20E', symbol=sym, time=pd.Timestamp(ts), event=c, meta={}))
    return tags


def state_candidates_e(sym, z, scored, leg_min):
    out = []
    pxs = pd.to_numeric(z.px, errors='coerce')
    gd = pd.to_numeric(z.gap_delta, errors='coerce')
    bps = gd / pxs.replace(0, np.nan) * 10000.0
    ready = z.ready_common & (bps >= V21E_RAW30_BPS)
    day = pd.to_datetime(z.time).dt.date
    state = None
    for i in range(len(z)):
        ts = pd.Timestamp(z.time.iloc[i])
        px = float(z.px.iloc[i]) if pd.notna(z.px.iloc[i]) else np.nan
        lo = float(z.lo.iloc[i]) if pd.notna(z.lo.iloc[i]) else np.nan
        if not np.isfinite(px) or not np.isfinite(lo):
            continue
        if i == 0 or day.iloc[i] != day.iloc[i - 1]:
            state = None
        if state is not None and (ts - state['armed_time']).total_seconds() / 60.0 > vsm.ARM_TTL_MIN:
            state = None
        if state is None and bool(ready.iloc[i]):
            j = max(0, i - 8)
            base_low = float(pd.to_numeric(z.lo.iloc[j:i + 1], errors='coerce').min())
            if np.isfinite(base_low) and base_low > 0:
                state = {
                    'armed_time': ts, 'armed_i': i, 'base_low': base_low,
                    'rebound_high': px, 'rebound_high_time': ts, 'stage': 'RISING',
                    'pullback_low': np.nan, 'pullback_start': pd.NaT,
                }
        if state is None:
            continue
        if lo <= state['base_low'] and i > state['armed_i']:
            state = None
            if bool(ready.iloc[i]):
                j = max(0, i - 8)
                base_low = float(pd.to_numeric(z.lo.iloc[j:i + 1], errors='coerce').min())
                if np.isfinite(base_low) and base_low > 0:
                    state = {
                        'armed_time': ts, 'armed_i': i, 'base_low': base_low,
                        'rebound_high': px, 'rebound_high_time': ts, 'stage': 'RISING',
                        'pullback_low': np.nan, 'pullback_start': pd.NaT,
                    }
            if state is None:
                continue
        leg = (state['rebound_high'] / state['base_low'] - 1.0) * 100.0
        prev = float(z.px.iloc[i - 1]) if i > 0 and pd.notna(z.px.iloc[i - 1]) else np.nan
        if state['stage'] == 'RISING':
            if px > state['rebound_high']:
                state['rebound_high'] = px
                state['rebound_high_time'] = ts
                leg = (state['rebound_high'] / state['base_low'] - 1.0) * 100.0
            if leg >= leg_min and np.isfinite(prev) and px < prev:
                state['stage'] = 'PULLBACK'
                state['pullback_start'] = ts
                state['pullback_low'] = lo
        else:
            state['pullback_low'] = min(float(state['pullback_low']), lo)
            higher_low = np.isfinite(state['pullback_low']) and state['pullback_low'] > state['base_low']
            reclaim = px > state['rebound_high']
            mom = (float(z.gap_delta.iloc[i]) > 0 and float(z.rsi_slope.iloc[i]) > 0)
            if higher_low and reclaim and mom:
                stop = state['pullback_low']
                dist = (px / stop - 1.0) * 100.0
                q5 = scored[scored.time <= ts.floor('5min')]
                if US_BUY_START_MINUTE <= minute_of(ts) < US_NO_ENTRY_MINUTE and not q5.empty:
                    ev = vsm.old.make_event(sym, q5.iloc[-1], px)
                    if ev is not None:
                        out.append(dict(
                            symbol=n(sym), time=ts, price=px,
                            structural_stop=stop, stop_dist_pct=dist,
                            first_rebound_high_time=state['rebound_high_time'],
                            pullback_start=state['pullback_start'],
                            volume_accel=float(z.volume_accel_3v10.iloc[i]) if pd.notna(z.volume_accel_3v10.iloc[i]) else np.nan,
                            gap_delta=float(z.gap_delta.iloc[i]),
                            rsi_slope=float(z.rsi_slope.iloc[i]),
                            event=ev,
                        ))
                state = None
    return pd.DataFrame(out)


def build_vrebound_e(raw, scored, micros, pf):
    allc = []
    vf = {}
    for s, bars in raw.items():
        z = vsm.add_features(pf[s], micros[s], bars).sort_values('time').reset_index(drop=True)
        vf[s] = z
        c = state_candidates_e(s, z, scored[s], integ.V_LEG_MIN)
        if len(c):
            allc.append(c)
    if not allc:
        return []
    q = pd.concat(allc, ignore_index=True)
    q = vra.add_pullback_reaccel(q, vf)
    q = vmp.add_preservation(q, vf)
    q = q[
        (q.stop_dist_pct <= integ.V_STOP_CAP)
        & q.reaccel_pass
        & (pd.to_numeric(q.volume_accel, errors='coerce') >= integ.V_VOL_MIN)
        & q.rsi_positive_all
        & (pd.to_numeric(q.gap_keep_ratio, errors='coerce') >= integ.V_GAP_KEEP_MIN)
    ].copy()
    q['day'] = pd.to_datetime(q.time).dt.date
    q = q.sort_values('time').drop_duplicates(['symbol', 'day'], keep='first')
    return [
        dict(
            source='V_REBOUND_E', symbol=n(r.symbol), time=pd.Timestamp(r.time), event=r.event,
            meta={'structural_stop': float(r.structural_stop)},
        )
        for _, r in q.iterrows()
    ]


def normalize_slow_boundary_e(allslow):
    x = allslow.copy()
    if x.empty:
        return x
    ready_min = pd.to_datetime(x.ready_time).dt.hour * 60 + pd.to_datetime(x.ready_time).dt.minute
    entry_min = pd.to_datetime(x.entry_time).dt.hour * 60 + pd.to_datetime(x.entry_time).dt.minute
    x = x[
        (ready_min >= US_BUY_START_MINUTE)
        & (entry_min >= US_BUY_START_MINUTE)
        & (entry_min < US_NO_ENTRY_MINUTE)
    ].copy()
    boundary = x.regime.astype(str).eq('BOUNDARY_8_12')
    gd = pd.to_numeric(x.gap_delta_5m, errors='coerce')
    px = pd.to_numeric(x.entry_price, errors='coerce')
    gd_bps = gd / px.replace(0, np.nan) * 10000.0
    rs = pd.to_numeric(x.rsi_slope_5m, errors='coerce')
    pp = pd.to_numeric(x.price_progress_1m_pct, errors='coerce')
    boundary_ok = (gd_bps >= V21E_RAW30_BPS) & (rs >= 10.0) & (pp >= 1.50)
    x.loc[boundary, 'selected_current'] = boundary_ok[boundary]
    x['gap_delta_bps_e'] = gd_bps
    return x


def merge_tags(*groups):
    by_key = {}
    for g in groups:
        for x in g:
            k = (pd.Timestamp(x['time']), n(x['symbol']))
            by_key.setdefault(k, x)
    return list(by_key.values())
