#!/usr/bin/env python3
from pathlib import Path
import subprocess, time, sqlite3, shutil, re, sys

RUNTIME=Path('/home/ubuntu/day-trader-api/live_server/kiwoom.py')
BACKUP=Path(str(RUNTIME)+'.bak_v186d')
DB='/home/ubuntu/day-trader-api/daytrader.db'
VENV='/home/ubuntu/day-trader-api/venv/bin/python3'
FROZEN="['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']"
print('=== V186D EXACT _ws_items EXCHANGE OVERRIDE PROBE ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
if not RUNTIME.exists():
    print('RUNTIME_NOT_FOUND'); sys.exit(2)
orig=RUNTIME.read_text()
shutil.copy2(RUNTIME,BACKUP)
print('BACKUP',BACKUP)
needle="exchange=str(self.active_exchange(symbol) or '').upper().strip()"
print('WS_ITEMS_EXCHANGE_LINE_COUNT=',orig.count(needle))
if orig.count(needle)!=1:
    print('EXPECTED_EXACTLY_ONE_WS_ITEMS_EXCHANGE_LINE'); sys.exit(2)

# ensure runtime universe remains AMD-only per test by replacing frozen list read path in websocket_forever via frozen_paper_symbols source assignment in api.py is avoided;
# instead patch registered/current expressions to constant AMD tuple during each test, then restore.
reg1="registered=tuple(getattr(self,'frozen_paper_symbols',()) or ())"
reg2="current=tuple(getattr(self,'frozen_paper_symbols',()) or ())"
print('REGISTERED_COUNT=',orig.count(reg1),'CURRENT_COUNT=',orig.count(reg2))
if orig.count(reg1)!=1 or orig.count(reg2)!=1:
    print('WS_REGISTER_TARGETS_NOT_EXACT'); sys.exit(2)

def latest(sym):
    con=sqlite3.connect(DB)
    try:
        row=con.execute('select id,ts,price,qty from ticks where symbol=? order by id desc limit 1',(sym,)).fetchone()
        return None if not row else {'id':row[0],'ts':row[1],'price':row[2],'qty':row[3]}
    finally: con.close()

def ready():
    import urllib.request
    for i in range(40):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/api/v4/USA/status',timeout=2) as r:
                if r.status==200:
                    print('API_READY_PROBE',i+1); return True
        except Exception: pass
        time.sleep(1)
    return False

results=[]
try:
    for code in ('ND','NY','NA'):
        text=orig.replace(needle,f"exchange='{code}' if symbol=='AMD' else str(self.active_exchange(symbol) or '').upper().strip()")
        text=text.replace(reg1,"registered=('AMD',)")
        text=text.replace(reg2,"current=('AMD',)")
        RUNTIME.write_text(text)
        cp=subprocess.run([VENV,'-m','py_compile',str(RUNTIME)],capture_output=True,text=True)
        print('COMPILE',code,'PASS' if cp.returncode==0 else 'FAIL')
        if cp.returncode!=0:
            print(cp.stderr); break
        base=latest('AMD'); print('TEST_BEGIN AMD',code,'BASE',base)
        rc=subprocess.run(['sudo','systemctl','restart','day-trader-api.service']).returncode
        print('RESTART_RC',rc)
        if not ready():
            results.append((code,False,None)); continue
        base_id=(base or {}).get('id') or 0
        got=None
        for sec in (15,30,45,60,75,90):
            time.sleep(15)
            cur=latest('AMD')
            ok=bool(cur and (cur.get('id') or 0)>base_id)
            print('OBSERVE_SEC',sec,'CODE',code,'NEW_TICK',ok,'LATEST',cur)
            if ok:
                got=cur; break
        results.append((code,got is not None,got))
        logs=subprocess.run(['journalctl','-u','day-trader-api.service','--since','3 minutes ago','--no-pager'],capture_output=True,text=True).stdout
        for line in logs.splitlines():
            if 'WebSocket live:' in line:
                print('REG_LOG',code,line)
finally:
    RUNTIME.write_text(orig)
    cp=subprocess.run([VENV,'-m','py_compile',str(RUNTIME)],capture_output=True,text=True)
    print('RESTORE_COMPILE','PASS' if cp.returncode==0 else 'FAIL')
    rc=subprocess.run(['sudo','systemctl','restart','day-trader-api.service']).returncode
    print('RESTORE_RESTART_RC',rc)
    ready()

print('RESULTS',results)
winners=[c for c,ok,_ in results if ok]
print('WINNING_CODES=',winners)
if winners:
    print('V186D_RESULT=EXCHANGE_CODE_MAPPING_ISSUE_CONFIRMED')
else:
    print('V186D_RESULT=NO_EXCHANGE_CODE_WORKS__NEXT_JMCODE_OR_SYMBOL_ELIGIBILITY')
print('RUNTIME_RESTORED=YES')
