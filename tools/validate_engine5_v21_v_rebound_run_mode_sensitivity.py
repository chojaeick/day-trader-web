from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.validate_engine5_v20_regime_transition as rt
import tools.validate_engine5_v21_v_rebound_structural_stop as old
import tools.validate_engine5_v21_v_rebound_state_machine as sm
import tools.validate_engine5_v21_v_rebound_reaccel as ra
import tools.validate_engine5_v21_v_rebound_momentum_preservation as mp
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
OUT_SUMMARY = OUT_DIR / 'v21_v_rebound_run_mode_sensitivity_summary.csv'
OUT_CASES = OUT_DIR / 'v21_v_rebound_run_mode_sensitivity_cases.csv'

THRESHOLD = 50
RAW_MIN = 30.0
LEG_MIN = 2.0
STOP_CAP = 2.0
VOL_MIN = 1.0
FEE_RT_PCT = 0.25
ACTIVATIONS = [1.0, 2.0, 3.0]
GIVEBACKS = [1.0, 1.5, 2.0, 3.0]


def n(x): return str(x).zfill(6)

def f(x):
    try:
        y = float(x)
        return y if np.isfinite(y) else np.nan
    except Exception:
        return np.nan

def ret_pct(a, b):
    a = f(a); b = f(b)
    return (b / a - 1.0) * 100.0 if np.isfinite(a) and a != 0 and np.isfinite(b) else np.nan


