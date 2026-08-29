from __future__ import annotations

from dataclasses import replace
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v15_boundary as v15
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_multi_symbol as multi
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

THRESHOLD = 50
OPEN_MINUTE = 9 * 60 + 10
OUT_DIR = '/home/ubuntu/day-trader-api/engine5_v16_full_validation'
HORIZONS = (1, 3, 5, 10)


def filt_open(ev):
    return {
        ts: rows for ts, rows in ev.items()
        if pd.Timestamp(ts).hour * 60 + pd.Timestamp(ts).minute >= OPEN_MINUTE
    }


def key_df(t: pd.DataFrame) -> pd.DataFrame:
    if not len(t):
        return t.copy()
    x = t.copy()
    x['symbol'] = x.symbol.astype(str).str.zfill(6)
    x['entry_time'] = pd.to_datetime(x.entry_time)
    x['key'] = list(zip(x.symbol, x.entry_time.astype(str)))
    return x


def event_lookup(events):
    out = {}
    for ts, rows in events.items():
        t = pd.Timestamp(ts)
        for c in rows:
            sym = str(c[0]).zfill(6)
            out[(sym, str(t))] = c
    return out


def row_at_or_before(df: pd.DataFrame, ts: pd.Timestamp):
    q = df[pd.to_datetime(df.time) <= ts]
    if not len(q):
        return None
    return q.iloc[-1]


def path_stats(raw_df: pd.DataFrame, entry_time: pd.Timestamp, entry_price: float):
    d = raw_df.copy()
    d['time'] = pd.to_datetime(d.time)
    out = {}
    for h in HORIZONS:
        q = d[(d.time > entry_time) & (d.time <= entry_time + pd.Timedelta(minutes=h))]
        if not len(q):
            out[f'mfe_{h}m_pct'] = np.nan
            out[f'mae_{h}m_pct'] = np.nan
            out[f'close_{h}m_pct'] = np.nan
            continue
        hi = pd.to_numeric(q.high, errors='coerce').max()
        lo = pd.to_numeric(q.low, errors='coerce').min()
        cl = float(pd.to_numeric(q.close, errors='coerce').iloc[-1])
        out[f'mfe_{h}m_pct'] = (float(hi) / entry_price - 1.0) * 100.0 if np.isfinite(hi) else np.nan
        out[f'mae_{h}m_pct'] = (float(lo) / entry_price - 1.0) * 100.0 if np.isfinite(lo) else np.nan
        out[f'close_{h}m_pct'] = (cl / entry_price - 1.0) * 100.0
    return out


