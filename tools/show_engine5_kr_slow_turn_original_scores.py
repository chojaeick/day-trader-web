from __future__ import annotations

"""Show the *underlying/original* Engine5 entry_score for the frozen KR Slow-turn
cut=-0.15 success/failure cases.

This intentionally does NOT report the forced simulator event score.  It rebuilds the KR
`scored` 5m frames exactly as the validator does, then looks up the most recent completed
5m score at each realized Slow-turn entry and joins the realized trade outcome.
"""

from dataclasses import replace
from pathlib import Path
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

ROOT = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
SIG = ROOT / 'integrated_slow_turn_rearm_deep_signals.csv'
TRD = ROOT / 'integrated_slow_turn_rearm_deep_trades.csv'
OUT = ROOT / 'kr_slow_turn_original_scores.csv'
CUT = -0.15
FEE = 0.25


def n(x): return str(x).zfill(6)

def num(x): return pd.to_numeric(x, errors='coerce')


def rebuild_scored():
    raw = {n(k): v for k, v in load_data().items()}
    cfg0 = DoubleBollingerEngine5Config()
    cfg = replace(cfg0, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)
    frames0 = base.build_cfg_frames(raw, cfg)
    f10 = {n(s): v10._refine_entry_frame(f) for s, f in frames0.items()}
    scored = {n(s): f.copy() for s, f in reweight(f10, cfg, 0.0).items()}
    for s, f in scored.items():
        f['time'] = pd.to_datetime(f['time'])
        f.sort_values('time', inplace=True)
    return scored


def score_at(scored, sym, ts):
    f = scored[n(sym)]
    q = f[f.time <= pd.Timestamp(ts).floor('5min')]
    if q.empty:
        return np.nan, pd.NaT
    r = q.iloc[-1]
    return float(pd.to_numeric(pd.Series([r.get('entry_score')]), errors='coerce').iloc[0]), pd.Timestamp(r.time)


def main():
    if not SIG.exists(): raise FileNotFoundError(SIG)
    if not TRD.exists(): raise FileNotFoundError(TRD)

    sig = pd.read_csv(SIG)
    tr = pd.read_csv(TRD)
    sig['cut'] = pd.to_numeric(sig['cut'], errors='coerce')
    tr['cut'] = pd.to_numeric(tr['cut'], errors='coerce')
    sig = sig[np.isclose(sig['cut'], CUT)].copy()
    tr = tr[np.isclose(tr['cut'], CUT)].copy()
    sig['symbol'] = sig.symbol.astype(str).str.zfill(6)
    tr['symbol'] = tr.symbol.astype(str).str.zfill(6)
    sig['entry_time'] = pd.to_datetime(sig.entry_time)
    tr['entry_time'] = pd.to_datetime(tr.entry_time)
    tr['net_pct'] = num(tr['pnl_pct']) - FEE

    # Only realized Slow-turn entries: selected signal must match a realized trade exactly.
    x = sig.merge(
        tr[['symbol','entry_time','exit_time','pnl_pct','net_pct','reason']].drop_duplicates(['symbol','entry_time']),
        on=['symbol','entry_time'], how='inner', validate='one_to_one'
    )
    if x.empty:
        raise SystemExit('NO MATCHED KR SLOW-TURN TRADES FOR cut=-0.15')

    scored = rebuild_scored()
    vals=[]; times=[]
    for _, r in x.iterrows():
        v,t = score_at(scored, r.symbol, r.entry_time)
        vals.append(v); times.append(t)
    x['underlying_entry_score'] = vals
    x['score_bar_time'] = times
    x['forced_event_score'] = 50.0
    x['forced_to_50'] = num(x['underlying_entry_score']) < 50.0
    x['result'] = np.where(num(x.net_pct) > 0, 'WIN', 'LOSS')

    cols = ['result','symbol','entry_time','exit_time','regime','underlying_entry_score','forced_event_score','forced_to_50','net_pct','reason']
    cols = [c for c in cols if c in x.columns]
    wins = x[x.result=='WIN'].sort_values('underlying_entry_score', ascending=False)
    losses = x[x.result=='LOSS'].sort_values('underlying_entry_score', ascending=False)

    print('=== KR SLOW-TURN ORIGINAL ENTRY SCORES | cut=-0.15 ===')
    print('Original score = rebuilt scored-frame entry_score. Forced event score is shown separately.')
    print(f'matched trades={len(x)} wins={len(wins)} losses={len(losses)}')
    print(f"original score median={num(x.underlying_entry_score).median():.4f} min={num(x.underlying_entry_score).min():.4f} max={num(x.underlying_entry_score).max():.4f}")
    print(f"forced_to_50={int(x.forced_to_50.sum())}/{len(x)}")

    print('\n=== SUCCESS CASES ===')
    print(wins[cols].to_string(index=False, float_format=lambda v:f'{v:.4f}'))
    print('\n=== FAILURE CASES ===')
    print(losses[cols].to_string(index=False, float_format=lambda v:f'{v:.4f}'))

    x[cols + [c for c in ['score_bar_time','macd_slope_spread_strength','rsi_slope_strength'] if c in x.columns]].to_csv(OUT,index=False)
    print('\nWROTE', OUT)

if __name__ == '__main__':
    main()
