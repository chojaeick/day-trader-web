from __future__ import annotations

"""Rebuild the EXISTING Slow-turn entry evaluation around transition strength.

This is not a fourth strategy.  Candidate generation, episode re-arm, DEEP structure,
V20, V-rebound and exits stay unchanged.  Only the Slow-turn event score is replaced.

Goal:
- reward abrupt MACD/RSI turn strength and acceleration;
- stop rewarding generic old-trend state, volume or Bollinger expansion;
- remove the historical max(50, entry_score) score-floor bypass;
- keep the score scale-invariant by normalizing each impulse to its own recent history.

The threshold sweep is diagnostic.  Do not freeze a threshold from this sample alone.
"""

from dataclasses import replace
from pathlib import Path
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_macd_strength as ms
import tools.validate_engine5_v20_regime_transition as rt
import tools.validate_engine5_integrated_full_history as integ
import tools.validate_engine5_integrated_slow_turn_rearm_deep as revised
import tools.diagnose_v20_transition_structure_targets as st
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
OUT_SUMMARY = OUT_DIR / 'slow_turn_transition_score_summary.csv'
OUT_DETAIL = OUT_DIR / 'slow_turn_transition_score_detail.csv'
CUT = -0.15
FEE_RT_PCT = 0.25
THRESHOLDS = (40, 50, 60, 70, 80)
LOOKBACK = 8

# Component weights sum to 100.  They intentionally emphasize actual turn impulse.
W_MACD_IMPULSE = 25.0
W_MACD_ACCEL = 20.0
W_RSI_IMPULSE = 25.0
W_RSI_ACCEL = 20.0
W_MICRO = 5.0
W_PRICE = 5.0


def n(x): return str(x).zfill(6)
def num(x): return pd.to_numeric(x, errors='coerce')


def finite(x):
    try:
        v = float(x)
        return v if np.isfinite(v) else np.nan
    except Exception:
        return np.nan


def sat_ratio(value, baseline, full_multiple=3.0):
    """0..1 strength. Full score requires ~3x the recent typical absolute move."""
    v = finite(value); b = finite(baseline)
    if not np.isfinite(v) or v <= 0 or not np.isfinite(b) or b <= 1e-12:
        return 0.0
    return float(np.clip(v / (b * float(full_multiple)), 0.0, 1.0))


def add_transition_context(frame: pd.DataFrame) -> pd.DataFrame:
    """Causal 5m transition impulses and their recent typical magnitudes."""
    f = frame.copy().sort_values('time').reset_index(drop=True)
    f['time'] = pd.to_datetime(f['time'])

    gd = num(f['macd_gap_delta']) if 'macd_gap_delta' in f else num(f['gap_delta'])
    rs = num(f['rsi_slope'])
    gacc = gd - gd.shift(1)
    racc = rs - rs.shift(1)

    f['turn_gap_delta'] = gd
    f['turn_gap_accel'] = gacc
    f['turn_rsi_slope'] = rs
    f['turn_rsi_accel'] = racc

    # shift(1): current bar cannot inflate its own baseline.
    f['base_gap_delta_abs'] = gd.abs().shift(1).rolling(LOOKBACK, min_periods=3).median()
    f['base_gap_accel_abs'] = gacc.abs().shift(1).rolling(LOOKBACK, min_periods=3).median()
    f['base_rsi_slope_abs'] = rs.abs().shift(1).rolling(LOOKBACK, min_periods=3).median()
    f['base_rsi_accel_abs'] = racc.abs().shift(1).rolling(LOOKBACK, min_periods=3).median()
    return f


def context_at(ctx: pd.DataFrame, ready_time) -> pd.Series | None:
    ts = pd.Timestamp(ready_time).floor('5min')
    q = ctx[ctx.time <= ts]
    return None if q.empty else q.iloc[-1]