def enrich_trade(r, event, scored_frame, rich_micro, micro15, gapmap, raw_df):
    ts = pd.Timestamp(r.entry_time)
    minute = ts.hour * 60 + ts.minute
    entry_price = float(r.entry_price)

    e = list(event) if event is not None else []
    score = float(e[2]) if len(e) > 2 else np.nan
    macd_score = float(e[3]) if len(e) > 3 else np.nan
    rsi_score = float(e[4]) if len(e) > 4 else np.nan
    band_r = float(e[5]) if len(e) > 5 else np.nan
    stop_dist = float(e[6]) if len(e) > 6 else np.nan
    extended = bool(e[11]) if len(e) > 11 else False
    breakout = bool(e[12]) if len(e) > 12 else False

    sf = row_at_or_before(scored_frame, ts)
    rm = row_at_or_before(rich_micro, ts)
    st = v15.slope_state_at(micro15, ts)
    gap = gapmap.get(ts.date(), np.nan)
    sensitive = bool(np.isfinite(gap) and gap >= v15.GAP_PCT and OPEN_MINUTE <= minute < v15.MICRO_END_MINUTE)
    would_wait = bool(sensitive and st['block'])

    rec = {
        'symbol': str(r.symbol).zfill(6),
        'entry_time': ts,
        'exit_time': pd.Timestamp(r.exit_time),
        'entry_minute': minute,
        'entry_clock': ts.strftime('%H:%M'),
        'pnl_pct': float(r.pnl_pct),
        'win': bool(float(r.pnl_pct) > 0),
        'reason': str(r.reason),
        'entry_price': entry_price,
        'score': score,
        'macd_score_component': macd_score,
        'rsi_score_component': rsi_score,
        'band_r_pct': band_r / entry_price * 100.0 if np.isfinite(band_r) and entry_price else np.nan,
        'stop_dist_pct': stop_dist / entry_price * 100.0 if np.isfinite(stop_dist) and entry_price else np.nan,
        'extended': extended,
        'breakout': breakout,
        'gap_pct': gap,
        'opening_sensitive_gap': sensitive,
        'would_v15_wait': would_wait,
        'down_steps': st['down_steps'],
        'fade_ratio': st['fade_ratio'],
        'step_ratio': st['step_ratio'],
    }

    if rm is not None:
        for c in ['macd_slope_1m','spread_1m','rsi_slope_1m']:
            rec[c] = float(rm[c]) if c in rm and np.isfinite(rm[c]) else np.nan
    else:
        rec.update({'macd_slope_1m': np.nan, 'spread_1m': np.nan, 'rsi_slope_1m': np.nan})

    if sf is not None:
        for c in ['trend_up','outer_expanding','mid_slope8','spread5','rsi5','entry_gate']:
            if c in sf.index:
                val = sf[c]
                rec[f'frame_{c}'] = bool(val) if c in ('trend_up','outer_expanding','entry_gate') else (float(val) if np.isfinite(val) else np.nan)

    rec.update(path_stats(raw_df, ts, entry_price))
    return rec


def metrics(label, t):
    p = pd.to_numeric(t.pnl_pct, errors='coerce').dropna() if len(t) else pd.Series(dtype=float)
    gp = float(p[p > 0].sum()) if len(p) else 0.0
    gl = float(-p[p < 0].sum()) if len(p) else 0.0
    pf = gp / gl if gl > 0 else np.inf
    print(f'{label}: trades={len(p)} wins={(p>0).sum()} losses={(p<=0).sum()} win={(p>0).mean()*100 if len(p) else 0:.2f}% gross={p.sum():+.4f}% avg={p.mean() if len(p) else 0:+.4f}% pf={pf:.3f}')


def summarize_group(x: pd.DataFrame, name: str):
    if not len(x):
        print(f'\n=== {name}: NONE ===')
        return
    print(f'\n=== {name} ===')
    print('count=', len(x), 'wins=', int(x.win.sum()), 'losses=', int((~x.win).sum()), 'win_pct=', round(float(x.win.mean()*100), 2), 'gross=', round(float(x.pnl_pct.sum()), 4))
    print('\n-- EXIT REASONS --')
    print(x.groupby(['win','reason']).size().rename('n').reset_index().sort_values(['win','n'], ascending=[True,False]).to_string(index=False))
    print('\n-- TIME BUCKET --')
    z=x.copy(); z['time_bucket']=pd.cut(z.entry_minute,[549,569,599,659,719,899],labels=['09:10-09:29','09:30-09:59','10:00-10:59','11:00-11:59','12:00-14:59'])
    print(z.groupby(['time_bucket','win'], observed=True).agg(n=('pnl_pct','size'),gross=('pnl_pct','sum'),avg=('pnl_pct','mean')).reset_index().to_string(index=False))
    print('\n-- WOULD_V15_WAIT --')
    print(x.groupby(['would_v15_wait','win']).agg(n=('pnl_pct','size'),gross=('pnl_pct','sum'),avg=('pnl_pct','mean')).reset_index().to_string(index=False))

    cols=['score','gap_pct','stop_dist_pct','band_r_pct','macd_slope_1m','spread_1m','rsi_slope_1m','down_steps','fade_ratio','step_ratio','mfe_1m_pct','mae_1m_pct','mfe_3m_pct','mae_3m_pct','mfe_5m_pct','mae_5m_pct','mfe_10m_pct','mae_10m_pct']
    cols=[c for c in cols if c in x.columns]
    print('\n-- WIN VS LOSS FEATURE MEDIANS --')
    print(x.groupby('win')[cols].median(numeric_only=True).T.to_string())
    print('\n-- WIN VS LOSS FEATURE MEANS --')
    print(x.groupby('win')[cols].mean(numeric_only=True).T.to_string())


