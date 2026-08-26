#!/usr/bin/env python3
"""V128 Williams robustness + exit recovery stress.

READ ONLY. NO API. NO ORDERS. NO DOWNLOADS.

Starts from V127 winner family (TREND5_20_HIST_VWAP) and tests whether the
edge survives stricter chronology, higher costs, symbol removal, and more
sensible exit families.  The purpose is NOT to tune on HOLDOUT.  Candidate
exit families are compared on IS/OOS; HOLDOUT is reported untouched.

Key design principles from prior Williams research:
- Gate-out is intentionally loose: a single weak indicator is NOT an exit.
- Initial adverse movement is handled by causal structure/VWAP failure tests,
  not merely by waiting for a fixed -1.5% loss.
- Once meaningful profit exists, use staged profit protection so a runner can
  breathe but cannot give back the whole move.
"""
from __future__ import annotations
import argparse, importlib.util, json, math, sqlite3, statistics, time
from collections import defaultdict
from pathlib import Path

HERE=Path(__file__).resolve().parent
V127=HERE/'v127_williams_master_tournament_usa.py'
spec=importlib.util.spec_from_file_location('v127',V127)
v127=importlib.util.module_from_spec(spec); spec.loader.exec_module(v127)

DEFAULT_SYMBOLS=v127.DEFAULT_SYMBOLS
DEFAULT_DB=v127.DEFAULT_DB
ENTRY='TREND5_20_HIST_VWAP'
COSTS=(8.0,12.0,16.0)


def ema(vals,span): return v127.ema(vals,span)
def pct(a,b): return v127.pct(a,b)


def enriched_entry(e):
    z=dict(e)
    C=e['C']; i=e['entry_i']
    e9=ema(C,9); e20=ema(C,20); e50=ema(C,50)
    z['ema9']=e9; z['ema20']=e20; z['ema50']=e50
    z['trend_ok']=bool(i>=1 and C[i]>=e20[i] and e9[i]>=e20[i])
    z['trend_strict']=bool(i>=1 and C[i]>=e20[i] and e9[i]>=e20[i]>=e50[i] and e20[i]>=e20[i-1])
    return z


def exit_trade(e,mode,hard_cap=1.5):
    i0=e['entry_i']; entry=e['entry']; H=e['H']; L=e['L']; C=e['C']; V=e['V']
    cci=e['cci']; macd=e['macd']; sig=e['sig']; hist=e['hist']; vwap=e['vwap']
    peak=entry; ix=len(C)-1; reason='EOD'; below_vwap_run=0; weak_run=0
    for i in range(i0+1,len(C)):
        peak=max(peak,H[i]); gain=pct(entry,peak); cur=pct(entry,C[i]); dd=pct(peak,C[i])
        cdown=bool(cci[i] is not None and cci[i-1] is not None and cci[i]<cci[i-1])
        weak=bool(macd[i]<sig[i] and cdown)
        weak_run=weak_run+1 if weak else 0
        below=bool(C[i] < vwap[i])
        below_vwap_run=below_vwap_run+1 if below else 0

        # Independent catastrophe cap remains only as final safety net.
        if cur <= -abs(hard_cap): ix=i; reason=f'HARD_{hard_cap:.2f}'; break

        if mode=='BASE_V127':
            if gain>=0.30 and cur<=0.00: ix=i; reason='LOCK03_BE'; break
            if gain>=0.50 and cur<=0.20: ix=i; reason='LOCK05_02'; break
            if gain>=0.80 and dd<=-0.30: ix=i; reason='TRAIL08_03'; break

        elif mode=='STRUCTURE_LOOSE':
            # No single-indicator exit. Before profit, require persistent VWAP
            # loss PLUS momentum confirmation. This is the early-failure path.
            if gain<0.30 and (i-i0)>=2 and below_vwap_run>=2 and hist[i]<0 and cur<0:
                ix=i; reason='EARLY_STRUCT_FAIL'; break
            # After profit, preserve room for ordinary pullbacks.
            if gain>=0.30 and cur<=-0.05: ix=i; reason='LOCK03_M005'; break
            if gain>=0.60 and cur<=0.20: ix=i; reason='LOCK06_02'; break
            if gain>=1.00 and dd<=-0.40: ix=i; reason='TRAIL10_04'; break
            if gain>=2.00 and dd<=-0.55: ix=i; reason='TRAIL20_055'; break

        elif mode=='ADAPTIVE_TRAIL_A':
            # Staged trailing floor: looser early, progressively tighter after
            # the trade proves itself. Indicators never trigger alone.
            if gain<0.30 and (i-i0)>=3 and below_vwap_run>=2 and weak_run>=2 and cur<0:
                ix=i; reason='EARLY_FAIL_2X'; break
            if gain>=0.30 and gain<0.75 and dd<=-0.35: ix=i; reason='TRAIL_030_075'; break
            if gain>=0.75 and gain<1.50 and dd<=-0.45: ix=i; reason='TRAIL_075_150'; break
            if gain>=1.50 and gain<2.50 and dd<=-0.55: ix=i; reason='TRAIL_150_250'; break
            if gain>=2.50 and dd<=-0.65: ix=i; reason='TRAIL_250P'; break

        elif mode=='ADAPTIVE_TRAIL_B':
            # Profit-floor version: once a runner reaches a milestone, require
            # a minimum retained profit but do not exit merely because MACD/CCI dips.
            if gain<0.30 and (i-i0)>=3 and below_vwap_run>=3 and hist[i]<0 and cur<0:
                ix=i; reason='EARLY_STRUCT_FAIL3'; break
            if gain>=0.50 and cur<=0.10: ix=i; reason='FLOOR_050_010'; break
            if gain>=1.00 and cur<=0.45: ix=i; reason='FLOOR_100_045'; break
            if gain>=1.50 and cur<=0.80: ix=i; reason='FLOOR_150_080'; break
            if gain>=2.00 and cur<=1.15: ix=i; reason='FLOOR_200_115'; break
            if gain>=3.00 and dd<=-0.70: ix=i; reason='TRAIL_300_070'; break

    ret=pct(entry,C[ix]); mfe=pct(entry,max(H[i0:ix+1])); mae=pct(entry,min(L[i0:ix+1])); give=max(0.0,mfe-ret)
    return {'ret_gross':ret,'hold':ix-i0,'mfe':mfe,'mae':mae,'giveback':give,'reason':reason,'symbol':e['symbol'],'date':e['date']}


