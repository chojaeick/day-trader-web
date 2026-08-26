#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, urllib.request, json, re
API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
KIO=Path('/home/ubuntu/day-trader-api/live_server/kiwoom.py')
DBP=Path('/home/ubuntu/day-trader-api/live_server/db.py')
BASE='http://127.0.0.1:8000'
print('=== V212 BATCH FE TICK WRITES + RESTORE FROZEN + VERIFY ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('PURPOSE=REDUCE_FE_SQLITE_WRITE_AMPLIFICATION; RESTORE_FROZEN19_EVALUATOR')
for p in (API,KIO,DBP):
    if not p.exists(): raise SystemExit(f'NOT_FOUND {p}')
    shutil.copy2(p,Path(str(p)+'.bak_v212'))

api=API.read_text(errors='ignore')
kio=KIO.read_text(errors='ignore')
dbs=DBP.read_text(errors='ignore')

# Restore V211-disabled frozen task line if present.
restored=0
lines=api.splitlines(True)
out=[]
for line in lines:
    if 'V211_AB_DISABLED' in line and 'frozen_usa_paper_forever' in line:
        indent=line[:len(line)-len(line.lstrip())]
        out.append(indent+'asyncio.create_task(frozen_usa_paper_forever()),\n')
        restored+=1
    else:
        out.append(line)
api=''.join(out)
print('FROZEN_TASK_RESTORED_COUNT=',restored)

# Locate existing per-tick DB write call(s) in Kiwoom websocket path.
for token in ['add_tick(', '.add_tick(', 'db.add_tick']:
    hits=[]; st=0
    while True:
        p=kio.find(token,st)
        if p<0: break
        hits.append((kio.count('\n',0,p)+1,kio[p:p+180].split('\n')[0]))
        st=p+1
    print('HITS',token,hits[:20])

# Add lightweight in-memory queue helpers to KiwoomClient, using existing DB.add_tick in batched thread flush.
init_anchor='        self.discovery = {'
if '_v212_tick_buffer' not in kio:
    p=kio.find(init_anchor)
    if p<0: raise SystemExit('INIT_ANCHOR_NOT_FOUND')
    # insert before discovery assignment, same indent
    insert=("        self._v212_tick_buffer=[]\n"
            "        self._v212_tick_flush_task=None\n"
            "        self._v212_tick_flush_interval=0.25\n"
            "        self._v212_tick_flush_max=500\n")
    kio=kio[:p]+insert+kio[p:]
    print('BUFFER_FIELDS_INSERTED=True')
else:
    print('BUFFER_FIELDS_ALREADY_PRESENT=True')

# Add async batch flush methods before _extract_f5, if absent.
marker='    def _extract_f5(self, msg: dict):\n'
if 'async def _v212_flush_tick_buffer' not in kio:
    p=kio.find(marker)
    if p<0: raise SystemExit('EXTRACT_MARKER_NOT_FOUND')
    methods='''    async def _v212_flush_tick_buffer(self):
        while True:
            await asyncio.sleep(self._v212_tick_flush_interval)
            if not self._v212_tick_buffer:
                continue
            batch=self._v212_tick_buffer[:self._v212_tick_flush_max]
            del self._v212_tick_buffer[:len(batch)]
            def _flush(rows):
                for sym,price,qty,cumvol,ts in rows:
                    try:
                        self.db.add_tick(sym,price,qty,cumvol,ts)
                    except TypeError:
                        try: self.db.add_tick(sym,price,qty,cumvol)
                        except Exception: pass
                    except Exception:
                        pass
            await asyncio.to_thread(_flush,batch)

    def _v212_queue_tick(self,sym,price,qty,cumvol,ts=None):
        self._v212_tick_buffer.append((sym,price,qty,cumvol,ts))
        if len(self._v212_tick_buffer)>5000:
            del self._v212_tick_buffer[:-5000]

'''
    kio=kio[:p]+methods+kio[p:]
    print('BATCH_METHODS_INSERTED=True')
else:
    print('BATCH_METHODS_ALREADY_PRESENT=True')

# Patch only obvious websocket per-message add_tick calls to queue. Avoid touching backfill/history code.
patched=0
klines=kio.splitlines(True)
out=[]
for i,line in enumerate(klines):
    low=line.replace(' ','')
    if 'self.db.add_tick(' in line and i>0:
        context=''.join(klines[max(0,i-8):i+3])
        if ('_extract_f5' in context or 'for symbol,price,qty,cumvol' in context or 'websocket' in context.lower()):
            indent=line[:len(line)-len(line.lstrip())]
            # extract args conservatively if standard symbol,price,qty,cumvol form
            m=re.search(r'self\.db\.add_tick\(([^\n]+)\)',line)
            if m:
                args=m.group(1).strip()
                parts=[x.strip() for x in args.split(',')]
                if len(parts)>=4:
                    ts=parts[4] if len(parts)>=5 else 'None'
                    out.append(indent+f'self._v212_queue_tick({parts[0]},{parts[1]},{parts[2]},{parts[3]},{ts})  # V212_FE_BATCH\n')
                    patched+=1
                    continue
    out.append(line)
kio=''.join(out)
print('WEBSOCKET_ADD_TICK_PATCHED=',patched)

# Ensure batch flusher task starts from websocket_forever once.
ws_marker='    async def websocket_forever(self):\n'
if 'V212_START_FLUSHER' not in kio:
    p=kio.find(ws_marker)
    if p<0: raise SystemExit('WS_MARKER_NOT_FOUND')
    body=p+len(ws_marker)
    guard=("        # V212_START_FLUSHER\n"
           "        if self._v212_tick_flush_task is None or self._v212_tick_flush_task.done():\n"
           "            self._v212_tick_flush_task=asyncio.create_task(self._v212_flush_tick_buffer())\n")
    kio=kio[:body]+guard+kio[body:]
    print('FLUSHER_START_INSERTED=True')
else:
    print('FLUSHER_START_ALREADY_PRESENT=True')

if patched<1:
    print('NO_SAFE_WEBSOCKET_ADD_TICK_PATCH; RESTORING_ALL')
    shutil.copy2(Path(str(API)+'.bak_v212'),API)
    shutil.copy2(Path(str(KIO)+'.bak_v212'),KIO)
    raise SystemExit('NO_SAFE_PATCH')

API.write_text(api); KIO.write_text(kio)
for p in (API,KIO):
    r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(p)],capture_output=True,text=True)
    print('COMPILE',p.name,'RC=',r.returncode)
    if r.stderr.strip(): print(r.stderr.strip())
    if r.returncode:
        shutil.copy2(Path(str(API)+'.bak_v212'),API); shutil.copy2(Path(str(KIO)+'.bak_v212'),KIO)
        raise SystemExit('COMPILE_FAIL_RESTORED')

