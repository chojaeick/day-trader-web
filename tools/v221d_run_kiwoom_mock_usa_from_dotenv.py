#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import os, subprocess, sys

ENV=Path('/home/ubuntu/day-trader-api/.env')
TARGET=Path('/home/ubuntu/day-trader-api-repo/tools/v221_kiwoom_mock_usa_real_order_smoke.py')
ALLOWED={'KIWOOM_MOCK_APP_KEY','KIWOOM_MOCK_APP_SECRET','KIWOOM_MOCK_ORDER_ENABLE','KIWOOM_MOCK_REST_BASE'}

print('=== V221D RUN USA KIWOOM MOCK ORDER FROM RUNTIME .ENV ===')
print('ENV_SOURCE=',ENV)
print('REAL_ACCOUNT_ENV_IMPORTED=NO')
print('ALLOWED_ENV_KEYS=',sorted(ALLOWED))

if not ENV.exists():
    raise SystemExit('V221D_ABORT missing runtime .env')
if not TARGET.exists():
    raise SystemExit('V221D_ABORT missing V221 target')

def parse_env(path: Path):
    out={}
    for raw in path.read_text(errors='ignore').splitlines():
        s=raw.strip()
        if not s or s.startswith('#') or '=' not in s: continue
        k,v=s.split('=',1); k=k.strip(); v=v.strip()
        if k not in ALLOWED: continue
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v=v[1:-1]
        out[k]=v
    return out

vals=parse_env(ENV)
print('FOUND_KEYS=',sorted(vals))
missing=[k for k in ('KIWOOM_MOCK_APP_KEY','KIWOOM_MOCK_APP_SECRET','KIWOOM_MOCK_REST_BASE') if not vals.get(k)]
if missing:
    raise SystemExit('V221D_ABORT missing keys: '+','.join(missing))
base=vals.get('KIWOOM_MOCK_REST_BASE','').rstrip('/')
if base!='https://mockapi.kiwoom.com':
    raise SystemExit('V221D_ABORT non-mock base refused: '+base)
if vals.get('KIWOOM_MOCK_ORDER_ENABLE','').lower() not in ('1','true','yes','on'):
    raise SystemExit('V221D_ABORT KIWOOM_MOCK_ORDER_ENABLE is not enabled')

env=os.environ.copy()
for k in ALLOWED:
    env.pop(k,None)
env.update(vals)
print('MOCK_BASE_OK=True ORDER_ENABLE_OK=True')
print('EXEC=',TARGET)
rc=subprocess.call(['/home/ubuntu/day-trader-api/venv/bin/python3',str(TARGET)],cwd='/home/ubuntu/day-trader-api-repo',env=env)
raise SystemExit(rc)
