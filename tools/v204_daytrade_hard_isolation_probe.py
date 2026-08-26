#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, urllib.request, json, re

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
KIO=Path('/home/ubuntu/day-trader-api/live_server/kiwoom.py')
BASE='http://127.0.0.1:8000'
print('=== V204 DAYTRADE HARD ISOLATION PROBE ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('PURPOSE=IDENTIFY_AND_STOP_ONLY_NON_FROZEN_BACKGROUND_CPU_WORK_DURING_DAYTRADE')
for p in (API,KIO):
    if not p.exists(): raise SystemExit(f'NOT_FOUND {p}')
    shutil.copy2(p,Path(str(p)+'.bak_v204'))

src=API.read_text(errors='ignore')
print('API_LEN=',len(src))
# Runtime source diagnostics: enumerate startup task creation and heavy loop defs.
for pat in ['create_task(', 'tasks.append(', 'minute recovery', 'bridge warmup', 'tracker warmup', 'v4_engine_forever', 'websocket_forever']:
    hits=[]
    st=0
    while True:
        p=src.find(pat,st)
        if p<0: break
        ln=src.count('\n',0,p)+1
        hits.append((ln,src[p:p+180].split('\n')[0]))
        st=p+1
    print('HITS',pat,hits[:30])

# V203 guard only pauses v4_engine_forever after it starts. Other startup/background
# coroutines can still consume CPU. For this probe, DAYTRADE startup launches only the
# WebSocket/data authority plus frozen paper loop; NORMAL remains untouched.
# We patch task append/create sites whose call text clearly names legacy warm/recovery/tracker/finder.
legacy_tokens=('warm','recovery','tracker','finder','bridge','discovery')
lines=src.splitlines(True)
out=[]; changed=[]
for i,line in enumerate(lines,1):
    low=line.lower()
    if ('create_task(' in low or 'tasks.append(' in low) and any(t in low for t in legacy_tokens):
        indent=line[:len(line)-len(line.lstrip())]
        # preserve original line under NORMAL only
        out.append(indent+"if _runtime_profile().get('mode')!='DAYTRADE':  # V204_DAYTRADE_HARD_ISOLATION\n")
        out.append('    '+line)
        changed.append((i,line.strip()))
    else:
        out.append(line)
src2=''.join(out)
print('PATCHED_LEGACY_TASK_SITES=',changed)
# If static names are indirect and nothing matched, do not guess-mutate runtime.
if not changed:
    print('NO_SAFE_STATIC_TASK_SITE_MATCH; READONLY_DIAG_ONLY=YES')
else:
    API.write_text(src2)
    r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(API)],capture_output=True,text=True)
    print('PY_COMPILE_RC=',r.returncode)
    if r.stderr.strip(): print(r.stderr.strip())
    if r.returncode:
        shutil.copy2(Path(str(API)+'.bak_v204'),API)
        raise SystemExit('COMPILE_FAIL_RESTORED')
    r=subprocess.run(['sudo','systemctl','restart','day-trader-api.service'],capture_output=True,text=True)
    print('RESTART_RC=',r.returncode)

def get(path,timeout=3):
    t=time.time()
    try:
        with urllib.request.urlopen(BASE+path,timeout=timeout) as f:
            raw=f.read().decode(errors='ignore')
            try: body=json.loads(raw)
            except Exception: body=raw
            return True,f.status,time.time()-t,body
    except Exception as e: return False,0,time.time()-t,repr(e)

ready=False
for i in range(1,31):
    ok,code,sec,body=get('/api/v4/runtime-mode',3)
    print('READY',i,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if ok and code==200:
        print('RUNTIME_MODE=',body); ready=True; break
    time.sleep(2)

p=subprocess.run("ps -eo pid,ppid,pcpu,pmem,stat,etime,cmd --sort=-pcpu | head -10",shell=True,capture_output=True,text=True)
print('=== PROCESS TOP ==='); print(p.stdout)
if ready:
    for ep in ('/api/v4/USA/status','/api/v4/USA/frozen-paper'):
        ok,code,sec,body=get(ep,10)
        print('ENDPOINT',ep,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
        if isinstance(body,dict) and ep.endswith('frozen-paper'):
            rows=body.get('rows') or []
            print('FROZEN_ROWS=',len(rows),'EVAL=',body.get('evaluations'),'ERRORS=',body.get('errors'),'PAPER_EVENTS=',body.get('paper_events'))
            print('FROZEN_SYMBOLS=',[x.get('symbol') for x in rows])
        elif not isinstance(body,dict): print('BODY=',body)

j=subprocess.run(['journalctl','-u','day-trader-api.service','-n','100','--no-pager'],capture_output=True,text=True)
text=j.stdout
print('LEGACY_ACTIVITY_COUNTS=',{k:text.lower().count(k) for k in ['minute recovery','tracker warmup','bridge warmup']})
print('=== JOURNAL_TAIL ==='); print(text[-9000:])
print('NEXT=IF_NO_SAFE_STATIC_MATCH_USE_OUTPUT_HITS_TO_PATCH_EXACT_STARTUP_TASKS; IF_READY_AND_FROZEN19_ROWS_LEAVE_RUNNING_PAPER_NOW')