def candidate_flags(x: pd.DataFrame):
    y=x.copy()
    y['flag_wait']=y.would_v15_wait.fillna(False)
    y['flag_1m_both_weak']=(pd.to_numeric(y.spread_1m,errors='coerce')<=0)&(pd.to_numeric(y.rsi_slope_1m,errors='coerce')<=0)
    y['flag_macd_nonpos']=pd.to_numeric(y.macd_slope_1m,errors='coerce')<=0
    y['flag_early_adverse_1pct']=pd.to_numeric(y.mae_3m_pct,errors='coerce')<=-1.0
    y['flag_no_early_followthrough']=pd.to_numeric(y.mfe_3m_pct,errors='coerce')<0.5
    y['flag_wait_or_weak']=y.flag_wait|y.flag_1m_both_weak
    y['flag_wait_or_no_follow']=y.flag_wait|y.flag_no_early_followthrough
    return y


def print_flag_table(x: pd.DataFrame, name: str):
    if not len(x): return
    y=candidate_flags(x)
    rows=[]
    for c in [z for z in y.columns if z.startswith('flag_')]:
        m=y[c].fillna(False)
        hit=y[m]; keep=y[~m]
        rows.append({
            'flag':c,
            'flagged_n':len(hit),
            'flagged_losses':int((~hit.win).sum()),
            'flagged_wins':int(hit.win.sum()),
            'loss_capture_pct':float((~hit.win).sum()/max((~y.win).sum(),1)*100),
            'winner_damage_pct':float(hit.win.sum()/max(y.win.sum(),1)*100),
            'flagged_gross_pct':float(hit.pnl_pct.sum()) if len(hit) else 0.0,
            'kept_n':len(keep),
            'kept_win_pct':float(keep.win.mean()*100) if len(keep) else np.nan,
            'kept_gross_pct':float(keep.pnl_pct.sum()) if len(keep) else 0.0,
        })
    print(f'\n=== {name}: CANDIDATE FAILURE FLAGS (DIAGNOSTIC ONLY) ===')
    print(pd.DataFrame(rows).sort_values(['loss_capture_pct','winner_damage_pct'],ascending=[False,True]).to_string(index=False))