def score_candidate(r, ctx_row):
    """Slow-turn transition score, independent of generic Engine5 entry_score."""
    if ctx_row is None:
        return dict(transition_score=0.0)

    macd_impulse_strength = sat_ratio(ctx_row.turn_gap_delta, ctx_row.base_gap_delta_abs)
    macd_accel_strength = sat_ratio(ctx_row.turn_gap_accel, ctx_row.base_gap_accel_abs)
    rsi_impulse_strength = sat_ratio(ctx_row.turn_rsi_slope, ctx_row.base_rsi_slope_abs)
    rsi_accel_strength = sat_ratio(ctx_row.turn_rsi_accel, ctx_row.base_rsi_accel_abs)

    # Existing 1m confirmation remains structural.  Here it contributes only a small
    # continuous quality bonus; it cannot rescue weak 5m transition energy.
    j1 = finite(getattr(r, 'joint1_persistence', np.nan))
    micro_strength = float(np.clip((j1 - 0.67) / 0.33, 0.0, 1.0)) if np.isfinite(j1) else 0.0

    px = finite(getattr(r, 'price_progress_1m_pct', np.nan))
    price_strength = float(np.clip(px / 2.0, 0.0, 1.0)) if np.isfinite(px) and px > 0 else 0.0

    parts = dict(
        macd_impulse_score=W_MACD_IMPULSE * macd_impulse_strength,
        macd_accel_score=W_MACD_ACCEL * macd_accel_strength,
        rsi_impulse_score=W_RSI_IMPULSE * rsi_impulse_strength,
        rsi_accel_score=W_RSI_ACCEL * rsi_accel_strength,
        micro_score=W_MICRO * micro_strength,
        price_score=W_PRICE * price_strength,
    )
    score = float(sum(parts.values()))
    return dict(
        transition_score=score,
        macd_impulse_strength=macd_impulse_strength,
        macd_accel_strength=macd_accel_strength,
        rsi_impulse_strength=rsi_impulse_strength,
        rsi_accel_strength=rsi_accel_strength,
        turn_gap_delta=finite(ctx_row.turn_gap_delta),
        turn_gap_accel=finite(ctx_row.turn_gap_accel),
        turn_rsi_slope=finite(ctx_row.turn_rsi_slope),
        turn_rsi_accel=finite(ctx_row.turn_rsi_accel),
        **parts,
    )


def replace_event_score(event, score):
    e = list(event)
    if len(e) < 3:
        raise ValueError('unexpected Slow-turn event tuple')
    e[2] = float(score)
    return tuple(e)


def attach_scores(sel: pd.DataFrame, contexts: dict[str, pd.DataFrame]) -> pd.DataFrame:
    x = sel.copy()
    rows = []
    for _, r in x.iterrows():
        c = context_at(contexts[n(r.symbol)], r.ready_time)
        rows.append(score_candidate(r, c))
    s = pd.DataFrame(rows, index=x.index)
    return pd.concat([x, s], axis=1)


def tags_from_scored(sel: pd.DataFrame, threshold: float):
    out = []
    q = sel[num(sel.transition_score) >= float(threshold)]
    for _, r in q.iterrows():
        out.append(dict(
            source='SLOW_TURN', symbol=n(r.symbol), time=pd.Timestamp(r.entry_time),
            event=replace_event_score(r.event, r.transition_score),
            meta=dict(
                regime=str(r.regime), transition_score=float(r.transition_score),
                norm_mid_slope_pct=finite(r.norm_mid_slope_pct),
            ),
        ))
    return out


