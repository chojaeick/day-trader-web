#!/usr/bin/env python3
from pathlib import Path
import subprocess, sys, re

print('=== V184 KIWOOM MASTER EXCHANGE CODE AUDIT ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')

ROOT=Path('/home/ubuntu/day-trader-api')
PY=ROOT/'venv/bin/python3'
if not PY.exists():
    print('VENV_PY_MISSING'); sys.exit(2)

code=r'''
import sys, json
sys.path.insert(0,'/home/ubuntu/day-trader-api')
from live_server.config import Settings
from live_server.db import DB
from live_server.kiwoom import KiwoomClient
s=Settings(); db=DB(s.db_path); k=KiwoomClient(s,db)
syms=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']
stale={'AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','SMCI','TSM'}
print('CONFIG_MAP_BEGIN')
for x in syms:
    try: ex=k.active_exchange(x)
    except Exception as e: ex=f'ERR:{e}'
    print('CONFIG',x,ex)
print('CONFIG_MAP_END')

# inspect available Kiwoom methods likely to query symbol/master/list info
names=[n for n in dir(k) if any(z in n.lower() for z in ('symbol','master','stock','usa','list','quote'))]
print('CANDIDATE_METHODS=',names)

# Prefer known REST helper methods by reflection, read-only only.
for sym in syms:
    rows=[]
    for name in names:
        if name.startswith('_'): continue
        fn=getattr(k,name,None)
        if not callable(fn): continue
        # only zero/one-symbol methods; avoid order/write names
        lname=name.lower()
        if any(z in lname for z in ('order','buy','sell','cancel','register','remove','websocket','discover')): continue
        try:
            import inspect
            sig=inspect.signature(fn)
            req=[p for p in sig.parameters.values() if p.default is p.empty and p.kind in (p.POSITIONAL_ONLY,p.POSITIONAL_OR_KEYWORD)]
            if len(req)==1:
                try:
                    v=fn(sym)
                    txt=json.dumps(v,ensure_ascii=False,default=str)[:1200]
                    if sym in txt or any(ex in txt for ex in ('ND','NY','NA','NASDAQ','NYSE','AMEX')):
                        rows.append((name,txt))
                except Exception:
                    pass
        except Exception:
            pass
    print('MASTER_PROBE',sym,'STALE' if sym in stale else 'FRESH','ROWS',len(rows))
    for n,t in rows[:8]: print(' ',n,t)
'''

p=subprocess.run([str(PY),'-c',code],text=True,capture_output=True)
print(p.stdout)
if p.stderr:
    print('STDERR=',p.stderr[-4000:])
print('RC=',p.returncode)
print('NEXT=COMPARE_KIWOOM_MASTER_EXCHANGE_WITH_WS_ITEM_MAP; IF_MISMATCH_PATCH_EXCHANGE_CODES; IF_MATCH_PROBE_SINGLE_STALE_SYMBOL_F5_VS_FRESH_CONTROL')
sys.exit(p.returncode)
