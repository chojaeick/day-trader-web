#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, urllib.request, json, re, sys

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
KIO=Path('/home/ubuntu/day-trader-api/live_server/kiwoom.py')
DBP=Path('/home/ubuntu/day-trader-api/live_server/db.py')
BASE='http://127.0.0.1:8000'
print('=== V213B TRUE SQLITE BATCH + RAW SAMPLING + RESTORE FROZEN FIXED ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('PURPOSE=FIX_V213_HELPER_SYNTAX; APPLY_REAL_BATCH_SQLITE_AND_RESTORE_FROZEN')

for p in (API,KIO,DBP):
    if not p.exists(): raise SystemExit(f'NOT_FOUND {p}')
    shutil.copy2(p,Path(str(p)+'.bak_v213b'))

api=API.read_text(errors='ignore')
kio=KIO.read_text(errors='ignore')
dbs=DBP.read_text(errors='ignore')

# 1) Restore the V211 A/B-disabled frozen evaluator startup task.
lines=api.splitlines(True)
out=[]
restored=0
for line in lines:
    if 'V211_AB: frozen evaluator task disabled' in line:
        indent=line[:len(line)-len(line.lstrip())]
        out.append(indent+'asyncio.create_task(frozen_usa_paper_forever()),  # V213B_RESTORED_FROZEN\n')
        restored+=1
    else:
        out.append(line)
api=''.join(out)
refs=[(i,l.strip()) for i,l in enumerate(api.splitlines(),1) if 'frozen_usa_paper_forever()' in l and not l.lstrip().startswith('async def ')]
print('FROZEN_RESTORED=',restored,'STARTUP_REFS=',refs[:10])
if not refs:
    raise SystemExit('NO_FROZEN_STARTUP_REF')

# 2) Add one real SQLite batch method. Use normal quoted strings only; no nested triple quotes.
if 'def add_ticks_batch(' not in dbs:
    marker='    def add_raw(self, payload: str, ts: str):\n'
    p=dbs.find(marker)
    if p<0: raise SystemExit('DB_MARKER_NOT_FOUND')
    method=(
"    def add_ticks_batch(self, rows):\n"
"        if not rows:\n"
"            return 0\n"
"        clean=[]\n"
"        syms=set()\n"
"        for row in rows:\n"
"            if len(row)<5:\n"
"                continue\n"
"            symbol,price,qty,cum_volume,ts=row[:5]\n"
"            if not symbol or ts is None:\n"
"                continue\n"
"            sym=str(symbol).upper()\n"
"            clean.append((sym,float(price),float(qty or 0),float(cum_volume or 0),str(ts)))\n"
"            syms.add(sym)\n"
"        if not clean:\n"
"            return 0\n"
"        with self.conn() as c:\n"
"            c.executemany('INSERT INTO ticks(symbol,price,qty,cum_volume,ts) VALUES(?,?,?,?,?)',clean)\n"
"            for symbol in syms:\n"
"                c.execute('DELETE FROM ticks WHERE id IN (SELECT id FROM ticks WHERE symbol=? ORDER BY id DESC LIMIT -1 OFFSET 250000)',(symbol,))\n"
"        return len(clean)\n\n"
    )
    dbs=dbs[:p]+method+dbs[p:]
    print('DB_BATCH_METHOD_INSERTED=True')
else:
    print('DB_BATCH_METHOD_ALREADY_PRESENT=True')

# 3) Ensure buffer fields/methods exist, then force flusher to call DB.add_ticks_batch once per batch.
if '_v212_tick_buffer' not in kio:
    anchor='        self.discovery = {'
    p=kio.find(anchor)
    if p<0: raise SystemExit('INIT_ANCHOR_NOT_FOUND')
    fields=("        self._v212_tick_buffer=[]\n"
            "        self._v212_tick_flush_task=None\n"
            "        self._v212_tick_flush_interval=0.25\n"
            "        self._v212_tick_flush_max=500\n")
    kio=kio[:p]+fields+kio[p:]
    print('BUFFER_FIELDS_INSERTED=True')
else:
    print('BUFFER_FIELDS_PRESENT=True')

extract_marker='    def _extract_f5(self, msg: dict):\n'
if 'async def _v212_flush_tick_buffer' not in kio:
    p=kio.find(extract_marker)
    if p<0: raise SystemExit('EXTRACT_MARKER_NOT_FOUND')
    methods=(
"    async def _v212_flush_tick_buffer(self):\n"
"        while True:\n"
"            await asyncio.sleep(self._v212_tick_flush_interval)\n"
"            if not self._v212_tick_buffer:\n"
"                continue\n"
"            batch=self._v212_tick_buffer[:self._v212_tick_flush_max]\n"
"            del self._v212_tick_buffer[:len(batch)]\n"
"            try:\n"
"                await asyncio.to_thread(self.db.add_ticks_batch,batch)\n"
"            except Exception as e:\n"
"                log.warning('V213B FE batch flush failed rows=%s err=%s',len(batch),e)\n\n"
"    def _v212_queue_tick(self,sym,price,qty,cumvol,ts=None):\n"
"        self._v212_tick_buffer.append((sym,price,qty,cumvol,ts))\n"
"        if len(self._v212_tick_buffer)>5000:\n"
"            del self._v212_tick_buffer[:-5000]\n\n"
    )
    kio=kio[:p]+methods+kio[p:]
    print('BATCH_METHODS_INSERTED=True')
else:
    s=kio.find('    async def _v212_flush_tick_buffer(self):\n')
    e=kio.find('    def _v212_queue_tick(',s)
    if s<0 or e<0: raise SystemExit('FLUSH_RANGE_NOT_FOUND')
    new_flush=(
"    async def _v212_flush_tick_buffer(self):\n"
"        while True:\n"
"            await asyncio.sleep(self._v212_tick_flush_interval)\n"
"            if not self._v212_tick_buffer:\n"
"                continue\n"
"            batch=self._v212_tick_buffer[:self._v212_tick_flush_max]\n"
"            del self._v212_tick_buffer[:len(batch)]\n"
"            try:\n"
"                await asyncio.to_thread(self.db.add_ticks_batch,batch)\n"
"            except Exception as e:\n"
"                log.warning('V213B FE batch flush failed rows=%s err=%s',len(batch),e)\n\n"
    )
    kio=kio[:s]+new_flush+kio[e:]
    print('TRUE_BATCH_FLUSH_FORCED=True')

# 4) Replace the two exact live websocket add_tick calls with queueing. These line forms came from V212 output.
patterns=[
    ("self.db.add_tick(sym,price,qty,cumvol,ts)","self._v212_queue_tick(sym,price,qty,cumvol,ts)  # V213B_FE_BATCH"),
    ("self.db.add_tick(sym,price,qty,cumvol)","self._v212_queue_tick(sym,price,qty,cumvol,None)  # V213B_FE_BATCH"),
]
patched=0
for old,new in patterns:
    c=kio.count(old)
    if c:
        print('DIRECT_TICK_CALL',old,'COUNT=',c)
        kio=kio.replace(old,new)
        patched+=c
print('DIRECT_TICK_CALLS_PATCHED=',patched)
queue_refs=[(i,l.strip()) for i,l in enumerate(kio.splitlines(),1) if '_v212_queue_tick(' in l and not l.lstrip().startswith('def ')]
print('QUEUE_REFS=',queue_refs[:10])
if not queue_refs:
    raise SystemExit('NO_QUEUE_REFS_AFTER_PATCH')

# Start flusher once from websocket_forever if needed.
if 'V212_START_FLUSHER' not in kio:
    marker='    async def websocket_forever(self):\n'
    p=kio.find(marker)
    if p<0: raise SystemExit('WS_MARKER_NOT_FOUND')
    body=p+len(marker)
    guard=("        # V212_START_FLUSHER\n"
           "        if self._v212_tick_flush_task is None or self._v212_tick_flush_task.done():\n"
           "            self._v212_tick_flush_task=asyncio.create_task(self._v212_flush_tick_buffer())\n")
    kio=kio[:body]+guard+kio[body:]
    print('FLUSHER_START_INSERTED=True')
else:
    print('FLUSHER_START_PRESENT=True')

# 5) Sample REAL raw websocket payloads at 1 Hz if exact call exists; otherwise leave raw path unchanged.
raw_patched=0
klines=kio.splitlines(True)
out=[]
for i,line in enumerate(klines):
    if 'self.db.add_raw(raw, now)' in line:
        indent=line[:len(line)-len(line.lstrip())]
        ctx=''.join(klines[max(0,i-10):i+2])
        if 'json.loads' in ctx or 'd=' in ctx:
            out.append(indent+"if str((d or {}).get('trnm') or '')!='REAL':\n")
            out.append(indent+"    await asyncio.to_thread(self.db.add_raw,raw,now)\n")
            out.append(indent+"else:\n")
            out.append(indent+"    _v213b_now=asyncio.get_running_loop().time()\n")
            out.append(indent+"    if _v213b_now-getattr(self,'_v213b_last_raw_real',0.0)>=1.0:\n")
            out.append(indent+"        self._v213b_last_raw_real=_v213b_now\n")
            out.append(indent+"        await asyncio.to_thread(self.db.add_raw,raw,now)  # V213B_RAW_REAL_1HZ\n")
            raw_patched+=1
            continue
    out.append(line)
kio=''.join(out)
print('RAW_PATH_PATCHED=',raw_patched)

# Preflight compile the would-be file contents in temp files before touching runtime.
tmpdir=Path('/tmp/v213b_preflight'); tmpdir.mkdir(parents=True,exist_ok=True)
pre=[(tmpdir/'api.py',api),(tmpdir/'kiwoom.py',kio),(tmpdir/'db.py',dbs)]
for p,txt in pre:
    p.write_text(txt)
    r=subprocess.run([sys.executable,'-m','py_compile',str(p)],capture_output=True,text=True)
    print('PREFLIGHT',p.name,'RC=',r.returncode)
    if r.stderr.strip(): print(r.stderr.strip())
    if r.returncode: raise SystemExit('PREFLIGHT_COMPILE_FAIL__RUNTIME_UNCHANGED')

API.write_text(api); KIO.write_text(kio); DBP.write_text(dbs)
for p in (API,KIO,DBP):
    r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(p)],capture_output=True,text=True)
    print('RUNTIME_COMPILE',p.name,'RC=',r.returncode)
    if r.stderr.strip(): print(r.stderr.strip())
    if r.returncode:
        for q in (API,KIO,DBP): shutil.copy2(Path(str(q)+'.bak_v213b'),q)
        raise SystemExit('RUNTIME_COMPILE_FAIL_RESTORED')

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
    except Exception as e: return False,0,time.time()-t,repr(e)

