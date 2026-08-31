from __future__ import annotations

"""Causal KR live execution adapter for Engine5 V22.

Entry and exit decisions in this module are the KR V22 order authority. Williams
may still produce telemetry/candidates, but must not decide broker BUY/SELL.
"""

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
    now = pd.Timestamp(datetime.now(timezone.utc).astimezone(_KST)).tz_localize(None)
    z = z[z['time'] < now.floor('min')].copy()
    return z.reset_index(drop=True)


def _aggregate_5m(one: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    q = one[one['time'] <= pd.Timestamp(cutoff)].copy()
    if q.empty:
        return pd.DataFrame()
    q['bucket'] = q['time'].dt.floor('5min')
    return q.groupby('bucket', as_index=False).agg(
        open=('open', 'first'), high=('high', 'max'), low=('low', 'min'),
        close=('close', 'last'), volume=('volume', 'sum'),
    ).rename(columns={'bucket': 'time'}).sort_values('time').reset_index(drop=True)


def _state_at(one: pd.DataFrame, cutoff: pd.Timestamp) -> dict:
    five = _aggregate_5m(one, cutoff)
    if len(five) < 22:
        return {'ok': False, 'reason': 'INSUFFICIENT_5M_HISTORY'}
    f = _ENG.enrich(five)
    if f.empty:
        return {'ok': False, 'reason': 'ENGINE5_EMPTY'}
    r = f.iloc[-1]
    iu, il = _f(r.get('inner_upper')), _f(r.get('inner_lower'))
    band_r = iu - il if np.isfinite(iu) and np.isfinite(il) else np.nan
    return {
        'ok': True, 'time': pd.Timestamp(cutoff),
        'entry_gate': bool(r.get('entry_gate', False)), 'score': _f(r.get('entry_score')),
        'trend_up': bool(r.get('trend_up', False)), 'outer_expanding': bool(r.get('outer_expanding', False)),
        'mid_slope8': _f(r.get('mid_slope8')), 'macd_slope_spread': _f(r.get('macd_slope_spread')),
        'rsi_slope': _f(r.get('rsi_slope')), 'inner_upper': iu, 'inner_lower': il,
        'outer_upper': _f(r.get('outer_upper')), 'band_r': band_r,
        'close': _f(r.get('close')), 'high': _f(r.get('high')),
    }


def evaluate_entry(row: dict) -> dict:
    sym = str((row or {}).get('symbol') or '').replace('A', '').zfill(6)
    one = _one_minute_frame(row or {})
    if len(one) < 30:
        return {'engine': ENGINE_NAME, 'symbol': sym, 'enter': False, 'reason': 'INSUFFICIENT_1M_HISTORY'}
    states = [_state_at(one, t) for t in list(one['time'].tail(5))]
    states = [x for x in states if x.get('ok') and np.isfinite(_f(x.get('score')))]
    if len(states) < 2:
        return {'engine': ENGINE_NAME, 'symbol': sym, 'enter': False, 'reason': 'INSUFFICIENT_CAUSAL_SCORE_PATH'}
    cur, prev = states[-1], states[-2]
    if not cur.get('entry_gate'):
        return {'engine': ENGINE_NAME, 'symbol': sym, 'enter': False, 'reason': 'ENGINE5_ENTRY_GATE_FALSE', 'score': cur.get('score'), 'bar_time': str(cur.get('time'))}
    decision = None
    if len(states) >= 4:
        d = early_entry_decision('V20', [float(x['score']) for x in states[-4:]])
        if d.enter:
            decision = d
    if decision is None:
        decision = normal_entry_decision(float(prev['score']), float(cur['score']))
    px, band_r = _f((row or {}).get('price')), _f(cur.get('band_r'))
    stop_price = px - band_r if px > 0 and band_r > 0 else np.nan
    tp1_price = px + 2.0 * band_r if px > 0 and band_r > 0 else np.nan
    return {
        'engine': ENGINE_NAME, 'symbol': sym, 'enter': bool(decision.enter), 'timing': decision.timing,
        'reason': decision.reason, 'effective_score': _f(decision.effective_score), 'last_step': _f(decision.last_step),
        'score': cur.get('score'), 'prev_score': prev.get('score'), 'bar_time': str(cur.get('time')),
        'band_r': band_r if np.isfinite(band_r) else None,
        'stop_price': stop_price if np.isfinite(stop_price) else None,
        'tp1_price': tp1_price if np.isfinite(tp1_price) else None,
        'outer_upper': cur.get('outer_upper'), 'inner_lower': cur.get('inner_lower'),
    }


def evaluate_exit(row: dict, position: dict) -> dict:
    """Authoritative Engine5 V22 position-management decision.

    Priority: -1R structural stop -> +2R TP1 sell 50% -> post-TP1 continuation
    outer-upper reduction -> 2/3 momentum fade -> final close below inner-lower.
    Fail closed when causal Engine5 state is unavailable; never fall back to Williams.
    """
    sym = str((row or {}).get('symbol') or '').replace('A', '').zfill(6)
    pos = position or {}
    qty = max(0, int(_f(pos.get('qty'), 0)))
    original_qty = max(qty, int(_f(pos.get('original_qty'), qty)))
    px = _f((row or {}).get('price'))
    stop = _f(pos.get('v22_stop_price') or pos.get('stop_price'))
    tp1 = _f(pos.get('v22_tp1_price') or pos.get('tp1_price'))
    if qty <= 0 or not np.isfinite(px) or px <= 0:
        return {'engine': ENGINE_NAME, 'symbol': sym, 'exit': False, 'reason': 'NO_POSITION_OR_PRICE'}
    if np.isfinite(stop) and stop > 0 and px <= stop:
        return {'engine': ENGINE_NAME, 'symbol': sym, 'exit': True, 'sell_qty': qty, 'reason': 'V22_STRUCTURAL_STOP', 'price': px, 'stop_price': stop}

    one = _one_minute_frame(row or {})
    if one.empty:
        return {'engine': ENGINE_NAME, 'symbol': sym, 'exit': False, 'reason': 'V22_EXIT_STATE_UNAVAILABLE'}
    cur = _state_at(one, one['time'].iloc[-1])
    if not cur.get('ok'):
        return {'engine': ENGINE_NAME, 'symbol': sym, 'exit': False, 'reason': cur.get('reason', 'V22_EXIT_STATE_UNAVAILABLE')}

    tp1_done = bool(pos.get('v22_tp1_done'))
    outer_done = bool(pos.get('v22_outer_reduced'))
    high = max(px, _f(cur.get('high'), px))
    if (not tp1_done) and np.isfinite(tp1) and tp1 > 0 and high >= tp1:
        sell_qty = min(qty, max(1, original_qty // 2))
        return {'engine': ENGINE_NAME, 'symbol': sym, 'exit': True, 'sell_qty': sell_qty, 'reason': 'V22_TP1_2R_50PCT', 'price': px, 'tp1_price': tp1, 'tp1_done': True}

    if tp1_done:
        outer = _f(cur.get('outer_upper'))
        if (not outer_done) and np.isfinite(outer) and outer > 0 and high >= outer:
            sell_qty = min(qty, max(1, qty // 2))
            return {'engine': ENGINE_NAME, 'symbol': sym, 'exit': True, 'sell_qty': sell_qty, 'reason': 'V22_RUNNER_OUTER_UPPER_HALF', 'price': px, 'outer_upper': outer, 'outer_reduced': True}
        weak = sum([
            _f(cur.get('mid_slope8'), 0.0) <= 0.0,
            _f(cur.get('macd_slope_spread'), 0.0) <= 0.0,
            _f(cur.get('rsi_slope'), 0.0) <= 0.0,
        ])
        if weak >= 2:
            return {'engine': ENGINE_NAME, 'symbol': sym, 'exit': True, 'sell_qty': qty, 'reason': 'V22_RUNNER_MOMENTUM_FADE_2OF3', 'price': px, 'weak_count': weak}
        inner_lower, close = _f(cur.get('inner_lower')), _f(cur.get('close'), px)
        if np.isfinite(inner_lower) and inner_lower > 0 and close < inner_lower:
            return {'engine': ENGINE_NAME, 'symbol': sym, 'exit': True, 'sell_qty': qty, 'reason': 'V22_RUNNER_INNER_LOWER_BREAK', 'price': px, 'inner_lower': inner_lower, 'close': close}
    return {'engine': ENGINE_NAME, 'symbol': sym, 'exit': False, 'reason': 'V22_HOLD', 'price': px}


class KoreaV22LiveEntryGate:
    def evaluate_row(self, row: dict) -> dict:
        return evaluate_entry(row)


KR_V22_LIVE_ENTRY_GATE = KoreaV22LiveEntryGate()
