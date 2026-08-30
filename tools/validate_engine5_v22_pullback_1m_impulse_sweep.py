from __future__ import annotations

"""Validate 1-minute impulse strength as the actual pullback re-entry trigger.

Hypothesis
----------
The 5m trend has already been qualified before the re-entry watch is armed.
Therefore the re-entry trigger should not wait for another heavy trend
confirmation. Instead, after a valid higher-low pullback, enter when the next
completed 1m bar rises by a meaningful amount while causal provisional MACD
and RSI are both rising.

This diagnostic compares several minimum 1m close-to-close impulse thresholds.
It keeps the existing pullback structural gates and Engine5 risk/exit geometry.
Diagnostic only; production V22 is unchanged.
"""

from dataclasses import replace
from pathlib import Path
import numpy as np
import pandas as pd

import tools.validate_engine5_v22_uptrend_pullback_reentry as pb
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT = Path('/home/ubuntu/day-trader-api/engine5_v22_pullback_1m_impulse_sweep')
IMPULSE_PCTS = [0.10, 0.20, 0.30, 0.50, 0.70, 1.00]
GROUPS = {
    'VETO15_ONLY': ['VETO15'],
    'LOSING_EXIT_ONLY': ['LOSING_EXIT'],
}
MIN_PULLBACK_SCORE = 65.0


def _finite(x):
    try:
        v = float(x)
        return v if np.isfinite(v) else np.nan
    except Exception:
        return np.nan


def find_first_impulse_candidates(raw, cfg, arms, min_impulse_pct: float):
    """First causal qualifying re-entry per arm for one impulse threshold.

    Structural gates:
      - 5m trend_up and positive mid_slope8 remain alive
      - a pullback has actually occurred after arm
      - pullback low stays above the pre-arm 10m structural low
      - MACD slope > 0 and RSI slope > 0 at probe time
      - completed 1m candle is green
      - completed 1m close rises >= min_impulse_pct from prior 1m close

    Unlike the previous trigger, this does NOT require close > previous high.
    The purpose is to test whether a sufficiently strong 1m rebound is enough
    once the larger trend has already been established.
    """
    rows = []
    if arms.empty:
        return pd.DataFrame()

    for arm in arms.itertuples(index=False):
        sym = pb.n(arm.symbol)
        bars = raw[sym].copy().sort_values('time').reset_index(drop=True)
        bars['time'] = pd.to_datetime(bars['time'])
        at = pd.Timestamp(arm.arm_time)

        pre = pb.raw_window(bars, at, 10)
        if pre.empty:
            continue
        pre_structural_low = float(pd.to_numeric(pre.low, errors='coerce').min())

        watch = bars[(bars.time >= at) &
                     (bars.time <= at + pd.Timedelta(minutes=pb.MAX_WATCH_MIN))].copy().reset_index(drop=True)
        if len(watch) < 4:
            continue

        pullback_seen = False
        pullback_low = np.inf
        down_bars = 0

        for i in range(1, len(watch)):
            r = watch.iloc[i]
            prev = watch.iloc[i - 1]
            ts = pd.Timestamp(r.time)
            if (ts - at).total_seconds() / 60.0 < pb.MIN_WAIT_MIN:
                continue

            if float(r.close) <= float(prev.close):
                pullback_seen = True
                down_bars += 1
                pullback_low = min(pullback_low, float(r.low))
            elif pullback_seen:
                pullback_low = min(pullback_low, float(r.low))

            if not pullback_seen:
                continue

            st = pb.provisional_state(bars, ts, cfg)
            if st is None:
                continue

            trend_alive = bool(st['trend_up']) and np.isfinite(st['mid_slope8']) and st['mid_slope8'] > 0.0
            if not trend_alive:
                break

            higher_low = np.isfinite(pullback_low) and pullback_low > pre_structural_low
            macd_rising = np.isfinite(st['macd_slope']) and st['macd_slope'] > 0.0
            rsi_rising = np.isfinite(st['rsi_slope']) and st['rsi_slope'] > 0.0

            prev_close = float(prev.close)
            impulse_pct = ((float(r.close) / prev_close) - 1.0) * 100.0 if prev_close > 0 else np.nan
            green_1m = float(r.close) > float(r.open)
            impulse_ok = np.isfinite(impulse_pct) and impulse_pct >= float(min_impulse_pct)

            close_above_mid = np.isfinite(st['mid']) and float(r.close) >= st['mid']
            volume_recovery = _finite(r.volume) > _finite(prev.volume)
            reaccel_proxy = bool(green_1m and impulse_ok)
            score, _, parts = pb.score_candidate(st, higher_low, reaccel_proxy, close_above_mid, volume_recovery)

            mandatory = (
                trend_alive and higher_low and macd_rising and rsi_rising
                and green_1m and impulse_ok and score >= MIN_PULLBACK_SCORE
            )
            if not mandatory:
                continue

            rows.append(dict(
                symbol=sym,
                arm_time=at,
                arm_reason=str(arm.arm_reason),
                primary_source=str(arm.primary_source),
                primary_time=pd.Timestamp(arm.primary_time),
                primary_jump=_finite(arm.primary_jump),
                candidate_time=ts,
                candidate_price=float(r.close),
                pullback_score=float(score),
                mandatory_pass=True,
                impulse_threshold_pct=float(min_impulse_pct),
                impulse_pct=float(impulse_pct),
                green_1m=bool(green_1m),
                down_bars=int(down_bars),
                pre_structural_low=float(pre_structural_low),
                pullback_low=float(pullback_low),
                higher_low=bool(higher_low),
                reaccel=True,
                macd=st['macd'], macd_signal=st['macd_signal'], macd_slope=st['macd_slope'],
                rsi=st['rsi'], rsi_slope=st['rsi_slope'],
                trend_up=st['trend_up'], mid=st['mid'], mid_slope8=st['mid_slope8'],
                inner_upper=st['inner_upper'], inner_lower=st['inner_lower'], outer_upper=st['outer_upper'],
                outer_expanding=st['outer_expanding'], close_above_mid=close_above_mid,
                volume_recovery=volume_recovery,
                base_live_score=st['entry_score'],
                **parts,
            ))
            break

    return pd.DataFrame(rows)


