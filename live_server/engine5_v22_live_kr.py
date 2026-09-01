from __future__ import annotations

"""Causal KR live execution adapter for Engine5 V22.

Entry and exit decisions in this module are the KR V22 order authority. Williams
may still produce telemetry/candidates, but must not decide broker BUY/SELL.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import math

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
    """Legacy 5m state retained for V22 exit management only."""
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


def _angle_deg(delta_indicator: float, price: float, minutes: float = 3.0) -> float:
    """Price-normalised slope angle.

    MACD/signal are first normalised to basis points of price so the angle is
    comparable across KR symbols with different price levels. One x-unit is one
    minute and one y-unit is one basis point of price.
    """
    if not np.isfinite(delta_indicator) or not np.isfinite(price) or price <= 0:
        return np.nan
    delta_bps = (float(delta_indicator) / float(price)) * 10000.0
    return math.degrees(math.atan(delta_bps / max(float(minutes), 1e-9)))


def _entry_state_1m(one: pd.DataFrame, cutoff: pd.Timestamp) -> dict:
    """New KR V22 100-point DBB entry state using completed 1-minute bars."""
    q = one[one['time'] <= pd.Timestamp(cutoff)].copy().sort_values('time').reset_index(drop=True)
    if len(q) < 30:
        return {'ok': False, 'reason': 'INSUFFICIENT_1M_HISTORY'}

    close = pd.to_numeric(q['close'], errors='coerce').astype(float)
    volume = pd.to_numeric(q['volume'], errors='coerce').fillna(0.0).astype(float)
    rsi = _ENG._rsi(close, _ENG.cfg.rsi_period)
    macd, signal = _ENG._macd(close)
    mid, iu, il, ou, ol = _ENG._bands(close)
    ema20 = close.ewm(span=20, adjust=False).mean()

    px = _f(close.iloc[-1])
    if not np.isfinite(px) or px <= 0:
        return {'ok': False, 'reason': 'INVALID_PRICE'}

    # 1) Trend 20: EMA20 now vs 5 minutes ago.
    ema_now, ema_5 = _f(ema20.iloc[-1]), _f(ema20.iloc[-6])
    ema_change_5m_pct = ((ema_now / ema_5) - 1.0) * 100.0 if ema_5 > 0 else np.nan
    if np.isfinite(ema_change_5m_pct) and ema_change_5m_pct >= 0.10:
        score_trend = min(20.0, ema_change_5m_pct * 100.0)
    else:
        score_trend = 0.0

    # 2) MACD 25: 1m MACD vs signal slope-angle difference, current vs 3m ago.
    macd_now, macd_3 = _f(macd.iloc[-1]), _f(macd.iloc[-4])
    sig_now, sig_3 = _f(signal.iloc[-1]), _f(signal.iloc[-4])
    macd_angle = _angle_deg(macd_now - macd_3, px, 3.0)
    signal_angle = _angle_deg(sig_now - sig_3, px, 3.0)
    macd_angle_diff = macd_angle - signal_angle if np.isfinite(macd_angle) and np.isfinite(signal_angle) else np.nan
    if np.isfinite(macd_angle_diff) and macd_angle_diff > 5.0:
        score_macd = min(25.0, max(0.0, macd_angle_diff - 5.0))
    else:
        score_macd = 0.0

    # 3) RSI 40: position 5 + 3-minute slope 35.
    rsi_now, rsi_3 = _f(rsi.iloc[-1]), _f(rsi.iloc[-4])
    rsi_delta_3m = rsi_now - rsi_3 if np.isfinite(rsi_now) and np.isfinite(rsi_3) else np.nan
    score_rsi_position = 5.0 if np.isfinite(rsi_now) and rsi_now >= 50.0 else 0.0
    # Existing agreed +10 RSI-point full-scale retained, rescaled from 30 to 35 max.
    score_rsi_slope = min(35.0, max(0.0, rsi_delta_3m * 3.5)) if np.isfinite(rsi_delta_3m) else 0.0

    # 4) Volume 5: latest 3 completed 1m bars vs preceding 3 bars.
    recent_vol = float(volume.iloc[-3:].sum())
    previous_vol = float(volume.iloc[-6:-3].sum())
    volume_ratio = recent_vol / previous_vol if previous_vol > 0 else np.nan
    score_volume = min(5.0, volume_ratio / 2.0) if np.isfinite(volume_ratio) and volume_ratio >= 2.0 else 0.0

    # 5) Outer-band expansion 5: current width vs previous 1m width, 20% = full score.
    width = ou - ol
    width_now, width_prev = _f(width.iloc[-1]), _f(width.iloc[-2])
    outer_expand_pct = ((width_now / width_prev) - 1.0) * 100.0 if width_prev > 0 else np.nan
    score_outer_expand = min(5.0, max(0.0, outer_expand_pct / 4.0)) if np.isfinite(outer_expand_pct) else 0.0

    # 6) Bollinger midline upward momentum 5: 3-minute change in price-vs-mid divergence.
    mid_now, mid_3 = _f(mid.iloc[-1]), _f(mid.iloc[-4])
    px_3 = _f(close.iloc[-4])
    divergence_now = ((px - mid_now) / mid_now) * 100.0 if mid_now > 0 else np.nan
    divergence_3 = ((px_3 - mid_3) / mid_3) * 100.0 if mid_3 > 0 else np.nan
    mid_momentum_pctpt = divergence_now - divergence_3 if np.isfinite(divergence_now) and np.isfinite(divergence_3) else np.nan
    score_mid_momentum = min(5.0, max(0.0, mid_momentum_pctpt / 0.2)) if np.isfinite(mid_momentum_pctpt) else 0.0

    base_score = (
        score_trend + score_macd + score_rsi_position + score_rsi_slope
        + score_volume + score_outer_expand + score_mid_momentum
    )

    # Strong-reversal exception: MACD >=20 and RSI-slope >=25 may borrow only
    # the unused trend slot. Attribution is split 50/50; total score remains <=100.
    trend_deficit = max(0.0, 20.0 - score_trend)
    reversal_exception = score_macd >= 20.0 and score_rsi_slope >= 25.0
    macd_trend_bonus = trend_deficit / 2.0 if reversal_exception else 0.0
    rsi_trend_bonus = trend_deficit / 2.0 if reversal_exception else 0.0
    effective_score = min(100.0, base_score + macd_trend_bonus + rsi_trend_bonus)

    inner_upper, inner_lower = _f(iu.iloc[-1]), _f(il.iloc[-1])
    band_r = inner_upper - inner_lower if np.isfinite(inner_upper) and np.isfinite(inner_lower) else np.nan

    return {
        'ok': True,
        'time': pd.Timestamp(cutoff),
        'score': effective_score,
        'base_score': base_score,
        'score_trend': score_trend,
        'score_macd': score_macd,
        'score_rsi_position': score_rsi_position,
        'score_rsi_slope': score_rsi_slope,
        'score_volume': score_volume,
        'score_outer_expand': score_outer_expand,
        'score_mid_momentum': score_mid_momentum,
        'ema_change_5m_pct': ema_change_5m_pct,
        'macd_angle': macd_angle,
        'signal_angle': signal_angle,
        'macd_angle_diff': macd_angle_diff,
        'rsi': rsi_now,
        'rsi_delta_3m': rsi_delta_3m,
        'volume_ratio': volume_ratio,
        'outer_expand_pct': outer_expand_pct,
        'mid_momentum_pctpt': mid_momentum_pctpt,
        'reversal_exception': reversal_exception,
        'trend_deficit': trend_deficit,
        'macd_trend_bonus': macd_trend_bonus,
        'rsi_trend_bonus': rsi_trend_bonus,
        'inner_upper': inner_upper,
        'inner_lower': inner_lower,
        'outer_upper': _f(ou.iloc[-1]),
        'band_r': band_r,
        'close': px,
        'high': _f(q['high'].iloc[-1]),
    }


def evaluate_entry(row: dict) -> dict:
    sym = str((row or {}).get('symbol') or '').replace('A', '').zfill(6)
    one = _one_minute_frame(row or {})
    if len(one) < 30:
        return {'engine': ENGINE_NAME, 'symbol': sym, 'enter': False, 'reason': 'INSUFFICIENT_1M_HISTORY'}

    states = [_entry_state_1m(one, t) for t in list(one['time'].tail(5))]
    states = [x for x in states if x.get('ok') and np.isfinite(_f(x.get('score')))]
    if len(states) < 2:
        return {'engine': ENGINE_NAME, 'symbol': sym, 'enter': False, 'reason': 'INSUFFICIENT_CAUSAL_SCORE_PATH'}

    cur, prev = states[-1], states[-2]
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
        'score': cur.get('score'), 'base_score': cur.get('base_score'), 'prev_score': prev.get('score'),
        'score_trend': cur.get('score_trend'), 'score_macd': cur.get('score_macd'),
        'score_rsi_position': cur.get('score_rsi_position'), 'score_rsi_slope': cur.get('score_rsi_slope'),
        'score_volume': cur.get('score_volume'), 'score_outer_expand': cur.get('score_outer_expand'),
        'score_mid_momentum': cur.get('score_mid_momentum'),
        'reversal_exception': cur.get('reversal_exception'),
        'macd_trend_bonus': cur.get('macd_trend_bonus'), 'rsi_trend_bonus': cur.get('rsi_trend_bonus'),
        'ema_change_5m_pct': cur.get('ema_change_5m_pct'), 'macd_angle_diff': cur.get('macd_angle_diff'),
        'rsi_delta_3m': cur.get('rsi_delta_3m'), 'volume_ratio': cur.get('volume_ratio'),
        'outer_expand_pct': cur.get('outer_expand_pct'), 'mid_momentum_pctpt': cur.get('mid_momentum_pctpt'),
        'bar_time': str(cur.get('time')),
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
