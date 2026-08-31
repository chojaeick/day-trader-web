#!/usr/bin/env python3
from __future__ import annotations

import json, os, py_compile, subprocess, tempfile, time, urllib.request
from pathlib import Path

RUNTIME=Path('/home/ubuntu/day-trader-api')
REPO=Path('/home/ubuntu/day-trader-api-repo')
BROKER=RUNTIME/'live_server'/'kiwoom_us_mock_broker.py'
RUNNER=RUNTIME/'live_server'/'v22e_us_mock_live.py'
API=RUNTIME/'live_server'/'api.py'
APP=REPO/'app_v5.py'
ACCOUNT=RUNTIME/'v22e_us_mock_account.json'
EVAL=RUNTIME/'v22e_us_mock_eval.json'
LOG=Path('/tmp/daytrader-v5.log')
PORT=8503


def run(*args,check=True,capture=False,cwd=None):
    print('+',' '.join(map(str,args)),flush=True)
    return subprocess.run(list(map(str,args)),check=check,text=True,capture_output=capture,cwd=str(cwd) if cwd else None)


def compile_text(text,prefix):
    fd,name=tempfile.mkstemp(prefix=prefix,suffix='.py'); os.close(fd)
    p=Path(name); p.write_text(text,encoding='utf-8'); py_compile.compile(str(p),doraise=True); return p


def wait_http(url,seconds=60):
    end=time.time()+seconds; last=None
    while time.time()<end:
        try:
            with urllib.request.urlopen(url,timeout=3) as r:
                if r.status==200:return
        except Exception as e:last=e
        time.sleep(2)
    raise SystemExit(f'ABORT HTTP {url}: {last}')


def patch_broker(s):
    if 'def balance_all(self)' in s:
        return s
    anchor='''    def balance(self, symbol: str = "", exchange: str = "NY") -> dict[str, Any]:\n        ex = self._check_exchange(exchange)\n        return self._post("/api/us/acnt", "ust21070", {"stex_tp": ex, "stk_cd": str(symbol).upper().strip()})\n'''
    if anchor not in s: raise SystemExit('ABORT broker balance anchor missing')
    add=anchor+'''\n    def balance_all(self) -> dict[str, Any]:\n        # Kiwoom ust21070: stex_tp/stk_cd are optional; omitted stk_cd means whole account.\n        return self._post("/api/us/acnt", "ust21070", {})\n'''
    return s.replace(anchor,add,1)


