from __future__ import annotations

from dataclasses import replace
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_breakout_first10_hwm1pct as v17c
import tools.diagnose_engine5_v19_strength_score as v19
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OPEN_MINUTE = 9 * 60 + 10
BASELINE_THRESHOLD = 50
EDGE_MIN = 0.10


def filt_open(ev):
    return {ts: rows for ts, rows in ev.items() if pd.Timestamp(ts).hour * 60 + pd.Timestamp(ts).minute >= OPEN_MINUTE}


def metrics(name, trades):
    if trades is None or len(trades) == 0:
        return dict(name=name, trades=0, wins=0, losses=0, win_rate=0.0, gross_pct=0.0, avg_pct=0.0, pf=np.nan, max_loss_pct=np.nan)
    p = pd.to_numeric(trades['pnl_pct'], errors='coerce').dropna()
    gp = float(p[p > 0].sum()); gl = float(-p[p < 0].sum())
    return dict(name=name, trades=int(len(p)), wins=int((p > 0).sum()), losses=int((p <= 0).sum()),
                win_rate=float((p > 0).mean() * 100.0), gross_pct=float(p.sum()), avg_pct=float(p.mean()),
                pf=(gp / gl if gl > 0 else np.inf), max_loss_pct=float(p.min()))


def print_metric(m):
    print(f"{m['name']}: trades={m['trades']} wins={m['wins']} losses={m['losses']} win={m['win_rate']:.2f}% gross={m['gross_pct']:+.4f}% avg={m['avg_pct']:+.4f}% pf={m['pf']:.3f} maxloss={m['max_loss_pct']:+.4f}%")


def key_present(events, sym, ts):
    t = pd.Timestamp(ts)
    return any(str(e[0]).zfill(6) == str(sym).zfill(6) for e in events.get(t, []))


def main():
    raw = load_data()
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    packed_exits = v8.base.pack_exit_events(raw, base_cfg)
    state_events = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    raw_frames = base.build_cfg_frames(raw, cfg)
    f10 = {s: v10._refine_entry_frame(f) for s, f in raw_frames.items()}
    scored = reweight(f10, cfg, 0.0)
    frames = v19.build_score_frames(scored)

    ev10 = filt_open(v8.pack_entry_events(scored))
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev17b, _, _ = v17b.build_v17b(ev16, scored, waits)
    rescored, diag = v19.rescore_events(ev17b, frames)

    gated = {}
    removed = []
    for ts, rows in rescored.items():
        keep = []
        for e in rows:
            sym = str(e[0]).zfill(6)
            q = diag[(diag['symbol'] == sym) & (pd.to_datetime(diag['time']) == pd.Timestamp(ts))]
            if q.empty:
                keep.append(e); continue
            d = q.iloc[0]
            if bool(d['breakout_entry']):
                keep.append(e); continue
            # Only apply the gate when current/previous MACD and Signal are safely positive and ratio mode is stable.
            # Near zero/sign-change cases remain governed by existing V16/V17B logic.
            if str(d['ratio_mode']) == 'RATIO' and np.isfinite(float(d['ratio_edge'])) and float(d['ratio_edge']) <= EDGE_MIN:
                removed.append(d.to_dict())
            else:
                keep.append(e)
        if keep:
            gated[ts] = keep

    print('=== ENGINE5 V19B MACD QUALITY-GATE DIAGNOSTIC ===')
    print('Single structural change: ordinary RATIO-mode BUY requires MACD_ratio - Signal_ratio > 0.10.')
    print('Breakout entries and near-zero/sign-change fallback cases are exempt.')

    # Manual regression checks.
    checks = [
        ('FAIL_950260_0821_1000','950260','2026-08-21 10:00:00+09:00'),
        ('FAIL_080220_0811_0955','080220','2026-08-11 09:55:00+09:00'),
        ('FAIL_257720_0818_1430','257720','2026-08-18 14:30:00+09:00'),
        ('GOOD_257720_0812_0910','257720','2026-08-12 09:10:00+09:00'),
        ('BREAKOUT_257720_0818_1420','257720','2026-08-18 14:20:00+09:00'),
    ]
    print('\n=== MANUAL REGRESSION ===')
    for label, sym, ts in checks:
        q = diag[(diag['symbol'] == sym) & (pd.to_datetime(diag['time']) == pd.Timestamp(ts))]
        edge = float(q.iloc[0]['ratio_edge']) if len(q) and np.isfinite(float(q.iloc[0]['ratio_edge'])) else np.nan
        mode = str(q.iloc[0]['ratio_mode']) if len(q) else 'N/A'
        br = bool(q.iloc[0]['breakout_entry']) if len(q) else False
        print(f'{label}: kept={key_present(gated,sym,ts)} edge={edge} mode={mode} breakout={br}')

    ta = v17c.simulate_unconditional_hwm(packed_exits, ev17b, state_events, BASELINE_THRESHOLD)
    tb = v17c.simulate_unconditional_hwm(packed_exits, gated, state_events, BASELINE_THRESHOLD)
    print('\n=== FULL PATH ===')
    ma = metrics('A_V17C_CURRENT', ta); mb = metrics('B_V19B_EDGE_GT_010', tb)
    print_metric(ma); print_metric(mb)
    print(f"DELTA: trades={mb['trades']-ma['trades']:+d} win={mb['win_rate']-ma['win_rate']:+.2f}pp gross={mb['gross_pct']-ma['gross_pct']:+.4f}%p avg={mb['avg_pct']-ma['avg_pct']:+.4f}%p pf={mb['pf']-ma['pf']:+.3f}")

    # Independent outcome of removed candidates under unchanged V17C exits.
    print('\n=== REMOVED CANDIDATES INDEPENDENT OUTCOME ===')
    outs = []
    for d in removed:
        ts = pd.Timestamp(d['time']); sym = str(d['symbol']).zfill(6)
        es = [e for e in ev17b.get(ts, []) if str(e[0]).zfill(6) == sym]
        if not es: continue
        t = v17c.simulate_unconditional_hwm(packed_exits, {ts:[es[0]]}, state_events, BASELINE_THRESHOLD)
        if len(t):
            outs.append((sym, ts, float(d['ratio_edge']), float(t.iloc[0].pnl_pct)))
    if outs:
        p = pd.Series([x[3] for x in outs], dtype=float)
        print(f'n={len(p)} wins={(p>0).sum()} losses={(p<=0).sum()} win={(p>0).mean()*100:.2f}% gross={p.sum():+.4f}% avg={p.mean():+.4f}%')
        print('worst10:')
        for row in sorted(outs, key=lambda x:x[3])[:10]: print(row)
        print('best10:')
        for row in sorted(outs, key=lambda x:x[3], reverse=True)[:10]: print(row)
    else:
        print('none')


if __name__ == '__main__':
    main()
