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
OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
TARGET_SYM = '950260'
TARGET_DAY = pd.Timestamp('2026-08-21').date()

# Concept test, not production tuning.
# 5m: a meaningful positive oscillator impulse/cross arms observation.
# 1m: buy only when the same positive oscillator trend persists instead of flickering.
ARM_LEVELS = [40.0, 50.0, 60.0]
LOOKBACKS = [3, 4, 5]
MIN_POS_RATIOS = [0.67, 0.75, 0.80]
MAX_PULLBACK_RATIOS = [0.25, 0.40]
MAX_WAIT_MIN = 5


def finite(x):
    return h.finite(x)


def arm_5m(row, arm_level):
    d = finite(row.get('macd_gap_delta'))
    gap = finite(row.get('macd_gap'))
    prev_gap = gap - d if np.isfinite(gap) and np.isfinite(d) else np.nan
    if not np.isfinite(d):
        return False
    # Do not require legacy trend_up. We are detecting the onset itself.
    # Either a strong oscillator impulse, or a positive cross accompanied by meaningful expansion.
    cross = bool(row.get('macd_golden_cross', False))
    return bool(d >= arm_level or (cross and d >= arm_level * 0.75))


def continuity_1m(m, ts, lookback, min_pos_ratio, max_pullback_ratio):
    q = m[m.time <= pd.Timestamp(ts)].tail(int(lookback)).copy()
    if len(q) < lookback:
        return False, None
    gaps = pd.to_numeric(q.macd_gap_1m, errors='coerce').to_numpy(float)
    if not np.isfinite(gaps).all():
        return False, None
    diffs = np.diff(gaps)
    if len(diffs) == 0:
        return False, None
    positive_ratio = float((diffs > 0).mean())
    total_rise = float(gaps[-1] - gaps[0])
    pos_sum = float(diffs[diffs > 0].sum()) if np.any(diffs > 0) else 0.0
    worst_pullback = float(-diffs.min()) if np.any(diffs < 0) else 0.0
    pullback_ratio = worst_pullback / max(pos_sum, 1e-9)
    above = bool(gaps[-1] > 0)
    # Golden/above-signal state lowers the burden, but does not create the signal.
    need_ratio = max(0.50, min_pos_ratio - (0.08 if above else 0.0))
    ok = bool(
        total_rise > 0
        and positive_ratio >= need_ratio
        and pullback_ratio <= max_pullback_ratio
        and finite(q.iloc[-1].get('macd_slope_1m')) > 0
    )
    metrics = dict(
        start_gap=gaps[0], end_gap=gaps[-1], total_rise=total_rise,
        positive_ratio=positive_ratio, pullback_ratio=pullback_ratio,
        above_signal=above,
    )
    return ok, metrics


def build_events(scored, micros, arm_level, lookback, min_pos_ratio, max_pullback_ratio):
    events, diag, seen = {}, [], set()
    for sym0, f in scored.items():
        sym = str(sym0).zfill(6)
        m = micros[sym]
        z = f.copy().sort_values('time').reset_index(drop=True)
        prev_arm = False
        for _, row5 in z.iterrows():
            ts = pd.Timestamp(row5.time)
            minute = ts.hour * 60 + ts.minute
            now_arm = bool(9*60+10 <= minute < base.NO_ENTRY_MINUTE and arm_5m(row5, arm_level))
            birth = now_arm and not prev_arm
            prev_arm = now_arm
            if not birth:
                continue
            q = m[(m.time >= ts) & (m.time <= ts + pd.Timedelta(minutes=MAX_WAIT_MIN))]
            chosen = None; mm = None
            for _, r in q.iterrows():
                ok, metrics = continuity_1m(m, r.time, lookback, min_pos_ratio, max_pullback_ratio)
                if ok:
                    chosen = r; mm = metrics; break
            rec = dict(symbol=sym, arm_time=ts, arm_gap_delta=finite(row5.get('macd_gap_delta')),
                       arm_gap=finite(row5.get('macd_gap')), trigger_time=pd.NaT, status='NO_CONTINUITY')
            if chosen is not None:
                dts = pd.Timestamp(chosen.time)
                ev = h.event_from_5m_row(sym, row5, dts, finite(chosen.close))
                key = (sym, dts)
                if ev is not None and key not in seen:
                    seen.add(key); events.setdefault(dts, []).append(ev)
                    rec.update(trigger_time=dts, status='TRIGGERED', **mm)
            diag.append(rec)
    return events, pd.DataFrame(diag)


