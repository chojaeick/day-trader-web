from __future__ import annotations

"""Validate the US-adapted Engine5 E-series on native USD / original ET data.

Naming:
  V17CE, V18E, V19E_D0..D3, V20E, V21E

E-series rules:
- input OHLC remains native USD (no FX conversion)
- timestamps remain exchange-local ET
- KR relative-session semantics are expressed on the US clock
- absolute KRW MACD thresholds are replaced by price-normalized bps thresholds
- KR engine files remain untouched
"""

import pickle
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_opening_5m_hwm_sweep as sweep
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v17c_multi_symbol as multi
import tools.validate_engine5_v19_prebuy_5m_1m_confirm as v19
import tools.validate_engine5_integrated_full_history as integ
import tools.validate_engine5_integrated_slow_turn_rearm_deep as revised
import tools.validate_engine5_v21_v_rebound_state_machine as vsm
import tools.validate_engine5_v21_v_rebound_reaccel as vra
import tools.validate_engine5_v21_v_rebound_momentum_preservation as vmp

ROOT = Path('/home/ubuntu/day-trader-api/engine5_us_e_cache')
CORE = ROOT / 'us_e_core.pkl'
PROV_DIR = ROOT / 'provisional'
SUMMARY = ROOT / 'us_e_all_versions_summary.csv'
TRADES = ROOT / 'us_e_all_versions_trades.csv'
SIGNALS = ROOT / 'us_e_v21e_signals.csv'
THRESHOLD = 50
CUTS = (-0.15,-0.20,-0.30,-0.50)
DELAYS = (0,1,2,3)

US_BUY_START_MINUTE = 9*60 + 40
US_OPENING_END_MINUTE = 10*60 + 30
US_NO_ENTRY_MINUTE = 15*60 + 30
US_FORCE_FLAT_MINUTE = 15*60 + 50

# KR-only parity calibration from V20 RAW52/REL1.45 validation.
# This is a scale-invariant price-relative strength threshold, not a USD threshold.
V20E_RAW_BPS = 11.166071
V20E_REL_MIN = 1.45
V20E_REQUIRE_ABOVE_SIGNAL = True
# Preserve the original V21 RAW30 : V20 RAW52 proportion without price-unit dependence.
V21E_RAW30_BPS = V20E_RAW_BPS * (30.0 / 52.0)


def n(x): return str(x).zfill(6)
def minute_of(ts):
    t = pd.Timestamp(ts)
    return t.hour*60 + t.minute

def count_events(ev): return sum(len(v) for v in ev.values())

def apply_us_session_clock():
    base.NO_ENTRY_MINUTE = US_NO_ENTRY_MINUTE
    base.FORCE_FLAT_MINUTE = US_FORCE_FLAT_MINUTE
    sweep.OPEN_BUY_MINUTE = US_BUY_START_MINUTE
    sweep.OPENING_ENTRY_END = US_OPENING_END_MINUTE
    multi.OPEN_MINUTE = US_BUY_START_MINUTE


def upgrade(ev):
    out = {}
    for ts, cs in ev.items():
        rows = []
        for c in cs:
            if len(c) == 13: rows.append(c)
            elif len(c) == 12: rows.append(tuple(c)+(False,))
            else: raise ValueError(f'unsupported event width={len(c)} at {ts}')
        out[pd.Timestamp(ts)] = rows
    return out


def hist_stat(label,tr,signals):
    g = pd.to_numeric(tr.pnl_pct,errors='coerce').dropna() if len(tr) else pd.Series(dtype=float)
    net = g - 0.25
    gp = float(net[net>0].sum()) if len(net) else 0.0
    gl = float(-net[net<0].sum()) if len(net) else 0.0
    return dict(variant=label,signals=signals,trades=len(net),wins=int((net>0).sum()),losses=int((net<=0).sum()),
                win_pct=float((net>0).mean()*100) if len(net) else 0.0,net_sum_pct=float(net.sum()),
                avg_net_pct=float(net.mean()) if len(net) else 0.0,pf=gp/gl if gl>0 else np.inf,
                max_loss_pct=float(net.min()) if len(net) else np.nan)

def run_hist(label,ev,packed,states):
    e = upgrade(ev)
    tr = multi.simulate_multi(packed,e,states,THRESHOLD)
    return tr,hist_stat(label,tr,count_events(e))


def load_pf(raw):
    out = {}
    for i,s in enumerate(raw,1):
        p = PROV_DIR / f'{n(s)}_provisional.pkl'
        if not p.exists(): raise FileNotFoundError(f'{p} missing; build E cache first')
        with p.open('rb') as fh: out[s] = pickle.load(fh)
        print(f'[PF {i}/{len(raw)}] {s} rows={len(out[s])}',flush=True)
    return out


