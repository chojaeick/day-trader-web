#!/usr/bin/env python3
from pathlib import Path
import os,re

print('=== V226 FIND USA MOCK ACCOUNT CREDENTIAL SOURCE (READ ONLY) ===')
print('TARGET_ACCOUNT_SUFFIX=6111-3076')
print('MUTATION=NONE REAL_ORDER=NONE SECRET_VALUES_PRINTED=NO')

needles=('6111','3076','appkey','appsecret','mock')
roots=[Path('/home/ubuntu'),Path('/home/ubuntu/day-trader-api')]
seen=set()
for root in roots:
    if not root.exists():
        continue
    try:
        for p in root.rglob('*'):
            if not p.is_file():
                continue
            name=p.name.lower()
            if any(n in name for n in needles):
                seen.add(p)
    except Exception:
        pass

for p in sorted(seen):
    s=str(p)
    # never print contents, only path metadata
    if any(x in p.name.lower() for x in ('6111','3076','appkey','appsecret','mock')):
        print('FILE_CANDIDATE=',s)

# Inspect env/config key names only, never values.
for p in [Path('/home/ubuntu/day-trader-api/.env'),Path('/home/ubuntu/.env')]:
    if not p.exists():
        continue
    try:
        txt=p.read_text(errors='ignore')
    except Exception:
        continue
    keys=[]
    for line in txt.splitlines():
        m=re.match(r'\s*([A-Za-z_][A-Za-z0-9_]*)\s*=',line)
        if not m:
            continue
        k=m.group(1)
        ku=k.upper()
        if 'KIWOOM' in ku and any(x in ku for x in ('USA','US','OVERSEAS','OVRS','MOCK','APP_KEY','APP_SECRET')):
            keys.append(k)
    print('ENV_KEY_CANDIDATES',p,sorted(set(keys)))

print('OFFICIAL_REST_NOTE=US_ORDER_HAS_NO_ACCOUNT_NUMBER_FIELD; ACCOUNT_IS_BOUND_TO_APP_KEY_SECRET')
print('NEXT=USE_6111_3076_BOUND_APP_KEY_SECRET_ONLY')