def make_extra_tags(q: pd.DataFrame):
    tags = []
    for r in q.itertuples(index=False):
        ev = pb.event_from_candidate(r)
        if ev is None:
            continue
        tags.append(dict(
            source='UPTREND_PULLBACK_1M_IMPULSE',
            symbol=pb.n(r.symbol),
            time=pd.Timestamp(r.candidate_time),
            event=ev,
            meta={
                'arm_reason': str(r.arm_reason),
                'arm_time': pd.Timestamp(r.arm_time),
                'primary_time': pd.Timestamp(r.primary_time),
                'impulse_pct': float(r.impulse_pct),
                'impulse_threshold_pct': float(r.impulse_threshold_pct),
            },
        ))
    return tags


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    print('=== V22 PULLBACK 1M IMPULSE SWEEP ===', flush=True)
    print('Trigger = established 5m uptrend + higher-low + MACD rising + RSI rising + strong completed 1m green bar.', flush=True)
    print('Impulse thresholds (%) =', IMPULSE_PCTS, flush=True)
    print('Previous-high breakout is NOT required in this diagnostic.', flush=True)

    raw = {pb.n(k): v for k, v in load_data().items()}
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)
    packed, states, tagged, baseline = pb.baseline_objects(raw, cfg)
    bstat = pb.summary('A_BASELINE', baseline)
    print('\nBASELINE', bstat)

    arms_all = pb.build_arms(raw, cfg, tagged, baseline)
    print('ALL ARMS', len(arms_all), arms_all.arm_reason.value_counts().to_dict() if len(arms_all) else {})

    summaries = [bstat]
    candidate_dump = []
    trade_dump = [baseline.assign(case='A_BASELINE')]

    for gname, reasons in GROUPS.items():
        arms = arms_all[arms_all.arm_reason.isin(reasons)].copy().reset_index(drop=True)
        print(f'\n=== {gname} ===')
        print('arms=', len(arms))

        for imp in IMPULSE_PCTS:
            q = find_first_impulse_candidates(raw, cfg, arms, imp)
            if not q.empty:
                q = q.sort_values(['candidate_time','impulse_pct'], ascending=[True,False]).drop_duplicates(['symbol','candidate_time'])
                qq = q.copy(); qq['group'] = gname
                candidate_dump.append(qq)

            extra_tags = make_extra_tags(q)
            tr = pb.integ.simulate(packed, states, list(tagged) + extra_tags)
            label = f'{gname}_IMP{str(imp).replace(".","p")}'
            st = pb.summary(label, tr)
            st['arms'] = len(arms)
            st['selected_candidates'] = len(q)
            st['impulse_threshold_pct'] = imp
            if len(q):
                st['avg_candidate_impulse_pct'] = float(q.impulse_pct.mean())
                st['avg_candidate_live_score'] = float(q.base_live_score.mean())
            else:
                st['avg_candidate_impulse_pct'] = np.nan
                st['avg_candidate_live_score'] = np.nan
            summaries.append(st)
            trade_dump.append(tr.assign(case=label))
            print(label, st)

            t = q[(q.symbol.astype(str).str.zfill(6) == '466100') &
                  (pd.to_datetime(q.arm_time).dt.date == pd.Timestamp('2026-08-14').date())] if len(q) else pd.DataFrame()
            if len(t):
                r = t.iloc[0]
                print('  TARGET466100',
                      'time=', r.candidate_time,
                      'price=', r.candidate_price,
                      'impulse_pct=', round(float(r.impulse_pct), 4),
                      'score=', r.pullback_score,
                      'macd_slope=', round(float(r.macd_slope), 6),
                      'rsi_slope=', round(float(r.rsi_slope), 6))

    sdf = pd.DataFrame(summaries)
    print('\n=== SUMMARY ===')
    print(sdf.to_string(index=False))
    sdf.to_csv(OUT / 'summary.csv', index=False)
    if candidate_dump:
        pd.concat(candidate_dump, ignore_index=True).to_csv(OUT / 'candidates.csv', index=False)
    if trade_dump:
        pd.concat(trade_dump, ignore_index=True).to_csv(OUT / 'trades.csv', index=False)
    arms_all.to_csv(OUT / 'arms.csv', index=False)
    print('\nWROTE', OUT)


if __name__ == '__main__':
    main()