def stats(label, t):
    g = pd.to_numeric(t.pnl_pct, errors='coerce').dropna() if len(t) else pd.Series(dtype=float)
    n = g - FEE_RT_PCT
    gp = float(n[n > 0].sum()) if len(n) else 0.0
    gl = float(-n[n < 0].sum()) if len(n) else 0.0
    return dict(label=label, trades=len(n), net_wins=int((n > 0).sum()),
                net_win_pct=float((n > 0).mean()*100) if len(n) else 0.0,
                net_sum_pct=float(n.sum()) if len(n) else 0.0,
                net_pf=gp/gl if gl > 0 else np.inf,
                gross_sum_pct=float(g.sum()) if len(g) else 0.0)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = load_data()
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)
    packed = v8.base.pack_exit_events(raw, base_cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    frames = base.build_cfg_frames(raw, cfg)
    f10 = {s:v10._refine_entry_frame(f) for s,f in frames.items()}
    scored = reweight(f10, cfg, 0.0)
    micros = {str(s).zfill(6):h.build_micro(b, cfg) for s,b in raw.items()}

    # Frozen V18 reference.
    raw_entries = v8.pack_entry_events(scored)
    ev10 = sweep.filt_open(raw_entries)
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev17, _, _ = v17b.build_v17b(ev16, scored, waits)
    ev18, _ = h.build_veto_stream(ev17, micros)
    t18 = multi.simulate_multi(packed, ev18, states, THRESHOLD)
    print('=== V20 TREND CONTINUITY: 5M -> 1M ===')
    print('Golden cross is context, not a standalone BUY. Weak/flickering 1m trends are rejected.')
    print(pd.DataFrame([stats('V18_REFERENCE', t18)]).to_string(index=False))

    rows=[]; target_rows=[]
    for arm in ARM_LEVELS:
        for lb in LOOKBACKS:
            for pr in MIN_POS_RATIOS:
                for pb in MAX_PULLBACK_RATIOS:
                    ev, diag = build_events(scored, micros, arm, lb, pr, pb)
                    t = multi.simulate_multi(packed, ev, states, THRESHOLD)
                    label=f'ARM{arm:.0f}_LB{lb}_POS{pr:.2f}_PB{pb:.2f}'
                    s=stats(label,t); s.update(arm_5m=arm, lookback=lb, min_pos_ratio=pr,
                                               max_pullback_ratio=pb, armed=len(diag),
                                               triggered=int((diag.status=='TRIGGERED').sum()) if len(diag) else 0)
                    rows.append(s)
                    if len(diag):
                        q=diag[(diag.symbol==TARGET_SYM) & (pd.to_datetime(diag.arm_time).dt.date==TARGET_DAY)].copy()
                        if len(q):
                            q.insert(0,'label',label); target_rows.append(q)
    summary=pd.DataFrame(rows).sort_values(['net_sum_pct','net_pf','net_win_pct'],ascending=False)
    print('\n=== SWEEP SUMMARY ===')
    print(summary.to_string(index=False))
    print('\n=== TARGET 950260 2026-08-21 ===')
    target=pd.concat(target_rows,ignore_index=True) if target_rows else pd.DataFrame()
    print(target.to_string(index=False) if len(target) else 'NO ARM')
    summary.to_csv(OUT_DIR/'v20_trend_continuity_summary.csv',index=False)
    target.to_csv(OUT_DIR/'v20_950260_0821_trend_continuity.csv',index=False)
    print('\nWROTE', OUT_DIR/'v20_trend_continuity_summary.csv')
    print('WROTE', OUT_DIR/'v20_950260_0821_trend_continuity.csv')

if __name__ == '__main__':
    main()
