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

THRESHOLD = 50
FEE_RT_PCT = 0.25
WATCH_MIN = 5
OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')

# Raw MACD-signal oscillator units.  Start broad and let the data decide.
ARM_5M_LEVELS = [40.0, 50.0, 60.0]
ONE_M_ABOVE_LEVELS = [75.0, 100.0, 125.0]
ONE_M_BELOW_LEVELS = [125.0, 150.0, 175.0]

TARGET_SYM = '950260'
TARGET_DAY = pd.Timestamp('2026-08-21').date()
TARGET_START = pd.Timestamp('2026-08-21 12:05:00+09:00')
TARGET_END = pd.Timestamp('2026-08-21 12:30:00+09:00')


def finite(x):
    return h.finite(x)


def five_minute_context(row) -> bool:
    """Only basic 5m structural context. Golden cross is NOT an arm condition."""
    if row is None:
        return False
    vals = [row.get('inner_upper'), row.get('inner_lower'), row.get('mid'),
            row.get('close'), row.get('entry_score'), row.get('macd_gap_delta')]
    if not all(np.isfinite(finite(x)) for x in vals):
        return False
    return bool(
        finite(row['inner_upper']) > finite(row['inner_lower'])
        and finite(row['close']) > finite(row['mid'])
        and bool(row.get('trend_up', False))
        and finite(row['entry_score']) >= THRESHOLD
    )


def armed_5m(row, arm_level: float) -> bool:
    """Meaningful 5m oscillator acceleration. Weak little crosses are ignored."""
    return bool(
        five_minute_context(row)
        and finite(row.get('macd_gap_delta')) >= float(arm_level)
    )


def one_minute_power(row, above_level: float, below_level: float):
    """Execution power rule.

    If 1m MACD is already above Signal, require the lower oscillator-delta level.
    If it is still below Signal, require the higher level.  Golden cross itself
    never arms the stock and weak crosses never qualify.
    """
    if row is None:
        return False, 'NO_1M', np.nan, False
    macd = finite(row.get('macd_1m'))
    signal = finite(row.get('signal_1m'))
    delta = finite(row.get('macd_gap_delta_1m'))
    if not all(np.isfinite(x) for x in [macd, signal, delta]):
        return False, 'NONFINITE_1M', delta, False

    above = bool(macd > signal)
    need = float(above_level if above else below_level)
    ok = bool(delta >= need)
    return ok, ('ABOVE_SIGNAL_POWER' if above else 'PRE_CROSS_POWER'), delta, above


def build_power_stream(scored, micros, raw, arm_level, above_level, below_level):
    """5m oscillator power arms; first sufficiently powerful completed 1m bar executes."""
    events = {}
    diag = []
    seen = set()
    first_dates = {
        str(sym).zfill(6): pd.to_datetime(bars['time']).min().date()
        for sym, bars in raw.items() if len(bars)
    }

    for sym0, f0 in scored.items():
        sym = str(sym0).zfill(6)
        m = micros[sym]
        f = f0.copy().sort_values('time').reset_index(drop=True)
        prev_arm = False

        for _, row5 in f.iterrows():
            ts = pd.Timestamp(row5['time'])
            minute = ts.hour * 60 + ts.minute
            arm = bool(9 * 60 + 10 <= minute < base.NO_ENTRY_MINUTE and armed_5m(row5, arm_level))
            birth = arm and not prev_arm
            prev_arm = arm
            if not birth:
                continue
            if ts.date() == first_dates.get(sym):
                continue

            q = m[(m['time'] >= ts) & (m['time'] < ts + pd.Timedelta(minutes=WATCH_MIN))]
            chosen = None
            chosen_reason = None
            chosen_delta = np.nan
            chosen_above = False
            for _, r1 in q.iterrows():
                ok, reason, delta, above = one_minute_power(r1, above_level, below_level)
                if ok:
                    chosen = r1
                    chosen_reason = reason
                    chosen_delta = delta
                    chosen_above = above
                    break

            rec = {
                'symbol': sym,
                'arm_time': ts,
                'arm_5m_gap': finite(row5.get('macd_gap')),
                'arm_5m_delta': finite(row5.get('macd_gap_delta')),
                'arm_5m_macd': finite(row5.get('macd')),
                'arm_5m_signal': finite(row5.get('macd_signal')),
                'trigger_time': pd.NaT,
                'trigger_1m_delta': np.nan,
                'trigger_1m_above_signal': False,
                'status': 'NO_1M_POWER',
            }
            if chosen is not None:
                dts = pd.Timestamp(chosen['time'])
                ev = h.event_from_5m_row(sym, row5, dts, finite(chosen['close']))
                key = (sym, dts)
                if ev is not None and key not in seen:
                    seen.add(key)
                    events.setdefault(dts, []).append(ev)
                    rec.update({
                        'trigger_time': dts,
                        'trigger_1m_delta': chosen_delta,
                        'trigger_1m_above_signal': chosen_above,
                        'status': chosen_reason,
                    })
            diag.append(rec)

    return events, pd.DataFrame(diag)


def net_stats(label, t):
    g = pd.to_numeric(t.pnl_pct, errors='coerce').dropna() if len(t) else pd.Series(dtype=float)
    n = g - FEE_RT_PCT
    gp = float(n[n > 0].sum()) if len(n) else 0.0
    gl = float(-n[n < 0].sum()) if len(n) else 0.0
    return {
        'label': label,
        'trades': len(n),
        'net_wins': int((n > 0).sum()),
        'net_losses': int((n <= 0).sum()),
        'net_win_pct': float((n > 0).mean() * 100.0) if len(n) else 0.0,
        'net_sum_pct': float(n.sum()) if len(n) else 0.0,
        'net_avg_pct': float(n.mean()) if len(n) else 0.0,
        'net_pf': gp / gl if gl > 0 else np.inf,
        'gross_sum_pct': float(g.sum()) if len(g) else 0.0,
        'max_net_loss_pct': float(n.min()) if len(n) else np.nan,
    }


