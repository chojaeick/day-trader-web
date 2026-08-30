from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import tools.validate_engine5_v17c_5m_context_1m_trigger as h
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
SRC = OUT_DIR / 'slow_turn_nodeep6_cases.csv'
OUT = OUT_DIR / 'slow_turn_near3_extension.csv'


def n(x):
    return str(x).zfill(6)


def num(x):
    return pd.to_numeric(x, errors='coerce')


def safe_pct(a, b):
    try:
        a = float(a); b = float(b)
        if not np.isfinite(a) or not np.isfinite(b) or a == 0:
            return np.nan
        return (b / a - 1.0) * 100.0
    except Exception:
        return np.nan


def last_valid(s):
    q = num(s).dropna()
    return float(q.iloc[-1]) if len(q) else np.nan


def first_valid(s):
    q = num(s).dropna()
    return float(q.iloc[0]) if len(q) else np.nan


def metric_window(m: pd.DataFrame, entry: pd.Timestamp):
    q = m[(m.time <= entry) & (m.time >= entry - pd.Timedelta(minutes=6))].copy().sort_values('time')
    if q.empty:
        return {}

    close = num(q.get('close', pd.Series(index=q.index, dtype=float)))
    high = num(q.get('high', pd.Series(index=q.index, dtype=float)))
    low = num(q.get('low', pd.Series(index=q.index, dtype=float)))
    gapd = num(q.get('macd_gap_delta_1m', pd.Series(index=q.index, dtype=float)))
    rsis = num(q.get('rsi_slope_1m', pd.Series(index=q.index, dtype=float)))

    c = close.dropna()
    total = safe_pct(c.iloc[0], c.iloc[-1]) if len(c) >= 2 else np.nan
    last1 = safe_pct(c.iloc[-2], c.iloc[-1]) if len(c) >= 2 else np.nan
    last2 = safe_pct(c.iloc[-3], c.iloc[-1]) if len(c) >= 3 else np.nan

    abs_total = abs(total) if np.isfinite(total) else np.nan
    last1_share = abs(last1) / abs_total if np.isfinite(last1) and np.isfinite(abs_total) and abs_total > 0 else np.nan
    last2_share = abs(last2) / abs_total if np.isfinite(last2) and np.isfinite(abs_total) and abs_total > 0 else np.nan

    hh = high.dropna()
    ll = low.dropna()
    entry_close = float(c.iloc[-1]) if len(c) else np.nan
    window_high = float(hh.max()) if len(hh) else np.nan
    window_low = float(ll.min()) if len(ll) else np.nan
    from_low = safe_pct(window_low, entry_close)
    below_high = safe_pct(window_high, entry_close) if np.isfinite(window_high) else np.nan

    gd = gapd.dropna()
    rs = rsis.dropna()
    gap_last = float(gd.iloc[-1]) if len(gd) else np.nan
    gap_prev = float(gd.iloc[-2]) if len(gd) >= 2 else np.nan
    rsi_last = float(rs.iloc[-1]) if len(rs) else np.nan
    rsi_prev = float(rs.iloc[-2]) if len(rs) >= 2 else np.nan

    return dict(
        bars=len(q),
        close_progress_6m_pct=total,
        last1m_return_pct=last1,
        last2m_return_pct=last2,
        last1_share_of_6m=last1_share,
        last2_share_of_6m=last2_share,
        rise_from_6m_low_pct=from_low,
        entry_vs_6m_high_pct=below_high,
        gap_delta_1m_last=gap_last,
        gap_delta_1m_prev=gap_prev,
        gap_delta_1m_accel=(gap_last-gap_prev if np.isfinite(gap_last) and np.isfinite(gap_prev) else np.nan),
        rsi_slope_1m_last=rsi_last,
        rsi_slope_1m_prev=rsi_prev,
        rsi_slope_1m_accel=(rsi_last-rsi_prev if np.isfinite(rsi_last) and np.isfinite(rsi_prev) else np.nan),
        gap_delta_1m_max=float(gd.max()) if len(gd) else np.nan,
        rsi_slope_1m_max=float(rs.max()) if len(rs) else np.nan,
    )


def main():
    if not SRC.exists():
        raise FileNotFoundError(f'{SRC} not found. Run tools.diagnose_engine5_slow_turn_nodeep6_cases first.')

    c = pd.read_csv(SRC)
    c['symbol'] = c['symbol'].astype(str).str.zfill(6)
    c['entry_time'] = pd.to_datetime(c['entry_time'])
    near = c[c['regime'] == 'NEAR_LE1_5'].copy().sort_values(['entry_time','symbol'])
    if near.empty:
        print('NO NEAR CASES')
        return

    raw = {n(k): v for k, v in load_data().items()}
    cfg = DoubleBollingerEngine5Config()
    micro_cache = {}
    rows = []

    for _, r in near.iterrows():
        sym = n(r.symbol)
        if sym not in micro_cache:
            micro_cache[sym] = h.build_micro(raw[sym], cfg)
        m = micro_cache[sym].copy()
        m['time'] = pd.to_datetime(m['time'])
        met = metric_window(m, pd.Timestamp(r.entry_time))
        rows.append(dict(
            result=r.get('result'), symbol=sym, entry_time=r.entry_time,
            net_pct=r.get('net_pct'), zero_cross_bars=r.get('zero_cross_bars'),
            gap_delta_5m=r.get('gap_delta_5m'), rsi_slope_5m=r.get('rsi_slope_5m'),
            joint5_persistence=r.get('joint5_persistence'), joint1_persistence=r.get('joint1_persistence'),
            price_progress_1m_pct=r.get('price_progress_1m_pct'), **met,
        ))

    out = pd.DataFrame(rows)
    out.to_csv(OUT, index=False)

    cols = [
        'result','symbol','entry_time','net_pct','zero_cross_bars',
        'gap_delta_5m','rsi_slope_5m','joint5_persistence','joint1_persistence',
        'close_progress_6m_pct','last1m_return_pct','last2m_return_pct',
        'last1_share_of_6m','last2_share_of_6m','rise_from_6m_low_pct','entry_vs_6m_high_pct',
        'gap_delta_1m_prev','gap_delta_1m_last','gap_delta_1m_accel',
        'rsi_slope_1m_prev','rsi_slope_1m_last','rsi_slope_1m_accel',
    ]

    print('\n=== SLOW TURN NEAR <=1.5 : 3-CASE EXTENSION DIAGNOSTIC ===')
    print('Descriptive only. No threshold/rule changed.')
    print(out[cols].to_string(index=False))
    print('\nInterpretation targets:')
    print('- Is the LOSS dominated by the last 1-2 minutes rather than a steady 6m rise?')
    print('- Is entry already sitting at/near the 6m high after a large rise from the 6m low?')
    print('- Do MACD/RSI show terminal acceleration versus coherent continuation?')
    print('WROTE', OUT)


if __name__ == '__main__':
    main()
