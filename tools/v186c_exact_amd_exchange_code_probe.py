#!/usr/bin/env python3
import os, time, shutil, subprocess, sqlite3, re
from pathlib import Path
from datetime import datetime, timezone

RUNTIME=Path('/home/ubuntu/day-trader-api')
K=RUNTIME/'live_server/kiwoom.py'
DB=RUNTIME/'daytrader.db'
BACKUP=Path(str(K)+'.bak_v186c')
SYMBOL='AMD'
CODES=['ND','NY','NA']

print('=== V186C EXACT AMD EXCHANGE CODE PROBE ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
shutil.copy2(K,BACKUP)
print('BACKUP',BACKUP)

orig=K.read_text(encoding='utf-8')

# Find active_exchange method and temporarily force AMD to requested exchange.
pat=re.compile(r'(\n\s*def active_exchange\(self,\s*symbol[^\n]*\):\n)')
m=pat.search(orig)
if not m:
    print('ACTIVE_EXCHANGE_DEF_NOT_FOUND')
    raise SystemExit(2)


def latest_tick():
    con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
    r=con.execute('select id,ts,price,qty from ticks where symbol=? order by id desc limit 1',(SYMBOL,)).fetchone()
    con.close()
    return dict(r) if r else None

def restart_wait():
    rc=subprocess.run(['sudo','systemctl','restart','day-trader-api'],capture_output=True,text=True).returncode
    print('RESTART_RC',rc)
    for i in range(40):
        p=subprocess.run(['curl','-sS','-m','2','-o','/dev/null','-w','%{http_code}','http://127.0.0.1:8000/api/v4/USA/status'],capture_output=True,text=True)
        if p.stdout.strip()=='200':
            print('API_READY_PROBE',i+1); return True
        time.sleep(1)
    print('API_READY=False'); return False

results=[]
try:
    for code in CODES:
        injected=m.group(1)+f"        if str(symbol or '').upper().strip() == 'AMD':\n            return '{code}'\n"
        txt=orig[:m.start()]+injected+orig[m.end():]
        K.write_text(txt,encoding='utf-8')
        cp=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(K)],capture_output=True,text=True)
        print('COMPILE',code,'PASS' if cp.returncode==0 else 'FAIL',cp.stderr.strip())
        if cp.returncode!=0: raise SystemExit(3)
        base=latest_tick(); print('TEST_BEGIN',code,'BASE',base)
        restart_wait()
        new=False; latest=None
        base_id=(base or {}).get('id') or -1
        for sec in (15,30,45,60,75,90):
            time.sleep(15 if sec==15 else 15)
            latest=latest_tick(); new=bool(latest and (latest.get('id') or -1)>base_id)
            print('OBSERVE_SEC',sec,'CODE',code,'NEW_TICK',new,'LATEST',latest)
            if new: break
        # show exact registration / ACK lines
        j=subprocess.run(['journalctl','-u','day-trader-api','--since','3 minutes ago','--no-pager'],capture_output=True,text=True)
        for line in j.stdout.splitlines():
            if 'WebSocket live:' in line or 'REG failed' in line or 'websocket reconnect' in line.lower():
                print('LOG',code,line[-1000:])
        results.append((code,new,latest))
finally:
    shutil.copy2(BACKUP,K)
    cp=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(K)],capture_output=True,text=True)
    print('RESTORE_COMPILE','PASS' if cp.returncode==0 else 'FAIL')
    restart_wait()

print('RESULTS',results)
winning=[c for c,ok,_ in results if ok]
print('WINNING_CODES=',winning)
if winning:
    print('V186C_RESULT=EXCHANGE_CODE_MAPPING_ISSUE_CONFIRMED')
else:
    print('V186C_RESULT=ALL_EXCHANGE_CODES_FAILED; MOVE_TO_JMCODE/ELIGIBILITY_PROBE')
print('RUNTIME_RESTORED=YES')
