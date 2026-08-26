#!/usr/bin/env python3
from pathlib import Path
import shutil, re, py_compile

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
KIO=Path('/home/ubuntu/day-trader-api/live_server/kiwoom.py')
FROZEN=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']
print('=== V180B RETARGET SINGLE WS FROZEN19 PRIORITY ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')

for p in (API,KIO):
    if not p.exists(): raise SystemExit(f'MISSING {p}')

# backups
for p in (API,KIO):
    b=p.with_name(p.name+'.bak_v180b')
    shutil.copy2(p,b)
    print('BACKUP',b)

api=API.read_text(encoding='utf-8')
kio=KIO.read_text(encoding='utf-8')

# Ensure dedicated frozen websocket startup task is disabled, regardless of exact formatting.
api2=api
patterns=[
    r'(?m)^\s*asyncio\.create_task\(k\.frozen19_websocket_forever\(\)\)\s*$',
    r'(?m)^\s*asyncio\.create_task\([^\n]*frozen19[^\n]*websocket[^\n]*\)\s*$',
]
disabled=0
for pat in patterns:
    def repl(m):
        nonlocal_disabled[0]+=1
        indent=re.match(r'\s*',m.group(0)).group(0)
        return indent+'# V180B disabled dedicated frozen19 websocket; single USA WS only.'
    nonlocal_disabled=[0]
    api2=re.sub(pat,repl,api2)
    disabled+=nonlocal_disabled[0]

# Also hard-disable direct awaited/background call if present but leave function definition intact.
api2=api2.replace('await k.frozen19_websocket_forever()','# V180B disabled: await k.frozen19_websocket_forever()')
API.write_text(api2,encoding='utf-8')

# Inject helper into KiwoomClient once. It creates single websocket ordered universe:
# frozen19 first, then dynamic symbols, de-duplicated.
marker='# V180B_SINGLE_WS_FROZEN19_PRIORITY_HELPER'
if marker not in kio:
    anchor='    def active_symbols(self) -> list[str]:'
    idx=kio.find(anchor)
    if idx<0: raise SystemExit('ANCHOR active_symbols NOT FOUND')
    helper=f'''    {marker}\n    def _v180b_ws_priority_symbols(self):\n        frozen={FROZEN!r}\n        dynamic=list(getattr(self.s,'symbols',[]) or [])\n        out=[]\n        for sym in frozen+dynamic:\n            sym=str(sym or '').upper()\n            if sym and sym not in out:\n                out.append(sym)\n        return out\n\n'''
    kio=kio[:idx]+helper+kio[idx:]

# Find websocket_forever body and replace ONLY places that derive registration universe from self.s.symbols.
# Covers tuple/list/current assignments introduced by older versions.
start=kio.find('    async def websocket_forever(')
if start<0: raise SystemExit('websocket_forever NOT FOUND')
end=kio.find('\n    async def ',start+10)
if end<0: end=len(kio)
body=kio[start:end]
orig_body=body
repls=[
    (r'current\s*=\s*tuple\(self\.s\.symbols\)', 'current=tuple(self._v180b_ws_priority_symbols())'),
    (r'current\s*=\s*list\(self\.s\.symbols\)', 'current=list(self._v180b_ws_priority_symbols())'),
    (r'current\s*=\s*self\.s\.symbols', 'current=self._v180b_ws_priority_symbols()'),
    (r'_ws_items\(tuple\(self\.s\.symbols\)\)', '_ws_items(tuple(self._v180b_ws_priority_symbols()))'),
    (r'_ws_items\(self\.s\.symbols\)', '_ws_items(self._v180b_ws_priority_symbols())'),
]
count=0
for pat,rep in repls:
    body,n=re.subn(pat,rep,body)
    count+=n

# Startup registration sometimes uses active_symbols()/local symbols before refresh loop.
# Retarget only inside websocket_forever when directly fed into _ws_items.
body,n=re.subn(r'_ws_items\(self\.active_symbols\(\)\)', '_ws_items(self._v180b_ws_priority_symbols())', body)
count+=n

if count==0:
    print('--- websocket_forever CURRENT CONTEXT ---')
    for i,line in enumerate(orig_body.splitlines(),1):
        if 'ws_items' in line or 'symbols' in line or 'registered' in line or "'REG'" in line or '"REG"' in line:
            print(i,line)
    raise SystemExit('NO SAFE WS UNIVERSE TARGET FOUND')

kio=kio[:start]+body+kio[end:]
KIO.write_text(kio,encoding='utf-8')

py_compile.compile(str(API),doraise=True); print('PY_COMPILE api.py PASS')
py_compile.compile(str(KIO),doraise=True); print('PY_COMPILE kiwoom.py PASS')

final_api=API.read_text(encoding='utf-8')
final_kio=KIO.read_text(encoding='utf-8')
print('DEDICATED_TASKS_DISABLED=',disabled)
print('WS_PRIORITY_TARGETS_PATCHED=',count)
print('SINGLE_USA_WS_ONLY=', 'create_task(k.frozen19_websocket_forever' not in final_api and 'await k.frozen19_websocket_forever()' not in final_api.replace('# V180B disabled: await k.frozen19_websocket_forever()',''))
print('FROZEN19_PRIORITY_HELPER=', marker in final_kio)
print('FROZEN19_COUNT=',len(FROZEN))
print('REAL_BROKER_CALLS_ADDED=NONE')
print('NEXT=V181_RESTART_VERIFY_SINGLE_WS_FROZEN19_LIVE_F5')