def main():
    raw = {n(k): v for k, v in load_data().items()}
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2., rsi_slope_full_ratio=1.5)
    packed = v8.base.pack_exit_events(raw, base_cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    frames = base.build_cfg_frames(raw, cfg)
    f10 = {n(s): v10._refine_entry_frame(x) for s, x in frames.items()}
    scored = {n(s): x for s, x in reweight(f10, cfg, 0.).items()}
    completed = {s: rt.add_completed_strength(x) for s, x in scored.items()}

    allc = []
    features = {}
    for sym, bars in raw.items():
        pf, m = old.load_cache(sym, bars, cfg, completed[sym])
        z = sm.add_features(pf, m, bars).sort_values('time').reset_index(drop=True)
        z['time'] = pd.to_datetime(z.time)
        features[sym] = z
        c = sm.state_candidates(sym, z, scored[sym], RAW_MIN, LEG_MIN)
        if len(c): allc.append(c)

    cand = pd.concat(allc, ignore_index=True) if allc else pd.DataFrame()
    if cand.empty:
        print('NO CANDIDATES'); return
    cand = ra.add_pullback_reaccel(cand, features)
    cand = mp.add_preservation(cand, features)

    # Use the broader 5-case cohort: same V architecture, but GAP_KEEP is descriptive only.
    q = cand[(cand.stop_dist_pct <= STOP_CAP) & cand.reaccel_pass &
             (pd.to_numeric(cand.volume_accel, errors='coerce') >= VOL_MIN) &
             cand.rsi_positive_all].copy()
    q['day'] = pd.to_datetime(q.time).dt.date
    q = q.sort_values('time').drop_duplicates(['symbol', 'day'], keep='first').reset_index(drop=True)
    if q.empty:
        print('NO COHORT'); return

    base_rows = []
    for _, r in q.iterrows():
        sym = n(r.symbol)
        vev, meta, _ = sm.select(pd.DataFrame([r]), RAW_MIN, LEG_MIN, STOP_CAP, None)
        tr = old.simulate_with_v_stop(packed, vev, states, THRESHOLD, meta)
        if tr.empty: continue
        t = tr.iloc[0]
        ep = f(t.entry_price); et = pd.Timestamp(t.entry_time); xt = pd.Timestamp(t.exit_time)
        baseline_net = f(t.pnl_pct) - FEE_RT_PCT

        z = features[sym].copy()
        close_col = 'px' if 'px' in z.columns else 'close'
        zz = z[z.time >= et].copy().sort_values('time')
        if zz.empty: continue
        zz['close'] = pd.to_numeric(zz[close_col], errors='coerce')
        zz = zz[zz['close'].notna()].copy()
        if zz.empty: continue
        # Same-day only; last row is the session-end proxy for this diagnostic.
        day = et.date()
        zz = zz[pd.to_datetime(zz.time).dt.date == day].copy()
        if zz.empty: continue
        zz['ret_pct'] = (zz['close'] / ep - 1.0) * 100.0
        zz['hwm_pct'] = zz['ret_pct'].cummax()
        zz['giveback_pct'] = zz['hwm_pct'] - zz['ret_pct']

        base_rows.append(dict(symbol=sym, entry_time=et, baseline_exit_time=xt,
                              baseline_net_pct=baseline_net, baseline_reason=t.get('reason',''),
                              entry_price=ep, session_end_ret_pct=f(zz.iloc[-1].ret_pct),
                              session_mfe_pct=f(zz.ret_pct.max()), path=zz))

    if not base_rows:
        print('NO SIMULATED CASES'); return

    detail = []
    summary = []
    baseline_total = float(sum(x['baseline_net_pct'] for x in base_rows))

    for act in ACTIVATIONS:
        for gb in GIVEBACKS:
            nets = []
            activated = 0
            changed = 0
            for b in base_rows:
                zz = b['path']
                hit = zz[zz.ret_pct >= act]
                if hit.empty:
                    net = b['baseline_net_pct']
                    exit_time = b['baseline_exit_time']
                    exit_type = 'BASELINE_NO_RUN'
                    run_time = pd.NaT
                else:
                    run_time = pd.Timestamp(hit.iloc[0].time)
                    # RUN must activate before the baseline exit, otherwise baseline already closed the trade.
                    if run_time > b['baseline_exit_time']:
                        net = b['baseline_net_pct']
                        exit_time = b['baseline_exit_time']
                        exit_type = 'BASELINE_BEFORE_RUN'
                    else:
                        activated += 1
                        post = zz[zz.time >= run_time].copy()
                        post['run_hwm_pct'] = post.ret_pct.cummax()
                        post['run_giveback_pct'] = post.run_hwm_pct - post.ret_pct
                        trig = post[post.run_giveback_pct >= gb]
                        if len(trig):
                            rr = trig.iloc[0]
                            gross = f(rr.ret_pct)
                            exit_time = pd.Timestamp(rr.time)
                            exit_type = 'RUN_GIVEBACK'
                        else:
                            rr = post.iloc[-1]
                            gross = f(rr.ret_pct)
                            exit_time = pd.Timestamp(rr.time)
                            exit_type = 'RUN_SESSION_END'
                        net = gross - FEE_RT_PCT
                        if abs(net - b['baseline_net_pct']) > 1e-12: changed += 1
                nets.append(net)
                detail.append(dict(activation_pct=act, giveback_pct=gb, symbol=b['symbol'],
                                   entry_time=b['entry_time'], baseline_net_pct=b['baseline_net_pct'],
                                   baseline_reason=b['baseline_reason'], run_time=run_time,
                                   hypothetical_exit_time=exit_time, hypothetical_net_pct=net,
                                   delta_vs_baseline_pct=net-b['baseline_net_pct'], exit_type=exit_type,
                                   session_mfe_pct=b['session_mfe_pct'], session_end_ret_pct=b['session_end_ret_pct']))
            arr = pd.Series(nets, dtype=float)
            summary.append(dict(activation_pct=act, giveback_pct=gb, trades=len(arr),
                                activated=activated, changed=changed,
                                total_net_pct=float(arr.sum()), delta_vs_baseline_pct=float(arr.sum()-baseline_total),
                                wins=int((arr>0).sum()), win_pct=float((arr>0).mean()*100),
                                max_loss_pct=float(arr.min())))

    s = pd.DataFrame(summary).sort_values(['total_net_pct','delta_vs_baseline_pct'], ascending=False)
    d = pd.DataFrame(detail).sort_values(['activation_pct','giveback_pct','entry_time'])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    s.to_csv(OUT_SUMMARY, index=False)
    d.drop(columns=[], errors='ignore').to_csv(OUT_CASES, index=False)

    print('\n=== V-REBOUND RUN-MODE SENSITIVITY ===')
    print('Diagnostic only. No entry/exit rule changed.')
    print('Broader 5-case cohort. Before RUN activation, baseline exit remains unchanged.')
    print('After RUN activation, this coarse test ignores fast momentum fade and exits only on HWM giveback or session end.')
    print(f'BASELINE_TOTAL_NET={baseline_total:.6f}%')
    print('\n=== SUMMARY ===')
    print(s.to_string(index=False))
    print('\n=== CHANGED CASES (best config only) ===')
    if len(s):
        best=s.iloc[0]
        qd=d[(d.activation_pct==best.activation_pct)&(d.giveback_pct==best.giveback_pct)&(d.delta_vs_baseline_pct.abs()>1e-12)]
        show=['symbol','entry_time','baseline_net_pct','run_time','hypothetical_exit_time','hypothetical_net_pct','delta_vs_baseline_pct','exit_type','session_mfe_pct','session_end_ret_pct']
        print(qd[show].to_string(index=False) if len(qd) else 'NONE')
    print('\nReading target:')
    print('- This is a coarse sensitivity check, NOT a threshold optimizer.')
    print('- If several nearby activation/giveback combinations improve total net, the two-state architecture is robust enough to keep.')
    print('- If only one exact cell works, do not freeze it; that would be overfit.')
    print('- Structural trend-failure logic should replace fixed giveback later if the RUN architecture survives this check.')
    print('WROTE', OUT_SUMMARY)
    print('WROTE', OUT_CASES)

if __name__ == '__main__':
    main()
