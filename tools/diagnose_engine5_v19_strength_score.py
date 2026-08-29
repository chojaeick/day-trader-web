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
import tools.validate_engine5_v17c_breakout_first10_hwm1pct as v17c
import tools.validate_engine5_v18_outer_upper_breakout_filter as v18
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUTDIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
OUTDIR.mkdir(parents=True, exist_ok=True)
OPEN_MINUTE = 9 * 60 + 10
BASELINE_THRESHOLD = 50
TEST_CUTOFFS = [60, 65, 70, 75, 80]


def clip_score(x, lo, hi, points):
    if not np.isfinite(x) or hi <= lo:
        return 0.0
    return float(points * np.clip((x - lo) / (hi - lo), 0.0, 1.0))


def filt_open(ev):
    return {ts: rows for ts, rows in ev.items() if pd.Timestamp(ts).hour * 60 + pd.Timestamp(ts).minute >= OPEN_MINUTE}


def build_score_frames(scored):
    out = {}
    for sym, f0 in scored.items():
        f = f0.copy().sort_values('time').reset_index(drop=True)
        numeric = ['close','macd','macd_signal','macd_slope','macd_signal_slope','macd_slope_spread','rsi','rsi_slope','mid_slope8','outer_width_ratio','volume_ratio','volume']
        for c in numeric:
            if c in f.columns:
                f[c] = pd.to_numeric(f[c], errors='coerce')
        f['prev_macd'] = f['macd'].shift(1)
        f['prev_signal'] = f['macd_signal'].shift(1)
        f['prev_macd_slope'] = f['macd_slope'].shift(1)
        f['prev_rsi'] = f['rsi'].shift(1)
        f['prev_rsi_slope'] = f['rsi_slope'].shift(1)
        f['prev_volume'] = f['volume'].shift(1)
        f['volume_prev_ratio'] = f['volume'] / f['prev_volume'].replace(0.0, np.nan)
        out[str(sym).zfill(6)] = f
    return out


def row_at_or_before(frames, sym, ts):
    f = frames.get(str(sym).zfill(6))
    if f is None or f.empty:
        return None
    t = pd.Timestamp(ts)
    q = f[pd.to_datetime(f['time']) <= t]
    return None if q.empty else q.iloc[-1]


def fv(r, name):
    if r is None:
        return np.nan
    try:
        x = float(r.get(name, np.nan))
        return x if np.isfinite(x) else np.nan
    except Exception:
        return np.nan


