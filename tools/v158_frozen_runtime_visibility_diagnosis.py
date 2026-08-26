#!/usr/bin/env python3
from pathlib import Path
import json, urllib.request, urllib.error, time, re, subprocess

BASE='http://127.0.0.1:8000'
ENG=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
print('=== V158 FROZEN RUNTIME VISIBILITY DIAGNOSIS ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')

def get(path,timeout=5):
    try:
        t=time.time()
        with urllib.request.urlopen(BASE+path,timeout=timeout) as r:
            raw=r.read().decode('utf-8','replace')
            try: data=json.loads(raw)
            except Exception: data=raw
            return r.status,round(time.time()-t,3),None,data
    except Exception as e:
        return None,round(time.time()-t,3),str(e),None

for p in ['/api/v4/runtime-mode','/api/v4/USA/status','/api/v4/USA/finder','/api/v4/USA/tracker']:
    code,sec,err,data=get(p,8)
    print('\nENDPOINT',p,'HTTP=',code,'SEC=',sec,'ERR=',err)
    if isinstance(data,dict):
        print('TOP_KEYS=',sorted(data.keys()))
        for k in ['session','market','updated_at','rows','tracker','finder','top','data']:
            if k in data:
                v=data[k]
                if isinstance(v,list): print(k,'LIST_LEN=',len(v))
                elif isinstance(v,dict): print(k,'DICT_KEYS=',sorted(v.keys())[:40])
                else: print(k,'=',v)
        txt=json.dumps(data,ensure_ascii=False)
        print('HAS_williams_frozen_eval=', 'williams_frozen_eval' in txt)
        print('HAS_williams_frozen_ctx=', 'williams_frozen_ctx' in txt)
        if 'williams_frozen_eval' in txt:
            hits=[]
            def walk(x,path='root'):
                if isinstance(x,dict):
                    if 'williams_frozen_eval' in x:
                        hits.append((path,x.get('symbol'),x.get('williams_frozen_eval')))
                    for kk,vv in x.items(): walk(vv,path+'.'+str(kk))
                elif isinstance(x,list):
                    for i,vv in enumerate(x): walk(vv,f'{path}[{i}]')
            walk(data)
            print('EVAL_HITS=',len(hits))
            for h in hits[:10]: print('EVAL',h)
    elif data is not None:
        print('BODY=',str(data)[:1000])

S=ENG.read_text(errors='ignore')
A=API.read_text(errors='ignore')
print('\n=== ENGINE MARKER LOCATIONS ===')
for pat in ['williams_frozen_ctx','williams_frozen_eval','_paper_williams_step','refresh_usa_tracker','build_usa_finder']:
    print(pat,[i+1 for i,l in enumerate(S.splitlines()) if pat in l][:20])
print('\n=== API USA STATUS/FINDER/TRACKER ROUTE LOCATIONS ===')
for pat in ["@app.get('/api/v4/{market}/status')","@app.get('/api/v4/{market}/finder')","@app.get('/api/v4/{market}/tracker')"]:
    print(pat,[i+1 for i,l in enumerate(A.splitlines()) if pat in l][:10])

print('\n=== RECENT FROZEN JOURNAL ===')
try:
    out=subprocess.run(['journalctl','-u','day-trader-api.service','--since','-15 min','--no-pager'],capture_output=True,text=True,timeout=8).stdout
    lines=[x for x in out.splitlines() if re.search(r'williams|frozen|V4 light tracker|USA',x,re.I)]
    for x in lines[-80:]: print(x)
except Exception as e: print('JOURNAL_ERR=',e)

print('\nNEXT=USE_OUTPUT_TO_TRACE_VISIBILITY_GAP_ONLY; DO_NOT_CHANGE_FROZEN_STRATEGY')