def filter_v20e(ev18,strength):
    out, diag = {}, []
    for ts in sorted(ev18):
        for c in ev18[ts]:
            sym = n(c[0]); f = strength[sym]
            q = f[f.time <= pd.Timestamp(ts)]
            if q.empty: continue
            r = q.iloc[-1]
            close = float(r.close) if pd.notna(r.close) else np.nan
            raw = float(r.macd_strength_raw) if pd.notna(r.macd_strength_raw) else np.nan
            rel = float(r.macd_strength_rel) if pd.notna(r.macd_strength_rel) else np.nan
            bps = raw/close*10000.0 if np.isfinite(raw) and np.isfinite(close) and close != 0 else np.nan
            above = bool(r.macd_above_signal) if 'macd_above_signal' in r.index else False
            ok = bool(np.isfinite(bps) and bps >= V20E_RAW_BPS and np.isfinite(rel) and rel >= V20E_REL_MIN and (above or not V20E_REQUIRE_ABOVE_SIGNAL))
            diag.append(dict(symbol=sym,time=pd.Timestamp(ts),raw_bps=bps,rel=rel,above_signal=above,keep=ok))
            if ok: out.setdefault(pd.Timestamp(ts),[]).append(c)
    return out,pd.DataFrame(diag)


def build_v20e_tags(ev18,strength,scored):
    ev,_ = filter_v20e(ev18,strength)
    tags=[]
    for ts,cs in ev.items():
        for c in cs:
            ext=integ.entry_extension_5m(scored,c[0],ts)
            if pd.notna(ext) and ext>=integ.V20_EXTREME_CAP: continue
            tags.append(dict(source='V20E',symbol=n(c[0]),time=pd.Timestamp(ts),event=c,meta={}))
    return tags


def state_candidates_e(sym,z,scored,leg_min):
    """V-rebound state machine with RAW30 replaced by normalized bps and US clock."""
    out=[]
    pxs=pd.to_numeric(z.px,errors='coerce')
    gd=pd.to_numeric(z.gap_delta,errors='coerce')
    bps=gd/pxs.replace(0,np.nan)*10000.0
    ready=z.ready_common & (bps >= V21E_RAW30_BPS)
    day=pd.to_datetime(z.time).dt.date
    state=None
    for i in range(len(z)):
        ts=pd.Timestamp(z.time.iloc[i]); px=float(z.px.iloc[i]) if pd.notna(z.px.iloc[i]) else np.nan; lo=float(z.lo.iloc[i]) if pd.notna(z.lo.iloc[i]) else np.nan
        if not np.isfinite(px) or not np.isfinite(lo): continue
        if i==0 or day.iloc[i]!=day.iloc[i-1]: state=None
        if state is not None and (ts-state['armed_time']).total_seconds()/60.0>vsm.ARM_TTL_MIN: state=None
        if state is None and bool(ready.iloc[i]):
            j=max(0,i-8); base_low=float(pd.to_numeric(z.lo.iloc[j:i+1],errors='coerce').min())
            if np.isfinite(base_low) and base_low>0:
                state={'armed_time':ts,'armed_i':i,'base_low':base_low,'rebound_high':px,'rebound_high_time':ts,'stage':'RISING','pullback_low':np.nan,'pullback_start':pd.NaT}
        if state is None: continue
        if lo<=state['base_low'] and i>state['armed_i']:
            state=None
            if bool(ready.iloc[i]):
                j=max(0,i-8); base_low=float(pd.to_numeric(z.lo.iloc[j:i+1],errors='coerce').min())
                if np.isfinite(base_low) and base_low>0:
                    state={'armed_time':ts,'armed_i':i,'base_low':base_low,'rebound_high':px,'rebound_high_time':ts,'stage':'RISING','pullback_low':np.nan,'pullback_start':pd.NaT}
            if state is None: continue
        leg=(state['rebound_high']/state['base_low']-1.0)*100.0
        prev=float(z.px.iloc[i-1]) if i>0 and pd.notna(z.px.iloc[i-1]) else np.nan
        if state['stage']=='RISING':
            if px>state['rebound_high']:
                state['rebound_high']=px; state['rebound_high_time']=ts
                leg=(state['rebound_high']/state['base_low']-1.0)*100.0
            if leg>=leg_min and np.isfinite(prev) and px<prev:
                state['stage']='PULLBACK'; state['pullback_start']=ts; state['pullback_low']=lo
        else:
            state['pullback_low']=min(float(state['pullback_low']),lo)
            higher_low=np.isfinite(state['pullback_low']) and state['pullback_low']>state['base_low']
            reclaim=px>state['rebound_high']
            mom=(float(z.gap_delta.iloc[i])>0 and float(z.rsi_slope.iloc[i])>0)
            if higher_low and reclaim and mom:
                stop=state['pullback_low']; dist=(px/stop-1.0)*100.0
                q5=scored[scored.time<=ts.floor('5min')]
                if minute_of(ts)>=US_BUY_START_MINUTE and minute_of(ts)<US_NO_ENTRY_MINUTE and not q5.empty:
                    ev=vsm.old.make_event(sym,q5.iloc[-1],px)
                    if ev is not None:
                        out.append(dict(symbol=n(sym),time=ts,price=px,structural_stop=stop,stop_dist_pct=dist,
                                        volume_accel=float(z.volume_accel_3v10.iloc[i]) if pd.notna(z.volume_accel_3v10.iloc[i]) else np.nan,
                                        gap_delta=float(z.gap_delta.iloc[i]),rsi_slope=float(z.rsi_slope.iloc[i]),event=ev))
                state=None
    return pd.DataFrame(out)


