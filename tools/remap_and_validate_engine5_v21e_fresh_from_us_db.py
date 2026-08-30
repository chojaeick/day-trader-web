from __future__ import annotations

"""Fresh V21E remap + validation directly from the US SQLite source DB.

Important:
- DOES NOT load us_e_core.pkl or old mapped caches.
- Reads US REGULAR 1m bars from SQLite again.
- Keeps native USD and original exchange-local ET.
- Rebuilds 1m/5m Engine5 indicators/features from scratch.
- Rebuilds V17/V18 prerequisite stream.
- Builds V21E = V20E + Slow-turn-E + V-rebound-E.
- Runs the integrated simulator and prints both gross and fee-adjusted metrics.
- KR engine source files are not modified.
"""

import pickle
from collections import Counter
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_opening_5m_hwm_sweep as sweep
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_regime_transition as rt
import tools.validate_engine5_v20_macd_strength as ms
import tools.validate_engine5_integrated_full_history as integ
import tools.validate_engine5_integrated_slow_turn_rearm_deep as revised
import tools.build_engine5_us_oos_cache as uscache
import tools.build_engine5_us_e_cache as src
import tools.validate_engine5_us_e_all_versions as e
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v21e_fresh_validation')
SIGNALS = OUT_DIR / 'v21e_fresh_signals.csv'
TRADES = OUT_DIR / 'v21e_fresh_trades.csv'
SUMMARY = OUT_DIR / 'v21e_fresh_summary.csv'
MAP_PKL = OUT_DIR / 'v21e_fresh_map.pkl'
CUT = -0.15
FEE_RT_PCT = 0.25


def n(x): return str(x).zfill(6)

def minute(ts):
    t = pd.Timestamp(ts)
    return t.hour * 60 + t.minute


def clip_entry_window(ev):
    return {
        pd.Timestamp(ts): list(cs)
        for ts, cs in ev.items()
        if e.US_BUY_START_MINUTE <= minute(ts) < e.US_NO_ENTRY_MINUTE
    }