def main():
    raw=load_data()
    base_cfg=DoubleBollingerEngine5Config()
    cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)

    packed=v8.base.pack_exit_events(raw,base_cfg)
    states=base.pack_state_events(base.build_cfg_frames(raw,base_cfg))
    frames=base.build_cfg_frames(raw,cfg)
    f10={s:v10._refine_entry_frame(f) for s,f in frames.items()}
    scored=reweight(f10,cfg,0.0)
    ev10=filt_open(v8.pack_entry_events(scored))

    # BASE ENGINE: ORIGINAL V17C = V16 WAIT/reaccel + V17B breakout/veto.
    ev16, waits=v16.build_wait_events(ev10,raw,cfg,False)
    ev_v17c, added_base, skipped_base=v17b.build_v17b(ev16,scored,waits)
    t_base=key_df(multi.simulate_multi(packed,ev_v17c,states,THRESHOLD))

    # EXPANDED ENTRY STREAM: remove opening WAIT, retain the rest of V17B/V17C logic.
    ev_exp, added_exp, skipped_exp=v17b.build_v17b(ev10,scored,pd.DataFrame())
    t_exp=key_df(multi.simulate_multi(packed,ev_exp,states,THRESHOLD))

    print('=== V17C BASE VS EXPANDED ENTRY FAILURE DIAGNOSTIC ===')
    print('BASE: V17C is frozen reference engine (V16 WAIT/reaccel + V17B breakout/veto).')
    print('EXPANDED: same engine/exit logic but opening WAIT removed so valid 09:10+ signals may enter immediately.')
    print('Goal: isolate EXTRA trades and identify common loss signatures; no production rule is changed here.')
    print('BASE_BREAKOUT_ADDED=',added_base)
    print('BASE_BREAKOUT_SKIPPED=',skipped_base)
    print('EXP_BREAKOUT_ADDED=',added_exp)
    print('EXP_BREAKOUT_SKIPPED=',skipped_exp)

    metrics('V17C_BASE',t_base)
    metrics('EXPANDED_ENTRY',t_exp)

    kb=set(t_base.key); ke=set(t_exp.key)
    extra_keys=ke-kb
    missing_keys=kb-ke
    common_keys=kb&ke
    extra=t_exp[t_exp.key.isin(extra_keys)].copy()
    common_exp=t_exp[t_exp.key.isin(common_keys)].copy()
    common_base=t_base[t_base.key.isin(common_keys)].copy()

    print('\n=== ENTRY SET DIFF ===')
    print('BASE_ENTRIES=',len(kb),'EXPANDED_ENTRIES=',len(ke),'COMMON=',len(common_keys),'EXTRA=',len(extra_keys),'BASE_ONLY=',len(missing_keys))

    evmap=event_lookup(ev_exp)
    rich={s:v16.build_rich_micro(raw[s],cfg) for s in raw}
    micro15={s:v15.build_1m_micro(raw[s],cfg) for s in raw}
    gaps={s:v15.daily_gap_map(raw[s]) for s in raw}

    detail=[]
    for _,r in extra.iterrows():
        sym=str(r.symbol).zfill(6); k=(sym,str(pd.Timestamp(r.entry_time)))
        detail.append(enrich_trade(r,evmap.get(k),scored[sym],rich[sym],micro15[sym],gaps[sym],raw[sym]))
    d=pd.DataFrame(detail)

    summarize_group(d,'EXTRA ENTRY TRADES')
    if len(d):
        summarize_group(d[~d.win],'EXTRA LOSSES ONLY')
        print_flag_table(d,'EXTRA ENTRY TRADES')
        print('\n=== WORST EXTRA LOSSES ===')
        show=['symbol','entry_time','pnl_pct','reason','score','gap_pct','would_v15_wait','down_steps','fade_ratio','step_ratio','macd_slope_1m','spread_1m','rsi_slope_1m','mfe_3m_pct','mae_3m_pct','mfe_10m_pct','mae_10m_pct']
        show=[c for c in show if c in d.columns]
        print(d.sort_values('pnl_pct').head(30)[show].to_string(index=False))

    # Common-entry path changes: useful to detect state effects even when entry key is shared.
    mb=common_base.set_index('key')[['pnl_pct','reason','exit_time']].rename(columns={'pnl_pct':'base_pnl','reason':'base_reason','exit_time':'base_exit'})
    me=common_exp.set_index('key')[['pnl_pct','reason','exit_time']].rename(columns={'pnl_pct':'exp_pnl','reason':'exp_reason','exit_time':'exp_exit'})
    ch=mb.join(me,how='inner')
    ch['delta_pct']=ch.exp_pnl-ch.base_pnl
    changed=ch[(ch.delta_pct.abs()>1e-9)|(ch.base_reason!=ch.exp_reason)|(pd.to_datetime(ch.base_exit)!=pd.to_datetime(ch.exp_exit))].reset_index()
    print('\n=== COMMON ENTRY PATH CHANGES CAUSED BY EXTRA OCCUPANCY/STATE ===')
    print('CHANGED_COMMON_COUNT=',len(changed))
    if len(changed): print(changed.sort_values('delta_pct').to_string(index=False))

    if len(d):
        detail_out=f'{OUT_DIR}/v17c_expanded_extra_entry_failure_detail.csv'
        d.to_csv(detail_out,index=False)
        print('[DETAIL CSV]',detail_out)
    changed_out=f'{OUT_DIR}/v17c_expanded_common_path_changes.csv'
    changed.to_csv(changed_out,index=False)
    print('[COMMON CHANGES CSV]',changed_out)


if __name__=='__main__':
    main()