def patch_runner(s):
    if 'V47_US_MARKET_OPEN_STABLE = True' in s:
        return s
    marker='V45_REGULAR_OPEN_REEVAL = True'
    if marker not in s: raise SystemExit('ABORT V45 runner marker missing')
    s=s.replace(marker,marker+'\nV47_US_MARKET_OPEN_STABLE = True',1)

    # Replace V45 account publisher with whole-account ust21070 + existing correct ust21110 cash call.
    start=s.find('def publish_live_account():')
    end=s.find('\n\ndef refresh_holdings(',start)
    if start<0 or end<0: raise SystemExit('ABORT publish_live_account block missing')
    fn=r'''def publish_live_account():
    b=broker(); errors=[]; holdings={}; bal={}; dep={}
    try:
        bal=b.balance_all()
    except Exception as e:
        errors.append('ust21070_all:'+repr(e))

    for x in bal.get('result_list') or []:
        sym=str(x.get('stk_cd') or '').upper().strip()
        qty=i(x.get('poss_qty') or x.get('sell_alowq') or x.get('qty') or 0)
        if not sym or qty<=0: continue
        holdings[sym]={
            'symbol':sym,
            'exchange':str(x.get('stex_tp') or ''),
            'name':x.get('frgn_stk_nm') or sym,
            'qty':qty,
            'sellable_qty':i(x.get('sell_alowq') or qty),
            'avg':f(x.get('frgn_stk_book_uv')),
            'price':f(x.get('now_pric')),
            'market_value':f(x.get('evlt_amt')),
            'purchase_amount':f(x.get('frgn_stk_book_amt')),
            'pnl':f(x.get('pl_amt')),
            'pnl_pct':f(x.get('pl_rt')),
        }

    # V45 deposit_usd is ust21110, the Kiwoom overseas-stock deposit TR.
    try:
        dep=b.deposit_usd()
    except Exception as e:
        errors.append('ust21110:'+repr(e))

    cash=f(dep.get('fc_entra'))
    stock_value=f(bal.get('tot_evlt_amt'))
    if not stock_value:
        stock_value=sum((h.get('market_value') or h.get('qty',0)*h.get('price',0)) for h in holdings.values())
    purchase=f(bal.get('tot_prch_amt'))
    if not purchase:
        purchase=sum((h.get('purchase_amount') or h.get('qty',0)*h.get('avg',0)) for h in holdings.values())
    pnl=f(bal.get('tot_pl_amt'))
    if not pnl:
        pnl=sum(h.get('pnl',0) for h in holdings.values())

    payload={
        'broker':'KIWOOM_US_MOCK_ONLY','account_api':'ust21070','cash_api':'ust21110',
        'currency':str(bal.get('crnc_code') or 'USD'),'cash':cash,
        'stock_value':stock_value,'purchase_amount':purchase,
        'total_assets':cash+stock_value,'pnl':pnl,
        'today_pnl':f(bal.get('tdy_pl_amt')),'today_pnl_pct':f(bal.get('tdy_pl_rt')),
        'holdings':list(holdings.values()),'holding_count':len(holdings),
        'errors':errors,'updated_at':datetime.now(timezone.utc).isoformat(),
    }
    try:
        tmp=ACCOUNT_PATH.with_suffix('.tmp')
        tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        tmp.replace(ACCOUNT_PATH)
    except Exception as e:
        log('ACCOUNT_STATE_WRITE_ERROR',error=repr(e))
    return payload
'''
    s=s[:start]+fn+s[end:]

    old='''    out = {}\n    b = broker()\n    for idx, ex in enumerate(('NY','ND','NA')):\n        try:\n            r = b.balance('', ex)\n            for x in r.get('result_list') or []:\n                h = parse_holding(x, ex)\n                if h:\n                    out[h['symbol']] = h\n        except Exception as e:\n            log('ACCOUNT_READ_ERROR', exchange=ex, error=repr(e))\n        if idx < 2:\n            time.sleep(0.8)\n'''
    new='''    out = {}\n    b = broker()\n    try:\n        r=b.balance_all()\n        for x in r.get('result_list') or []:\n            sym=str(x.get('stk_cd') or '').upper().strip()\n            ex=str(x.get('stex_tp') or settings.exchange_for(sym))\n            h=parse_holding(x,ex)\n            if h: out[h['symbol']]=h\n    except Exception as e:\n        log('ACCOUNT_READ_ERROR', exchange='ALL', error=repr(e))\n'''
    if old not in s: raise SystemExit('ABORT V45 refresh_holdings anchor missing')
    s=s.replace(old,new,1)
    return s


def patch_api(s):
    if 'V47_US_FINDER_V22E_FALLBACK' in s:
        return s
    anchor="manual_scan_state={'last_started_monotonic':0.0,'last_result':None}"
    if anchor not in s: raise SystemExit('ABORT API global anchor missing')
    helper=r'''

V47_US_FINDER_V22E_FALLBACK=True
V47_US_EVAL_PATH=Path('/home/ubuntu/day-trader-api/v22e_us_mock_eval.json')

def _v47_usa_v22e_finder():
    try:
        d=json.loads(V47_US_EVAL_PATH.read_text(encoding='utf-8')) if V47_US_EVAL_PATH.exists() else {}
        rows=[]
        for sym,r0 in (d.get('rows') or {}).items():
            r=dict(r0 or {})
            score=r.get('effective_score') if r.get('effective_score') is not None else r.get('score')
            rows.append({
                'market':'USA','symbol':str(sym).upper(),'name':str(sym).upper(),
                'finder_score':float(score or 0),'price':r.get('price'),
                'direction':'UP' if bool(r.get('enter')) else 'WATCH',
                'finder_reason':r.get('reason') or 'V22E live evaluation',
                'engine5_v22_decision':r,'source':'V22E_LIVE_EVAL',
            })
        rows.sort(key=lambda x:x.get('finder_score') or 0,reverse=True)
        rows=rows[:20]
        for n,r in enumerate(rows,1): r['rank']=n
        return {'rows':rows,'updated_at':d.get('updated_at'),'source':'V22E_LIVE_EVAL'}
    except Exception as e:
        return {'rows':[],'updated_at':None,'source':'V22E_LIVE_EVAL_ERROR','error':repr(e)}
'''
    s=s.replace(anchor,anchor+helper,1)

    old="""def v4_status(market:str):\n    market=market.upper()\n    if market not in ('USA','KOREA'): raise HTTPException(400,'market must be USA or KOREA')\n    return v4.status(market)"""
    new="""def v4_status(market:str):\n    market=market.upper()\n    if market not in ('USA','KOREA'): raise HTTPException(400,'market must be USA or KOREA')\n    out=v4.status(market)\n    if market=='USA' and not ((out.get('finder') or {}).get('rows') or []):\n        fb=_v47_usa_v22e_finder()\n        if fb.get('rows'): out['finder']=fb\n    return out"""
    if old not in s: raise SystemExit('ABORT API status anchor missing')
    s=s.replace(old,new,1)
    return s