def score_row(r):
    close = fv(r, 'close'); macd = fv(r, 'macd'); signal = fv(r, 'macd_signal')
    prev_macd = fv(r, 'prev_macd'); prev_signal = fv(r, 'prev_signal')
    macd_slope = fv(r, 'macd_slope'); prev_macd_slope = fv(r, 'prev_macd_slope'); spread = fv(r, 'macd_slope_spread')

    gap_bps = ((macd - signal) / close * 10000.0) if np.isfinite(close) and close > 0 and np.isfinite(macd) and np.isfinite(signal) else np.nan
    s_macd_gap = clip_score(gap_bps, 0.0, 20.0, 15.0)

    ratio_mode = 'RATIO'; ratio_edge = np.nan
    eps = close * 0.00005 if np.isfinite(close) and close > 0 else 0.0
    stable_ratio = np.isfinite(macd) and np.isfinite(signal) and np.isfinite(prev_macd) and np.isfinite(prev_signal) and macd > eps and signal > eps and prev_macd > eps and prev_signal > eps
    if stable_ratio:
        macd_ratio = macd / prev_macd; signal_ratio = signal / prev_signal; ratio_edge = macd_ratio - signal_ratio
        s_macd_sep = clip_score(ratio_edge, 0.0, 0.25, 15.0)
    else:
        ratio_mode = 'SPREAD_BPS_FALLBACK'
        slope_adv_bps = (spread / close * 10000.0) if np.isfinite(spread) and np.isfinite(close) and close > 0 else np.nan
        s_macd_sep = clip_score(slope_adv_bps, 0.0, 10.0, 15.0)
        macd_ratio = np.nan; signal_ratio = np.nan

    if not np.isfinite(macd_slope) or macd_slope <= 0:
        macd_reaccel_ratio = np.nan; s_macd_reaccel = 0.0
    elif np.isfinite(prev_macd_slope) and prev_macd_slope > 0:
        macd_reaccel_ratio = macd_slope / prev_macd_slope
        s_macd_reaccel = clip_score(macd_reaccel_ratio, 1.0, 2.0, 10.0)
    else:
        macd_reaccel_ratio = np.nan; s_macd_reaccel = 5.0
    macd_score = s_macd_gap + s_macd_sep + s_macd_reaccel

    rsi = fv(r, 'rsi'); prev_rsi = fv(r, 'prev_rsi'); rsi_slope = fv(r, 'rsi_slope'); prev_rsi_slope = fv(r, 'prev_rsi_slope')
    if not np.isfinite(rsi): s_rsi_level_base = 0.0
    elif rsi < 45: s_rsi_level_base = 0.0
    elif rsi < 50: s_rsi_level_base = clip_score(rsi, 45.0, 50.0, 2.0)
    elif rsi < 60: s_rsi_level_base = 4.0 + clip_score(rsi, 50.0, 60.0, 4.0)
    elif rsi < 70: s_rsi_level_base = 8.0 + clip_score(rsi, 60.0, 70.0, 2.0)
    else: s_rsi_level_base = 10.0
    cross50 = bool(np.isfinite(prev_rsi) and np.isfinite(rsi) and prev_rsi < 50 <= rsi)
    cross70 = bool(np.isfinite(prev_rsi) and np.isfinite(rsi) and prev_rsi < 70 <= rsi)
    strong_cross_bonus = min(2.0, 2.0 * rsi_slope / 8.0) if (cross50 or cross70) and np.isfinite(rsi_slope) and rsi_slope > 0 else 0.0
    s_rsi_level = min(12.0, s_rsi_level_base + strong_cross_bonus)
    s_rsi_slope = clip_score(rsi_slope, 0.0, 8.0, 10.0)
    if not np.isfinite(rsi_slope) or rsi_slope <= 0:
        rsi_accel_ratio = np.nan; s_rsi_accel = 0.0
    elif np.isfinite(prev_rsi_slope) and prev_rsi_slope > 0:
        rsi_accel_ratio = rsi_slope / prev_rsi_slope; s_rsi_accel = clip_score(rsi_accel_ratio, 1.0, 2.0, 8.0)
    else:
        rsi_accel_ratio = np.nan; s_rsi_accel = 4.0
    rsi_score = s_rsi_level + s_rsi_slope + s_rsi_accel

    mid_slope8 = fv(r, 'mid_slope8')
    mid_slope_bps = (mid_slope8 / close * 10000.0) if np.isfinite(mid_slope8) and np.isfinite(close) and close > 0 else np.nan
    s_dbb_mid = clip_score(mid_slope_bps, 0.0, 5.0, 15.0)
    outer_expand = fv(r, 'outer_width_ratio'); s_dbb_expand = clip_score(outer_expand, 0.0, 0.03, 5.0)
    dbb_score = s_dbb_mid + s_dbb_expand
    volume_ratio = fv(r, 'volume_ratio'); s_volume = clip_score(volume_ratio, 1.0, 3.0, 10.0)
    total = float(np.clip(macd_score + rsi_score + dbb_score + s_volume, 0.0, 100.0))
    return {'v19_score': total,'macd_score':macd_score,'macd_gap_score':s_macd_gap,'macd_sep_score':s_macd_sep,'macd_reaccel_score':s_macd_reaccel,'gap_bps':gap_bps,'macd_ratio':macd_ratio,'signal_ratio':signal_ratio,'ratio_edge':ratio_edge,'ratio_mode':ratio_mode,'macd_slope':macd_slope,'prev_macd_slope':prev_macd_slope,'macd_reaccel_ratio':macd_reaccel_ratio,'rsi_score':rsi_score,'rsi_level_score':s_rsi_level,'rsi_slope_score':s_rsi_slope,'rsi_accel_score':s_rsi_accel,'rsi':rsi,'prev_rsi':prev_rsi,'rsi_slope':rsi_slope,'prev_rsi_slope':prev_rsi_slope,'rsi_accel_ratio':rsi_accel_ratio,'cross50':cross50,'cross70':cross70,'dbb_score':dbb_score,'dbb_mid_score':s_dbb_mid,'dbb_expand_score':s_dbb_expand,'mid_slope_bps':mid_slope_bps,'outer_width_ratio':outer_expand,'volume_score':s_volume,'volume_ratio':volume_ratio,'volume_prev_ratio':fv(r,'volume_prev_ratio')}


def event_diag(event, ts, frames):
    sym = str(event[0]).zfill(6); r = row_at_or_before(frames, sym, ts)
    d = {'symbol':sym,'time':pd.Timestamp(ts),'price':float(event[1]),'old_score':float(event[2])}; d.update(score_row(r)); d['breakout_entry']=bool(event[-1]) if len(event)>=13 else False
    return d


def replace_event_score(event, score):
    x=list(event); x[2]=float(score); return tuple(x)


