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
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5, DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

THRESHOLD = 50
READY_LIFETIME_MIN = 5
OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')

TARGETS = [
    ('043260', pd.Timestamp('2026-08-18 10:25:00+09:00')),
    ('257720', pd.Timestamp('2026-08-18 14:30:00+09:00')),
    ('950160', pd.Timestamp('2026-08-21 09:50:00+09:00')),
]


def finite(x):
    try:
        v = float(x)
        return v if np.isfinite(v) else np.nan
    except Exception:
        return np.nan


def build_micro(raw_bars: pd.DataFrame, cfg: DoubleBollingerEngine5Config) -> pd.DataFrame:
    """Completed 1m indicators only; no future bar is used."""
    f = raw_bars.copy().sort_values('time').reset_index(drop=True)
    f['time'] = pd.to_datetime(f['time'])
    close = pd.to_numeric(f['close'], errors='coerce').astype(float)
    eng = DoubleBollingerEngine5(cfg)
    macd, signal = eng._macd(close)
    rsi = eng._rsi(close, cfg.rsi_period)
    f['macd_1m'] = macd
    f['signal_1m'] = signal
    f['macd_gap_1m'] = macd - signal
    f['macd_gap_delta_1m'] = f['macd_gap_1m'].diff()
    f['macd_slope_1m'] = macd.diff()
    f['signal_slope_1m'] = signal.diff()
    f['spread_1m'] = f['macd_slope_1m'] - f['signal_slope_1m']
    f['rsi_1m'] = rsi
    f['rsi_slope_1m'] = rsi.diff()
    f['vol'] = pd.to_numeric(f.get('volume', np.nan), errors='coerce')
    f['vol_med20'] = f['vol'].rolling(20, min_periods=5).median()
    f['vol_ratio20'] = f['vol'] / f['vol_med20'].replace(0, np.nan)
    return f[['time','open','high','low','close','macd_1m','signal_1m','macd_gap_1m','macd_gap_delta_1m',
              'macd_slope_1m','spread_1m','rsi_1m','rsi_slope_1m','vol_ratio20']]


def micro_row_at(m: pd.DataFrame, ts: pd.Timestamp):
    q = m[m['time'] <= pd.Timestamp(ts)]
    return None if q.empty else q.iloc[-1]


def stale_veto(row) -> bool:
    """Block only when both fast momentum legs are already weakening at execution."""
    if row is None:
        return True
    gd = finite(row['macd_gap_delta_1m'])
    rs = finite(row['rsi_slope_1m'])
    return bool(np.isfinite(gd) and np.isfinite(rs) and gd <= 0 and rs <= 0)


def fast_trigger(prev, row) -> bool:
    """Fast 1m trigger: positive MACD impulse + improving gap + positive RSI impulse.

    This intentionally asks less than the old V16 reacceleration gate: the MACD
    slope itself need not exceed the previous bar. The 5m context supplies the
    trend filter; 1m only decides whether to pull the trigger now.
    """
    if prev is None or row is None:
        return False
    vals = [row['macd_slope_1m'], row['macd_gap_delta_1m'], row['rsi_slope_1m']]
    if not all(np.isfinite(finite(x)) for x in vals):
        return False
    return bool(
        finite(row['macd_slope_1m']) > 0
        and finite(row['macd_gap_delta_1m']) > 0
        and finite(row['rsi_slope_1m']) > 0
    )


def event_from_5m_row(sym: str, row, exec_ts: pd.Timestamp, exec_price: float):
    iu = finite(row['inner_upper']); il = finite(row['inner_lower']); ou = finite(row['outer_upper']); mid = finite(row['mid'])
    band_r = iu - il if np.isfinite(iu) and np.isfinite(il) else np.nan
    if not np.isfinite(band_r) or band_r <= 0:
        return None
    score = finite(row['entry_score'])
    if not np.isfinite(score) or score < THRESHOLD:
        return None
    extended = bool(np.isfinite(ou) and float(exec_price) > ou)
    # Match V8/V17C tuple geometry; final bool is breakout_entry.
    return (
        str(sym).zfill(6), float(exec_price), score,
        finite(row.get('macd_slope_spread_strength', np.nan)),
        finite(row.get('rsi_slope_strength', np.nan)),
        float(band_r), float(band_r), iu, il, ou, mid, extended, False,
    )


def build_veto_stream(ev_v17c, micros):
    out = {}
    blocked = []
    for ts in sorted(ev_v17c):
        for c in ev_v17c[ts]:
            sym = str(c[0]).zfill(6)
            row = micro_row_at(micros[sym], pd.Timestamp(ts))
            veto = stale_veto(row)
            if veto:
                blocked.append({
                    'symbol': sym, 'time': pd.Timestamp(ts), 'score': finite(c[2]),
                    'macd_gap': finite(row['macd_gap_1m']) if row is not None else np.nan,
                    'macd_gap_delta': finite(row['macd_gap_delta_1m']) if row is not None else np.nan,
                    'rsi': finite(row['rsi_1m']) if row is not None else np.nan,
                    'rsi_slope': finite(row['rsi_slope_1m']) if row is not None else np.nan,
                })
            else:
                out.setdefault(pd.Timestamp(ts), []).append(c)
    return out, pd.DataFrame(blocked)


