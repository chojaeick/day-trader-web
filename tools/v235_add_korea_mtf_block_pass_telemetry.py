#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, urllib.request, json

print('=== V235 ADD KOREA MTF BLOCK/PASS TELEMETRY ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE REAL_ACCOUNT_CHANGE=NONE USA_CHANGE=NONE')
print('PURPOSE=LOG_EACH_FRESH_WILLIAMS_SIGNAL_AS_MTF_PASS_OR_BLOCK_WITH_DIAGNOSTICS')

ENG=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
if not ENG.exists(): raise SystemExit('V235_ABORT missing runtime engine')
s=ENG.read_text()
backup=ENG.with_name('v4_engine.py.bak_v235')
shutil.copy2(ENG,backup)
marker='V235_MTF_TELEMETRY'
if marker in s:
    print('PATCH_ALREADY_PRESENT=YES'); patched=0
else:
    anchor="""                    if not _mtf_ok:\n                        out['signal']=False\n                        out['stage']='BLOCKED_MTF'\n"""
    if s.count(anchor)!=1:
        raise SystemExit(f'V235_ABORT anchor count={s.count(anchor)}')
    repl="""                    # V235_MTF_TELEMETRY: record only fresh Williams signals reaching this guard.\n                    # No strategy/order authority change.\n                    try:\n                        _etype='WILLIAMS_MTF_PASS' if _mtf_ok else 'WILLIAMS_MTF_BLOCK'\n                        _msg=(f'{sym} MTF ' + ('PASS' if _mtf_ok else 'BLOCK') +\n                              f' 1m={_one_ok} 5m={_five_ok} improve={_improve}')\n                        self.store.event('KOREA',str(sym),_etype,None,\n                                         'ENTRY_CANDIDATE' if _mtf_ok else 'BLOCKED_MTF',\n                                         power=None,message=_msg,payload={\n                            'price':current_price,'finder_rank':finder_rank,\n                            'trigger':out.get('trigger'),'rsi2':out.get('rsi2'),\n                            'raw_cross':out.get('raw_cross'),\n                            'historical_cross_recovered':out.get('historical_cross_recovered'),\n                            'struct5_signal':out.get('struct5_signal'),\n                            'struct5_resistance':out.get('struct5_resistance'),\n                            'struct5_reason':out.get('struct5_reason'),\n                            'mtf':out.get('v234_mtf_guard'),\n                        })\n                    except Exception:\n                        pass\n                    if not _mtf_ok:\n                        out['signal']=False\n                        out['stage']='BLOCKED_MTF'\n"""
    s=s.replace(anchor,repl)
    ENG.write_text(s); patched=1
print('PATCH_TELEMETRY=',patched)
py='/home/ubuntu/day-trader-api/venv/bin/python3'
rc=subprocess.run([py,'-m','py_compile',str(ENG)]).returncode
print('PY_COMPILE_RC=',rc)
checks={
 'V233_GUARD':'V233_STRUCT5_LIVE_PRICE_GUARD' in ENG.read_text(),
 'V234_GUARD':'V234_MTF_ENTRY_GUARD' in ENG.read_text(),
 'V235_TELEMETRY':marker in ENG.read_text(),
 'BUY_PATH':'buy_market(sym,qty)' in ENG.read_text(),
}
print('STATIC_CHECKS=',checks)
if rc!=0 or not all(checks.values()):
    raise SystemExit('V235_FAIL_STATIC')
rr=subprocess.run(['sudo','systemctl','restart','day-trader-api']).returncode
print('RESTART_RC=',rr)
ready=False
for i in range(1,16):
    time.sleep(1)
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/api/v4/runtime-mode',timeout=2) as r:
            body=json.loads(r.read().decode()); print('READY',i,'HTTP=',r.status,'MODE=',body.get('mode')); ready=True; break
    except Exception as e: print('READY',i,'FAIL=',type(e).__name__)
active=subprocess.run(['systemctl','is-active','day-trader-api'],capture_output=True,text=True).stdout.strip()
print('API_READY=',ready)
print('SERVICE_ACTIVE=',active)
print('V235_PASS=',bool(ready and active=='active' and all(checks.values())))
print('NEXT=WAIT_FOR_NEXT_WILLIAMS_SIGNAL_THEN_QUERY_WILLIAMS_MTF_PASS_BLOCK_EVENTS')
