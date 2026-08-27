#!/usr/bin/env python3
import urllib.request, urllib.error, subprocess
from pathlib import Path

print('=== V237B VERIFY RUNTIME WITH REAL HEALTH ENDPOINT ===')
print('READ_ONLY=YES CODE_CHANGE=NONE RESTART=NONE')

ENG=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
API=Path('/home/ubuntu/day-trader-api/live_server/api.py')

checks={
    'V233_STRUCT5_PRICE_GUARD': False,
    'V234_MTF_GUARD': False,
    'V235_TELEMETRY': False,
    'V237_PASS_BRIDGE': False,
}
if ENG.exists():
    s=ENG.read_text(errors='ignore')
    checks['V233_STRUCT5_PRICE_GUARD']='V233_STRUCT5_LIVE_PRICE_GUARD' in s
    checks['V234_MTF_GUARD']='V234_MTF_ENTRY_GUARD' in s
    checks['V235_TELEMETRY']='V235_MTF_TELEMETRY' in s
    checks['V237_PASS_BRIDGE']='V237_MTF_PASS_BRIDGE' in s
print('STATIC_CHECKS=', checks)

cands=['/health','/api/health','/docs','/openapi.json','/runtime-mode','/api/runtime-mode']
working=[]
for p in cands:
    url='http://127.0.0.1:8000'+p
    try:
        with urllib.request.urlopen(url,timeout=3) as r:
            body=r.read(300).decode('utf-8','ignore').replace('\n',' ')
            print('HTTP',r.status,p,body[:180])
            if 200 <= r.status < 300:
                working.append(p)
    except urllib.error.HTTPError as e:
        body=e.read(200).decode('utf-8','ignore').replace('\n',' ')
        print('HTTP',e.code,p,body[:140])
    except Exception as e:
        print('ERR',p,type(e).__name__,str(e)[:120])

svc=subprocess.run(['systemctl','is-active','day-trader-api'],capture_output=True,text=True)
print('SERVICE_ACTIVE=',svc.stdout.strip())
port=subprocess.run(['bash','-lc','ss -ltnp | grep ":8000 " || true'],capture_output=True,text=True)
print('PORT8000=',port.stdout.strip())

ok=all(checks.values()) and svc.stdout.strip()=='active' and bool(working)
print('WORKING_HEALTH_ENDPOINTS=',working)
print('V237B_PASS=',ok)
print('NEXT=IF_PASS_OBSERVE_NEXT_MTF_PASS_FOR_MOCK_BUY')