def build_ready_trigger_stream(scored, micros):
    """5m coarse context -> first valid 1m trigger within five minutes.

    Coarse context deliberately does NOT require the full V10/V17C entry_gate.
    It asks that the 5m trend is already structurally bullish and price is above
    the DBB mid, while MACD context is acceptable. This creates ENTRY_READY;
    execution is delegated to the 1m trigger.
    """
    events = {}
    diag = []
    seen = set()

    for sym, f in scored.items():
        sym = str(sym).zfill(6)
        m = micros[sym]
        z = f.copy().sort_values('time')
        for _, row in z.iterrows():
            ts = pd.Timestamp(row['time'])
            minute = ts.hour * 60 + ts.minute
            if minute < 9 * 60 + 10 or minute >= base.NO_ENTRY_MINUTE:
                continue

            iu = finite(row.get('inner_upper')); il = finite(row.get('inner_lower')); mid = finite(row.get('mid'))
            close5 = finite(row.get('close')); score = finite(row.get('entry_score'))
            bb_valid = np.isfinite(iu) and np.isfinite(il) and iu > il and np.isfinite(mid)
            context = bool(
                bb_valid
                and np.isfinite(close5) and close5 > mid
                and bool(row.get('trend_up', False))
                and bool(row.get('gate_macd_context', False))
                and np.isfinite(score) and score >= THRESHOLD
            )
            if not context:
                continue

            q = m[(m['time'] >= ts) & (m['time'] < ts + pd.Timedelta(minutes=READY_LIFETIME_MIN))].copy()
            if q.empty:
                continue
            prev = micro_row_at(m, ts - pd.Timedelta(minutes=1))
            chosen = None
            for _, mr in q.iterrows():
                if fast_trigger(prev, mr):
                    chosen = mr
                    break
                prev = mr

            rec = {
                'symbol': sym, 'ready_time': ts, 'ready_close_5m': close5, 'score': score,
                'trigger_time': pd.NaT, 'trigger_price': np.nan,
                'delay_min': np.nan, 'status': 'NO_1M_TRIGGER',
            }
            if chosen is not None:
                dts = pd.Timestamp(chosen['time'])
                key = (sym, dts)
                ev = event_from_5m_row(sym, row, dts, finite(chosen['close']))
                if ev is not None and key not in seen:
                    seen.add(key)
                    events.setdefault(dts, []).append(ev)
                    rec.update({
                        'trigger_time': dts,
                        'trigger_price': finite(chosen['close']),
                        'delay_min': (dts - ts).total_seconds() / 60.0,
                        'status': 'TRIGGERED',
                    })
            diag.append(rec)

    return events, pd.DataFrame(diag)


def stats(label, t):
    p = pd.to_numeric(t.pnl_pct, errors='coerce').dropna() if len(t) else pd.Series(dtype=float)
    gp = float(p[p > 0].sum()) if len(p) else 0.0
    gl = float(-p[p < 0].sum()) if len(p) else 0.0
    gross = float(p.sum()) if len(p) else 0.0
    n = len(p)
    return {
        'label': label, 'trades': n, 'wins': int((p > 0).sum()), 'losses': int((p <= 0).sum()),
        'win_pct': float((p > 0).mean() * 100) if n else 0.0,
        'gross_pct': gross, 'avg_pct': float(p.mean()) if n else 0.0,
        'pf': gp / gl if gl > 0 else np.inf,
        'maxloss_pct': float(p.min()) if n else np.nan,
        'net_rt025_sum_pct': gross - n * 0.25,
        'net_rt050_sum_pct': gross - n * 0.50,
    }


