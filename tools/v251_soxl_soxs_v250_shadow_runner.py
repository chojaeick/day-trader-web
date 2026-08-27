#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sqlite3, sys, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from live_server.analytics import ticks_to_bars

SYMBOLS=('SOXL','SOXS')

def load_ticks(db_path,symbol,limit=5000):
    with sqlite3.connect(db_path) as c:
        c.row_factory=sqlite3.Row
        rows=c.execute("SELECT symbol,price,qty,cum_volume,ts FROM ticks WHERE symbol=? ORDER BY ts DESC LIMIT ?",(symbol,limit)).fetchall()
    return [dict(r) for r in reversed(rows)]

def rsi(s,period=14):
    s=pd.to_numeric(s,errors='coerce').astype(float)
    d=s.diff(); g=d.clip(lower=0); l=-d.clip(upper=0)
    ag=g.ewm(alpha=1/period,adjust=False,min_periods=period).mean(); al=l.ewm(alpha=1/period,adjust=False,min_periods=period).mean()
    rs=ag/al.mask(al==0,np.nan)
    return (100-100/(1+rs)).fillna(100.0)

def macd(s):
    s=pd.to_numeric(s,errors='coerce').astype(float)
    m=s.ewm(span=12,adjust=False).mean()-s.ewm(span=26,adjust=False).mean(); sig=m.ewm(span=9,adjust=False).mean()
    return m,sig,m-sig

def normalize_bars(b):
    x=b.copy()
    tc='time' if 'time' in x.columns else ('ts' if 'ts' in x.columns else None)
    if tc is None: raise RuntimeError('BAR_TIME_COLUMN_MISSING')
    x['time']=pd.to_datetime(x[tc],utc=True,errors='coerce')
    for c in ('open','high','low','close','volume'): x[c]=pd.to_numeric(x[c],errors='coerce')
    return x.dropna(subset=['time','open','high','low','close']).sort_values('time').reset_index(drop=True)

def completed_5m(b1):
    q=b1.set_index('time')
    b5=q.resample('5min',label='left',closed='left').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna(subset=['open','high','low','close'])
    if b5.empty: return b5
    current_bucket=pd.Timestamp.now(tz='UTC').floor('5min')
    b5=b5[b5.index < current_bucket].copy()
    c=b5['close']; rr=rsi(c,14); m,s,h=macd(c); mid=c.rolling(20).mean(); sd=c.rolling(20).std(ddof=0)
    b5['rsi']=rr; b5['macd']=m; b5['sig']=s; b5['hist']=h; b5['mid']=mid; b5['iu']=mid+0.5*sd; b5['il']=mid-0.5*sd; b5['ou']=mid+3.0*sd; b5['iw']=b5['iu']-b5['il']
    b5['volavg']=b5['volume'].shift(1).rolling(20).mean(); b5['volratio']=b5['volume']/b5['volavg']
    return b5

def entry_signal(b1):
    b5=completed_5m(b1)
    if len(b5)<30 or len(b1)<30: return None
    a=b5.iloc[-2]; r=b5.iloc[-1]
    need=('rsi','macd','sig','hist','iu','iw','ou')
    if any(pd.isna(r[k]) for k in need) or any(pd.isna(a[k]) for k in ('rsi','macd','sig','hist','iu')): return None
    revent=any(float(a.rsi)<=lv<float(r.rsi) for lv in (30,50,70))
    mcross=float(a.macd)<=float(a.sig) and float(r.macd)>float(r.sig)
    mbull=float(r.macd)>float(r.sig) and float(r.hist)>=float(a.hist)
    inner=(float(a.close)<=float(a.iu) and float(r.close)>float(r.iu)) or float(r.close)>float(r.iu)
    c1=b1['close'].tail(41).reset_index(drop=True); rr=rsi(c1,14); m,s,h=macd(c1)
    timing=len(c1)>=29 and ((rr.iat[-1]>=rr.iat[-2]) or (h.iat[-1]>=h.iat[-2])) and (m.iat[-1]>s.iat[-1] or (m.iat[-2]<=s.iat[-2] and m.iat[-1]>s.iat[-1])) and not (rr.iat[-2]>=70>rr.iat[-1])
    if not (revent and (mcross or mbull) and inner and timing): return None
    score=sum([revent,float(r.rsi)>float(a.rsi),mcross or mbull,float(r.hist)>float(a.hist),inner,float(r.volratio if pd.notna(r.volratio) else 0)>=1.5])
    entry=float(b1.iloc[-1].close); risk=float(r.iw)
    if not (risk>0 and entry-risk>0): return None
    return {'entry':entry,'stop':entry-risk,'risk':risk,'outer_upper':float(r.ou),'inner_lower':float(r.il),'score':float(score),'rsi5':float(r.rsi),'macd5':float(r.macd),'sig5':float(r.sig),'volratio5':None if pd.isna(r.volratio) else float(r.volratio)}