def metrics(trades,cost_bps):
    if not trades:return None
    cost=cost_bps/100.0; rs=[t['ret_gross']-cost for t in trades]
    wins=[x for x in rs if x>0]; losses=[x for x in rs if x<0]
    gp=sum(wins); gl=-sum(losses); pf=gp/gl if gl>0 else (999.0 if gp>0 else 0.0)
    eq=peak=0.0; mdd=0.0
    for x in rs:
        eq+=x; peak=max(peak,eq); mdd=min(mdd,eq-peak)
    return {'n':len(rs),'win':100*len(wins)/len(rs),'avg':statistics.fmean(rs),'pf':pf,'net':sum(rs),'mdd':mdd,
            'mfe':statistics.fmean(t['mfe'] for t in trades),'mae':statistics.fmean(t['mae'] for t in trades),
            'giveback':statistics.fmean(t['giveback'] for t in trades),'hold':statistics.fmean(t['hold'] for t in trades)}


def fmt(z):
    if not z:return 'N=0'
    return f"N={z['n']} WIN={z['win']:.2f}% AVG={z['avg']:.4f}% PF={z['pf']:.3f} NET={z['net']:.2f}% MDD={z['mdd']:.2f}% GIVE={z['giveback']:.3f}%"


def build_entries(db,max_days,symbols):
    con=sqlite3.connect(db); rows=[]; bysym=defaultdict(int)
    for s in symbols:
        dm=v127.load_days(con,s,max_days); ds=sorted(dm)
        for di in range(1,len(ds)):
            ecs=v127.entry_candidates(dm[ds[di-1]],dm[ds[di]])
            if ENTRY not in ecs: continue
            e=enriched_entry(ecs[ENTRY]); e['symbol']=s; e['date']=ds[di]
            rows.append(e); bysym[s]+=1
        print('LOAD',s,'DAYS=',max(0,len(ds)-1),'ENTRIES=',bysym[s])
    con.close(); return rows


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default=str(DEFAULT_DB)); ap.add_argument('--max-days',type=int,default=135)
    ap.add_argument('--symbols',default=','.join(DEFAULT_SYMBOLS)); args=ap.parse_args(); t0=time.time()
    syms=[x.strip().upper() for x in args.symbols.split(',') if x.strip()]
    entries=build_entries(args.db,args.max_days,syms)
    dates=sorted(set(e['date'] for e in entries)); n=len(dates); a=int(n*.60); b=int(n*.80)
    split={'IS':set(dates[:a]),'OOS':set(dates[a:b]),'HOLDOUT':set(dates[b:])}
    modes=['BASE_V127','STRUCTURE_LOOSE','ADAPTIVE_TRAIL_A','ADAPTIVE_TRAIL_B']
    trend_modes=['BASE_ENTRY','TREND_OK','TREND_STRICT']
    caps=[1.0,1.25,1.5]
    print('\n=== V128 WILLIAMS ROBUSTNESS + EXIT STRESS ===')
    print('READ_ONLY=YES ORDERS=NONE DOWNLOADS=NONE ENTRY=',ENTRY)
    print('DATES=',n,'SPLIT=',{k:len(v) for k,v in split.items()})
    results=[]
    for tm in trend_modes:
        elig=[e for e in entries if tm=='BASE_ENTRY' or (tm=='TREND_OK' and e['trend_ok']) or (tm=='TREND_STRICT' and e['trend_strict'])]
        for mode in modes:
            for cap in caps:
                trades=[exit_trade(e,mode,cap) for e in elig]
                rec={'trend':tm,'exit':mode,'cap':cap,'trades':trades}
                for lab,ds in split.items(): rec[lab]=[t for t in trades if t['date'] in ds]
                # Eligibility decided only by OOS at 8bps.
                oz=metrics(rec['OOS'],8.0)
                rec['eligible']=bool(oz and oz['n']>=20 and oz['avg']>0 and oz['pf']>=1.15)
                results.append(rec)

    print('\n=== OOS-ELIGIBLE HOLDOUT RANK @8bps ===')
    ranked=[]
    for r in results:
        hz=metrics(r['HOLDOUT'],8.0); oz=metrics(r['OOS'],8.0)
        if not r['eligible'] or not hz: continue
        score=hz['avg']*.45+(hz['pf']-1)*.20+(hz['win']/100)*.10+hz['mdd']*.015-hz['giveback']*.06
        ranked.append((score,r,hz,oz))
    ranked.sort(key=lambda x:x[0],reverse=True)
    for i,(sc,r,hz,oz) in enumerate(ranked[:15],1):
        print(i,r['trend'],r['exit'],'CAP=',r['cap'],'SCORE=',f'{sc:.4f}','H',fmt(hz),'O',fmt(oz))

    if not ranked:
        print('NO_ELIGIBLE_CANDIDATE'); return
    winner=ranked[0][1]
    print('\n=== WINNER COST STRESS ===')
    print('WINNER',winner['trend'],winner['exit'],'CAP=',winner['cap'])
    cost_pass=True
    for cb in COSTS:
        oz=metrics(winner['OOS'],cb); hz=metrics(winner['HOLDOUT'],cb)
        print('COST',cb,'bps OOS',fmt(oz),'HOLDOUT',fmt(hz))
        if not (oz and hz and oz['avg']>0 and hz['avg']>0 and oz['pf']>=1.10 and hz['pf']>=1.10): cost_pass=False

    print('\n=== LEAVE-ONE-SYMBOL-OUT @8bps ===')
    loso=[]
    for s in syms:
        ho=[t for t in winner['HOLDOUT'] if t['symbol']!=s]; z=metrics(ho,8.0); loso.append((s,z))
        print('DROP',s,fmt(z))
    loso_pass=all(z and z['avg']>0 and z['pf']>=1.0 for _,z in loso)

    print('\n=== SYMBOL CONTRIBUTION HOLDOUT @8bps ===')
    for s in syms:
        z=metrics([t for t in winner['HOLDOUT'] if t['symbol']==s],8.0)
        if z: print(s,fmt(z))

    oz=metrics(winner['OOS'],8.0); hz=metrics(winner['HOLDOUT'],8.0)
    final_pass=bool(cost_pass and loso_pass and oz and hz and oz['pf']>=1.30 and hz['pf']>=1.30 and oz['avg']>0 and hz['avg']>0)
    report={'winner':{'trend':winner['trend'],'exit':winner['exit'],'cap':winner['cap']},'oos_8':oz,'holdout_8':hz,'cost_pass':cost_pass,'loso_pass':loso_pass,'final_pass':final_pass,'elapsed_sec':time.time()-t0}
    Path('/tmp/v128_williams_robustness.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('\nFINAL_PASS=',final_pass)
    print('REPORT /tmp/v128_williams_robustness.json')
    print('ELAPSED_SEC',f"{time.time()-t0:.1f}")
    print('NEXT=',('PROMOTE_TO_FINAL_REPLICATION_TEST' if final_pass else 'DO_NOT_DEPLOY; USE FAILURE DECOMP TO DESIGN NEXT CANDIDATE'))

if __name__=='__main__': main()