def stat(label, trades):
    p = num(trades['pnl_pct']).dropna() if len(trades) else pd.Series(dtype=float)
    net = p - FEE_RT_PCT
    gp = float(net[net > 0].sum()) if len(net) else 0.0
    gl = float(-net[net < 0].sum()) if len(net) else 0.0
    return dict(
        label=label, trades=len(net), wins=int((net > 0).sum()),
        win_pct=float((net > 0).mean() * 100.0) if len(net) else 0.0,
        net_sum_pct=float(net.sum()) if len(net) else 0.0,
        pf=(gp / gl if gl > 0 else np.inf),
        max_loss_pct=float(net.min()) if len(net) else np.nan,
    )


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = {n(k): v for k, v in load_data().items()}
    cfg0 = DoubleBollingerEngine5Config()
    cfg = replace(cfg0, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    packed = v8.base.pack_exit_events(raw, cfg0)
    states = base.pack_state_events(base.build_cfg_frames(raw, cfg0))
    frames0 = base.build_cfg_frames(raw, cfg)
    f10 = {n(s): v10._refine_entry_frame(f) for s, f in frames0.items()}
    scored = {n(s): f for s, f in reweight(f10, cfg, 0.0).items()}
    strength = {s: ms.add_strength(f) for s, f in scored.items()}
    completed = {s: rt.add_completed_strength(f) for s, f in scored.items()}
    micros = {s: h.build_micro(raw[s], cfg) for s in raw}
    contexts = {s: add_transition_context(completed[s]) for s in raw}

    print('=== REBUILD EXISTING SLOW-TURN TRANSITION SCORE ===', flush=True)
    print('Generic Engine5 entry_score is NOT used for Slow-turn.', flush=True)
    print('No volume/Bollinger/trend_up/MACD-above-signal points. No forced score floor.', flush=True)

    old_tagged = integ.build_sources(raw, cfg, scored, strength, completed, micros)
    non_slow = [x for x in old_tagged if x['source'] != 'SLOW_TURN']

    allc = revised.build_all_slow(raw, cfg, completed, micros)
    sel = revised.select_revised(allc, CUT)
    scored_sel = attach_scores(sel, contexts)
    if scored_sel.empty:
        raise SystemExit('NO SELECTED SLOW-TURN CANDIDATES')

    # Realized Slow-only outcome at each threshold plus full integrated V21 result.
    rows = []
    for th in THRESHOLDS:
        stags = tags_from_scored(scored_sel, th)
        slow_tr = integ.simulate(packed, states, stags)
        all_tags = sorted(non_slow + stags, key=lambda z: (pd.Timestamp(z['time']), z['symbol'], z['source']))
        full_tr = integ.simulate(packed, states, all_tags)
        a = stat(f'SLOW_TH{th}', slow_tr)
        b = stat(f'FULL_TH{th}', full_tr)
        rows.append(dict(
            threshold=th, slow_signals=len(stags),
            slow_trades=a['trades'], slow_wins=a['wins'], slow_win_pct=a['win_pct'],
            slow_net_pct=a['net_sum_pct'], slow_pf=a['pf'], slow_max_loss=a['max_loss_pct'],
            full_trades=b['trades'], full_wins=b['wins'], full_win_pct=b['win_pct'],
            full_net_pct=b['net_sum_pct'], full_pf=b['pf'], full_max_loss=b['max_loss_pct'],
        ))

    summary = pd.DataFrame(rows)
    summary.to_csv(OUT_SUMMARY, index=False)

    # Attach known realized outcomes from current cut=-0.15 baseline for score discrimination audit.
    trade_path = OUT_DIR / 'integrated_slow_turn_rearm_deep_trades.csv'
    if trade_path.exists():
        tr = pd.read_csv(trade_path)
        tr['cut'] = num(tr.get('cut'))
        tr = tr[np.isclose(tr['cut'], CUT)].copy()
        tr['symbol'] = tr.symbol.astype(str).str.zfill(6)
        tr['entry_time'] = pd.to_datetime(tr.entry_time)
        tr['net_pct'] = num(tr.pnl_pct) - FEE_RT_PCT
        scored_sel['symbol'] = scored_sel.symbol.astype(str).str.zfill(6)
        scored_sel['entry_time'] = pd.to_datetime(scored_sel.entry_time)
        scored_sel = scored_sel.merge(
            tr[['symbol','entry_time','net_pct']].drop_duplicates(['symbol','entry_time']),
            on=['symbol','entry_time'], how='left'
        )
        scored_sel['result'] = np.where(num(scored_sel.net_pct) > 0, 'WIN',
                                np.where(num(scored_sel.net_pct) <= 0, 'LOSS', 'UNMATCHED'))

    scored_sel.drop(columns=['event'], errors='ignore').to_csv(OUT_DETAIL, index=False)

    print('\n=== THRESHOLD DIAGNOSTIC ===')
    print(summary.to_string(index=False, float_format=lambda v: f'{v:.4f}'))

    print('\n=== CANONICAL KR CASES ===')
    targets = [
        ('058610', pd.Timestamp('2026-08-13 09:25:00+09:00'), 'V_TURN_SUCCESS'),
        ('122630', pd.Timestamp('2026-08-20 13:06:00+09:00'), 'GRADUAL_FAILURE'),
        ('950160', pd.Timestamp('2026-08-14 10:59:00+09:00'), 'VALID_SLOW_SUCCESS'),
    ]
    for sym, ts, label in targets:
        q = scored_sel[(scored_sel.symbol == sym) & (scored_sel.entry_time == ts)]
        print(f'\n[{label}] {sym} {ts}')
        if q.empty:
            print('NOT FOUND')
            continue
        r = q.iloc[0]
        cols = [
            'transition_score','macd_impulse_score','macd_accel_score',
            'rsi_impulse_score','rsi_accel_score','micro_score','price_score',
            'turn_gap_delta','turn_gap_accel','turn_rsi_slope','turn_rsi_accel',
            'joint1_persistence','price_progress_1m_pct','regime','net_pct','result'
        ]
        for c in cols:
            if c in q.columns:
                v = r[c]
                print(f'{c:24s} {v:.4f}' if isinstance(v, (float, np.floating)) and np.isfinite(v) else f'{c:24s} {v}')

    print('\nREADING:')
    print('- V-turn success should score materially above gradual failure. If not, stop and revise score before integration.')
    print('- 950160 is the guard against making Slow-turn require a completed trend_up/MACD crossover.')
    print('- Threshold rows are diagnostics only; do not choose the best in-sample threshold mechanically.')
    print('WROTE', OUT_SUMMARY)
    print('WROTE', OUT_DETAIL)


if __name__ == '__main__':
    main()
