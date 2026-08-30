from __future__ import annotations

"""Cross-validate Engine5 versions on US regular-session data in original ET.

US market timestamps remain untouched (09:30..15:59 ET).  Only price units are
converted to KRW-equivalent scale in the cache.  Session-time rules are adapted
at the ENGINE layer, not by shifting DB/cache timestamps.
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
import tools.validate_engine5_v20_macd_strength as ms
import tools.validate_engine5_integrated_full_history as integ
import tools.validate_engine5_integrated_slow_turn_rearm_deep as revised
import tools.validate_engine5_v21_v_rebound_state_machine as vsm
import tools.validate_engine5_v21_v_rebound_reaccel as vra
import tools.validate_engine5_v21_v_rebound_momentum_preservation as vmp

ROOT=Path('/home/ubuntu/day-trader-api/engine5_us_kr_mapped_cache')
CORE=ROOT/'us_kr_mapped_core.pkl'
PROV_DIR=ROOT/'provisional'
SUMMARY=ROOT/'us_kr_mapped_all_versions_summary.csv'
TRADES=ROOT/'us_kr_mapped_all_versions_trades.csv'
SIGNALS=ROOT/'us_kr_mapped_v21_signals.csv'
THRESHOLD=50
CUTS=(-0.15,-0.20,-0.30,-0.50)
DELAYS=(0,1,2,3)

# KR engine semantics expressed on the US exchange clock.
# KR: open 09:00, buy after 10m 09:10, opening regime end 10:00,
#     no new entries 15:00, force-flat 15:20, close 15:30.
# US: open 09:30, so every wall-clock session rule moves +30m.
US_OPEN_MINUTE=9*60+30
US_BUY_START_MINUTE=9*60+40
US_OPENING_END_MINUTE=10*60+30
US_NO_ENTRY_MINUTE=15*60+30
US_FORCE_FLAT_MINUTE=15*60+50


def n(x): return str(x).zfill(6)
def count_events(ev): return sum(len(v) for v in ev.values())

def apply_us_session_clock():
    """Patch imported historical validators to identical relative-session rules."""
    base.NO_ENTRY_MINUTE=US_NO_ENTRY_MINUTE
    base.FORCE_FLAT_MINUTE=US_FORCE_FLAT_MINUTE
    sweep.OPEN_BUY_MINUTE=US_BUY_START_MINUTE
    sweep.OPENING_ENTRY_END=US_OPENING_END_MINUTE
    multi.OPEN_MINUTE=US_BUY_START_MINUTE


def upgrade(ev):
    out={}
    for ts,cs in ev.items():
        rows=[]
        for c in cs:
            if len(c)==13: rows.append(c)
            elif len(c)==12: rows.append(tuple(c)+(False,))
            else: raise ValueError(f'unsupported event width={len(c)} at {ts}')
        out[pd.Timestamp(ts)]=rows
    return out

def hist_stat(label,tr,signals):
    g=pd.to_numeric(tr.pnl_pct,errors='coerce').dropna() if len(tr) else pd.Series(dtype=float)
    net=g-0.25; gp=float(net[net>0].sum()); gl=float(-net[net<0].sum())
    return dict(variant=label,signals=signals,trades=len(net),wins=int((net>0).sum()),losses=int((net<=0).sum()),win_pct=float((net>0).mean()*100) if len(net) else 0.,net_sum_pct=float(net.sum()),avg_net_pct=float(net.mean()) if len(net) else 0.,pf=gp/gl if gl>0 else np.inf,max_loss_pct=float(net.min()) if len(net) else np.nan)
def run_hist(label,ev,packed,states):
    e=upgrade(ev); tr=multi.simulate_multi(packed,e,states,THRESHOLD); return tr,hist_stat(label,tr,count_events(e))

def load_pf(raw):
    out={}
    for i,s in enumerate(raw,1):
        p=PROV_DIR/f'{n(s)}_provisional.pkl'
        if not p.exists(): raise FileNotFoundError(f'{p} missing; rebuild original-ET cache first')
        with p.open('rb') as fh: out[s]=pickle.load(fh)
        print(f'[PF CACHE {i}/{len(raw)}] {s} rows={len(out[s])}',flush=True)
    return out

def build_vrebound(raw,cfg,scored,completed,micros,pf):
    allc=[]; vf={}
    for s,bars in raw.items():
        z=vsm.add_features(pf[s],micros[s],bars).sort_values('time').reset_index(drop=True); vf[s]=z
        c=vsm.state_candidates(s,z,scored[s],integ.V_RAW_MIN,integ.V_LEG_MIN)
        if len(c): allc.append(c)
    if not allc:return []
    q=pd.concat(allc,ignore_index=True)
    q=vra.add_pullback_reaccel(q,vf); q=vmp.add_preservation(q,vf)
    q=q[(q.stop_dist_pct<=integ.V_STOP_CAP)&q.reaccel_pass&
        (pd.to_numeric(q.volume_accel,errors='coerce')>=integ.V_VOL_MIN)&q.rsi_positive_all&
        (pd.to_numeric(q.gap_keep_ratio,errors='coerce')>=integ.V_GAP_KEEP_MIN)].copy()
    q['day']=pd.to_datetime(q.time).dt.date; q=q.sort_values('time').drop_duplicates(['symbol','day'],keep='first')
    return [dict(source='V_REBOUND',symbol=n(r.symbol),time=pd.Timestamp(r.time),event=r.event,meta={'structural_stop':float(r.structural_stop)}) for _,r in q.iterrows()]

def main():
    if not CORE.exists(): raise FileNotFoundError(f'{CORE} missing; rebuild original-ET cache first')
    with CORE.open('rb') as fh:d=pickle.load(fh)
    shift=d.get('time_shift_minutes')
    if shift not in (0,0.0,None):
        raise RuntimeError(f'INVALID CACHE: time_shift_minutes={shift}; expected 0. Rebuild with --rebuild before validation.')

    apply_us_session_clock()
    raw=d['raw']; cfg=d['cfg']; packed=d['packed']; states=d['states']; scored=d['scored']; strength=d['strength']; completed=d['completed']; micros=d['micros']
    print('=== US LARGE OOS — ORIGINAL ET + US SESSION ENGINE RULES ===',flush=True)
    print(f"symbols={len(raw)} rows={sum(len(x) for x in raw.values())} fx={d.get('fx')} shift={shift}m",flush=True)
    print(f'SESSION: open=09:30 buy_start=09:40 opening_end=10:30 no_entry=15:30 force_flat=15:50 close=16:00',flush=True)
    print('DB/CACHE CLOCK UNCHANGED. ENGINE SESSION RULES ONLY.',flush=True)

    raw_entries=v8.pack_entry_events(scored); ev10=sweep.filt_open(raw_entries); ev16,waits=v16.build_wait_events(ev10,raw,cfg,False); ev17,_,_=v17b.build_v17b(ev16,scored,waits); ev18,_=h.build_veto_stream(ev17,micros)
    rows=[]; parts=[]
    for label,ev in [('V17C',ev17),('V18',ev18)]:
        tr,s=run_hist(label,ev,packed,states); rows.append(s); q=tr.copy();q['variant']=label;parts.append(q)
        print(f"{label}: trades={s['trades']} win={s['win_pct']:.2f}% net={s['net_sum_pct']:+.4f}% PF={s['pf']:.3f}",flush=True)

    for delay in DELAYS:
        fast,dg=v19.build_v19_events(scored,micros,raw,delay); ev,_=v19.merge_additive(ev18,fast); label=f'V19_D{delay}'
        tr,s=run_hist(label,ev,packed,states); rows.append(s); q=tr.copy();q['variant']=label;parts.append(q)
        print(f"{label}: trades={s['trades']} win={s['win_pct']:.2f}% net={s['net_sum_pct']:+.4f}% PF={s['pf']:.3f}",flush=True)

    ev20,_=ms.filter_events(ev18,strength,raw_min=integ.V20_RAW,rel_min=integ.V20_REL)
    v20=[]
    for ts,cs in ev20.items():
        for c in cs:
            ext=integ.entry_extension_5m(scored,c[0],ts)
            if pd.notna(ext) and ext>=integ.V20_EXTREME_CAP: continue
            v20.append(dict(source='V20',symbol=n(c[0]),time=pd.Timestamp(ts),event=c,meta={}))
    tr=integ.simulate(packed,states,v20); s=integ.stat('V20',tr); row=dict(variant='V20',signals=len(v20),**{k:s[k] for k in ['trades','wins','losses','win_pct','net_sum_pct','avg_net_pct','pf','max_loss_pct']}); rows.append(row); q=tr.copy();q['variant']='V20';parts.append(q)
    print(f"V20: trades={row['trades']} win={row['win_pct']:.2f}% net={row['net_sum_pct']:+.4f}% PF={row['pf']:.3f}",flush=True)

    print('LOAD ORIGINAL-ET PROVISIONAL CACHE FOR V21...',flush=True); pf=load_pf(raw)
    old=revised.st.load_or_build_cache; revised.st.load_or_build_cache=lambda sym,*_:(pf[n(sym)],micros[n(sym)])
    try: allslow=revised.build_all_slow(raw,cfg,completed,micros)
    finally: revised.st.load_or_build_cache=old
    vr=build_vrebound(raw,cfg,scored,completed,micros,pf)
    print(f'V21 components: V20={len(v20)} V_REBOUND={len(vr)} ALL_SLOW_READY={len(allslow)}',flush=True)
    sigrows=[]
    for cut in CUTS:
        slow=revised.slow_tags(revised.select_revised(allslow,cut)); tags=sorted(v20+vr+slow,key=lambda x:(pd.Timestamp(x['time']),x['symbol'],x['source']))
        tr=integ.simulate(packed,states,tags); st=integ.stat(f'V21_{cut}',tr); label=f'V21_{cut}'
        rr=dict(variant=label,signals=len(tags),**{k:st[k] for k in ['trades','wins','losses','win_pct','net_sum_pct','avg_net_pct','pf','max_loss_pct']}); rows.append(rr); q=tr.copy();q['variant']=label;parts.append(q)
        cc=Counter(x['source'] for x in tags)
        print(f"{label}: trades={rr['trades']} win={rr['win_pct']:.2f}% net={rr['net_sum_pct']:+.4f}% PF={rr['pf']:.3f} | V20={cc['V20']} SLOW={cc['SLOW_TURN']} V={cc['V_REBOUND']}",flush=True)
        sigrows += [dict(variant=label,source=x['source'],symbol=x['symbol'],time=x['time']) for x in tags]

    pd.DataFrame(rows).to_csv(SUMMARY,index=False); pd.concat(parts,ignore_index=True).to_csv(TRADES,index=False); pd.DataFrame(sigrows).to_csv(SIGNALS,index=False)
    print('WROTE',SUMMARY);print('WROTE',TRADES);print('WROTE',SIGNALS)

if __name__=='__main__':main()