def build_vrebound_e(raw,scored,micros,pf):
    allc=[]; vf={}
    for s,bars in raw.items():
        z=vsm.add_features(pf[s],micros[s],bars).sort_values('time').reset_index(drop=True); vf[s]=z
        c=state_candidates_e(s,z,scored[s],integ.V_LEG_MIN)
        if len(c): allc.append(c)
    if not allc: return []
    q=pd.concat(allc,ignore_index=True)
    q=vra.add_pullback_reaccel(q,vf); q=vmp.add_preservation(q,vf)
    q=q[(q.stop_dist_pct<=integ.V_STOP_CAP)&q.reaccel_pass&
        (pd.to_numeric(q.volume_accel,errors='coerce')>=integ.V_VOL_MIN)&q.rsi_positive_all&
        (pd.to_numeric(q.gap_keep_ratio,errors='coerce')>=integ.V_GAP_KEEP_MIN)].copy()
    q['day']=pd.to_datetime(q.time).dt.date
    q=q.sort_values('time').drop_duplicates(['symbol','day'],keep='first')
    return [dict(source='V_REBOUND_E',symbol=n(r.symbol),time=pd.Timestamp(r.time),event=r.event,
                 meta={'structural_stop':float(r.structural_stop)}) for _,r in q.iterrows()]


def normalize_slow_boundary_e(allslow):
    """Keep existing Slow-turn regimes, but replace BOUNDARY MACD30 with bps and enforce US session."""
    x=allslow.copy()
    if x.empty: return x
    ready_min=pd.to_datetime(x.ready_time).dt.hour*60+pd.to_datetime(x.ready_time).dt.minute
    entry_min=pd.to_datetime(x.entry_time).dt.hour*60+pd.to_datetime(x.entry_time).dt.minute
    x=x[(ready_min>=US_BUY_START_MINUTE)&(entry_min>=US_BUY_START_MINUTE)&(entry_min<US_NO_ENTRY_MINUTE)].copy()
    boundary=x.regime.astype(str).eq('BOUNDARY_8_12')
    gd=pd.to_numeric(x.gap_delta_5m,errors='coerce')
    px=pd.to_numeric(x.entry_price,errors='coerce')
    gd_bps=gd/px.replace(0,np.nan)*10000.0
    rs=pd.to_numeric(x.rsi_slope_5m,errors='coerce')
    pp=pd.to_numeric(x.price_progress_1m_pct,errors='coerce')
    boundary_ok=(gd_bps>=V21E_RAW30_BPS)&(rs>=10.0)&(pp>=1.50)
    x.loc[boundary,'selected_current']=boundary_ok[boundary]
    x['gap_delta_bps_e']=gd_bps
    return x


