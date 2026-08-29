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
import tools.validate_engine5_v17c_breakout_first10_hwm1pct as v17c
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data, summary

OUTDIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
OUTDIR.mkdir(parents=True, exist_ok=True)
THRESHOLD = 50
OPEN_MINUTE = 9 * 60 + 10
EDGE_MIN = 0.10
VOL_PREV_MIN = 1.50
TARGET_SYM = '950260'
TARGET_TS = pd.Timestamp('2026-08-21 10:00:00+09:00')


def filt_open(ev):
    return {ts: rows for ts, rows in ev.items() if pd.Timestamp(ts).hour * 60 + pd.Timestamp(ts).minute >= OPEN_MINUTE}


def build_diag_frames(scored):
    out = {}
    for sym, f0 in scored.items():
        f = f0.copy().sort_values('time').reset_index(drop=True)
        for c in ['macd','macd_signal','rsi_slope','volume','outer_upper','close']:
            if c in f.columns:
                f[c] = pd.to_numeric(f[c], errors='coerce')
        f['prev_macd'] = f['macd'].shift(1)
        f['prev_signal'] = f['macd_signal'].shift(1)
        f['prev_rsi_slope'] = f['rsi_slope'].shift(1)
        f['prev_volume'] = f['volume'].shift(1)
        f['macd_ratio'] = f['macd'] / f['prev_macd'].replace(0.0, np.nan)
        f['signal_ratio'] = f['macd_signal'] / f['prev_signal'].replace(0.0, np.nan)
        f['ratio_edge'] = f['macd_ratio'] - f['signal_ratio']
        f['volume_prev_ratio'] = f['volume'] / f['prev_volume'].replace(0.0, np.nan)
        out[str(sym).zfill(6)] = f
    return out


def row_at_or_before(frames, sym, ts):
    f = frames.get(str(sym).zfill(6))
    if f is None or f.empty:
        return None
    t = pd.Timestamp(ts)
    q = f[pd.to_datetime(f['time']) <= t]
    if q.empty:
        return None
    return q.iloc[-1]


def event_diag(event, ts, frames):
    sym = str(event[0]).zfill(6)
    close = float(event[1])
    ou = float(event[9]) if len(event) > 9 and np.isfinite(float(event[9])) else np.nan
    breakout = bool(event[-1]) if len(event) >= 13 else False
    r = row_at_or_before(frames, sym, ts)
    def fv(name):
        if r is None:
            return np.nan
        try:
            x = float(r.get(name, np.nan))
            return x if np.isfinite(x) else np.nan
        except Exception:
            return np.nan
    outer_touch = bool(np.isfinite(ou) and close >= ou)
    ratio_edge = fv('ratio_edge')
    macd_ratio = fv('macd_ratio')
    signal_ratio = fv('signal_ratio')
    rsi_slope = fv('rsi_slope')
    prev_rsi_slope = fv('prev_rsi_slope')
    vol_prev_ratio = fv('volume_prev_ratio')
    edge_ok = bool(np.isfinite(macd_ratio) and macd_ratio > 1.0 and np.isfinite(ratio_edge) and ratio_edge > EDGE_MIN)
    rsi_ok = bool(np.isfinite(rsi_slope) and rsi_slope > 0 and np.isfinite(prev_rsi_slope) and rsi_slope >= prev_rsi_slope)
    vol_ok = bool(np.isfinite(vol_prev_ratio) and vol_prev_ratio >= VOL_PREV_MIN)
    return {
        'symbol': sym, 'time': pd.Timestamp(ts), 'close': close, 'outer_upper': ou,
        'outer_touch': outer_touch, 'breakout_entry': breakout,
        'macd': fv('macd'), 'prev_macd': fv('prev_macd'),
        'signal': fv('macd_signal'), 'prev_signal': fv('prev_signal'),
        'macd_ratio': macd_ratio, 'signal_ratio': signal_ratio, 'ratio_edge': ratio_edge,
        'rsi_slope': rsi_slope, 'prev_rsi_slope': prev_rsi_slope,
        'volume_prev_ratio': vol_prev_ratio,
        'edge_ok': edge_ok, 'rsi_ok': rsi_ok, 'vol_ok': vol_ok,
    }