ready=False
for i in range(1,41):
    ok,code,sec,body=get('/api/v4/runtime-mode',4)
    print('READY',i,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if ok and code==200:
        print('RUNTIME_MODE=',body); ready=True; break
    time.sleep(2)
print('API_READY=',ready)
lat=[]
if ready:
    for i in range(6):
        ok,code,sec,body=get('/api/v4/runtime-mode',4)
        lat.append(sec if ok else 99.0)
        print('RUNTIME_REPEAT',i+1,'OK=',ok,'SEC=',round(sec,3))
        time.sleep(1)

time.sleep(8)
frozen_ok=False; rowsn=barsn=ctxn=0
for ep in ('/api/v4/USA/status','/api/v4/USA/frozen-paper'):
    ok,code,sec,body=get(ep,12)
    print('ENDPOINT',ep,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if isinstance(body,dict) and ep.endswith('frozen-paper'):
        rows=body.get('rows') or []
        rowsn=len(rows); barsn=sum(1 for x in rows if x.get('bar')); ctxn=sum(1 for x in rows if x.get('ctx'))
        print('FROZEN_ROWS=',rowsn,'BAR_COUNT=',barsn,'CTX_COUNT=',ctxn,'EVAL=',body.get('evaluations'),'ERRORS=',body.get('errors'),'PAPER_EVENTS=',body.get('paper_events'))
        frozen_ok=ok and code==200 and rowsn==19
    elif not isinstance(body,dict):
        print('BODY=',body)

p=subprocess.run("ps -eo pid,ppid,pcpu,pmem,stat,etime,cmd --sort=-pcpu | grep 'uvicorn live_server.api:app' | grep -v grep | head -1",shell=True,capture_output=True,text=True)
print('UVICORN_CPU_LINE=',p.stdout.strip())
j=subprocess.run(['journalctl','-u','day-trader-api.service','-n','100','--no-pager'],capture_output=True,text=True)
low=j.stdout.lower()
print('RECOVERY_BATCH_COUNT=',low.count('minute recovery batch'))
print('BATCH_ERROR_COUNT=',low.count('v213b fe batch flush failed'))
fast=bool(lat and max(lat)<2.0)
print('RUNTIME_MODE_FAST=',fast)
print('V213B_PASS=',bool(ready and fast and frozen_ok))
print('USA_PAPER_RUNTIME_READY=',bool(ready and fast and frozen_ok and barsn>0))
print('NEXT=IF_PASS_LEAVE_RUNNING_PAPER; ELSE_SEND_OUTPUT_ONLY')
