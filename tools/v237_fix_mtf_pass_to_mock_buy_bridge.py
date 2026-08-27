#!/usr/bin/env python3
from pathlib import Path
import re, shutil, subprocess, time, urllib.request, json

print('=== V237 FIX MTF PASS -> KIWOOM MOCK BUY BRIDGE ===')
print('SCOPE=KOREA_KIWOOM_MOCK_ONLY REAL_ACCOUNT_CHANGE=NONE USA_CHANGE=NONE')
print('PURPOSE=ENSURE_V234_MTF_PASS_PRESERVES_WILLIAMS_ENTRY_SIGNAL_TO_MOCK_BUY_PATH')

ENG=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
if not ENG.exists():
    raise SystemExit('V237_ABORT runtime engine missing')

s=ENG.read_text()
backup=ENG.with_name('v4_engine.py.bak_v237')
shutil.copy2(ENG, backup)
print('BACKUP=', backup)

# V234 currently mutates out['signal']; V235 telemetry can observe a PASS,
# but downstream row construction/order bridge may still read an earlier/local signal value.
# Make the post-guard result authoritative and mirror it to all Williams entry aliases.
marker='V237_MTF_PASS_BRIDGE_SYNC'
patched=0
if marker not in s:
    # place immediately before the evaluator's final return in the V234 block
    anchor="""                    if not _mtf_ok:\n                        out['signal']=False\n                        out['stage']='BLOCKED_MTF'\n                except Exception as _e:\n"""
    if s.count(anchor)!=1:
        raise SystemExit(f'V237_ABORT mtf anchor count={s.count(anchor)}')
    repl="""                    if not _mtf_ok:\n                        out['signal']=False\n                        out['stage']='BLOCKED_MTF'\n                    else:\n                        # V237_MTF_PASS_BRIDGE_SYNC: make the guarded PASS authoritative\n                        # for downstream row/order aliases. Mock-only order path still\n                        # applies V233 live-price guard before buy submission.\n                        out['signal']=True\n                        out['williams_entry']=True\n                        out['williams_signal_entry']=True\n                        out['stage']='ENTRY_CANDIDATE'\n                except Exception as _e:\n"""
    s=s.replace(anchor,repl)
    ENG.write_text(s)
    patched=1
print('PATCH_PASS_BRIDGE=',patched)

py='/home/ubuntu/day-trader-api/venv/bin/python3'
rc=subprocess.run([py,'-m','py_compile',str(ENG)]).returncode
print('PY_COMPILE_RC=',rc)

txt=ENG.read_text()
checks={
 'V233_STRUCT5_PRICE_GUARD':'V233_STRUCT5_LIVE_PRICE_GUARD' in txt,
 'V234_MTF_GUARD':'V234_MTF_ENTRY_GUARD' in txt,
 'V235_TELEMETRY':'V235_MTF_TELEMETRY' in txt,
 'V237_PASS_BRIDGE':marker in txt,
 'MOCK_BUY_PATH':'buy_market' in txt and 'WILLIAMS_MOCK_BUY' in txt,
}
print('STATIC_CHECKS=',checks)

if rc!=0 or not all(checks.values()):
    print('V237_PASS=False')
    print('KOREA_MOCK_RESTARTED=NO')
    raise SystemExit(1)

rr=subprocess.run(['sudo','systemctl','restart','day-trader-api']).returncode
print('RESTART_RC=',rr)
ready=False
for i in range(1,31):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/api/runtime-mode',timeout=2) as r:
            body=json.loads(r.read().decode())
            print('READY',i,'HTTP=',r.status,'MODE=',body.get('mode'))
            ready=True
            break
    except Exception as e:
        print('READY',i,'FAIL=',type(e).__name__)
        time.sleep(2)
active=subprocess.run(['systemctl','is-active','day-trader-api'],capture_output=True,text=True).stdout.strip()
print('API_READY=',ready)
print('SERVICE_ACTIVE=',active)
print('V237_PASS=',bool(rr==0 and ready and active=='active'))
print('NEXT=ON_NEXT_WILLIAMS_MTF_PASS_EXPECT_WILLIAMS_MOCK_BUY_OR_EXPLICIT_V233_PRICE_BLOCK')