def filter_events(events, frames, mode):
    out = {}
    removed = []
    for ts, rows in events.items():
        keep = []
        for e in rows:
            d = event_diag(e, ts, frames)
            # V17B massive-volume breakout events already have their own strong-breakout
            # qualification and V16 WAIT veto. Do not re-filter those here.
            if d['breakout_entry'] or not d['outer_touch']:
                keep.append(e)
                continue
            if mode == 'EDGE':
                ok = d['edge_ok']
            elif mode == 'EDGE_RSI':
                ok = d['edge_ok'] and d['rsi_ok']
            elif mode == 'EDGE_RSI_VOL':
                ok = d['edge_ok'] and d['rsi_ok'] and d['vol_ok']
            else:
                raise ValueError(mode)
            if ok:
                keep.append(e)
            else:
                d['filter_mode'] = mode
                removed.append(d)
        if keep:
            out[ts] = keep
    return out, pd.DataFrame(removed)


def metrics(name, trades):
    if trades is None or len(trades) == 0:
        return {'name': name, 'trades': 0, 'wins': 0, 'losses': 0, 'win_rate': 0.0, 'gross_pct': 0.0, 'avg_pct': 0.0, 'pf': np.nan, 'max_loss_pct': np.nan}
    p = pd.to_numeric(trades['pnl_pct'], errors='coerce').dropna()
    wins = int((p > 0).sum()); losses = int((p <= 0).sum())
    gp = float(p[p > 0].sum()); gl = float(-p[p < 0].sum())
    return {
        'name': name, 'trades': len(p), 'wins': wins, 'losses': losses,
        'win_rate': wins / len(p) * 100.0 if len(p) else 0.0,
        'gross_pct': float(p.sum()), 'avg_pct': float(p.mean()),
        'pf': gp / gl if gl > 0 else np.inf,
        'max_loss_pct': float(p.min()) if len(p) else np.nan,
    }


def independent_removed_outcomes(removed, original_events, packed_exits, state_events):
    rows = []
    if removed is None or removed.empty:
        return pd.DataFrame()
    for d in removed.itertuples(index=False):
        ts = pd.Timestamp(d.time); sym = str(d.symbol).zfill(6)
        es = [e for e in original_events.get(ts, []) if str(e[0]).zfill(6) == sym]
        if not es:
            continue
        t = v17c.simulate_unconditional_hwm(packed_exits, {ts: [es[0]]}, state_events, THRESHOLD)
        rec = dict(d._asdict())
        if len(t):
            r = t.iloc[0]
            rec.update({'ind_exit_time': pd.Timestamp(r.exit_time), 'ind_pnl_pct': float(r.pnl_pct), 'ind_reason': str(r.reason)})
        else:
            rec.update({'ind_exit_time': pd.NaT, 'ind_pnl_pct': np.nan, 'ind_reason': 'NO_TRADE'})
        rows.append(rec)
    return pd.DataFrame(rows)


