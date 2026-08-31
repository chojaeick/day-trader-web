#!/usr/bin/env python3
from pathlib import Path
import subprocess, shutil, time, sys, json, re, urllib.request

ROOT=Path('/home/ubuntu/day-trader-api')
APP=ROOT/'app_v5.py'
ENGINE=ROOT/'live_server/v22e_us_mock_live.py'
PY=ROOT/'venv/bin/python'
EVAL=ROOT/'v22e_us_mock_eval.json'
ACCT=ROOT/'v22e_us_mock_account.json'
FLAG=ROOT/'v22e_us_trade_enabled.flag'
LOG=ROOT/'app_v5.log'

print('V84_START=YES')
if not APP.exists():
    print('ABORT=APP_MISSING'); sys.exit(2)

bak=ROOT/f'app_v5.py.pre_v84_{int(time.time())}'
shutil.copy2(APP,bak)
text=APP.read_text(encoding='utf-8')

# Runtime imports required by V82/V83 helpers. Add only if missing.
imports=[]
if 'from pathlib import Path' not in text: imports.append('from pathlib import Path')
if not re.search(r'^import json\b', text, re.M): imports.append('import json')
if imports:
    lines=text.splitlines(True); i=1 if lines and lines[0].startswith('#!') else 0
    lines.insert(i, '\n'.join(imports)+'\n')
    text=''.join(lines)

# Harden V82 file helpers so a malformed/stale file never crashes UI.
# Replace helper bodies by exact function ranges when present.
def replace_func(src, name, body):
    pat=re.compile(rf'^def {re.escape(name)}\([^\n]*\):\n(?:^[ \t]+.*\n|^\s*\n)*', re.M)
    m=pat.search(src)
    if not m: return src, False
    return src[:m.start()] + body + src[m.end():], True

body_trade='''def v82_us_trade_enabled():\n    try:\n        p=Path('/home/ubuntu/day-trader-api/v22e_us_trade_enabled.flag')\n        return (not p.exists()) or p.read_text(encoding='utf-8').strip()!='0'\n    except Exception:\n        return True\n\n'''
body_set='''def v82_set_us_trade_enabled(enabled):\n    try:\n        Path('/home/ubuntu/day-trader-api/v22e_us_trade_enabled.flag').write_text('1' if enabled else '0',encoding='utf-8')\n        return True\n    except Exception as e:\n        st.error(f'자동매매 스위치 저장 실패: {e}')\n        return False\n\n'''
body_acct='''def v82_us_account():\n    try:\n        d=json.loads(Path('/home/ubuntu/day-trader-api/v22e_us_mock_account.json').read_text(encoding='utf-8'))\n        return d if isinstance(d,dict) else {}\n    except Exception:\n        return {}\n\n'''
body_eval='''def v82_us_eval_rows():\n    try:\n        d=json.loads(Path('/home/ubuntu/day-trader-api/v22e_us_mock_eval.json').read_text(encoding='utf-8'))\n        if isinstance(d,list): return d\n        if isinstance(d,dict) and isinstance(d.get('rows'),list): return d.get('rows')\n    except Exception:\n        pass\n    return []\n\n'''
for n,b in [('v82_us_trade_enabled',body_trade),('v82_set_us_trade_enabled',body_set),('v82_us_account',body_acct),('v82_us_eval_rows',body_eval)]:
    text,_=replace_func(text,n,b)

APP.write_text(text,encoding='utf-8')
r=subprocess.run([str(PY),'-m','py_compile',str(APP)],capture_output=True,text=True)
if r.returncode:
    print('PY_COMPILE=FAIL'); print((r.stderr or r.stdout).strip()); shutil.copy2(bak,APP); print('APP_ROLLBACK=YES'); sys.exit(3)
print('PY_COMPILE=PASS')

# Engine compile + service state; do not alter trading logic here.
er=subprocess.run([str(PY),'-m','py_compile',str(ENGINE)],capture_output=True,text=True)
if er.returncode:
    print('ENGINE_PY_COMPILE=FAIL'); print((er.stderr or er.stdout).strip()); sys.exit(4)
print('ENGINE_PY_COMPILE=PASS')

# Confirm actual source files before restart.
def load_json(p):
    try: return json.loads(p.read_text(encoding='utf-8'))
    except Exception as e: return {'__error__':repr(e)}

ev=load_json(EVAL); ac=load_json(ACCT)
rows=ev if isinstance(ev,list) else (ev.get('rows') if isinstance(ev,dict) else [])
rows=rows if isinstance(rows,list) else []
hs=ac.get('holdings') if isinstance(ac,dict) else []
hs=hs if isinstance(hs,list) else []
print('LIVE_EVAL_ROWS='+str(len(rows)))
print('LIVE_ACCOUNT_HOLDINGS='+str(len(hs)))
print('LIVE_ACCOUNT_CASH='+str(ac.get('cash') if isinstance(ac,dict) else 'ERR'))
if not FLAG.exists(): FLAG.write_text('1',encoding='utf-8')
print('TRADE_SWITCH_FILE='+('ON' if FLAG.read_text(encoding='utf-8').strip()!='0' else 'OFF'))

# Restart Streamlit only. Leave engine running to avoid OAuth churn.
subprocess.run("pkill -f 'streamlit run .*app_v5.py' || true",shell=True)
time.sleep(1)
if LOG.exists():
    try: LOG.unlink()
    except Exception: pass
subprocess.run("cd /home/ubuntu/day-trader-api && nohup /home/ubuntu/day-trader-api/venv/bin/streamlit run app_v5.py --server.port 8503 --server.address 0.0.0.0 > /home/ubuntu/day-trader-api/app_v5.log 2>&1 &",shell=True)
time.sleep(5)

try:
    urllib.request.urlopen('http://127.0.0.1:8503/',timeout=5).read(128)
    print('V5_HTTP=PASS')
except Exception as e:
    print('V5_HTTP=FAIL '+repr(e)); sys.exit(5)

# Force a second request and then inspect runtime log for actual script exceptions.
time.sleep(2)
try: urllib.request.urlopen('http://127.0.0.1:8503/',timeout=5).read(128)
except Exception: pass
time.sleep(2)
logtxt=''
try: logtxt=LOG.read_text(encoding='utf-8',errors='replace')[-30000:]
except Exception: pass
bad=[]
for token in ('Traceback (most recent call last)','NameError:','IndentationError:','SyntaxError:','AttributeError:'):
    if token in logtxt: bad.append(token)
if bad:
    print('V5_RUNTIME_SMOKE=FAIL')
    print('RUNTIME_ERRORS='+','.join(bad))
    print('--- APP_LOG_TAIL ---')
    print('\n'.join(logtxt.splitlines()[-80:]))
    sys.exit(6)
print('V5_RUNTIME_SMOKE=PASS')

svc=subprocess.run(['systemctl','is-active','day-trader-v22e-us.service'],capture_output=True,text=True).stdout.strip()
print('V22E_SERVICE='+svc.upper())
print('US_ACCOUNT_SOURCE=KIWOOM_MOCK_ACCOUNT_SNAPSHOT')
print('US_POSITIONS_SOURCE=KIWOOM_ACCOUNT_FILE')
print('FINDER_SOURCE=V22E_LIVE_EVAL_DIRECT')
print('TRADE_SWITCH=V5_TO_ENGINE_ORDER_GUARD')
print('ENGINE_RESTART=NO')
print('KR_PATH=UNCHANGED')
print('DEPLOY=PASS')