r=subprocess.run(['sudo','systemctl','restart','day-trader-api.service'],capture_output=True,text=True)
print('RESTART_RC=',r.returncode)

def get(path,timeout=4):
    t=time.time()
    try:
        with urllib.request.urlopen(BASE+path,timeout=timeout) as f:
            raw=f.read().decode(errors='ignore')
            try: body=json.loads(raw)
            except Exception: body=raw
            return True,f.status,time.time()-t,body
    except Exception as e:return False,0,time.time()-t,repr(e)

ready=False
for i in range(1,41):
    ok,code,sec,body=get('/api/v4/runtime-mode',4)
    print('READY',i,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if ok and code==200:
        print('RUNTIME_MODE=',body); ready=True; break
    time.sleep(2)

lat=[]
if ready:
    for i in range(5):
        ok,code,sec,body=get('/api/v4/runtime-mode',4)
        lat.append(sec if ok else 99.0)
        print('RUNTIME_REPEAT',i+1,'SEC=',round(sec,3),'OK=',ok)
        time.sleep(1)

frozen_ok=False; rowsn=barsn=ctxn=0
ok,code,sec,body=get('/api/v4/USA/frozen-paper',12)
print('FROZEN_ENDPOINT OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
if isinstance(body,dict):
    rows=body.get('rows') or []
    rowsn=len(rows); barsn=sum(1 for x in rows if x.get('bar')); ctxn=sum(1 for x in rows if x.get('ctx'))
    print('FROZEN_ROWS=',rowsn,'BAR_COUNT=',barsn,'CTX_COUNT=',ctxn,'EVAL=',body.get('evaluations'),'ERRORS=',body.get('errors'),'PAPER_EVENTS=',body.get('paper_events'))
    frozen_ok=ok and code==200 and rowsn==19
else: print('BODY=',body)

p=subprocess.run("ps -eo pid,ppid,pcpu,pmem,stat,etime,cmd --sort=-pcpu | head -10",shell=True,capture_output=True,text=True)
print('=== PROCESS TOP ==='); print(p.stdout)
fast=bool(lat and max(lat)<2.0)
print('RUNTIME_MODE_FAST=',fast)
print('V212_PASS=',bool(ready and fast and frozen_ok))
print('USA_PAPER_RUNTIME_READY=',bool(ready and fast and frozen_ok and barsn>0))
print('NEXT=IF_PASS_LEAVE_RUNNING_PAPER; IF_FAIL_SEND_OUTPUT_ONLY')
