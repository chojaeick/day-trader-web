#!/usr/bin/env python3
from pathlib import Path
import subprocess, time, sqlite3, json, re
from datetime import datetime, timezone

RUNTIME=Path('/home/ubuntu/day-trader-api/live_server/kiwoom.py')
SERVICE='day-trader-api.service'
DB='/home/ubuntu/day-trader-api/daytrader.db'
TARGETS=[('AMD','ND'),('AMZN','ND'),('ARM','ND'),('AVGO','ND'),('GOOGL','ND'),('ORCL','NY'),('TSM','NY'),('NVDA','ND'),('QQQ','ND')]

print('=== V193 TARGET7 LIVE SINGLE-REG HARNESS ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('PURPOSE=TEMPORARY_USA_WS_SINGLE_SYMBOL_DELIVERY_TEST')

src=RUNTIME.read_text()
bak=RUNTIME.with_suffix('.py.bak_v193')
bak.write_text(src)
print('BACKUP',bak)

pat_reg="registered=tuple(getattr(self,'frozen_paper_symbols',()) or ())"
pat_cur="current=tuple(getattr(self,'frozen_paper_symbols',()) or ())"
if src.count(pat_reg)!=1 or src.count(pat_cur)!=1:
    print('TARGET_ASSIGNMENTS_NOT_EXACT',src.count(pat_reg),src.count(pat_cur)); raise SystemExit(2)

def latest(sym):
    con=sqlite3.connect(DB)
    try:
        return con.execute('select id,ts,price,qty from ticks where symbol=? order by id desc limit 1',(sym,)).fetchone()
    finally:
        con.close()

def restart():
    rc=subprocess.run(['sudo','systemctl','restart',SERVICE]).returncode
    print('RESTART_RC',rc)
    for i in range(1,61):
        p=subprocess.run(['curl','-fsS','http://127.0.0.1:8000/api/v4/USA/status'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if p.returncode==0:
            print('API_READY_PROBE',i); return True
        time.sleep(1)
    return False

results=[]
try:
    for sym,ex in TARGETS:
        probe=src.replace(pat_reg,f"registered=('{sym}',)").replace(pat_cur,f"current=('{sym}',)")
        # force exact exchange only for this symbol during test
        line="exchange=str(self.active_exchange(symbol) or '').upper().strip()"
        if probe.count(line)!=1:
            print('EXCHANGE_LINE_COUNT',probe.count(line)); raise SystemExit(3)
        probe=probe.replace(line,f"exchange='{ex}' if symbol=='{sym}' else str(self.active_exchange(symbol) or '').upper().strip()")
        RUNTIME.write_text(probe)
        cp=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(RUNTIME)])
        print('COMPILE',sym,ex,'PASS' if cp.returncode==0 else 'FAIL')
        if cp.returncode!=0: raise SystemExit(4)
        base=latest(sym)
        print('TEST_BEGIN',sym,ex,'BASE',base)
        if not restart():
            print('API_READY_FAIL',sym); results.append((sym,ex,False,None)); continue
        got=False; row=None
        for sec in (20,40,60,80,100,120):
            time.sleep(20)
            row=latest(sym)
            got=bool(row and (base is None or row[0]!=base[0]))
            print('OBSERVE_SEC',sec,'SYMBOL',sym,'NEW_TICK',got,'LATEST',row)
            if got: break
        j=subprocess.run(['journalctl','-u',SERVICE,'--since','3 minutes ago','--no-pager'],capture_output=True,text=True).stdout
        lines=[x for x in j.splitlines() if 'WebSocket live:' in x or 'REG failed' in x or 'WebSocket reconnect' in x]
        for x in lines[-5:]: print('WS_LOG',sym,x)
        results.append((sym,ex,got,row if got else None))
finally:
    RUNTIME.write_text(src)
    cp=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(RUNTIME)])
    print('RESTORE_COMPILE','PASS' if cp.returncode==0 else 'FAIL')
    restart()

print('RESULTS',results)
print('SUCCESS_SYMBOLS',[r[0] for r in results if r[2]])
print('FAIL_SYMBOLS',[r[0] for r in results if not r[2]])
print('RUNTIME_RESTORED=YES')
print('NEXT=IF_FRESH_CONTROLS_PASS_AND_TARGET7_FAIL_CONFIRM_SYMBOL_SPECIFIC_F5_DELIVERY; IF_ALL_PASS_GROUP_REGISTRATION_BEHAVIOR; IF_CONTROLS_FAIL_SESSION_OR_HARNESS_ISSUE')
