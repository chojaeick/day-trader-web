from __future__ import annotations

"""Causal diagnostic for a missing Engine5 entry source: UPTREND_PULLBACK_REENTRY.

Intent
------
After a primary Engine5 attempt has *causally* failed in one of two ways:
  1) a normal-time primary signal is rejected by the V22 last-1m jump veto (>=15), or
  2) an executed baseline trade is closed as a net loser,
keep watching the symbol only while the 5m uptrend structure remains alive.  If
price pulls back without breaking the pre-arm structural low and then attempts
to rise again while MACD and RSI are both rising, create a new independent
UPTREND_PULLBACK_REENTRY candidate.

This script is diagnostic only.  It does not modify production V22.
All features at probe time are built only from completed 1m bars before that
probe timestamp (same causal provisional-5m convention as the V22 minute-score
diagnostic).
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
import tools.diagnose_engine5_v22_preentry_minute_scores as diag
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5, DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT = Path('/home/ubuntu/day-trader-api/engine5_v22_uptrend_pullback_reentry')
JUMP_VETO = 15.0
FEE_RT_PCT = integ.FEE_RT_PCT
SCORE_THRESHOLDS = [65.0, 70.0, 75.0, 80.0]
MAX_WATCH_MIN = 60
MIN_WAIT_MIN = 2


def n(x): return str(x).zfill(6)


def finite(x):
    try:
        v = float(x)
        return v if np.isfinite(v) else np.nan
    except Exception:
        return np.nan


def provisional_state(bars: pd.DataFrame, probe_ts: pd.Timestamp, cfg: DoubleBollingerEngine5Config):
    """Return the last causal provisional-5m Engine5 row at probe_ts."""
    p5 = diag.provisional_5m(bars, pd.Timestamp(probe_ts))
    if len(p5) < max(30, int(cfg.bb_period) + 5):
        return None
    eng = DoubleBollingerEngine5(cfg)
    f = v10._refine_entry_frame(eng.enrich(p5))
    s = reweight({'X': f}, cfg, 0.0)['X']
    if s.empty:
        return None
    r = s.iloc[-1]
    return {
        'time': pd.Timestamp(probe_ts),
        'close5': finite(r.get('close')),
        'entry_score': finite(r.get('entry_score')),
        'trend_up': bool(r.get('trend_up', False)),
        'mid': finite(r.get('mid')),
        'mid_slope8': finite(r.get('mid_slope8')),
        'inner_upper': finite(r.get('inner_upper')),
        'inner_lower': finite(r.get('inner_lower')),
        'outer_upper': finite(r.get('outer_upper')),
        'outer_expanding': bool(r.get('outer_expanding', False)),
        'macd': finite(r.get('macd')),
        'macd_signal': finite(r.get('macd_signal')),
        'macd_slope': finite(r.get('macd_slope')),
        'macd_slope_spread': finite(r.get('macd_slope_spread')),
        'rsi': finite(r.get('rsi')),
        'rsi_slope': finite(r.get('rsi_slope')),
    }


def raw_window(bars: pd.DataFrame, end_ts: pd.Timestamp, minutes: int):
    x = bars.copy()
    x['time'] = pd.to_datetime(x['time'])
    end_ts = pd.Timestamp(end_ts)
    return x[(x.time < end_ts) & (x.time >= end_ts - pd.Timedelta(minutes=minutes))].sort_values('time')


def baseline_objects(raw, cfg):
    packed = v8.base.pack_exit_events(raw, DoubleBollingerEngine5Config())
    states = base.pack_state_events(base.build_cfg_frames(raw, DoubleBollingerEngine5Config()))
    frames0 = base.build_cfg_frames(raw, cfg)
    f10 = {n(s): v10._refine_entry_frame(f) for s, f in frames0.items()}
    scored = {n(s): f for s, f in reweight(f10, cfg, 0.0).items()}
    strength = {s: ms.add_strength(f) for s, f in scored.items()}
    completed = {s: rt.add_completed_strength(f) for s, f in scored.items()}
    micros = {s: h.build_micro(raw[s], cfg) for s in raw}
    tagged = integ.build_sources(raw, cfg, scored, strength, completed, micros)
    trades = integ.simulate(packed, states, tagged)
    return packed, states, tagged, trades


def build_arms(raw, cfg, tagged, trades):
    """Create only causally knowable watch arms.

    VETO arm: knowable immediately at the rejected primary signal time.
    LOSS arm: knowable only at the actual losing exit time.
    """
    arms = []

    # V22 normal-time last-jump veto arms.
    for item in tagged:
        ts = pd.Timestamp(item['time'])
        sym = n(item['symbol'])
        s0 = diag.score_at(raw[sym], ts, cfg)
        s1 = diag.score_at(raw[sym], ts - pd.Timedelta(minutes=1), cfg)
        if s0 is None or s1 is None:
            continue
        jump = finite(s0.get('live_score')) - finite(s1.get('live_score'))
        if np.isfinite(jump) and jump >= JUMP_VETO:
            arms.append(dict(symbol=sym, arm_time=ts, arm_reason='VETO15',
                             primary_source=item['source'], primary_time=ts,
                             primary_jump=jump))

    # Losing executed trades become eligible only after their loss is realized.
    for tr in trades.itertuples(index=False):
        net = float(tr.pnl_pct) - FEE_RT_PCT
        if net <= 0.0:
            arms.append(dict(symbol=n(tr.symbol), arm_time=pd.Timestamp(tr.exit_time),
                             arm_reason='LOSING_EXIT', primary_source=str(tr.source),
                             primary_time=pd.Timestamp(tr.entry_time), primary_jump=np.nan))

    a = pd.DataFrame(arms)
    if a.empty:
        return a
    a = a.sort_values(['symbol','arm_time','arm_reason']).drop_duplicates(['symbol','arm_time','arm_reason'])
    return a.reset_index(drop=True)


def score_candidate(st, higher_low, reaccel, close_above_mid, volume_recovery):
    macd_rising = np.isfinite(st['macd_slope']) and st['macd_slope'] > 0.0
    rsi_rising = np.isfinite(st['rsi_slope']) and st['rsi_slope'] > 0.0
    trend_alive = bool(st['trend_up']) and np.isfinite(st['mid_slope8']) and st['mid_slope8'] > 0.0

    # Transparent 100-point pullback score.  MACD + RSI are mandatory gates,
    # not merely compensable score components.
    parts = {
        'pts_trend_alive': 20.0 if trend_alive else 0.0,
        'pts_higher_low': 15.0 if higher_low else 0.0,
        'pts_macd_rising': 10.0 if macd_rising else 0.0,
        'pts_rsi_rising': 10.0 if rsi_rising else 0.0,
        'pts_reaccel': 10.0 if reaccel else 0.0,
        'pts_close_above_mid': 10.0 if close_above_mid else 0.0,
        'pts_outer_expanding': 10.0 if st['outer_expanding'] else 0.0,
        'pts_volume_recovery': 15.0 if volume_recovery else 0.0,
    }
    score = float(sum(parts.values()))
    mandatory = trend_alive and higher_low and macd_rising and rsi_rising and reaccel
    return score, mandatory, parts


def find_pullback_candidates(raw, cfg, arms):
    rows = []
    if arms.empty:
        return pd.DataFrame()

    for arm in arms.itertuples(index=False):
        sym = n(arm.symbol)
        bars = raw[sym].copy().sort_values('time').reset_index(drop=True)
        bars['time'] = pd.to_datetime(bars['time'])
        at = pd.Timestamp(arm.arm_time)

        pre = raw_window(bars, at, 10)
        if pre.empty:
            continue
        pre_structural_low = float(pd.to_numeric(pre.low, errors='coerce').min())

        watch = bars[(bars.time >= at) & (bars.time <= at + pd.Timedelta(minutes=MAX_WATCH_MIN))].copy()
        if len(watch) < 4:
            continue

        peak_high = -np.inf
        pullback_low = np.inf
        pullback_seen = False
        down_bars = 0

        for i in range(1, len(watch)):
            r = watch.iloc[i]
            prev = watch.iloc[i-1]
            ts = pd.Timestamp(r.time)
            if (ts - at).total_seconds()/60.0 < MIN_WAIT_MIN:
                peak_high = max(peak_high, float(r.high))
                continue

            peak_high = max(peak_high, float(prev.high), float(r.high))
            if float(r.close) <= float(prev.close):
                down_bars += 1
                pullback_seen = True
                pullback_low = min(pullback_low, float(r.low))
            elif pullback_seen:
                pullback_low = min(pullback_low, float(r.low))

            if not pullback_seen:
                continue

            st = provisional_state(bars, ts, cfg)
            if st is None:
                continue

            # Stop watching once the 5m structural trend itself is no longer alive.
            trend_alive_now = bool(st['trend_up']) and np.isfinite(st['mid_slope8']) and st['mid_slope8'] > 0.0
            if not trend_alive_now:
                break

            higher_low = np.isfinite(pullback_low) and pullback_low > pre_structural_low
            reaccel = float(r.close) > float(prev.high) and float(r.close) > float(prev.close)
            close_above_mid = np.isfinite(st['mid']) and float(r.close) >= st['mid']

            # Volume recovery is deliberately relative to immediately preceding
            # completed 1m bar, not a tuned multiplier.
            volume_recovery = finite(r.volume) > finite(prev.volume)
            score, mandatory, parts = score_candidate(st, higher_low, reaccel, close_above_mid, volume_recovery)

            rec = dict(
                symbol=sym, arm_time=at, arm_reason=arm.arm_reason,
                primary_source=arm.primary_source, primary_time=arm.primary_time,
                primary_jump=arm.primary_jump,
                candidate_time=ts, candidate_price=float(r.close),
                pullback_score=score, mandatory_pass=mandatory,
                pullback_seen=pullback_seen, down_bars=down_bars,
                pre_structural_low=pre_structural_low, pullback_low=pullback_low,
                higher_low=higher_low, reaccel=reaccel,
                macd=st['macd'], macd_signal=st['macd_signal'], macd_slope=st['macd_slope'],
                rsi=st['rsi'], rsi_slope=st['rsi_slope'],
                trend_up=st['trend_up'], mid=st['mid'], mid_slope8=st['mid_slope8'],
                inner_upper=st['inner_upper'], inner_lower=st['inner_lower'], outer_upper=st['outer_upper'],
                outer_expanding=st['outer_expanding'], close_above_mid=close_above_mid,
                volume_recovery=volume_recovery,
                base_live_score=st['entry_score'],
                **parts,
            )
            rows.append(rec)

            # One pullback attempt per arm: once a mandatory reacceleration occurs,
            # later thresholds are evaluated from this same first causal attempt.
            if mandatory:
                break

    return pd.DataFrame(rows)


def event_from_candidate(r):
    iu, il, ou, mid = map(finite, [r.inner_upper, r.inner_lower, r.outer_upper, r.mid])
    band_r = iu - il if np.isfinite(iu) and np.isfinite(il) else np.nan
    if not np.isfinite(band_r) or band_r <= 0.0:
        return None
    close = float(r.candidate_price)
    # Same risk geometry as protected Engine5 V20 stream: 1 full inner-band width.
    stop_dist = band_r
    extended = bool(np.isfinite(ou) and close > ou)
    return (n(r.symbol), close, float(r.pullback_score),
            max(0.0, finite(r.macd_slope)) if np.isfinite(finite(r.macd_slope)) else 0.0,
            max(0.0, finite(r.rsi_slope)) if np.isfinite(finite(r.rsi_slope)) else 0.0,
            band_r, stop_dist, iu, il, ou, mid, extended, False)


def summary(label, tr):
    g = pd.to_numeric(tr.pnl_pct, errors='coerce').dropna() if len(tr) else pd.Series(dtype=float)
    net = g - FEE_RT_PCT
    gp = float(net[net > 0].sum()) if len(net) else 0.0
    gl = float(-net[net < 0].sum()) if len(net) else 0.0
    return dict(case=label, trades=len(net), wins=int((net > 0).sum()),
                win_pct=float((net > 0).mean()*100.0) if len(net) else 0.0,
                net_sum_pct=float(net.sum()) if len(net) else 0.0,
                avg_net_pct=float(net.mean()) if len(net) else 0.0,
                pf=(gp/gl if gl > 0 else np.inf),
                max_loss_pct=float(net.min()) if len(net) else np.nan,
                max_win_pct=float(net.max()) if len(net) else np.nan)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    print('=== V22 UPTREND PULLBACK REENTRY CAUSAL DIAGNOSTIC ===', flush=True)
    print('Arm = VETO15 rejected primary signal OR realized losing exit.', flush=True)
    print('Mandatory = uptrend alive + higher-low + MACD rising + RSI rising + 1m reacceleration.', flush=True)
    print('Score thresholds =', SCORE_THRESHOLDS, flush=True)

    raw = {n(k): v for k, v in load_data().items()}
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)
    packed, states, tagged, baseline = baseline_objects(raw, cfg)

    bstat = summary('A_BASELINE', baseline)
    print('\nBASELINE', bstat)

    arms = build_arms(raw, cfg, tagged, baseline)
    print('arms=', len(arms), 'by_reason=', arms.arm_reason.value_counts().to_dict() if len(arms) else {})

    probes = find_pullback_candidates(raw, cfg, arms)
    if probes.empty:
        print('NO PULLBACK PROBES FOUND')
        return

    mandatory = probes[probes.mandatory_pass].copy().sort_values(['symbol','candidate_time'])
    print('mandatory_first_attempts=', len(mandatory))

    summaries = [bstat]
    all_trades = [baseline.assign(case='A_BASELINE')]
    selected_rows = []

    for th in SCORE_THRESHOLDS:
        q = mandatory[mandatory.pullback_score >= th].copy()
        # Prevent duplicate same symbol/time candidates from overlapping arms.
        q = q.sort_values(['candidate_time','pullback_score'], ascending=[True,False]).drop_duplicates(['symbol','candidate_time'])
        extra = []
        for r in q.itertuples(index=False):
            ev = event_from_candidate(r)
            if ev is None:
                continue
            extra.append(dict(source='UPTREND_PULLBACK_REENTRY', symbol=n(r.symbol),
                              time=pd.Timestamp(r.candidate_time), event=ev,
                              meta={'arm_reason':r.arm_reason, 'primary_source':r.primary_source}))

        combined_tagged = list(tagged) + extra
        tr = integ.simulate(packed, states, combined_tagged)
        label = f'PULLBACK_{int(th)}'
        summaries.append(summary(label, tr))
        all_trades.append(tr.assign(case=label))
        if len(q):
            qq = q.copy(); qq['case'] = label; selected_rows.append(qq)
        print(label, summaries[-1], 'selected_candidates=', len(extra))

    sm = pd.DataFrame(summaries)
    print('\n=== SUMMARY ===')
    print(sm.to_string(index=False))

    # Explicit target case requested during design discussion.
    target = probes[(probes.symbol == '466100') &
                    (pd.to_datetime(probes.candidate_time).dt.date == pd.Timestamp('2026-08-14').date())]
    print('\n=== TARGET 466100 2026-08-14 ===')
    if target.empty:
        print('NO TARGET PROBES')
    else:
        cols = ['arm_time','arm_reason','primary_time','primary_jump','candidate_time','candidate_price',
                'pullback_score','mandatory_pass','base_live_score','trend_up','mid_slope8','higher_low',
                'pullback_low','pre_structural_low','macd_slope','rsi_slope','reaccel','close_above_mid',
                'outer_expanding','volume_recovery']
        print(target[cols].to_string(index=False))

    sm.to_csv(OUT / 'summary.csv', index=False)
    arms.to_csv(OUT / 'arms.csv', index=False)
    probes.to_csv(OUT / 'all_probe_rows.csv', index=False)
    if selected_rows:
        pd.concat(selected_rows, ignore_index=True).to_csv(OUT / 'selected_candidates.csv', index=False)
    pd.concat(all_trades, ignore_index=True).to_csv(OUT / 'trades_all_cases.csv', index=False)
    print('\nWROTE', OUT)


if __name__ == '__main__':
    main()
