#!/usr/bin/env python3
from pathlib import Path
P=Path('/home/ubuntu/day-trader-api/live_server/api.py')
S=P.read_text(errors='ignore').splitlines()
print('=== V154B RUNTIME TRACKER ROUTE INSPECT ===')
for i,line in enumerate(S,1):
    if "@app.get('/api/v4/{market}/tracker')" in line or '@app.get("/api/v4/{market}/tracker")' in line:
        a=max(1,i-3); b=min(len(S),i+12)
        for j in range(a,b+1): print(f'{j}: {S[j-1]}')
        break
else:
    print('TRACKER_ROUTE_NOT_FOUND')