def rescore_events(events, frames):
    out={}; diags=[]
    for ts,rows in events.items():
        rr=[]
        for e in rows:
            d=event_diag(e,ts,frames); applied=100.0 if d['breakout_entry'] else d['v19_score']; d['applied_score']=applied; rr.append(replace_event_score(e,applied)); diags.append(d)
        if rr: out[ts]=rr
    return out,pd.DataFrame(diags)


def metrics(name,trades):
    if trades is None or len(trades)==0: return {'name':name,'trades':0,'wins':0,'losses':0,'win_rate':0.0,'gross_pct':0.0,'avg_pct':0.0,'pf':np.nan,'max_loss_pct':np.nan}
    p=pd.to_numeric(trades['pnl_pct'],errors='coerce').dropna(); wins=int((p>0).sum()); losses=int((p<=0).sum()); gp=float(p[p>0].sum()); gl=float(-p[p<0].sum())
    return {'name':name,'trades':int(len(p)),'wins':wins,'losses':losses,'win_rate':wins/len(p)*100 if len(p) else 0.0,'gross_pct':float(p.sum()),'avg_pct':float(p.mean()),'pf':gp/gl if gl>0 else np.inf,'max_loss_pct':float(p.min()) if len(p) else np.nan}


def print_metric(m): print(f"{m['name']}: trades={m['trades']} wins={m['wins']} losses={m['losses']} win={m['win_rate']:.2f}% gross={m['gross_pct']:+.4f}% avg={m['avg_pct']:+.4f}% pf={m['pf']:.3f} maxloss={m['max_loss_pct']:+.4f}%")

def trade_entry_columns(trades):
    sym=next((c for c in ['symbol','code','ticker'] if c in trades.columns),None); ts=next((c for c in ['entry_time','time','buy_time'] if c in trades.columns),None); return sym,ts

def baseline_trade_score_join(trades,diag):
    if trades is None or trades.empty or diag.empty: return pd.DataFrame()
    symc,tsc=trade_entry_columns(trades)
    if symc is None or tsc is None: return pd.DataFrame()
    t=trades.copy(); t['_sym']=t[symc].astype(str).str.zfill(6); t['_ts']=pd.to_datetime(t[tsc])
    # Avoid suffix collisions if simulator trade rows already carry breakout_entry.
    if 'breakout_entry' in t.columns: t=t.rename(columns={'breakout_entry':'trade_breakout_entry'})
    d=diag.copy(); d['_sym']=d['symbol'].astype(str).str.zfill(6); d['_ts']=pd.to_datetime(d['time'])
    cols=['_sym','_ts','v19_score','applied_score','breakout_entry','macd_score','rsi_score','dbb_score','volume_score']
    return t.merge(d[cols],on=['_sym','_ts'],how='left')

def print_component_table(df):
    cols=['symbol','time','price','old_score','v19_score','applied_score','breakout_entry','macd_score','macd_gap_score','macd_sep_score','macd_reaccel_score','gap_bps','ratio_edge','ratio_mode','macd_reaccel_ratio','rsi_score','rsi_level_score','rsi_slope_score','rsi_accel_score','rsi','prev_rsi','rsi_slope','prev_rsi_slope','rsi_accel_ratio','cross50','cross70','dbb_score','dbb_mid_score','dbb_expand_score','mid_slope_bps','outer_width_ratio','volume_score','volume_ratio','volume_prev_ratio']
    print(df[[c for c in cols if c in df.columns]].to_string(index=False))