def target_report(label, events, micros):
    print(f'\n=== {label}: TARGET WINDOWS ===')
    for sym, target in TARGETS:
        print(f'-- {sym} around {target} --')
        rows = []
        for ts in sorted(events):
            if target - pd.Timedelta(minutes=20) <= pd.Timestamp(ts) <= target + pd.Timedelta(minutes=5):
                for c in events[ts]:
                    if str(c[0]).zfill(6) == sym:
                        mr = micro_row_at(micros[sym], pd.Timestamp(ts))
                        rows.append({
                            'event_time': pd.Timestamp(ts), 'price': finite(c[1]), 'score': finite(c[2]),
                            'macd_gap': finite(mr['macd_gap_1m']) if mr is not None else np.nan,
                            'gap_delta': finite(mr['macd_gap_delta_1m']) if mr is not None else np.nan,
                            'rsi': finite(mr['rsi_1m']) if mr is not None else np.nan,
                            'rsi_slope': finite(mr['rsi_slope_1m']) if mr is not None else np.nan,
                        })
        print(pd.DataFrame(rows).to_string(index=False) if rows else 'NONE')


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = load_data()
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    packed = v8.base.pack_exit_events(raw, base_cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    frames = base.build_cfg_frames(raw, cfg)
    f10 = {s: v10._refine_entry_frame(f) for s, f in frames.items()}
    scored = reweight(f10, cfg, 0.0)
    raw_entries = v8.pack_entry_events(scored)
    ev10 = sweep.filt_open(raw_entries)
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev_v17c, added, skipped = v17b.build_v17b(ev16, scored, waits)

    micros = {str(sym).zfill(6): build_micro(bars, cfg) for sym, bars in raw.items()}

    # A) Frozen V17C reference.
    t_base = multi.simulate_multi(packed, ev_v17c, states, THRESHOLD)

    # B) Same V17C entries, but cancel execution if 1m MACD gap and RSI are both weakening.
    ev_veto, vetoed = build_veto_stream(ev_v17c, micros)
    t_veto = multi.simulate_multi(packed, ev_veto, states, THRESHOLD)

    # C) Coarse 5m trend context arms the setup; first 1m impulse pulls the trigger.
    ev_hybrid, ready_diag = build_ready_trigger_stream(scored, micros)
    t_hybrid = multi.simulate_multi(packed, ev_hybrid, states, THRESHOLD)

    rows = pd.DataFrame([
        stats('V17C_BASE', t_base),
        stats('V17C_PLUS_1M_STALE_VETO', t_veto),
        stats('HYBRID_5M_CONTEXT_1M_TRIGGER', t_hybrid),
    ])

    print('=== V17C 5M CONTEXT + 1M TRIGGER EXPERIMENT ===')
    print('V17C_BASE is frozen reference.')
    print('VETO: same V17C entry stream; cancel only when current completed 1m MACD-gap delta <=0 AND RSI slope <=0.')
    print('HYBRID: 5m trend_up + MACD context + close>DBB mid + score>=50 arms ENTRY_READY; first 1m MACD impulse + gap expansion + RSI rise within 5m executes.')
    print('No production rule is changed.')
    print('BASE_BREAKOUT_ADDED=', added)
    print('BASE_BREAKOUT_SKIPPED=', skipped)
    print('\n=== SUMMARY ===')
    print(rows.to_string(index=False))
    print('\nVETO_BLOCKED_EVENTS=', len(vetoed))
    print('HYBRID_READY_COUNT=', len(ready_diag), 'TRIGGERED=', int((ready_diag.status == 'TRIGGERED').sum()) if len(ready_diag) else 0)

    target_report('V17C_BASE', ev_v17c, micros)
    target_report('V17C_PLUS_1M_STALE_VETO', ev_veto, micros)
    target_report('HYBRID_5M_CONTEXT_1M_TRIGGER', ev_hybrid, micros)

    print('\n=== VETOED TARGET/EXAMPLES ===')
    print(vetoed.sort_values(['time','symbol']).head(40).to_string(index=False) if len(vetoed) else 'NONE')

    print('\n=== HYBRID READY/TRIGGER EXAMPLES ===')
    focus = ready_diag[
        ready_diag.symbol.isin(['043260','257720','950160'])
        & (pd.to_datetime(ready_diag.ready_time).dt.date.isin([
            pd.Timestamp('2026-08-18').date(), pd.Timestamp('2026-08-21').date()
        ]))
    ] if len(ready_diag) else ready_diag
    print(focus[['symbol','ready_time','ready_close_5m','score','trigger_time','trigger_price','delay_min','status']].to_string(index=False) if len(focus) else 'NONE')

    rows.to_csv(OUT_DIR / 'v17c_5m_context_1m_trigger_summary.csv', index=False)
    vetoed.to_csv(OUT_DIR / 'v17c_1m_stale_vetoed_events.csv', index=False)
    ready_diag.to_csv(OUT_DIR / 'v17c_5m_ready_1m_trigger_detail.csv', index=False)
    t_base.to_csv(OUT_DIR / 'v17c_5m1m_base_trades.csv', index=False)
    t_veto.to_csv(OUT_DIR / 'v17c_5m1m_veto_trades.csv', index=False)
    t_hybrid.to_csv(OUT_DIR / 'v17c_5m1m_hybrid_trades.csv', index=False)

    print('\n[CSV]', OUT_DIR / 'v17c_5m_context_1m_trigger_summary.csv')
    print('[CSV]', OUT_DIR / 'v17c_1m_stale_vetoed_events.csv')
    print('[CSV]', OUT_DIR / 'v17c_5m_ready_1m_trigger_detail.csv')


if __name__ == '__main__':
    main()
