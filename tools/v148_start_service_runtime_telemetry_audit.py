#!/usr/bin/env python3
"""V148 start service and audit frozen USA runtime telemetry.

This script starts day-trader-api.service, then checks:
- service active
- V140/V142/V145 runtime markers still present
- frozen module importable
- no new real-broker authority in USA frozen block
- recent journal contains no immediate fatal traceback/import error

USA frozen path remains paper-ledger only.
"""
from __future__ import annotations
from pathlib import Path
import subprocess, time, py_compile, re

ROOT=Path('/home/ubuntu/day-trader-api')
ENG=ROOT/'live_server'/'v4_engine.py'
MOD=ROOT/'live_server'/'williams_usa_frozen.py'
S=ENG.read_text(errors='ignore') if ENG.exists() else ''

print('=== V148 START SERVICE + RUNTIME TELEMETRY AUDIT ===')
print('USA_FROZEN_MODE=PAPER_ONLY')
print('REAL_BROKER_AUTHORITY=NONE_ADDED')

# Pre-start compile guard.
try:
    py_compile.compile(str(ENG),doraise=True); py_compile.compile(str(MOD),doraise=True)
    print('PRESTART_COMPILE=PASS')
except Exception as e:
    print('PRESTART_COMPILE=FAIL',e)
    raise SystemExit(2)

# Start service.
p=subprocess.run(['sudo','systemctl','start','day-trader-api.service'],capture_output=True,text=True)
print('SYSTEMCTL_START_RC=',p.returncode)
if p.stdout.strip(): print('START_STDOUT=',p.stdout.strip())
if p.stderr.strip(): print('START_STDERR=',p.stderr.strip())
time.sleep(4)

q=subprocess.run(['systemctl','is-active','day-trader-api.service'],capture_output=True,text=True)
state=(q.stdout or q.stderr).strip()
print('SERVICE_STATE=',state)

checks={
 'v140_eval':'def _v140_usa_frozen_williams_eval' in S,
 'v142_context':'williams_frozen_ctx' in S,
 'v145_authority':'V145_USA_FROZEN_PAPER_AUTHORITY' in S,
 'paper_enter':"self.paper.enter('USA'" in S or 'self.paper.enter("USA"' in S,
 'paper_exit':"self.paper.exit('USA'" in S or 'self.paper.exit("USA"' in S,
 'frozen_strategy':'WILLIAMS_FROZEN_V136' in S,
}
start=S.find('V145_USA_FROZEN_PAPER_AUTHORITY')
block=S[start:start+2200] if start>=0 else ''
checks['no_real_broker_in_frozen_block']=not any(x in block for x in ['KiwoomMockBroker','send_order(','place_order(','broker.'])
for k,v in checks.items(): print(k,'PASS' if v else 'FAIL')

j=subprocess.run(['journalctl','-u','day-trader-api.service','-n','120','--no-pager'],capture_output=True,text=True)
log=(j.stdout or '')
# Print compact tail relevant to startup/errors only.
print('=== JOURNAL STARTUP/ERROR SCAN ===')
lines=log.splitlines()
rel=[]
for line in lines:
    low=line.lower()
    if any(x in low for x in ['traceback','error','exception','failed','started','uvicorn','application startup','williams','frozen']):
        rel.append(line)
for line in rel[-30:]: print(line[:260])

fatal=bool(re.search(r'Traceback|ModuleNotFoundError|SyntaxError|ImportError|Application startup failed',log,re.I))
print('FATAL_STARTUP_ERROR=',fatal)
static_ok=all(checks.values())
pass_ok=(state=='active' and static_ok and not fatal)
print('STATIC_RUNTIME_PASS=',static_ok)
print('RUNTIME_TELEMETRY_PASS=',pass_ok)
print('ORDER_MODE=USA_PAPER_ONLY')
print('NEXT=' + ('V149_VERIFY_RUNTIME_MODE_AND_USA_LIVE_FEED' if pass_ok else 'STOP_AND_FIX_RUNTIME_STARTUP'))
