#!/usr/bin/env python3
from pathlib import Path
p=Path('/home/ubuntu/day-trader-api/live_server/v22e_us_mock_live.py')
lines=p.read_text(encoding='utf-8').splitlines()
for a,b,title in [(350,430,'ACCOUNT_READ'),(480,625,'MAIN_LOOP')]:
    print(f'===== {title} lines {a}-{b} =====')
    for i in range(a-1,min(b,len(lines))):
        print(f'{i+1}: {lines[i]}')