def patch_app(s):
    # V45 already reads v22e_us_mock_account.json; ensure US engine badge is V22E.
    s=s.replace("engine_txt='ENGINE5 V22' if market=='KOREA' else 'ENGINE5 V22'","engine_txt='ENGINE5 V22' if market=='KOREA' else 'ENGINE5 V22E'")
    s=s.replace("US <span class=\"g\">● "+"'+str(sess)+'"+"</span> &nbsp;&nbsp; ENGINE5 V22 &nbsp;&nbsp;","US <span class=\"g\">● "+"'+str(sess)+'"+"</span> &nbsp;&nbsp; ENGINE5 V22E &nbsp;&nbsp;")
    s=s.replace('DAY TRADER V5 <small>v45</small>','DAY TRADER V5 <small>v47</small>')
    s=s.replace('DAY TRADER V5 <small>v44</small>','DAY TRADER V5 <small>v47</small>')
    return s


def main():
    for p in (BROKER,RUNNER,API,APP):
        if not p.exists(): raise SystemExit('ABORT missing '+str(p))
    old={p:p.read_text(encoding='utf-8') for p in (BROKER,RUNNER,API,APP)}
    new={BROKER:patch_broker(old[BROKER]),RUNNER:patch_runner(old[RUNNER]),API:patch_api(old[API]),APP:patch_app(old[APP])}
    tmps={}
    try:
        for p,t in new.items(): tmps[p]=compile_text(t,'v47_')
        print('PY_COMPILE=PASS',flush=True)
        for p,tmp in tmps.items():
            bak=Path(str(p)+'.pre_v47')
            if not bak.exists(): run('sudo','cp','-a',p,bak)
            if str(p).startswith(str(RUNTIME)): run('sudo','install','-m','0644',tmp,p)
            else: p.write_text(new[p],encoding='utf-8')
    finally:
        for t in tmps.values(): t.unlink(missing_ok=True)

    # Import real runtime before restart.
    code="import os,sys;os.chdir('/home/ubuntu/day-trader-api');sys.path.insert(0,'/home/ubuntu/day-trader-api');from dotenv import load_dotenv;load_dotenv('/home/ubuntu/day-trader-api/.env');import live_server.api,live_server.v22e_us_mock_live;from live_server.kiwoom_us_mock_broker import KiwoomUSMockBroker;b=KiwoomUSMockBroker();d=b.balance_all();print('UST21070_ALL_RETURN',d.get('return_code'));print('UST21070_ALL_ROWS',len(d.get('result_list') or []));print('UST21070_ALL_TOTAL',d.get('tot_evlt_amt'));print('UST21070_ALL_SYMBOLS',','.join(str(x.get('stk_cd') or '') for x in (d.get('result_list') or [])[:20]));c=b.deposit_usd();print('UST21110_FC_ENTRA',c.get('fc_entra'));print('RUNTIME_IMPORT=PASS')"
    p=run('sudo','-u','ubuntu','-H',RUNTIME/'venv/bin/python','-c',code,check=False,capture=True,cwd=RUNTIME)
    if p.stdout: print(p.stdout.strip(),flush=True)
    if p.returncode!=0:
        if p.stderr: print('RUNTIME_PROBE_STDERR',p.stderr.strip(),flush=True)
        raise SystemExit('ABORT live Kiwoom probe failed before service restart')

    run('sudo','systemctl','restart','day-trader-api'); wait_http('http://127.0.0.1:8000/health',60); print('API_HEALTH=PASS',flush=True)
    run('sudo','systemctl','restart','day-trader-v22e-us'); time.sleep(6)
    if subprocess.check_output(['sudo','systemctl','is-active','day-trader-v22e-us'],text=True).strip()!='active':
        run('sudo','journalctl','-u','day-trader-v22e-us','-n','100','--no-pager',check=False); raise SystemExit('ABORT V22E inactive')
    print('V22E_SERVICE=ACTIVE',flush=True)

    # Wait for fresh account publication, not a stale V45 file.
    old_m=ACCOUNT.stat().st_mtime if ACCOUNT.exists() else 0
    end=time.time()+45
    while time.time()<end:
        if ACCOUNT.exists() and ACCOUNT.stat().st_mtime>old_m:
            break
        time.sleep(2)
    if not ACCOUNT.exists(): raise SystemExit('ABORT US account state file absent')
    acct=json.loads(ACCOUNT.read_text(encoding='utf-8'))
    print('US_MOCK_ACCOUNT='+json.dumps({'total_assets':acct.get('total_assets'),'cash':acct.get('cash'),'stock_value':acct.get('stock_value'),'holdings':acct.get('holding_count'),'symbols':[x.get('symbol') for x in acct.get('holdings') or []],'errors':acct.get('errors')},ensure_ascii=False),flush=True)

    st=json.loads(urllib.request.urlopen('http://127.0.0.1:8000/api/v4/USA/status',timeout=10).read().decode())
    fr=(st.get('finder') or {}).get('rows') or []
    print('USA_SESSION='+str(st.get('session')),flush=True)
    print('USA_FINDER_ROWS='+str(len(fr)),flush=True)
    print('USA_FINDER_SOURCE='+str((st.get('finder') or {}).get('source') or 'V4_LIVE'),flush=True)
    print('USA_FINDER_SYMBOLS='+','.join(str(x.get('symbol') or '') for x in fr[:20]),flush=True)

    j=run('sudo','journalctl','-u','day-trader-v22e-us','-n','80','--no-pager',check=False,capture=True)
    lines=[x for x in (j.stdout or '').splitlines() if any(k in x for k in ('V22E_HEARTBEAT','REGULAR_OPEN_REEVAL','ORDER_ATTEMPT','ORDER_ACCEPTED','ORDER_FAILED_NO_RETRY','BROKER_CONNECTED','AUTHORITY'))]
    print('V22E_RECENT_MARKERS_BEGIN',flush=True)
    for x in lines[-20:]: print(x,flush=True)
    print('V22E_RECENT_MARKERS_END',flush=True)

    run('sudo','rm','-f',LOG,check=False); run('sudo','-u','ubuntu','touch',LOG); run('sudo','chown','ubuntu:ubuntu',LOG)
    subprocess.run(['sudo','pkill','-f','streamlit run app_v5.py'],check=False); time.sleep(1)
    cmd=f"cd {REPO} && DAYTRADER_API_URL=http://127.0.0.1:8000 nohup {RUNTIME}/venv/bin/python -m streamlit run app_v5.py --server.address=0.0.0.0 --server.port={PORT} --server.headless=true > {LOG} 2>&1 &"
    run('sudo','-u','ubuntu','-H','bash','-lc',cmd); wait_http(f'http://127.0.0.1:{PORT}/',45); print('V5_HTTP=PASS',flush=True)

    print('US_ACCOUNT=KIWOOM_UST21070_WHOLE_ACCOUNT',flush=True)
    print('US_CASH=KIWOOM_UST21110',flush=True)
    print('US_FINDER_EMPTY_FALLBACK=V22E_LIVE_EVAL_TOP20',flush=True)
    print('US_MARKET_OPEN_REEVAL=PERMANENT',flush=True)
    print('US_BUY_AUTHORITY=ENGINE5_V22E_USA',flush=True)
    print('US_SELL_AUTHORITY=ENGINE5_V22E_USA',flush=True)
    print('DEPLOY=PASS',flush=True)

if __name__=='__main__': main()
