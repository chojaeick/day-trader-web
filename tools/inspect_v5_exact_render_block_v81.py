#!/usr/bin/env python3
from pathlib import Path
import ast, subprocess
ROOT=Path('/home/ubuntu/day-trader-api')
APP=ROOT/'app_v5.py.pre_v76_1788195415'
PY=ROOT/'venv/bin/python'
print('V81_READ_ONLY=YES')
print('SOURCE='+str(APP))
text=APP.read_text(encoding='utf-8')
lines=text.splitlines()
for a,b,label in [(1220,1305,'LINES_1220_1305')]:
    print('=== '+label+' ===')
    for i in range(a,min(b,len(lines))+1):
        print(f'{i}: {lines[i-1]}')
print('=== AST FUNCTIONS ===')
t=ast.parse(text)
for n in t.body:
    if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in {'render_positions','get_market_status'}:
        print(f'FUNC {n.name} lineno={n.lineno} end={getattr(n,"end_lineno",None)}')
        for i in range(n.lineno,min(getattr(n,'end_lineno',n.lineno),n.lineno+45)+1):
            print(f'{i}: {lines[i-1]}')
print('=== ACCOUNT ANCHORS ===')
for i,s in enumerate(lines,1):
    if 'v45_us_live_account' in s or 'status=get_market_status' in s or 'render_positions(' in s:
        print(f'{i}: {s}')
r=subprocess.run([str(PY),'-m','py_compile',str(APP)],capture_output=True,text=True)
print('SOURCE_COMPILE=' + ('PASS' if r.returncode==0 else 'FAIL'))
if r.returncode: print((r.stderr or r.stdout).strip())
print('SERVICE_RESTART=NO')
print('ORDER_SENT=NO')
