from __future__ import annotations

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
import tools.validate_engine5_v20_macd_strength as ms
import tools.validate_engine5_integrated_full_history as integ
import tools.validate_engine5_integrated_slow_turn_rearm_deep as rev
import tools.diagnose_engine5_slow_turn_rearm_ablation as rearm
import tools.validate_engine5_v21_v_rebound_state_machine as vsm
import tools.validate_engine5_v21_v_rebound_reaccel as vra
import tools.validate_engine5_v21_v_rebound_momentum_preservation as vmp
import tools.build_engine5_us_oos_cache as uscache

CACHE_DIR = Path('/home/ubuntu/day-trader-api/engine5_us_oos_cache')
CORE = CACHE_DIR / 'us_engine5_core.pkl'
OUT_SUMMARY = CACHE_DIR / 'us_oos_full_engine_audit_summary.csv'
OUT_DETAIL = CACHE_DIR / 'us_oos_full_engine_audit_detail.csv'
OUT_SLOW = CACHE_DIR / 'us_oos_full_engine_audit_slow_candidates.csv'
OUT_V = CACHE_DIR / 'us_oos_full_engine_audit_v_candidates.csv'
CUTS = (-0.15, -0.20, -0.30, -0.50)
NY_TZ = 'America/New_York'


def n(x): return str(x).zfill(6)

def num(x): return pd.to_numeric(x, errors='coerce')

def event_count(ev): return sum(len(v) for v in ev.values())

def add(rows, section, metric, value, note=''):
    rows.append(dict(section=section, metric=metric, value=value, note=note))


def data_audit(raw, scored, rows, detail):
    for sym, b0 in raw.items():
        b = b0.copy(); b['time'] = pd.to_datetime(b.time)
        local = b.time
        dates = local.dt.date
        counts = pd.Series(1, index=dates).groupby(level=0).sum()
        mins = local.dt.hour * 60 + local.dt.minute
        dups = int(local.duplicated().sum())
        out_session = int(((mins < 570) | (mins > 959)).sum())
        bad_days = int((counts != 390).sum())
        sf = scored[sym].copy(); sf['time'] = pd.to_datetime(sf.time)
        causal_bad = int((sf.time.dt.minute % 5 != 0).sum())
        detail.append(dict(section='DATA', symbol=sym, rows=len(b), days=len(counts), duplicate_times=dups,
                           out_of_regular_session=out_session, days_not_390=bad_days,
                           scored_5m_rows=len(sf), scored_non_5m_close_label=causal_bad,
                           start=str(local.min()), end=str(local.max()), timezone=str(local.dt.tz)))
    add(rows, 'DATA', 'symbols', len(raw))
    add(rows, 'DATA', 'minute_rows_total', sum(len(x) for x in raw.values()))
    add(rows, 'DATA', 'duplicate_times_total', sum(int(pd.to_datetime(x.time).duplicated().sum()) for x in raw.values()))


def build_core_pipeline(raw, cfg, scored, strength, micros, rows, detail):
    raw_entries = v8.pack_entry_events(scored)
    ev10 = sweep.filt_open(raw_entries)
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev17, added, skipped = v17b.build_v17b(ev16, scored, waits)
    ev18, blocked = h.build_veto_stream(ev17, micros)
    ev_raw, d_raw = ms.filter_events(ev18, strength, raw_min=integ.V20_RAW)
    ev_rel, d_rel = ms.filter_events(ev18, strength, rel_min=integ.V20_REL)
    ev20, d_both = ms.filter_events(ev18, strength, raw_min=integ.V20_RAW, rel_min=integ.V20_REL)

    ext_keep = 0; ext_block = 0
    for ts, cs in ev20.items():
        for c in cs:
            ext = integ.entry_extension_5m(scored, c[0], ts)
            if np.isfinite(ext) and ext >= integ.V20_EXTREME_CAP: ext_block += 1
            else: ext_keep += 1

    for name, ev in [('RAW_ENTRY', raw_entries), ('OPEN_FILTER', ev10), ('WAIT_REACCEL', ev16),
                     ('V17C', ev17), ('V18_STALE_VETO', ev18), ('V20_RAW_ONLY', ev_raw),
                     ('V20_REL_ONLY', ev_rel), ('V20_RAW_REL', ev20)]:
        add(rows, 'CORE', name, event_count(ev))
    add(rows, 'CORE', 'V18_BLOCKED_STALE', len(blocked))
    add(rows, 'CORE', 'V20_EXTREME_GUARD_KEEP', ext_keep)
    add(rows, 'CORE', 'V20_EXTREME_GUARD_BLOCK', ext_block)

    for label, d in [('V20_RAW', d_raw), ('V20_REL', d_rel), ('V20_BOTH', d_both)]:
        if len(d):
            detail.append(dict(section='V20_GATE', symbol='ALL', gate=label, total=len(d), kept=int(d.keep.sum()),
                               raw_median=float(num(d.raw).median()), raw_max=float(num(d.raw).max()),
                               rel_median=float(num(d.rel).median()), rel_max=float(num(d.rel).max())))
    return ev17, ev18, ev20


