#!/usr/bin/env python3
from pathlib import Path

print('=== V170 FROZEN UNIVERSE FEED WIRING AUDIT ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')

paths=[
    Path('/home/ubuntu/day-trader-api/live_server/api.py'),
    Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py'),
    Path('/home/ubuntu/day-trader-api/live_server/kiwoom.py'),
    Path('/home/ubuntu/day-trader-api/live_server/config.py'),
]
terms=(
    'tracked_symbols','warmed_usa','warm_usa_symbols','WebSocket universe refreshed',
    'FALLBACK_UNIVERSE','CORE_WATCHLIST','subscription','subscribe','universe',
    'refresh_usa_tracker','finder_syms','positions(\'USA\')','self.paper.positions',
)
for p in paths:
    print('\nFILE',p,'EXISTS=',p.exists())
    if not p.exists():
        continue
    lines=p.read_text(errors='replace').splitlines()
    hits=[]
    for i,line in enumerate(lines,1):
        if any(t in line for t in terms):
            hits.append(i)
    print('HITS=',hits[:80])
    for i in hits[:40]:
        a=max(1,i-6); b=min(len(lines),i+10)
        print(f'--- CONTEXT {a}:{b} ---')
        for n in range(a,b+1):
            print(f'{n}: {lines[n-1]}')

print('\nFROZEN_CORE_19=', ['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM'])
print('NEXT=PATCH_PAPER_ONLY_FROZEN_UNIVERSE_FEED_AT_SINGLE_SAFE_WIRING_POINT; KEEP_LEGACY_TRACKER_AND_STRATEGY_CONSTANTS_UNCHANGED')
