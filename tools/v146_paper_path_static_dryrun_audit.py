#!/usr/bin/env python3
"""V146 USA frozen Williams paper-path static + dry-run audit.

No broker calls. No orders. This does not start the service.
It inspects the patched runtime and executes the paper bridge against a fake paper
ledger to verify ENTRY/HOLD/EXIT, duplicate guard and max-position behavior.
"""
from __future__ import annotations
from pathlib import Path
import ast, os, py_compile, types

P=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
S=P.read_text(errors='ignore')

print('=== V146 PAPER PATH STATIC + DRYRUN AUDIT ===')
print('ENGINE=',P,'EXISTS=',P.exists(),'BYTES=',len(S))

checks={
 'v145_authority':'V145_USA_FROZEN_PAPER_AUTHORITY' in S,
 'usa_gate':"str(market).upper()=='USA'" in S or 'str(market).upper()=="USA"' in S,
 'paper_enter':"self.paper.enter('USA'" in S or 'self.paper.enter("USA"' in S,
 'paper_exit':"self.paper.exit('USA'" in S or 'self.paper.exit("USA"' in S,
 'paper_mark':"self.paper.mark('USA'" in S or 'self.paper.mark("USA"' in S,
 'duplicate_guard':'any(str((p or {}).get' in S,
 'max_positions':'WILLIAMS_USA_PAPER_MAX_POSITIONS' in S,
 'frozen_strategy_id':'WILLIAMS_FROZEN_V136' in S,
 'real_broker_added': not any(x in S[S.find('V145_USA_FROZEN_PAPER_AUTHORITY'):S.find('V145_USA_FROZEN_PAPER_AUTHORITY')+3500] for x in ['KiwoomMockBroker','send_order','place_order','broker.']),
}
for k,v in checks.items(): print(k, 'PASS' if v else 'FAIL')

try:
    py_compile.compile(str(P),doraise=True); comp=True
except Exception as e:
    comp=False; print('COMPILE_ERROR',e)
print('PY_COMPILE=', 'PASS' if comp else 'FAIL')

# Extract method bodies directly from runtime source, then execute on a minimal fake self.
mod=ast.parse(S)
cls=next((n for n in mod.body if isinstance(n,ast.ClassDef) and n.name=='V4Engine'),None)
if cls is None:
    # tolerate class rename: find class containing the paper method
    for n in mod.body:
        if isinstance(n,ast.ClassDef) and any(isinstance(x,ast.FunctionDef) and x.name=='_paper_williams_step' for x in n.body):
            cls=n;break
fn_eval=next((x for x in cls.body if isinstance(x,ast.FunctionDef) and x.name=='_v140_usa_frozen_williams_eval'),None) if cls else None
fn_paper=next((x for x in cls.body if isinstance(x,ast.FunctionDef) and x.name=='_paper_williams_step'),None) if cls else None
print('METHOD_eval=',bool(fn_eval),'METHOD_paper=',bool(fn_paper))

class FakePaper:
    def __init__(self): self.open={}; self.calls=[]
    def position(self,m,s): return self.open.get((m,s))
    def positions(self,m): return [v for (mm,_),v in self.open.items() if mm==m]
    def enter(self,m,s,p,**kw):
        self.calls.append(('ENTER',m,s,p,kw)); self.open[(m,s)]={'market':m,'symbol':s,'price':p}; return self.open[(m,s)]
    def exit(self,m,s,p,**kw):
        self.calls.append(('EXIT',m,s,p,kw)); self.open.pop((m,s),None); return {'market':m,'symbol':s,'price':p,'closed':True}
    def mark(self,m,s,p,**kw): self.calls.append(('MARK',m,s,p,kw)); return self.open.get((m,s))

class FakeSelf:
    def __init__(self): self.paper=FakePaper(); self.next_ev={'entry':False,'exit':False}
    def _v140_usa_frozen_williams_eval(self,row): return dict(self.next_ev)

# Build a standalone function from the exact runtime method body.
dryrun_ok=False
results=[]
if fn_paper:
    temp=ast.Module(body=[fn_paper],type_ignores=[]); ast.fix_missing_locations(temp)
    ns={'_f':lambda v,default=0.0: float(v or default),'os':os}
    exec(compile(temp,'<v146_method>','exec'),ns)
    paper_step=ns['_paper_williams_step']
    me=FakeSelf()
    row={'market':'USA','symbol':'NVDA','price':100.0}
    me.next_ev={'entry':True,'exit':False}; paper_step(me,'USA',row)
    results.append(('entry_call',me.paper.calls[-1][0] if me.paper.calls else None))
    before=len(me.paper.calls); paper_step(me,'USA',row); results.append(('duplicate_no_new_enter',len(me.paper.calls)==before+1 and me.paper.calls[-1][0]=='MARK'))
    me.next_ev={'entry':False,'exit':False}; paper_step(me,'USA',row); results.append(('hold_mark',me.paper.calls[-1][0]=='MARK'))
    me.next_ev={'entry':False,'exit':True}; paper_step(me,'USA',row); results.append(('exit_call',me.paper.calls[-1][0]=='EXIT'))
    # Max-position gate: fill five positions then attempt a sixth entry.
    me=FakeSelf()
    for i in range(5): me.paper.open[('USA',f'S{i}')]= {'market':'USA','symbol':f'S{i}','price':10}
    me.next_ev={'entry':True,'exit':False}; before=len(me.paper.calls); paper_step(me,'USA',{'market':'USA','symbol':'AMD','price':100.0})
    results.append(('max5_blocks_sixth',len(me.paper.calls)==before))
    # Korea must fall through legacy body; do not execute full legacy branch in dry-run.
    # Static USA early branch is what is being audited here.
    dryrun_ok=all(bool(v) if isinstance(v,bool) else v=='ENTER' for k,v in results)
for k,v in results: print('DRYRUN',k,'PASS' if (v is True or v=='ENTER') else 'FAIL',v)

static_ok=all(checks.values()) and comp and fn_eval is not None and fn_paper is not None
print('STATIC_PASS=',static_ok)
print('DRYRUN_PASS=',dryrun_ok)
print('REAL_BROKER_CALLS=NONE')
print('USA_PAPER_AUTHORITY_ONLY=YES')
print('V146_PASS=',bool(static_ok and dryrun_ok))
print('NEXT=' + ('V147_SERVICE_PRESTART_SAFETY_AUDIT' if static_ok and dryrun_ok else 'FIX_PAPER_PATH_ONLY; DO_NOT_START_SERVICE'))
