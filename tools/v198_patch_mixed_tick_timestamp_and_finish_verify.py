#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, json, urllib.request

A=Path('/home/ubuntu/day-trader-api/live_server/analytics.py')
API='http://127.0.0.1:8000'
print('=== V198 PATCH MIXED FE TICK TIMESTAMP + FINISH VERIFY ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('PURPOSE=FIX_FE_MIXED_ISO_TIMESTAMP_PARSE_AND_FINISH_FROZEN19_RUNTIME_VERIFY')
if not A.exists(): raise SystemExit('ANALYTICS_NOT_FOUND')
src=A.read_text(errors='ignore')
bak=Path(str(A)+'.bak_v198')
shutil.copy2(A,bak); print('BACKUP',bak)
old="df=pd.DataFrame(ticks); df['ts']=pd.to_datetime(df['ts'], utc=True); df['price']=pd.to_numeric(df['price'], errors='coerce')"
new="df=pd.DataFrame(ticks); df['ts']=pd.to_datetime(df['ts'], utc=True, format='mixed'); df['price']=pd.to_numeric(df['price'], errors='coerce')"
print('TARGET_COUNT=',src.count(old))
if old not in src:
    if "pd.to_datetime(df['ts'], utc=True, format='mixed')" in src:
        print('ALREADY_PATCHED=YES')
    else:
        raise SystemExit('TIMESTAMP_PARSE_TARGET_NOT_FOUND')
else:
    src=src.replace(old,new,1)
    A.write_text(src)

r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(A)])
print('PY_COMPILE=', 'PASS' if r.returncode==0 else 'FAIL')
if r.returncode: raise SystemExit(2)

# Direct mixed-format regression test using runtime venv/import path.
code="""import sys;sys.path.insert(0,'/home/ubuntu/day-trader-api');from live_server.analytics import ticks_to_bars\nt=[{'ts':'2026-08-26T16:31:55+00:00','price':100,'qty':1,'cum_volume':10},{'ts':'2026-08-26T16:32:01.123456+00:00','price':101,'qty':2,'cum_volume':12}]\nb=ticks_to_bars(t,1);print('MIXED_PARSE_BARS',len(b),b[['time','close']].to_dict('records'))"""
r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-c',code],capture_output=True,text=True,timeout=20)
print('MIXED_PARSE_RC=',r.returncode)
print((r.stdout or '').strip())
if r.stderr: print('MIXED_PARSE_ERR=',r.stderr.strip()[-1000:])
if r.returncode: raise SystemExit('MIXED_PARSE_REGRESSION_FAIL')

r=subprocess.run(['sudo','systemctl','restart','day-trader-api.service'])
print('RESTART_RC=',r.returncode)

def get(path,timeout=5):
    with urllib.request.urlopen(API+path,timeout=timeout) as f:
        return f.status,json.loads(f.read().decode())

# Startup has repeatedly needed ~25-35s on this host. Wait up to 70s.
ready=False
for i in range(1,71):
    try:
        code,d=get('/api/v4/runtime-mode',timeout=2)
        if code==200:
            ready=True; print('API_READY_PROBE=',i,'MODE=',d.get('mode')); break
    except Exception:
        time.sleep(1)
if not ready: raise SystemExit('API_NOT_READY_AFTER_70S')

# Ensure DAYTRADE; restart default should already persist, but restore explicitly if necessary.
code,mode=get('/api/v4/runtime-mode',timeout=5)
if str(mode.get('mode','')).upper()!='DAYTRADE':
    req=urllib.request.Request(API+'/api/v4/runtime-mode/DAYTRADE',data=b'{}',headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(req,timeout=5) as f: print('SET_DAYTRADE_HTTP=',f.status)
    time.sleep(2)
code,mode=get('/api/v4/runtime-mode',timeout=5)
print('RUNTIME_MODE=',mode)

# Give completed-minute loop one boundary opportunity without blocking for long.
time.sleep(8)
for attempt in range(1,4):
    t0=time.time()
    try:
        code,d=get('/api/v4/USA/frozen-paper',timeout=12)
        sec=round(time.time()-t0,3)
        rows=d.get('rows') or []
        print('FROZEN_HTTP=',code,'SEC=',sec,'ATTEMPT=',attempt)
        print('ROWS=',len(rows),'EVALUATIONS=',d.get('evaluations'),'ERRORS=',d.get('errors'),'PAPER_EVENTS=',d.get('paper_events'),'UPDATED_AT=',d.get('updated_at'))
        good_ctx=0; current_rows=0; error_rows=[]
        for x in rows:
            sym=x.get('symbol'); ctx=bool(x.get('ctx')); reason=x.get('eval_reason'); bar=x.get('bar'); ticks=x.get('ticks')
            if ctx: good_ctx+=1
            if bar: current_rows+=1
            if reason=='ERROR' or x.get('error'): error_rows.append((sym,x.get('error') or reason))
            print('ROW',sym,'BAR',bar,'CTX',ctx,'REASON',reason,'TICKS',ticks,'ENTRY',x.get('entry'),'EXIT',x.get('exit'),'PAPER_EVENT',x.get('paper_event'))
        print('CTX_COUNT=',good_ctx,'BAR_PRESENT_COUNT=',current_rows,'ERROR_ROWS=',error_rows)
        if len(rows)==19 and not error_rows and current_rows>=18:
            print('V198_PASS=True')
            print('USA_PAPER_RUNTIME_READY=True')
            print('NEXT=LEAVE_RUNNING_AND_OBSERVE_PAPER_EVENTS')
            raise SystemExit(0)
    except SystemExit: raise
    except Exception as e:
        print('FROZEN_ATTEMPT_ERROR',attempt,repr(e))
    time.sleep(10)

print('V198_PASS=False')
print('NEXT=USE_THIS_OUTPUT_TO_PATCH_ONLY_REMAINING_FROZEN_LOOP_DEFECT')
raise SystemExit(3)