def main():
    raw = load_data()
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    packed_exits = v8.base.pack_exit_events(raw, base_cfg)
    state_events = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    raw_frames = base.build_cfg_frames(raw, cfg)
    f10 = {s: v10._refine_entry_frame(f) for s, f in raw_frames.items()}
    scored = reweight(f10, cfg, 0.0)
    diag_frames = build_diag_frames(scored)

    ev10 = filt_open(v8.pack_entry_events(scored))
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev17b, added, skipped = v17b.build_v17b(ev16, scored, waits)

    print('=== ENGINE5 V18 OUTER-UPPER STRONG-BREAKOUT FILTER VALIDATION ===')
    print(f'Rule scope: ordinary entries only when close >= outer_upper. Existing V17B massive-volume breakout branch is preserved.')
    print(f'EDGE = MACD_ratio - Signal_ratio > {EDGE_MIN:.2f}, with MACD_ratio > 1.0')
    print('RSI = current RSI slope > 0 and not weaker than previous completed 5m RSI slope')
    print(f'VOL = current 5m volume / previous 5m volume >= {VOL_PREV_MIN:.2f}x')

    target = []
    for ts, rows in ev17b.items():
        for e in rows:
            if str(e[0]).zfill(6) == TARGET_SYM and pd.Timestamp(ts) == TARGET_TS:
                target.append(event_diag(e, ts, diag_frames))
    print('\n=== TARGET 950260 2026-08-21 10:00 ===')
    print(pd.DataFrame(target).to_string(index=False) if target else 'TARGET_EVENT_NOT_FOUND')

    variants = [('A_V17C_CURRENT', ev17b, pd.DataFrame())]
    for mode in ['EDGE', 'EDGE_RSI', 'EDGE_RSI_VOL']:
        ev, removed = filter_events(ev17b, diag_frames, mode)
        variants.append((f'B_{mode}', ev, removed))

    summary_rows = []
    base_trades = None
    for name, ev, removed in variants:
        t = v17c.simulate_unconditional_hwm(packed_exits, ev, state_events, THRESHOLD)
        if base_trades is None:
            base_trades = t
        m = metrics(name, t)
        m['removed_candidates'] = int(len(removed))
        summary_rows.append(m)
        print(f"\n{name}: trades={m['trades']} wins={m['wins']} losses={m['losses']} win={m['win_rate']:.2f}% gross={m['gross_pct']:+.4f}% avg={m['avg_pct']:+.4f}% pf={m['pf']:.3f} maxloss={m['max_loss_pct']:+.4f}% removed={len(removed)}")
        if len(removed):
            ind = independent_removed_outcomes(removed, ev17b, packed_exits, state_events)
            print('REMOVED CANDIDATES + INDEPENDENT CURRENT-RULE OUTCOME')
            cols = ['symbol','time','close','outer_upper','macd_ratio','signal_ratio','ratio_edge','rsi_slope','prev_rsi_slope','volume_prev_ratio','ind_pnl_pct','ind_reason']
            print(ind[[c for c in cols if c in ind.columns]].to_string(index=False))
            ind.to_csv(OUTDIR / f'v18_{name.lower()}_removed_independent.csv', index=False)

    board = pd.DataFrame(summary_rows)
    print('\n=== SUMMARY ===')
    print(board.to_string(index=False))

    # Regression checks: preserve known good/important paths and verify target filtering.
    print('\n=== REGRESSION CHECKS ===')
    for name, ev, removed in variants[1:]:
        target_removed = False if removed.empty else bool(((removed['symbol'].astype(str).str.zfill(6) == TARGET_SYM) & (pd.to_datetime(removed['time']) == TARGET_TS)).any())
        good_257720 = any(str(e[0]).zfill(6) == '257720' for e in ev.get(pd.Timestamp('2026-08-12 09:10:00+09:00'), []))
        breakout_257720 = any(str(e[0]).zfill(6) == '257720' for e in ev.get(pd.Timestamp('2026-08-18 14:20:00+09:00'), []))
        veto_484810 = not any(str(e[0]).zfill(6) == '484810' for e in ev.get(pd.Timestamp('2026-08-11 09:10:00+09:00'), []))
        print(f'{name}: target950260_removed={target_removed} preserve_257720_0812_0910={good_257720} preserve_257720_breakout_0818_1420={breakout_257720} preserve_484810_veto={veto_484810}')

    out = OUTDIR / 'v18_outer_upper_breakout_filter_summary.csv'
    board.to_csv(out, index=False)
    print('\n[CSV]', out)


if __name__ == '__main__':
    main()
