#!/usr/bin/env python3
from pathlib import Path
import subprocess, shutil, time, urllib.request, json, re
API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
print('=== V216 FIX FROZEN19 NO_CTX + VERIFY ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
if not API.exists(): raise SystemExit('API_NOT_FOUND')
src=API.read_text(errors='ignore')
bak=Path(str(API)+'.bak_v216'); shutil.copy2(API,bak); print('BACKUP',bak)
# Locate frozen loop and helper call exactly.
pos=src.find('async def frozen_usa_paper_forever():')
if pos<0: raise SystemExit('FROZEN_LOOP_NOT_FOUND')
end=src.find('\nasync def ',pos+10)
loop=src[pos:end if end>0 else len(src)]
print('LOOP_LEN=',len(loop))
print('WIRE_CALL_COUNT=',loop.count('_v161_wire_usa_frozen_ctx'))
# Make NO_CTX visible with minimal detail, no logic change: expose missing ctx keys and bar count.
old="""                    rec['ctx']=bool(isinstance(ctx,dict) and ctx.get('entry_args'))
                    paper_result=v4._paper_williams_step('USA',row)
                    ev=row.get('williams_frozen_eval') or {}
"""
new="""                    rec['ctx']=bool(isinstance(ctx,dict) and ctx.get('entry_args'))
                    if isinstance(ctx,dict):
                        rec['ctx_keys']=sorted(list(ctx.keys()))[:30]
                        rec['ctx_missing']=[k for k in ('entry_args','exit_args') if not ctx.get(k)]
                    try: rec['bars']=int(len(bars))
                    except Exception: rec['bars']=0
                    paper_result=v4._paper_williams_step('USA',row)
                    ev=row.get('williams_frozen_eval') or {}
"""
if old in src and 'ctx_missing' not in loop:
    src=src.replace(old,new,1)
    print('TELEMETRY_PATCHED=1')
else:
    print('TELEMETRY_PATCHED=0')
API.write_text(src)
r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(API)],capture_output=True,text=True)
print('PY_COMPILE_RC=',r.returncode)
if r.stderr.strip(): print(r.stderr.strip())
if r.returncode:
    shutil.copy2(bak,API); raise SystemExit('COMPILE_FAIL_RESTORED')
r=subprocess.run(['sudo','systemctl','restart','day-trader-api.service'],capture_output=True,text=True)
print('RESTART_RC=',r.returncode)
BASE='http://127.0.0.1:8000'
def get(path,t=5):
    try:
        with urllib.request.urlopen(BASE+path,timeout=t) as f:
            raw=f.read().decode(errors='ignore')
            try:return True,f.status,json.loads(raw)
            except:return True,f.status,raw
    except Exception as e:return False,0,repr(e)
for i in range(1,31):
    ok,code,body=get('/api/v4/runtime-mode',3)
    print('READY',i,'OK=',ok,'HTTP=',code)
    if ok and code==200: break
    time.sleep(2)
time.sleep(35)
ok,code,body=get('/api/v4/USA/frozen-paper',8)
print('FROZEN_HTTP=',code,'OK=',ok)
if isinstance(body,dict):
    rows=body.get('rows') or []
    print('ROWS=',len(rows),'EVAL=',body.get('evaluations'),'ERRORS=',body.get('errors'))
    for r in rows:
        print('ROW',r.get('symbol'),'REASON=',r.get('eval_reason'),'CTX=',r.get('ctx'),'BARS=',r.get('bars'),'MISSING=',r.get('ctx_missing'),'KEYS=',r.get('ctx_keys'))
# Print exact runtime helper body for one-shot diagnosis.
eng=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py').read_text(errors='ignore')
p=eng.find('def _v161_wire_usa_frozen_ctx')
print('CTX_HELPER_POS=',p)
if p>=0: print(eng[p:p+7000])
print('NEXT=PATCH_EXACT_CTX_MISSING_ONLY; DO_NOT_TOUCH_STRATEGY')
