#!/usr/bin/env python3
from pathlib import Path
p=Path('/home/ubuntu/day-trader-api/app_v5.py')
if not p.exists(): raise SystemExit('ABORT app_v5.py missing')
lines=p.read_text(encoding='utf-8').splitlines()
keys=('USA','US ','V22E','finder','tracker','session','streaming','positions','holdings','account','requests.get','urllib','api/v4')
for i,x in enumerate(lines,1):
    if any(k in x for k in keys):
        print(f'{i}: {x}')