def main():
    raw=load_data(); base_cfg=DoubleBollingerEngine5Config(); cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    packed_exits=v8.base.pack_exit_events(raw,base_cfg); state_events=base.pack_state_events(base.build_cfg_frames(raw,base_cfg)); raw_frames=base.build_cfg_frames(raw,cfg)
    f10={s:v10._refine_entry_frame(f) for s,f in raw_frames.items()}; scored=reweight(f10,cfg,0.0); frames=build_score_frames(scored)
    ev10=filt_open(v8.pack_entry_events(scored)); ev16,waits=v16.build_wait_events(ev10,raw,cfg,False); ev17b,added,skipped=v17b.build_v17b(ev16,scored,waits); rescored,diag=rescore_events(ev17b,frames)

    print('=== ENGINE5 V19 STRENGTH-SCORE DIAGNOSTIC ==='); print('No live-engine change. Existing direction gates / V16 WAIT / V17B breakout semantics are unchanged.'); print('Score only: MACD 40 + RSI 30 + DBB 20 + Volume 10. Breakout branch remains exempt (applied_score=100).')
    cases=[('FAIL_950260_0821_1000','950260','2026-08-21 10:00:00+09:00'),('FAIL_080220_0811_0955','080220','2026-08-11 09:55:00+09:00'),('FAIL_257720_0818_1430','257720','2026-08-18 14:30:00+09:00'),('GOOD_257720_0812_0910','257720','2026-08-12 09:10:00+09:00'),('BREAKOUT_257720_0818_1420','257720','2026-08-18 14:20:00+09:00')]
    print('\n=== MANUAL CASE SCORE BREAKDOWN ===')
    for label,sym,ts in cases:
        q=diag[(diag['symbol']==sym)&(pd.to_datetime(diag['time'])==pd.Timestamp(ts))].copy(); print(f'\n[{label}]'); print('EVENT_NOT_FOUND') if q.empty else print_component_table(q)
    baseline=v17c.simulate_unconditional_hwm(packed_exits,ev17b,state_events,BASELINE_THRESHOLD)
    print('\n=== BASELINE V17C ==='); print_metric(metrics('V17C_CURRENT',baseline))
    print('\n=== V19 FULL-PATH CUTOFF DIAGNOSTIC ==='); boards=[]
    for cutoff in TEST_CUTOFFS:
        t=v17c.simulate_unconditional_hwm(packed_exits,rescored,state_events,cutoff); m=metrics(f'V19_SCORE_GE_{cutoff}',t); boards.append(m); print_metric(m)
    joined=baseline_trade_score_join(baseline,diag)
    print('\n=== BASELINE 84-TRADE DIRECT CLASSIFICATION @ 70 ===')
    if joined.empty or 'pnl_pct' not in joined.columns: print('JOIN_UNAVAILABLE')
    else:
        j=joined[pd.to_numeric(joined['applied_score'],errors='coerce').notna()].copy(); j['pnl_pct']=pd.to_numeric(j['pnl_pct'],errors='coerce'); j['keep70']=pd.to_numeric(j['applied_score'],errors='coerce')>=70.0
        for flag,name in [(True,'KEEP_70'),(False,'REMOVE_LT70')]:
            q=j[j['keep70']==flag]; p=q['pnl_pct'].dropna();
            if len(p): print(f"{name}: n={len(p)} wins={(p>0).sum()} losses={(p<=0).sum()} win={(p>0).mean()*100:.2f}% gross={p.sum():+.4f}% avg={p.mean():+.4f}%")
        losers=j[j['pnl_pct']<=0]; winners=j[j['pnl_pct']>0]
        print(f"LOSS_REMOVAL_RATE={(~losers['keep70']).mean()*100:.2f}% ({(~losers['keep70']).sum()}/{len(losers)})" if len(losers) else 'LOSS_REMOVAL_RATE=n/a'); print(f"WIN_PRESERVATION_RATE={(winners['keep70']).mean()*100:.2f}% ({(winners['keep70']).sum()}/{len(winners)})" if len(winners) else 'WIN_PRESERVATION_RATE=n/a')
        cols=['_sym','_ts','pnl_pct','applied_score','v19_score','breakout_entry','macd_score','rsi_score','dbb_score','volume_score']
        print('\n--- WORST 15 BASELINE TRADES + V19 SCORE ---'); print(j.sort_values('pnl_pct').head(15)[[c for c in cols if c in j.columns]].to_string(index=False)); print('\n--- BEST 15 BASELINE TRADES + V19 SCORE ---'); print(j.sort_values('pnl_pct',ascending=False).head(15)[[c for c in cols if c in j.columns]].to_string(index=False)); joined.to_csv(OUTDIR/'v19_strength_score_baseline_trade_classification.csv',index=False)
    print('\n=== SCORE DISTRIBUTION BY BASELINE OUTCOME ===')
    if not joined.empty and 'pnl_pct' in joined.columns:
        jj=joined.copy(); jj['pnl_pct']=pd.to_numeric(jj['pnl_pct'],errors='coerce'); jj['applied_score']=pd.to_numeric(jj['applied_score'],errors='coerce')
        for name,q in [('WIN',jj[jj['pnl_pct']>0]),('LOSS',jj[jj['pnl_pct']<=0])]:
            s=q['applied_score'].dropna();
            if len(s): print(f"{name}: n={len(s)} mean={s.mean():.2f} median={s.median():.2f} p25={s.quantile(.25):.2f} p75={s.quantile(.75):.2f}")
    diag.to_csv(OUTDIR/'v19_strength_score_all_events.csv',index=False); pd.DataFrame(boards).to_csv(OUTDIR/'v19_strength_score_cutoff_summary.csv',index=False)
    print('\n[CSV]',OUTDIR/'v19_strength_score_all_events.csv'); print('[CSV]',OUTDIR/'v19_strength_score_cutoff_summary.csv'); print('[CSV]',OUTDIR/'v19_strength_score_baseline_trade_classification.csv')

if __name__=='__main__': main()
