from __future__ import annotations

"""Second-pass Slow-turn transition scoring.

Fixes the timing flaw in v1: the score is measured over the actual transition episode
leading into the existing Slow-turn READY/1m-confirmed entry, not from one completed 5m
bar sampled at/after READY.

Existing Slow-turn candidate generation, episode re-arm, DEEP selector, V20, V-rebound,
and exits remain unchanged.  This file is a validator only.
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
OUT_SUMMARY = OUT_DIR / 'slow_turn_transition_score_v2_summary.csv'
OUT_DETAIL = OUT_DIR / 'slow_turn_transition_score_v2_detail.csv'
CUT = -0.15
FEE_RT_PCT = 0.25
THRESHOLDS = (30, 40, 50, 60, 70)

W_MACD = 35.0
W_RSI = 35.0
W_SYNC = 15.0
W_PRICE = 10.0
W_MICRO = 5.0


def n(x): return str(x).zfill(6)
def num(x): return pd.to_numeric(x, errors='coerce')

def finite(x):
    try:
        v = float(x)
        return v if np.isfinite(v) else np.nan
    except Exception:
        return np.nan


def clip01(x):
    return float(np.clip(float(x), 0.0, 1.0)) if np.isfinite(x) else 0.0


def robust_scale(s: pd.Series) -> float:
    x = num(s).dropna().abs()
    if x.empty: return np.nan
    q = float(x.median())
    if q <= 1e-12: q = float(x.mean())
    return q if q > 1e-12 else np.nan


def pick_series(df: pd.DataFrame, names):
    for c in names:
        if c in df.columns:
            s = num(df[c])
            if s.notna().any():
                return s, c
    return None, None


def episode_features_for_candidate(r, pf: pd.DataFrame, micro: pd.DataFrame):
    ready = pd.Timestamp(r.ready_time)
    entry = pd.Timestamp(r.entry_time)

    z = pf.copy().sort_values('time')
    z['time'] = pd.to_datetime(z['time'])

    # 20-minute causal provisional window ending at READY.
    w5 = z[(z.time <= ready) & (z.time >= ready - pd.Timedelta(minutes=20))].copy()
    if len(w5) < 3:
        return dict(transition_score=np.nan, score_error='short_window', episode_rows=len(w5))

    # Prefer level series when available.  Provisional Slow-turn caches often carry
    # gap_delta/rsi_slope rather than macd_gap/rsi, so reconstruct levels causally by
    # cumulative sum.  Only relative turn shape/speed is used, so additive origin is irrelevant.
    gap, gap_col = pick_series(w5, ['macd_gap', 'gap'])
    if gap is None:
        gd, gd_col = pick_series(w5, ['gap_delta', 'macd_gap_delta'])
        if gd is not None:
            gap = gd.fillna(0.0).cumsum()
            gap_col = f'cumsum({gd_col})'

    rsi, rsi_col = pick_series(w5, ['rsi'])
    if rsi is None:
        rs, rs_col = pick_series(w5, ['rsi_slope'])
        if rs is not None:
            rsi = rs.fillna(0.0).cumsum()
            rsi_col = f'cumsum({rs_col})'

    if gap is None or rsi is None:
        return dict(
            transition_score=np.nan,
            score_error=f'missing_series gap={gap_col} rsi={rsi_col}',
            episode_rows=len(w5),
            pf_columns='|'.join(map(str, w5.columns)),
        )

    # Use positional arrays to avoid idxmin/.loc index-label mismatch after filtering.
    gv = np.asarray(gap, dtype=float)
    rv = np.asarray(rsi, dtype=float)
    gt = pd.to_datetime(w5['time']).reset_index(drop=True)

    if not np.isfinite(gv).any() or not np.isfinite(rv).any():
        return dict(transition_score=np.nan, score_error='nonfinite_series', episode_rows=len(w5))

    gi = int(np.nanargmin(gv)); ri = int(np.nanargmin(rv))
    gtail = gv[gi:]; rtail = rv[ri:]

    g_gain = finite(gtail[-1] - gtail[0]) if len(gtail) >= 2 else 0.0
    r_gain = finite(rtail[-1] - rtail[0]) if len(rtail) >= 2 else 0.0
    g_steps = max(len(gtail) - 1, 1)
    r_steps = max(len(rtail) - 1, 1)
    g_speed = g_gain / g_steps
    r_speed = r_gain / r_steps

    # Baseline from the 12 provisional observations immediately before the earlier turn anchor.
    anchor_pos = min(gi, ri)
    pre = z[z.time < pd.Timestamp(gt.iloc[anchor_pos])].tail(12).copy()

    pre_gap, _ = pick_series(pre, ['macd_gap', 'gap'])
    if pre_gap is None:
        pre_gd, _ = pick_series(pre, ['gap_delta', 'macd_gap_delta'])
        if pre_gd is not None:
            # For a delta-backed current series, baseline is directly the typical delta magnitude.
            g_base = robust_scale(pre_gd)
        else:
            g_base = np.nan
    else:
        g_base = robust_scale(pre_gap.diff())

    pre_rsi, _ = pick_series(pre, ['rsi'])
    if pre_rsi is None:
        pre_rs, _ = pick_series(pre, ['rsi_slope'])
        r_base = robust_scale(pre_rs) if pre_rs is not None else np.nan
    else:
        r_base = robust_scale(pre_rsi.diff())

    g_strength = clip01(g_speed / (3.0 * g_base)) if np.isfinite(g_base) and g_base > 0 and g_speed > 0 else 0.0
    r_strength = clip01(r_speed / (3.0 * r_base)) if np.isfinite(r_base) and r_base > 0 and r_speed > 0 else 0.0
    sync = min(g_strength, r_strength)

    px = finite(getattr(r, 'price_progress_1m_pct', np.nan))
    price_strength = clip01(px / 1.5) if np.isfinite(px) and px > 0 else 0.0
    j1 = finite(getattr(r, 'joint1_persistence', np.nan))
    micro_strength = clip01((j1 - 0.67) / 0.33) if np.isfinite(j1) else 0.0

    score_macd = W_MACD * g_strength
    score_rsi = W_RSI * r_strength
    score_sync = W_SYNC * sync
    score_price = W_PRICE * price_strength
    score_micro = W_MICRO * micro_strength
    score = score_macd + score_rsi + score_sync + score_price + score_micro

    return dict(
        transition_score=float(score), score_error='', episode_rows=len(w5),
        gap_source=gap_col, rsi_source=rsi_col,
        macd_episode_score=score_macd,
        rsi_episode_score=score_rsi,
        sync_score=score_sync,
        price_score=score_price,
        micro_score=score_micro,
        macd_episode_gain=g_gain,
        rsi_episode_gain=r_gain,
        macd_episode_steps=g_steps,
        rsi_episode_steps=r_steps,
        macd_episode_speed=g_speed,
        rsi_episode_speed=r_speed,
        macd_baseline_step=g_base,
        rsi_baseline_step=r_base,
        macd_turn_start=pd.Timestamp(gt.iloc[gi]),
        rsi_turn_start=pd.Timestamp(gt.iloc[ri]),
        episode_start=pd.Timestamp(w5.iloc[0].time),
        episode_end=pd.Timestamp(w5.iloc[-1].time),
    )


def replace_event_score(event, score):
    e = list(event); e[2] = float(score); return tuple(e)


def attach_scores(sel, pf_by_symbol, micros):
    rows=[]
    for _, r in sel.iterrows():
        d = episode_features_for_candidate(r, pf_by_symbol[n(r.symbol)], micros[n(r.symbol)])
        rows.append(d)
    out = pd.concat([sel.copy(), pd.DataFrame(rows, index=sel.index)], axis=1)
    bad = out[num(out.transition_score).isna()]
    if len(bad):
        print(f'[SCORE ERROR] {len(bad)}/{len(out)} candidates could not be scored')
        cols=[c for c in ['symbol','ready_time','entry_time','score_error','episode_rows','gap_source','rsi_source'] if c in bad.columns]
        print(bad[cols].head(20).to_string(index=False))
    return out


def tags_from_scored(sel, threshold):
    out=[]
    q = sel[num(sel.transition_score).notna() & (num(sel.transition_score) >= float(threshold))]
    for _, r in q.iterrows():
        out.append(dict(source='SLOW_TURN', symbol=n(r.symbol), time=pd.Timestamp(r.entry_time),
                        event=replace_event_score(r.event, r.transition_score),
                        meta=dict(regime=str(r.regime), transition_score=float(r.transition_score),
                                  norm_mid_slope_pct=finite(r.norm_mid_slope_pct))))
    return out


def stat(trades):
    p = num(trades['pnl_pct']).dropna() if len(trades) else pd.Series(dtype=float)
    net = p - FEE_RT_PCT
    gp=float(net[net>0].sum()) if len(net) else 0.0
    gl=float(-net[net<0].sum()) if len(net) else 0.0
    return dict(trades=len(net), wins=int((net>0).sum()), win_pct=float((net>0).mean()*100) if len(net) else 0.0,
                net_sum_pct=float(net.sum()) if len(net) else 0.0,
                pf=(gp/gl if gl>0 else np.inf), max_loss_pct=float(net.min()) if len(net) else np.nan)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw={n(k):v for k,v in load_data().items()}
    cfg0=DoubleBollingerEngine5Config()
    cfg=replace(cfg0, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)
    packed=v8.base.pack_exit_events(raw,cfg0)
    states=base.pack_state_events(base.build_cfg_frames(raw,cfg0))
    frames0=base.build_cfg_frames(raw,cfg)
    f10={n(s):v10._refine_entry_frame(f) for s,f in frames0.items()}
    scored={n(s):f for s,f in reweight(f10,cfg,0.0).items()}
    strength={s:ms.add_strength(f) for s,f in scored.items()}
    completed={s:rt.add_completed_strength(f) for s,f in scored.items()}
    micros={s:h.build_micro(raw[s],cfg) for s in raw}

    pf_by_symbol={}
    for s in raw:
        pf,_=st.load_or_build_cache(s,raw[s],cfg,completed[s])
        pf_by_symbol[s]=pf

    old_tagged=integ.build_sources(raw,cfg,scored,strength,completed,micros)
    non_slow=[x for x in old_tagged if x['source']!='SLOW_TURN']
    allc=revised.build_all_slow(raw,cfg,completed,micros)
    sel=revised.select_revised(allc,CUT)
    x=attach_scores(sel,pf_by_symbol,micros)

    trade_path=OUT_DIR/'integrated_slow_turn_rearm_deep_trades.csv'
    if trade_path.exists():
        tr=pd.read_csv(trade_path); tr['cut']=num(tr.get('cut')); tr=tr[np.isclose(tr['cut'],CUT)].copy()
        tr['symbol']=tr.symbol.astype(str).str.zfill(6); tr['entry_time']=pd.to_datetime(tr.entry_time); tr['net_pct']=num(tr.pnl_pct)-FEE_RT_PCT
        x['symbol']=x.symbol.astype(str).str.zfill(6); x['entry_time']=pd.to_datetime(x.entry_time)
        x=x.merge(tr[['symbol','entry_time','net_pct']].drop_duplicates(['symbol','entry_time']),on=['symbol','entry_time'],how='left')
        x['result']=np.where(num(x.net_pct)>0,'WIN',np.where(num(x.net_pct)<=0,'LOSS','UNMATCHED'))

    rows=[]
    for th in THRESHOLDS:
        stags=tags_from_scored(x,th)
        slow=integ.simulate(packed,states,stags)
        full=integ.simulate(packed,states,sorted(non_slow+stags,key=lambda z:(pd.Timestamp(z['time']),z['symbol'],z['source'])))
        a=stat(slow); b=stat(full)
        rows.append(dict(threshold=th,slow_signals=len(stags),slow_trades=a['trades'],slow_wins=a['wins'],slow_win_pct=a['win_pct'],slow_net_pct=a['net_sum_pct'],slow_pf=a['pf'],slow_max_loss=a['max_loss_pct'],full_trades=b['trades'],full_wins=b['wins'],full_win_pct=b['win_pct'],full_net_pct=b['net_sum_pct'],full_pf=b['pf'],full_max_loss=b['max_loss_pct']))
    summary=pd.DataFrame(rows); summary.to_csv(OUT_SUMMARY,index=False)
    x.drop(columns=['event'],errors='ignore').to_csv(OUT_DETAIL,index=False)

    print('=== SLOW-TURN TRANSITION SCORE V2 | EPISODE-BASED ===')
    print(summary.to_string(index=False,float_format=lambda v:f'{v:.4f}'))
    print('\n=== CANONICAL KR CASES ===')
    targets=[('058610','2026-08-13 09:25:00+09:00','V_TURN_SUCCESS'),('122630','2026-08-20 13:06:00+09:00','GRADUAL_FAILURE'),('950160','2026-08-14 10:59:00+09:00','VALID_SLOW_SUCCESS')]
    cols=['transition_score','score_error','episode_rows','gap_source','rsi_source','macd_episode_score','rsi_episode_score','sync_score','price_score','micro_score','macd_episode_gain','rsi_episode_gain','macd_episode_speed','rsi_episode_speed','macd_baseline_step','rsi_baseline_step','macd_turn_start','rsi_turn_start','episode_start','episode_end','net_pct','result']
    for sym,t,label in targets:
        ts=pd.Timestamp(t); q=x[(x.symbol==sym)&(x.entry_time==ts)]
        print(f'\n[{label}] {sym} {ts}')
        if q.empty: print('NOT FOUND'); continue
        r=q.iloc[0]
        for c in cols:
            if c in q.columns: print(f'{c:24s} {r[c]}')
    print('\nPASS CONDITION: V_TURN_SUCCESS must score well above GRADUAL_FAILURE, while VALID_SLOW_SUCCESS must remain viable without requiring trend_up/MACD crossover.')
    print('WROTE',OUT_SUMMARY); print('WROTE',OUT_DETAIL)

if __name__=='__main__': main()