def rebuild_from_db():
    raw, db_audit = src.load_native_usd_et()
    if not raw:
        raise SystemExit('NO US REGULAR DATA')

    cfg0 = DoubleBollingerEngine5Config()
    cfg = replace(cfg0, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    print('\n[1/4] REBUILD CORE FEATURES DIRECTLY FROM SQLITE...', flush=True)
    packed = v8.base.pack_exit_events(raw, cfg0)
    states = base.pack_state_events(base.build_cfg_frames(raw, cfg0))
    frames0 = base.build_cfg_frames(raw, cfg)
    f10 = {n(s): v10._refine_entry_frame(f) for s, f in frames0.items()}
    scored = {n(s): f for s, f in reweight(f10, cfg, 0.0).items()}
    strength = {s: ms.add_strength(f) for s, f in scored.items()}
    completed = {s: rt.add_completed_strength(f) for s, f in scored.items()}
    micros = {s: h.build_micro(b, cfg) for s, b in raw.items()}

    print('[2/4] REBUILD PROVISIONAL FRAMES IN MEMORY...', flush=True)
    pf = {}
    for i, s in enumerate(raw, 1):
        pf[s] = uscache.build_minimal_provisional_fast(raw[s], cfg, completed[s])
        print(f'  [{i}/{len(raw)}] {s} rows={len(pf[s])}', flush=True)

    return raw, db_audit, cfg0, cfg, packed, states, scored, strength, completed, micros, pf


def build_v21e(raw, cfg, scored, strength, completed, micros, pf):
    e.apply_us_session_clock()

    print('[3/4] REBUILD V17/V18 PREREQUISITE STREAM...', flush=True)
    raw_entries = v8.pack_entry_events(scored)
    ev10 = clip_entry_window(sweep.filt_open(raw_entries))
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev16 = clip_entry_window(ev16)
    ev17, _, _ = v17b.build_v17b(ev16, scored, waits)
    ev17 = clip_entry_window(ev17)
    ev18, _ = h.build_veto_stream(ev17, micros)
    ev18 = clip_entry_window(ev18)

    print('[4/4] BUILD FRESH V21E SOURCES...', flush=True)
    v20 = e.build_v20e_tags(ev18, strength, scored)
    v20 = [x for x in v20 if e.US_BUY_START_MINUTE <= minute(x['time']) < e.US_NO_ENTRY_MINUTE]

    old = revised.st.load_or_build_cache
    revised.st.load_or_build_cache = lambda sym, *_: (pf[n(sym)], micros[n(sym)])
    try:
        allslow = revised.build_all_slow(raw, cfg, completed, micros)
    finally:
        revised.st.load_or_build_cache = old
    allslow = e.normalize_slow_boundary_e(allslow)
    slow = revised.slow_tags(revised.select_revised(allslow, CUT))
    for x in slow:
        x['source'] = 'SLOW_TURN_E'
    slow = [x for x in slow if e.US_BUY_START_MINUTE <= minute(x['time']) < e.US_NO_ENTRY_MINUTE]

    vr = e.build_vrebound_e(raw, scored, micros, pf)
    vr = [x for x in vr if e.US_BUY_START_MINUTE <= minute(x['time']) < e.US_NO_ENTRY_MINUTE]

    tags = sorted(v20 + slow + vr, key=lambda x: (pd.Timestamp(x['time']), x['symbol'], x['source']))
    return ev17, ev18, v20, slow, vr, tags


def metrics(trades):
    gross = pd.to_numeric(trades.get('pnl_pct'), errors='coerce').dropna() if len(trades) else pd.Series(dtype=float)
    net = gross - FEE_RT_PCT

    def pf(x):
        gp = float(x[x > 0].sum()) if len(x) else 0.0
        gl = float(-x[x < 0].sum()) if len(x) else 0.0
        return gp / gl if gl > 0 else np.inf

    return dict(
        trades=int(len(gross)),
        gross_wins=int((gross > 0).sum()),
        gross_win_pct=float((gross > 0).mean() * 100.0) if len(gross) else 0.0,
        gross_sum_pct=float(gross.sum()) if len(gross) else 0.0,
        gross_avg_pct=float(gross.mean()) if len(gross) else 0.0,
        gross_pf=float(pf(gross)),
        net025_wins=int((net > 0).sum()),
        net025_win_pct=float((net > 0).mean() * 100.0) if len(net) else 0.0,
        net025_sum_pct=float(net.sum()) if len(net) else 0.0,
        net025_avg_pct=float(net.mean()) if len(net) else 0.0,
        net025_pf=float(pf(net)),
        max_gross_loss_pct=float(gross.min()) if len(gross) else np.nan,
        max_net025_loss_pct=float(net.min()) if len(net) else np.nan,
    )


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print('=== FRESH V21E REMAP + PERFORMANCE VALIDATION ===', flush=True)
    print('SOURCE=SQLite US REGULAR | USD native | ET original | old cache NOT used', flush=True)
    print('V21E = V20E + SLOW_TURN_E + V_REBOUND_E', flush=True)

    raw, db_audit, cfg0, cfg, packed, states, scored, strength, completed, micros, pf = rebuild_from_db()
    ev17, ev18, v20, slow, vr, tags = build_v21e(raw, cfg, scored, strength, completed, micros, pf)

    source_counts = Counter(x['source'] for x in tags)
    print('\n=== FRESH MAP COUNTS ===')
    print(f"V17CE prerequisite signals={sum(len(v) for v in ev17.values())}")
    print(f"V18E prerequisite signals={sum(len(v) for v in ev18.values())}")
    print(f"V20E={len(v20)} SLOW_TURN_E={len(slow)} V_REBOUND_E={len(vr)} TOTAL_V21E={len(tags)}")
    print('source_counts=', dict(source_counts))

    trades = integ.simulate(packed, states, tags)
    m = metrics(trades)

    print('\n=== V21E PERFORMANCE ===')
    print(f"trades={m['trades']}")
    print(f"GROSS: wins={m['gross_wins']} WR={m['gross_win_pct']:.2f}% sum={m['gross_sum_pct']:+.4f}% avg={m['gross_avg_pct']:+.4f}% PF={m['gross_pf']:.3f} maxloss={m['max_gross_loss_pct']:+.4f}%")
    print(f"NET fee0.25: wins={m['net025_wins']} WR={m['net025_win_pct']:.2f}% sum={m['net025_sum_pct']:+.4f}% avg={m['net025_avg_pct']:+.4f}% PF={m['net025_pf']:.3f} maxloss={m['max_net025_loss_pct']:+.4f}%")

    summary = pd.DataFrame([{**m,
        'v17ce_signals': sum(len(v) for v in ev17.values()),
        'v18e_signals': sum(len(v) for v in ev18.values()),
        'v20e_signals': len(v20),
        'slow_turn_e_signals': len(slow),
        'v_rebound_e_signals': len(vr),
        'v21e_total_signals': len(tags),
        'cut': CUT,
        'price_unit': 'USD',
        'session': 'US_REGULAR_ET',
        'old_cache_used': False,
    }])

    sig = pd.DataFrame([
        dict(source=x['source'], symbol=x['symbol'], time=pd.Timestamp(x['time']))
        for x in tags
    ])
    summary.to_csv(SUMMARY, index=False)
    trades.to_csv(TRADES, index=False)
    sig.to_csv(SIGNALS, index=False)

    # Save the fresh map ingredients for reproducibility; no historical cache is read.
    with MAP_PKL.open('wb') as fh:
        pickle.dump(dict(
            schema='V21E_FRESH_SQLITE_USD_ET_V1',
            price_unit='USD', time_shift_minutes=0, source='SQLite historical_minute_bars REGULAR',
            raw=raw, cfg=cfg, scored=scored, strength=strength, completed=completed, micros=micros,
            tags=tags,
        ), fh, pickle.HIGHEST_PROTOCOL)

    print('\nWROTE', SUMMARY)
    print('WROTE', TRADES)
    print('WROTE', SIGNALS)
    print('WROTE', MAP_PKL)
    print('KR engine unchanged. This run remapped V21E from SQLite from scratch.', flush=True)


if __name__ == '__main__':
    main()
