#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, urllib.request, json, re

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
KIO=Path('/home/ubuntu/day-trader-api/live_server/kiwoom.py')
DBP=Path('/home/ubuntu/day-trader-api/live_server/db.py')
BASE='http://127.0.0.1:8000'
print('=== V213 TRUE SQLITE BATCH + RAW SAMPLING + RESTORE FROZEN ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('PURPOSE=REMOVE_PER_FE_TICK_SQLITE_COMMIT_DELETE_AMPLIFICATION_AND_RESTORE_PAPER_EVALUATOR')
for p in (API,KIO,DBP):
    if not p.exists(): raise SystemExit(f'NOT_FOUND {p}')
    shutil.copy2(p,Path(str(p)+'.bak_v213'))

api=API.read_text(errors='ignore')
kio=KIO.read_text(errors='ignore')
dbs=DBP.read_text(errors='ignore')

# 1) Restore V211 frozen evaluator line if the A/B comment is present.
restore_count=0
alines=api.splitlines(True)
out=[]
for line in alines:
    if 'V211_AB: frozen evaluator task disabled' in line:
        indent=line[:len(line)-len(line.lstrip())]
        out.append(indent+'asyncio.create_task(frozen_usa_paper_forever()),  # V213_RESTORED_FROZEN\n')
        restore_count+=1
    elif 'V211_AB frozen evaluator disabled' in line and 'frozen_usa_paper_forever' not in line:
        indent=line[:len(line)-len(line.lstrip())]
        out.append(indent+'asyncio.create_task(frozen_usa_paper_forever()),  # V213_RESTORED_FROZEN\n')
        restore_count+=1
    else:
        out.append(line)
api=''.join(out)
# If already restored, accept exactly one startup task ref outside function def.
refs=[]
for i,l in enumerate(api.splitlines(),1):
    if 'frozen_usa_paper_forever()' in l and not l.lstrip().startswith('async def '): refs.append((i,l.strip()))
print('FROZEN_RESTORE_COUNT=',restore_count,'STARTUP_REFS=',refs[:10])
if not refs:
    print('NO_FROZEN_STARTUP_REF__ABORT_SAFE')
    raise SystemExit('FROZEN_RESTORE_TARGET_NOT_FOUND')

# 2) Add real DB batch API: one connection/transaction, executemany, one cleanup per symbol.
if 'def add_ticks_batch(' not in dbs:
    marker='    def add_raw(self, payload: str, ts: str):\n'
    p=dbs.find(marker)
    if p<0: raise SystemExit('DB_ADD_RAW_MARKER_NOT_FOUND')
    method='''    def add_ticks_batch(self, rows):
        """V213: batch FE ticks in one SQLite transaction; cleanup once per symbol."""
        if not rows:
            return 0
        clean=[]
        syms=set()
        for row in rows:
            if len(row)<5:
                continue
            symbol,price,qty,cum_volume,ts=row[:5]
            if not symbol or ts is None:
                continue
            clean.append((str(symbol).upper(),float(price),float(qty or 0),float(cum_volume or 0),str(ts)))
            syms.add(str(symbol).upper())
        if not clean:
            return 0
        with self.conn() as c:
            c.executemany('INSERT INTO ticks(symbol,price,qty,cum_volume,ts) VALUES(?,?,?,?,?)',clean)
            for symbol in syms:
                c.execute('''DELETE FROM ticks WHERE id IN (
                  SELECT id FROM ticks WHERE symbol=? ORDER BY id DESC LIMIT -1 OFFSET 250000
                )''',(symbol,))
        return len(clean)

'''
    dbs=dbs[:p]+method+dbs[p:]
    print('DB_BATCH_METHOD_INSERTED=True')
else:
    print('DB_BATCH_METHOD_ALREADY_PRESENT=True')

# 3) Replace V212 flusher implementation with true DB batch call.
start=kio.find('    async def _v212_flush_tick_buffer(self):\n')
end=kio.find('    def _v212_queue_tick(',start if start>=0 else 0)
print('V212_FLUSH_METHOD_RANGE=',start,end)
if start<0 or end<0 or end<=start:
    raise SystemExit('V212_FLUSH_METHOD_NOT_FOUND')
new_flush='''    async def _v212_flush_tick_buffer(self):
        while True:
            await asyncio.sleep(self._v212_tick_flush_interval)
            if not self._v212_tick_buffer:
                continue
            batch=self._v212_tick_buffer[:self._v212_tick_flush_max]
            del self._v212_tick_buffer[:len(batch)]
            try:
                await asyncio.to_thread(self.db.add_ticks_batch,batch)
            except Exception as e:
                log.warning('V213 FE batch flush failed rows=%s err=%s',len(batch),e)

'''
kio=kio[:start]+new_flush+kio[end:]
print('TRUE_BATCH_FLUSH_PATCHED=True')

# Confirm websocket is already queueing ticks rather than direct DB writes.
queue_refs=[]
for i,l in enumerate(kio.splitlines(),1):
    if '_v212_queue_tick(' in l and not l.lstrip().startswith('def '): queue_refs.append((i,l.strip()))
print('QUEUE_CALL_REFS=',queue_refs[:10])
if not queue_refs:
    raise SystemExit('NO_WEBSOCKET_QUEUE_CALL_REF')

# 4) FE raw websocket diagnostics: keep non-REAL messages; sample REAL raw payload at 1 Hz.
# This table is diagnostic only and is not strategy/order authority.
klines=kio.splitlines(True)
patched_raw=0
out=[]
for i,line in enumerate(klines):
    if 'self.db.add_raw(raw, now)' in line:
        ctx=''.join(klines[max(0,i-8):i+3])
        if 'json.loads' in ctx or 'd=' in ctx:
            indent=line[:len(line)-len(line.lstrip())]
            out.append(indent+"if str((d or {}).get('trnm') or '')!='REAL':\n")
            out.append(indent+"    await asyncio.to_thread(self.db.add_raw,raw,now)\n")
            out.append(indent+"else:\n")
            out.append(indent+"    _v213_now=asyncio.get_running_loop().time()\n")
            out.append(indent+"    if _v213_now-getattr(self,'_v213_last_raw_real',0.0)>=1.0:\n")
            out.append(indent+"        self._v213_last_raw_real=_v213_now\n")
            out.append(indent+"        await asyncio.to_thread(self.db.add_raw,raw,now)  # V213_RAW_REAL_1HZ\n")
            patched_raw+=1
            continue
    out.append(line)
kio=''.join(out)
print('RAW_ADD_PATCHED=',patched_raw)
if patched_raw<1 and 'V213_RAW_REAL_1HZ' not in kio:
    raise SystemExit('NO_SAFE_RAW_WS_PATCH')

# Write then compile. Restore all on any compile failure.
API.write_text(api); KIO.write_text(kio); DBP.write_text(dbs)
for p in (API,KIO,DBP):
    r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(p)],capture_output=True,text=True)
    print('COMPILE',p.name,'RC=',r.returncode)
    if r.stderr.strip(): print(r.stderr.strip())
    if r.returncode:
        for q in (API,KIO,DBP): shutil.copy2(Path(str(q)+'.bak_v213'),q)
        raise SystemExit('COMPILE_FAIL_RESTORED')

