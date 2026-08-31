from __future__ import annotations

"""Causal KR live execution adapter for Engine5 V22.

This module removes Williams entry authority from the KR mock order path.  It
builds the Engine5 5-minute state from the already-fetched Kiwoom 1-minute bars,
computes causal provisional scores minute by minute, then applies the frozen KR
V22 timing policy (R3 +5 early merit, last-rise <20, normal-T jump>=15 veto).

It is deliberately fail-closed: missing/invalid bars => no entry.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from live_server.double_bollinger_engine5 import DoubleBollingerEngine5, DoubleBollingerEngine5Config
from live_server.engine5_v22_kr import early_entry_decision, normal_entry_decision

ENGINE_NAME = "ENGINE5_V22_KR_LIVE"
_KST = ZoneInfo("Asia/Seoul")
_ENG = DoubleBollingerEngine5(DoubleBollingerEngine5Config(entry_score=50.0))


def _f(v, default=np.nan):
    try:
        x = float(v)
        return x if np.isfinite(x) else default
    except Exception:
        return default


def _parse_time(v):
    s = ''.join(ch for ch in str(v or '') if ch.isdigit())
    if len(s) >= 14:
        return pd.to_datetime(s[:14], format='%Y%m%d%H%M%S', errors='coerce')
    if len(s) >= 12:
        return pd.to_datetime(s[:12], format='%Y%m%d%H%M', errors='coerce')
    return pd.NaT


def _one_minute_frame(row: dict) -> pd.DataFrame:
    gate = (row or {}).get('shadow_gate') or {}
    raw = gate.get('bars_raw') or (row or {}).get('bars_raw') or []
    if not isinstance(raw, list) or len(raw) < 30:
        return pd.DataFrame()
    z = pd.DataFrame(raw).copy()
    need = {'time', 'open', 'high', 'low', 'close'}
    if not need.issubset(z.columns):
        return pd.DataFrame()
    z['time'] = z['time'].map(_parse_time)
    z = z.dropna(subset=['time']).copy()
    for c in ('open', 'high', 'low', 'close', 'volume'):
        if c not in z.columns:
            z[c] = 0.0
        z[c] = pd.to_numeric(z[c], errors='coerce')
    z = z.dropna(subset=['open', 'high', 'low', 'close'])
    z = z.sort_values('time').drop_duplicates('time', keep='last').reset_index(drop=True)

    # Current Kiwoom minute may still be forming.  Only completed 1m bars are
    # allowed to affect a production order decision.
    now = pd.Timestamp(datetime.now(timezone.utc).astimezone(_KST)).tz_localize(None)
    current_minute = now.floor('min')
    z = z[z['time'] < current_minute].copy()
    return z.reset_index(drop=True)


def _aggregate_5m(one: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    q = one[one['time'] <= pd.Timestamp(cutoff)].copy()
    if q.empty:
        return pd.DataFrame()
    q['bucket'] = q['time'].dt.floor('5min')
    five = q.groupby('bucket', as_index=False).agg(
        open=('open', 'first'),
        high=('high', 'max'),
        low=('low', 'min'),
        close=('close', 'last'),
        volume=('volume', 'sum'),
    ).rename(columns={'bucket': 'time'})
    return five.sort_values('time').reset_index(drop=True)


def _state_at(one: pd.DataFrame, cutoff: pd.Timestamp) -> dict:
    five = _aggregate_5m(one, cutoff)
    if len(five) < 22:
        return {'ok': False, 'reason': 'INSUFFICIENT_5M_HISTORY'}
    f = _ENG.enrich(five)
    if f.empty:
        return {'ok': False, 'reason': 'ENGINE5_EMPTY'}
    r = f.iloc[-1]
    iu = _f(r.get('inner_upper'))
    il = _f(r.get('inner_lower'))
    band_r = iu - il if np.isfinite(iu) and np.isfinite(il) else np.nan
    return {
        'ok': True,
        'time': pd.Timestamp(cutoff),
        'entry_gate': bool(r.get('entry_gate', False)),
        'score': _f(r.get('entry_score')),
        'trend_up': bool(r.get('trend_up', False)),
        'outer_expanding': bool(r.get('outer_expanding', False)),
        'mid_slope8': _f(r.get('mid_slope8')),
        'macd_slope_spread': _f(r.get('macd_slope_spread')),
        'rsi_slope': _f(r.get('rsi_slope')),
        'inner_upper': iu,
        'inner_lower': il,
        'outer_upper': _f(r.get('outer_upper')),
        'band_r': band_r,
    }


def evaluate_entry(row: dict) -> dict:
    """Return the authoritative causal KR V22 entry decision for one row."""
    sym = str((row or {}).get('symbol') or '').replace('A', '').zfill(6)
    one = _one_minute_frame(row or {})
    if len(one) < 30:
        return {'engine': ENGINE_NAME, 'symbol': sym, 'enter': False, 'reason': 'INSUFFICIENT_1M_HISTORY'}

    cutoffs = list(one['time'].tail(5))
    states = [_state_at(one, t) for t in cutoffs]
    states = [x for x in states if x.get('ok') and np.isfinite(_f(x.get('score')))]
    if len(states) < 2:
        return {'engine': ENGINE_NAME, 'symbol': sym, 'enter': False, 'reason': 'INSUFFICIENT_CAUSAL_SCORE_PATH'}

    cur = states[-1]
    prev = states[-2]
    if not cur.get('entry_gate'):
        return {
            'engine': ENGINE_NAME, 'symbol': sym, 'enter': False,
            'reason': 'ENGINE5_ENTRY_GATE_FALSE', 'score': cur.get('score'),
            'bar_time': str(cur.get('time')),
        }

    # First try the frozen R3 +5 early path using four consecutive causal
    # minute-end provisional scores.  No future bar is referenced here.
    decision = None
    if len(states) >= 4:
        path = [float(x['score']) for x in states[-4:]]
        d = early_entry_decision('V20', path)
        if d.enter:
            decision = d

    # Otherwise this is a normal-T decision and the >=15 one-minute score jump
    # veto is authoritative.
    if decision is None:
        decision = normal_entry_decision(float(prev['score']), float(cur['score']))

    px = _f((row or {}).get('price'))
    band_r = _f(cur.get('band_r'))
    stop_price = px - band_r if px > 0 and band_r > 0 else np.nan
    tp1_price = px + 2.0 * band_r if px > 0 and band_r > 0 else np.nan
    return {
        'engine': ENGINE_NAME,
        'symbol': sym,
        'enter': bool(decision.enter),
        'timing': decision.timing,
        'reason': decision.reason,
        'effective_score': _f(decision.effective_score),
        'last_step': _f(decision.last_step),
        'score': cur.get('score'),
        'prev_score': prev.get('score'),
        'bar_time': str(cur.get('time')),
        'band_r': band_r if np.isfinite(band_r) else None,
        'stop_price': stop_price if np.isfinite(stop_price) else None,
        'tp1_price': tp1_price if np.isfinite(tp1_price) else None,
        'outer_upper': cur.get('outer_upper'),
        'inner_lower': cur.get('inner_lower'),
    }


# Backward-compatible object name used by the deployment/import smoke test.
class KoreaV22LiveEntryGate:
    def evaluate_row(self, row: dict) -> dict:
        return evaluate_entry(row)


KR_V22_LIVE_ENTRY_GATE = KoreaV22LiveEntryGate()