def main():
    if not CORE.exists(): raise FileNotFoundError(f'{CORE} missing; run tools.build_engine5_us_e_cache --rebuild')
    with CORE.open('rb') as fh:d=pickle.load(fh)
    if d.get('cache_schema')!='US_E_USD_ET_V1' or d.get('price_unit')!='USD' or d.get('fx_applied') is not False:
        raise RuntimeError('INVALID E CACHE: expected native USD / original ET / no FX')
    if d.get('time_shift_minutes')!=0: raise RuntimeError('INVALID E CACHE: timestamp shift is forbidden')

    apply_us_session_clock()
    raw=d['raw']; cfg=d['cfg']; packed=d['packed']; states=d['states']; scored=d['scored']; strength=d['strength']; completed=d['completed']; micros=d['micros']
    print('=== ENGINE5 US E-SERIES — NATIVE USD / ORIGINAL ET ===',flush=True)
    print('versions=V17CE,V18E,V19E,V20E,V21E | FX=NONE | session=09:30-16:00 ET',flush=True)
    print(f'V20E strength={V20E_RAW_BPS:.6f}bps + REL>={V20E_REL_MIN} + MACD>signal',flush=True)
    print(f'V21E RAW30-equivalent={V21E_RAW30_BPS:.6f}bps',flush=True)

    raw_entries=v8.pack_entry_events(scored)
    ev10=sweep.filt_open(raw_entries)
    ev16,waits=v16.build_wait_events(ev10,raw,cfg,False)
    ev17,_,_=v17b.build_v17b(ev16,scored,waits)
    ev18,_=h.build_veto_stream(ev17,micros)

    rows=[]; parts=[]
    for label,ev in [('V17CE',ev17),('V18E',ev18)]:
        tr,s=run_hist(label,ev,packed,states); rows.append(s); q=tr.copy(); q['variant']=label; parts.append(q)
        print(f"{label}: signals={s['signals']} trades={s['trades']}",flush=True)

    for delay in DELAYS:
        fast,_=v19.build_v19_events(scored,micros,raw,delay)
        fast={ts:cs for ts,cs in fast.items() if US_BUY_START_MINUTE<=minute_of(ts)<US_NO_ENTRY_MINUTE}
        ev,_=v19.merge_additive(ev18,fast); label=f'V19E_D{delay}'
        tr,s=run_hist(label,ev,packed,states); rows.append(s); q=tr.copy(); q['variant']=label; parts.append(q)
        print(f"{label}: signals={s['signals']} trades={s['trades']}",flush=True)

    v20=build_v20e_tags(ev18,strength,scored)
    tr=integ.simulate(packed,states,v20); s=integ.stat('V20E',tr)
    row=dict(variant='V20E',signals=len(v20),**{k:s[k] for k in ['trades','wins','losses','win_pct','net_sum_pct','avg_net_pct','pf','max_loss_pct']})
    rows.append(row); q=tr.copy(); q['variant']='V20E'; parts.append(q)
    print(f"V20E: signals={len(v20)} trades={row['trades']}",flush=True)

    pf=load_pf(raw)
    old=revised.st.load_or_build_cache
    revised.st.load_or_build_cache=lambda sym,*_:(pf[n(sym)],micros[n(sym)])
    try: allslow=revised.build_all_slow(raw,cfg,completed,micros)
    finally: revised.st.load_or_build_cache=old
    allslow=normalize_slow_boundary_e(allslow)
    vr=build_vrebound_e(raw,scored,micros,pf)
    print(f'V21E components: V20E={len(v20)} V_REBOUND_E={len(vr)} ALL_SLOW_E={len(allslow)}',flush=True)

    sigrows=[]
    for cut in CUTS:
        slow=revised.slow_tags(revised.select_revised(allslow,cut))
        for x in slow: x['source']='SLOW_TURN_E'
        tags=sorted(v20+vr+slow,key=lambda x:(pd.Timestamp(x['time']),x['symbol'],x['source']))
        tr=integ.simulate(packed,states,tags); st=integ.stat(f'V21E_{cut}',tr); label=f'V21E_{cut}'
        rr=dict(variant=label,signals=len(tags),**{k:st[k] for k in ['trades','wins','losses','win_pct','net_sum_pct','avg_net_pct','pf','max_loss_pct']})
        rows.append(rr); q=tr.copy(); q['variant']=label; parts.append(q)
        cc=Counter(x['source'] for x in tags)
        print(f"{label}: signals={len(tags)} trades={rr['trades']} | V20E={cc['V20E']} SLOW_E={cc['SLOW_TURN_E']} V_E={cc['V_REBOUND_E']}",flush=True)
        sigrows += [dict(variant=label,source=x['source'],symbol=x['symbol'],time=x['time']) for x in tags]

    pd.DataFrame(rows).to_csv(SUMMARY,index=False)
    if parts: pd.concat(parts,ignore_index=True).to_csv(TRADES,index=False)
    pd.DataFrame(sigrows).to_csv(SIGNALS,index=False)
    print('WROTE',SUMMARY); print('WROTE',TRADES); print('WROTE',SIGNALS)
    print('E-series only. KR engines unchanged.',flush=True)


if __name__=='__main__': main()