r=subprocess.run(['sudo','systemctl','restart','day-trader-api.service'],capture_output=True,text=True)
print('RESTART_RC=',r.returncode)
if r.stderr.strip(): print(r.stderr.strip())

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
print('API_READY=',ready)

lat=[]
if ready:
    for i in range(6):
        ok,code,sec,body=get('/api/v4/runtime-mode',4)
        lat.append(sec if ok else 99.0)
        print('RUNTIME_REPEAT',i+1,'OK=',ok,'SEC=',round(sec,3))
        time.sleep(1)

# Allow frozen evaluator a little time to build current rows.
time.sleep(8)
for ep in ('/api/v4/USA/status','/api/v4/USA/frozen-paper'):
    ok,code,sec,body=get(ep,12)
    print('ENDPOINT',ep,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if isinstance(body,dict) and ep.endswith('frozen-paper'):
        rows=body.get('rows') or []
        print('FROZEN_ROWS=',len(rows),'BAR_COUNT=',sum(1 for x in rows if x.get('bar')),
              'CTX_COUNT=',sum(1 for x in rows if x.get('ctx')),'EVAL=',body.get('evaluations'),
              'ERRORS=',body.get('errors'),'PAPER_EVENTS=',body.get('paper_events'))
        for x in rows:
            print('ROW',x.get('symbol'),'BAR',x.get('bar'),'CTX',x.get('ctx'),'REASON',x.get('eval_reason'),'TICKS',x.get('ticks'))
    elif not isinstance(body,dict): print('BODY=',body)

p=subprocess.run("ps -eo pid,ppid,pcpu,pmem,stat,etime,cmd --sort=-pcpu | grep 'uvicorn live_server.api:app' | grep -v grep | head -1",shell=True,capture_output=True,text=True)
print('UVICORN_CPU_LINE=',p.stdout.strip())

j=subprocess.run(['journalctl','-u','day-trader-api.service','-n','100','--no-pager'],capture_output=True,text=True)
low=j.stdout.lower()
print('RECOVERY_BATCH_COUNT=',low.count('minute recovery batch'))
print('V213_BATCH_ERROR_COUNT=',low.count('v213 fe batch flush failed'))
fast=bool(lat and max(lat)<2.0)
print('RUNTIME_MODE_FAST=',fast)
print('NEXT=IF_API_FAST_AND_FROZEN_ROWS_19_LEAVE_RUNNING_USA_PAPER; ELSE_SEND_OUTPUT_ONLY')