def slow_audit(raw, scored, completed, micros, pf_by_symbol, rows, detail):
    parts=[]
    for i, sym in enumerate(raw, 1):
        print(f'[SLOW AUDIT {i}/{len(raw)}] {sym}', flush=True)
        q = rearm.build_all_candidates(sym, pf_by_symbol[sym], micros[sym], scored[sym])
        if len(q): parts.append(q)
    allc = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if allc.empty:
        add(rows, 'SLOW', 'ALL_REARM_READY_CONFIRMED', 0)
        return allc
    allc['symbol'] = allc.symbol.astype(str).str.zfill(6)
    allc['entry_time'] = pd.to_datetime(allc.entry_time)
    allc['ready_time'] = pd.to_datetime(allc.ready_time)
    allc['day'] = allc.entry_time.dt.date
    allc = allc.sort_values(['symbol','day','entry_time','ready_time']).reset_index(drop=True)
    allc['candidate_no'] = allc.groupby(['symbol','day']).cumcount()+1
    allc['norm_mid_slope_pct'] = num(allc.mid_slope8)/num(allc.entry_price)*100.0

    add(rows, 'SLOW', 'ALL_REARM_READY_CONFIRMED', len(allc))
    add(rows, 'SLOW', 'FIRST_PER_DAY_EQUIVALENT', int((allc.candidate_no == 1).sum()))
    add(rows, 'SLOW', 'ADDITIONAL_AFTER_FIRST', int((allc.candidate_no > 1).sum()))
    rc = allc.regime.value_counts()
    for rg, cnt in rc.items(): add(rows, 'SLOW_REGIME', str(rg), int(cnt))
    add(rows, 'SLOW', 'OLD_SELECTOR_PASS', int(allc.selected_current.sum()))

    for cut in CUTS:
        sel = rev.select_revised(allc, cut)
        add(rows, 'SLOW_REVISED', f'CUT_{cut:.2f}', len(sel), 'pre-specified KR plateau; not tuned on US')
        detail.append(dict(section='SLOW_REVISED', symbol='ALL', gate=f'CUT_{cut:.2f}', total=len(allc), kept=len(sel)))
    return allc


def v_audit(raw, scored, micros, pf_by_symbol, rows, detail):
    allc=[]
    ready_common=0; ready_raw30=0
    for i, (sym, bars) in enumerate(raw.items(), 1):
        print(f'[V AUDIT {i}/{len(raw)}] {sym}', flush=True)
        z = vsm.add_features(pf_by_symbol[sym], micros[sym], bars).sort_values('time').reset_index(drop=True)
        rb = z.ready_common.fillna(False)
        rr = rb & (num(z.gap_delta) >= integ.V_RAW_MIN)
        ready_common += int(rb.sum()); ready_raw30 += int(rr.sum())
        c = vsm.state_candidates(sym, z, scored[sym], integ.V_RAW_MIN, integ.V_LEG_MIN)
        if len(c): allc.append(c)
    cand = pd.concat(allc, ignore_index=True) if allc else pd.DataFrame()
    add(rows, 'V_REBOUND', 'READY_COMMON_MINUTES', ready_common)
    add(rows, 'V_REBOUND', 'READY_AFTER_RAW30_MINUTES', ready_raw30)
    add(rows, 'V_REBOUND', 'STATE_MACHINE_RECLAIM_CANDIDATES', len(cand))
    if cand.empty: return cand

    # Apply the exact selected structural filters in the same order as integrated_full_history.
    q = cand.copy()
    stages=[('STOP_LE2', num(q.stop_dist_pct) <= integ.V_STOP_CAP)]
    q=q[stages[-1][1]].copy(); add(rows,'V_REBOUND','AFTER_STOP_LE2',len(q))
    if len(q):
        # Need feature frames for reaccel/preservation helpers.
        vf={}
        for sym,bars in raw.items(): vf[sym]=vsm.add_features(pf_by_symbol[sym],micros[sym],bars).sort_values('time').reset_index(drop=True)
        q=vra.add_pullback_reaccel(q,vf); add(rows,'V_REBOUND','AFTER_REACCEL',int(q.reaccel_pass.sum()))
        q=q[q.reaccel_pass].copy()
        if len(q):
            q=vmp.add_preservation(q,vf)
            q1=q[num(q.volume_accel)>=integ.V_VOL_MIN].copy(); add(rows,'V_REBOUND','AFTER_VOLUME',len(q1)); q=q1
            q1=q[q.rsi_positive_all].copy(); add(rows,'V_REBOUND','AFTER_RSI_POSITIVE',len(q1)); q=q1
            q1=q[num(q.gap_keep_ratio)>=integ.V_GAP_KEEP_MIN].copy(); add(rows,'V_REBOUND','AFTER_GAP_KEEP',len(q1)); q=q1
            if len(q):
                q['day']=pd.to_datetime(q.time).dt.date
                ded=q.sort_values('time').drop_duplicates(['symbol','day'],keep='first')
                add(rows,'V_REBOUND','FINAL_DAY_DEDUP_SIGNALS',len(ded))
    return cand