def current_bands(b1):
    b5=completed_5m(b1)
    if b5.empty: return None
    r=b5.iloc[-1]
    if any(pd.isna(r[k]) for k in ('ou','il')): return None
    return {'outer_upper':float(r.ou),'inner_lower':float(r.il),'mid':float(r.mid) if pd.notna(r.mid) else None}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='/home/ubuntu/day-trader-api/daytrader.db'); ap.add_argument('--poll-sec',type=float,default=5.0); ap.add_argument('--heartbeat-sec',type=float,default=60.0); ap.add_argument('--log',default='/home/ubuntu/day-trader-api/shadow_v251.jsonl'); a=ap.parse_args()
    state=None; last_hb=0.0; log=Path(a.log); log.parent.mkdir(parents=True,exist_ok=True)
    print('=== V251 SOXL/SOXS V250 SHADOW ===',flush=True)
    print('ORDER=NONE FINDER=OFF ENTRY=V240D_ORIGINAL STOP=INNER_WIDTH CAP=1.00 PARTIAL=OUTER_UPPER_50 RUNNER_EXIT=INNER_LOWER_TOUCH',flush=True)
    while True:
        now=datetime.now(timezone.utc).isoformat()
        try:
            bars={s:normalize_bars(ticks_to_bars(load_ticks(a.db,s),1)) for s in SYMBOLS}
            event=None
            if state is None:
                cands=[]
                for s in SYMBOLS:
                    sig=entry_signal(bars[s])
                    if sig: cands.append((sig['score'],s,sig))
                if cands:
                    cands.sort(reverse=True); _,s,sig=cands[0]
                    state={'symbol':s,'entry':sig['entry'],'stop':sig['stop'],'partial':False,'partial_price':None,'opened_at':now}
                    event={'ts':now,'type':'ENTER','symbol':s,**sig}; print('SHADOW_ENTER',json.dumps(event,ensure_ascii=False),flush=True)
            else:
                s=state['symbol']; b=bars[s]; last=b.iloc[-1]; hi=float(last.high); lo=float(last.low); px=float(last.close); bd=current_bands(b)
                if lo<=state['stop']:
                    event={'ts':now,'type':'EXIT','reason':'INNER_WIDTH_STOP','symbol':s,'price':state['stop'],'state':state}; print('SHADOW_EXIT',json.dumps(event,ensure_ascii=False),flush=True); state=None
                elif (not state['partial']) and bd and hi>=bd['outer_upper']:
                    state['partial']=True; state['partial_price']=bd['outer_upper']; event={'ts':now,'type':'PARTIAL','reason':'OUTER_UPPER_TOUCH','symbol':s,'price':bd['outer_upper'],'remaining':0.5}; print('SHADOW_PARTIAL',json.dumps(event,ensure_ascii=False),flush=True)
                elif state['partial'] and bd and lo<=bd['inner_lower']:
                    event={'ts':now,'type':'EXIT','reason':'INNER_LOWER_TOUCH','symbol':s,'price':bd['inner_lower'],'state':state}; print('SHADOW_EXIT',json.dumps(event,ensure_ascii=False),flush=True); state=None
            if time.time()-last_hb>=a.heartbeat_sec:
                snap={'ts':now,'type':'HEARTBEAT','state':state,'last':{s:float(bars[s].iloc[-1].close) if not bars[s].empty else None for s in SYMBOLS}}
                print('HEARTBEAT',json.dumps(snap,ensure_ascii=False),flush=True); event=event or snap; last_hb=time.time()
            if event:
                with log.open('a',encoding='utf-8') as f: f.write(json.dumps(event,ensure_ascii=False,default=str)+'\n')
        except KeyboardInterrupt:
            print('STOPPED',flush=True); break
        except Exception as e:
            print('ERROR',json.dumps({'ts':now,'error':type(e).__name__,'detail':str(e)},ensure_ascii=False),flush=True)
        time.sleep(max(1.0,a.poll_sec))
if __name__=='__main__': main()