def print_target_raw(scored, micros):
    print('\n=== TARGET 950260 2026-08-21 5M 11:55-12:30 ===')
    f = scored[TARGET_SYM]
    q5 = f[(f['time'] >= pd.Timestamp('2026-08-21 11:55:00+09:00')) &
           (f['time'] <= TARGET_END)].copy()
    cols5 = ['time','close','macd','macd_signal','macd_gap','macd_gap_delta',
             'macd_golden_cross','trend_up','entry_score']
    print(q5[[c for c in cols5 if c in q5.columns]].to_string(index=False))

    print('\n=== TARGET 950260 2026-08-21 1M 12:05-12:30 ===')
    m = micros[TARGET_SYM]
    q1 = m[(m['time'] >= TARGET_START) & (m['time'] <= TARGET_END)].copy()
    q1['above_signal'] = q1['macd_1m'] > q1['signal_1m']
    q1['golden_cross_now'] = (~q1['above_signal'].shift(1).fillna(False)) & q1['above_signal']
    cols1 = ['time','close','macd_1m','signal_1m','macd_gap_1m','macd_gap_delta_1m',
             'above_signal','golden_cross_now','rsi_1m','rsi_slope_1m']
    print(q1[cols1].to_string(index=False))


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
    ev17, _, _ = v17b.build_v17b(ev16, scored, waits)
    micros = {str(sym).zfill(6): h.build_micro(bars, cfg) for sym, bars in raw.items()}
    ev18, _ = h.build_veto_stream(ev17, micros)
    t18 = multi.simulate_multi(packed, ev18, states, THRESHOLD)

    print('=== V20 5M OSCILLATOR TREND -> 1M POWER VALIDATION ===')
    print('5m golden cross is NOT an arm condition.')
    print('5m arm = raw MACD-signal oscillator delta >= threshold + basic bullish structure.')
    print('1m BUY = strong raw oscillator delta; lower threshold if MACD>Signal, higher threshold if still below.')
    print('Weak/skinny crosses are ignored. Fee = 0.25% round-trip.')
    print('5m timestamps are completed-bar close labels, so no future 5m bar is used.')

    print_target_raw(scored, micros)

    rows = [net_stats('V18_REFERENCE', t18)]
    target_rows = []
    for arm in ARM_5M_LEVELS:
        for above in ONE_M_ABOVE_LEVELS:
            for below in ONE_M_BELOW_LEVELS:
                if below <= above:
                    continue
                events, diag = build_power_stream(scored, micros, raw, arm, above, below)
                t = multi.simulate_multi(packed, events, states, THRESHOLD)
                label = f'ARM{arm:.0f}_ABOVE{above:.0f}_BELOW{below:.0f}'
                s = net_stats(label, t)
                s.update({'arm_5m': arm, 'one_m_above': above, 'one_m_below': below,
                          'armed': len(diag),
                          'triggered': int((diag.status != 'NO_1M_POWER').sum()) if len(diag) else 0})
                rows.append(s)

                td = diag[(diag.symbol == TARGET_SYM) &
                          (pd.to_datetime(diag.arm_time).dt.date == TARGET_DAY)] if len(diag) else diag
                if len(td):
                    for _, r in td.iterrows():
                        target_rows.append({
                            'label': label,
                            'arm_time': r.arm_time,
                            'arm_5m_delta': r.arm_5m_delta,
                            'trigger_time': r.trigger_time,
                            'trigger_1m_delta': r.trigger_1m_delta,
                            'above_signal': r.trigger_1m_above_signal,
                            'status': r.status,
                        })

    summary = pd.DataFrame(rows)
    sweep_only = summary[summary.label != 'V18_REFERENCE'].copy().sort_values(
        ['net_sum_pct','net_pf','net_win_pct','trades'], ascending=[False,False,False,False]
    )

    print('\n=== V18 REFERENCE ===')
    print(summary[summary.label == 'V18_REFERENCE'].to_string(index=False))
    print('\n=== SWEEP SUMMARY ===')
    print(sweep_only.to_string(index=False))
    print('\n=== BEST ===')
    print(sweep_only.iloc[0].to_string() if len(sweep_only) else 'NONE')

    print('\n=== TARGET 950260 2026-08-21 ARM/TRIGGER RESULTS ===')
    tr = pd.DataFrame(target_rows)
    if len(tr):
        focus = tr[(pd.to_datetime(tr.arm_time) >= pd.Timestamp('2026-08-21 11:55:00+09:00')) &
                   (pd.to_datetime(tr.arm_time) <= TARGET_END)]
        print(focus.to_string(index=False) if len(focus) else 'NO ARM IN 11:55-12:30')
    else:
        print('NO TARGET ARMS')

    out_summary = OUT_DIR / 'v20_5m_oscillator_summary.csv'
    out_target = OUT_DIR / 'v20_950260_0821_oscillator_diag.csv'
    summary.to_csv(out_summary, index=False)
    pd.DataFrame(target_rows).to_csv(out_target, index=False)
    print('\nWROTE', out_summary)
    print('WROTE', out_target)


if __name__ == '__main__':
    main()
