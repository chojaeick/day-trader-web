#!/usr/bin/env python3
from pathlib import Path
import os,re,subprocess

print('=== V221C FIND KIWOOM MOCK ENV SOURCE (READ ONLY) ===')
print('MUTATION=NONE REAL_ORDER=NONE')
keys=('KIWOOM_MOCK_APP_KEY','KIWOOM_MOCK_APP_SECRET','KIWOOM_MOCK_ORDER_ENABLE','KIWOOM_MOCK_REST_BASE','KIWOOM_APP_KEY','KIWOOM_APP_SECRET')

# 1) current shell/process env
present=[k for k in keys if os.getenv(k)]
print('CURRENT_PROCESS_KEYS=',present)

# 2) systemd unit metadata, including EnvironmentFiles, without exposing secret values
for unit in ('day-trader-api.service','day-trader-v5.service'):
    p=subprocess.run(['systemctl','show',unit,'-p','EnvironmentFiles','-p','FragmentPath','-p','DropInPaths'],text=True,capture_output=True)
    print(f'[{unit}] RC={p.returncode}')
    for line in p.stdout.splitlines():
        print(line)

# 3) likely dotenv/config files in runtime tree. Print only key NAMES, never values.
roots=[Path('/home/ubuntu/day-trader-api'),Path('/home/ubuntu')]
seen=set()
for root in roots:
    if not root.exists(): continue
    for pat in ('.env','.env.*','*.env','config*','settings*'):
        for f in root.glob(pat) if root==Path('/home/ubuntu/day-trader-api') else []:
            if f.is_file(): seen.add(f)

# also inspect exact runtime .env candidates
for f in [Path('/home/ubuntu/day-trader-api/.env'),Path('/home/ubuntu/day-trader-api/.env.local'),Path('/home/ubuntu/.env')]:
    if f.exists() and f.is_file(): seen.add(f)

for f in sorted(seen):
    try: txt=f.read_text(errors='ignore')
    except Exception: continue
    found=[k for k in keys if re.search(rf'(?m)^\s*{re.escape(k)}\s*=',txt)]
    if found:
        print('ENV_FILE_CANDIDATE=',f,'KEYS=',found)

# 4) inspect service command line only
p=subprocess.run(['systemctl','show','day-trader-api.service','-p','ExecStart','-p','WorkingDirectory'],text=True,capture_output=True)
print(p.stdout.strip())
print('NEXT=USE_EXACT_DISCOVERED_SOURCE; DO_NOT_GUESS')