def final_current_audit(raw,cfg,packed,states,scored,strength,completed,micros,pf_by_symbol,base_cand,rows):
    # Reproduce current US wiring without any slow/V feature rebuild.
    persist_path = CACHE_DIR/'slow_turn_persistence_candidates.csv'
    old_persist=integ.PERSIST_SRC; old_reconstruct=integ.sri.reconstruct_base_candidates; old_vload=integ.vold.load_cache; old_read=integ.pd.read_csv
    def fast_reconstruct(*_): return base_cand.copy()
    def fast_vload(sym,*_):
        s=n(sym); return pf_by_symbol[s], micros[s]
    def us_read(path,*args,**kwargs):
        df=old_read(path,*args,**kwargs)
        try: same=Path(path)==persist_path
        except TypeError: same=False
        if same and 'entry_time' in df.columns: df['entry_time']=pd.to_datetime(df.entry_time,utc=True).dt.tz_convert(NY_TZ)
        return df
    integ.PERSIST_SRC=persist_path; integ.sri.reconstruct_base_candidates=fast_reconstruct; integ.vold.load_cache=fast_vload; integ.pd.read_csv=us_read
    try: tagged=integ.build_sources(raw,cfg,scored,strength,completed,micros)
    finally:
        integ.PERSIST_SRC=old_persist; integ.sri.reconstruct_base_candidates=old_reconstruct; integ.vold.load_cache=old_vload; integ.pd.read_csv=old_read
    cnt=Counter(x['source'] for x in tagged)
    for src in ['V20','SLOW_TURN','V_REBOUND']: add(rows,'CURRENT_WIRING',f'{src}_SIGNALS',cnt.get(src,0))
    tr=integ.simulate(packed,states,tagged)
    s=integ.stat('CURRENT_WIRING',tr)
    for k in ['trades','wins','losses','win_pct','net_sum_pct','avg_net_pct','pf','max_loss_pct']: add(rows,'CURRENT_RESULT',k,s[k])
    if len(tr) and 'reason' in tr.columns:
        for reason,c in tr.reason.value_counts().items(): add(rows,'EXIT_REASON',str(reason),int(c))


def main():
    if not CORE.exists(): raise FileNotFoundError(CORE)
    with CORE.open('rb') as fh: d=pickle.load(fh)
    raw=d['raw']; cfg=d['cfg']; packed=d['packed']; states=d['states']; scored=d['scored']; strength=d['strength']; completed=d['completed']; micros=d['micros']

    rows=[]; detail=[]
    print('=== US OOS FULL ENGINE AUDIT ===', flush=True)
    print('NO THRESHOLD CHANGES. NO OOS TUNING.', flush=True)
    data_audit(raw,scored,rows,detail)
    _,_,_=build_core_pipeline(raw,cfg,scored,strength,micros,rows,detail)

    print('LOAD FAST PROVISIONAL...', flush=True)
    base_cand,pf_by_symbol=uscache.build_base_candidates_fast(raw,cfg,scored,micros,completed)
    add(rows,'SLOW','LEGACY_BASE_CANDIDATES',len(base_cand),'first-per-day path used by current integrated_full_history')
    allslow=slow_audit(raw,scored,completed,micros,pf_by_symbol,rows,detail)
    vcand=v_audit(raw,scored,micros,pf_by_symbol,rows,detail)
    final_current_audit(raw,cfg,packed,states,scored,strength,completed,micros,pf_by_symbol,base_cand,rows)

    summary=pd.DataFrame(rows)
    summary.to_csv(OUT_SUMMARY,index=False)
    pd.DataFrame(detail).to_csv(OUT_DETAIL,index=False)
    if len(allslow): allslow.drop(columns=['event'],errors='ignore').to_csv(OUT_SLOW,index=False)
    if len(vcand): vcand.drop(columns=['event'],errors='ignore').to_csv(OUT_V,index=False)

    print('\n=== AUDIT SUMMARY ===')
    for section in ['DATA','CORE','SLOW','SLOW_REGIME','SLOW_REVISED','V_REBOUND','CURRENT_WIRING','CURRENT_RESULT','EXIT_REASON']:
        q=summary[summary.section==section]
        if len(q):
            print(f'\n[{section}]')
            for _,r in q.iterrows(): print(f"{r.metric}: {r.value}" + (f" | {r.note}" if str(r.note) not in ('','nan') else ''))
    print('\nSTRUCTURE CHECK: current integrated_full_history uses the legacy first-per-day Slow-turn reconstruction. Revised re-arm + guarded DEEP is audited separately above for all pre-specified KR cuts.')
    print('IMPORTANT: do not tune any threshold from this audit.')
    print('WROTE',OUT_SUMMARY); print('WROTE',OUT_DETAIL); print('WROTE',OUT_SLOW); print('WROTE',OUT_V)

if __name__=='__main__': main()
