from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUTDIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
OUTDIR.mkdir(parents=True, exist_ok=True)
OPEN_MINUTE = 9 * 60 + 10
THRESHOLD = 50

WORST = [
    ('950260', '2026-08-21 10:00:00+09:00'),
    ('257720', '2026-08-18 14:30:00+09:00'),
    ('080220', '2026-08-11 09:55:00+09:00'),
    ('080220', '2026-08-13 10:20:00+09:00'),
    ('043260', '2026-08-18 09:25:00+09:00'),
    ('080220', '2026-08-18 09:35:00+09:00'),
    ('058610', '2026-08-14 09:10:00+09:00'),
    ('484810', '2026-08-18 09:10:00+09:00'),
]


def filt_open(ev):
    return {ts: rows for ts, rows in ev.items() if pd.Timestamp(ts).hour * 60 + pd.Timestamp(ts).minute >= OPEN_MINUTE}


def slope_flags(f: pd.DataFrame) -> pd.DataFrame:
    z = f.copy().sort_values('time').reset_index(drop=True)
    for c in ('macd_slope','macd_signal_slope','macd_slope_spread','rsi_slope','mid_slope8','entry_score','close'):
        if c in z.columns:
            z[c] = pd.to_numeric(z[c], errors='coerce')
    z['macd_slope_d1'] = z['macd_slope'].diff()
    z['macd_slope_d2'] = z['macd_slope_d1'].diff()
    z['rsi_slope_d1'] = z['rsi_slope'].diff()
    z['spread_d1'] = z['macd_slope_spread'].diff()
    z['mid_slope_d1'] = z['mid_slope8'].diff()
    return z


def row_snapshot(sym: str, ts: pd.Timestamp, f: pd.DataFrame, raw: pd.DataFrame) -> dict:
    z = slope_flags(f)
    q = z[pd.to_datetime(z['time']) == ts]
    if q.empty:
        return {'symbol': sym, 'entry_time': ts, 'missing_5m': True}
    r = q.iloc[-1]
    idx = q.index[-1]
    p1 = z.loc[idx-1] if idx-1 in z.index else None
    p2 = z.loc[idx-2] if idx-2 in z.index else None

    rich = v16.build_rich_micro(raw, DoubleBollingerEngine5Config())
    m = rich[(pd.to_datetime(rich['time']) >= ts - pd.Timedelta(minutes=15)) & (pd.to_datetime(rich['time']) <= ts)].copy()
    last3 = m.tail(3)
    last5 = m.tail(5)

    def fv(x):
        try:
            x = float(x)
            return x if np.isfinite(x) else np.nan
        except Exception:
            return np.nan

    def b(name):
        try: return bool(r.get(name))
        except Exception: return False

    return {
        'symbol': sym,
        'entry_time': ts,
        'close': fv(r.get('close')),
        'score': fv(r.get('entry_score')),
        'trend_up': b('trend_up'),
        'gate_macd_context': b('gate_macd_context'),
        'gate_macd_rising': b('gate_macd_rising'),
        'gate_rsi_persistent': b('gate_rsi_persistent'),
        'macd_golden_cross': b('macd_golden_cross'),
        'inner_traverse_up': b('inner_traverse_up'),
        'macd_slope': fv(r.get('macd_slope')),
        'macd_slope_prev': fv(p1.get('macd_slope')) if p1 is not None else np.nan,
        'macd_slope_prev2': fv(p2.get('macd_slope')) if p2 is not None else np.nan,
        'macd_slope_d1': fv(r.get('macd_slope_d1')),
        'macd_slope_d2': fv(r.get('macd_slope_d2')),
        'spread': fv(r.get('macd_slope_spread')),
        'spread_prev': fv(p1.get('macd_slope_spread')) if p1 is not None else np.nan,
        'spread_d1': fv(r.get('spread_d1')),
        'rsi_slope': fv(r.get('rsi_slope')),
        'rsi_slope_prev': fv(p1.get('rsi_slope')) if p1 is not None else np.nan,
        'rsi_slope_d1': fv(r.get('rsi_slope_d1')),
        'mid_slope8': fv(r.get('mid_slope8')),
        'mid_slope_d1': fv(r.get('mid_slope_d1')),
        'micro_macd_last3_down_steps': int((np.diff(pd.to_numeric(last3['macd_slope_1m'], errors='coerce').to_numpy(dtype=float)) < 0).sum()) if len(last3) >= 2 else 0,
        'micro_macd_last5_down_steps': int((np.diff(pd.to_numeric(last5['macd_slope_1m'], errors='coerce').to_numpy(dtype=float)) < 0).sum()) if len(last5) >= 2 else 0,
        'micro_rsi_last': fv(last3.iloc[-1]['rsi_slope_1m']) if len(last3) else np.nan,
        'micro_rsi_prev': fv(last3.iloc[-2]['rsi_slope_1m']) if len(last3) >= 2 else np.nan,
        'micro_spread_last': fv(last3.iloc[-1]['spread_1m']) if len(last3) else np.nan,
        'micro_spread_prev': fv(last3.iloc[-2]['spread_1m']) if len(last3) >= 2 else np.nan,
    }


def classify(r: dict) -> str:
    # Diagnostic labels only. These do NOT alter the strategy.
    ms, msp = r.get('macd_slope'), r.get('macd_slope_prev')
    rs, rsp = r.get('rsi_slope'), r.get('rsi_slope_prev')
    md = r.get('macd_slope_d1')
    rd = r.get('rsi_slope_d1')
    micro_down = r.get('micro_macd_last5_down_steps', 0)
    micro_rsi = r.get('micro_rsi_last')
    if np.isfinite(ms) and np.isfinite(msp) and ms < msp and np.isfinite(rs) and rs <= 0:
        return 'DECELERATION_WAIT_OR_EXIT'
    if np.isfinite(md) and md < 0 and np.isfinite(rd) and rd < 0:
        return 'BOTH_SLOPES_DECELERATING'
    if micro_down >= 3 and np.isfinite(micro_rsi) and micro_rsi < 0:
        return 'MICRO_DECAY'
    if np.isfinite(ms) and np.isfinite(msp) and ms > msp and np.isfinite(rs) and np.isfinite(rsp) and rs > rsp:
        return 'ACCELERATION'
    return 'MIXED'


def main():
    raw = load_data()
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)
    raw_frames = base.build_cfg_frames(raw, cfg)
    f10 = {sym: v10._refine_entry_frame(f) for sym, f in raw_frames.items()}
    scored = reweight(f10, cfg, 0.0)
    ev10 = filt_open(v8.pack_entry_events(scored))

    rows = []
    for sym, tstr in WORST:
        ts = pd.Timestamp(tstr)
        snap = row_snapshot(sym, ts, scored[sym], raw[sym])
        snap['candidate_at_threshold'] = any(str(e[0]).zfill(6) == sym and float(e[2]) >= THRESHOLD for e in ev10.get(ts, []))
        snap['diagnostic_class'] = classify(snap)
        rows.append(snap)

    out = pd.DataFrame(rows)
    print('=== ENGINE5 V16 WORST-TRADE STATE DIAGNOSTIC ===')
    print('Diagnostic only: no rule changes, no parameter sweep. Uses cached Engine5 dataset.')
    print(out.to_string(index=False))
    print('\n=== CLASS COUNTS ===')
    print(out['diagnostic_class'].value_counts(dropna=False).to_string())
    path = OUTDIR / 'v16_worst_trade_state_diagnostic.csv'
    out.to_csv(path, index=False)
    print('\n[CSV]', path)


if __name__ == '__main__':
    main()
