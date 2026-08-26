#!/usr/bin/env python3
import json,time,urllib.request,subprocess
from datetime import datetime,timezone,timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

BASE='http://127.0.0.1:8000'
LOG=Path('/home/ubuntu/day-trader-api/v219_paper_watch.jsonl')
ET=ZoneInfo('America/New_York')
print('=== V219 RUN FROZEN19 PAPER THROUGH US CLOSE ===')
print('READ_ONLY_WATCH=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')
print('LOG=',LOG)

def get(path,timeout=5):
    t=time.time()
    try:
        with urllib.request.urlopen(BASE+path,timeout=timeout) as f:
            raw=f.read().decode(errors='ignore')
            try: body=json.loads(raw)
            except Exception: body={'raw':raw}
            return True,f.status,time.time()-t,body
    except Exception as e:
        return False,0,time.time()-t,{'error':repr(e)}

def cpu_line():
    p=subprocess.run("ps -eo pid,pcpu,pmem,etime,cmd | grep '[u]vicorn live_server.api:app'",shell=True,capture_output=True,text=True)
    return p.stdout.strip()

now_et=datetime.now(timezone.utc).astimezone(ET)
close_et=now_et.replace(hour=16,minute=0,second=0,microsecond=0)
if now_et>=close_et:
    close_et=now_et
stop_et=close_et+timedelta(minutes=3)
print('NOW_ET=',now_et.isoformat())
print('WATCH_UNTIL_ET=',stop_et.isoformat())

seen_events=0
seen_signals=set()
samples=0
while True:
    now_et=datetime.now(timezone.utc).astimezone(ET)
    ok,code,lat,fp=get('/api/v4/USA/frozen-paper',5)
    ok2,code2,lat2,st=get('/api/v4/USA/status',5)
    rows=(fp.get('rows') or []) if isinstance(fp,dict) else []
    evals=int(fp.get('evaluations') or 0) if isinstance(fp,dict) else 0
    errors=int(fp.get('errors') or 0) if isinstance(fp,dict) else 0
    pe=int(fp.get('paper_events') or 0) if isinstance(fp,dict) else 0
    sig=[]
    for r in rows:
        sym=r.get('symbol')
        if r.get('entry') or r.get('exit') or r.get('paper_event'):
            key=(sym,str(r.get('bar')),bool(r.get('entry')),bool(r.get('exit')),bool(r.get('paper_event')))
            if key not in seen_signals:
                seen_signals.add(key)
                sig.append({'symbol':sym,'bar':r.get('bar'),'entry':bool(r.get('entry')),'exit':bool(r.get('exit')),'paper_event':bool(r.get('paper_event')),'reason':r.get('eval_reason')})
    pos=[]
    if isinstance(st,dict):
        pos=st.get('paper_positions') or st.get('positions') or []
    rec={'ts_utc':datetime.now(timezone.utc).isoformat(),'ts_et':now_et.isoformat(),
         'frozen_http':code,'frozen_lat':round(lat,3),'status_http':code2,'status_lat':round(lat2,3),
         'rows':len(rows),'eval':evals,'errors':errors,'paper_events':pe,'new_signals':sig,'positions':pos}
    LOG.parent.mkdir(parents=True,exist_ok=True)
    with LOG.open('a',encoding='utf-8') as f: f.write(json.dumps(rec,ensure_ascii=False,default=str)+'\n')
    samples+=1
    if pe!=seen_events or sig or samples%5==1:
        print('ET',now_et.strftime('%H:%M:%S'),'ROWS',len(rows),'EVAL',evals,'ERR',errors,'PAPER_EVENTS',pe,
              'LAT',round(lat,3),'STATUS_LAT',round(lat2,3),'NEW',sig,'POS',len(pos))
        if sig:
            for x in sig: print('SIGNAL',x)
        if pe!=seen_events: print('PAPER_EVENT_COUNT_CHANGED',seen_events,'->',pe)
        seen_events=pe
    if now_et>=stop_et:
        break
    time.sleep(60)

print('=== V219 CLOSE SUMMARY ===')
print('SAMPLES=',samples)
print('UNIQUE_SIGNAL_EVENTS=',len(seen_signals))
print('FINAL_PAPER_EVENTS=',seen_events)
print('CPU=',cpu_line())
print('LOG=',LOG)
print('V219_WATCH_COMPLETE=True')
