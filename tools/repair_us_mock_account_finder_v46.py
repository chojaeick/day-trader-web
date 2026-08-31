#!/usr/bin/env python3
from __future__ import annotations

import json, os, py_compile, re, subprocess, tempfile, time, urllib.request
from pathlib import Path

RUNTIME=Path('/home/ubuntu/day-trader-api')
REPO=Path('/home/ubuntu/day-trader-api-repo')
BROKER=RUNTIME/'live_server'/'kiwoom_us_mock_broker.py'
RUNNER=RUNTIME/'live_server'/'v22e_us_mock_live.py'
API=RUNTIME/'live_server'/'api.py'
APP=REPO/'app_v5.py'
ENV=RUNTIME/'.env'
ACCOUNT=RUNTIME/'v22e_us_mock_account.json'
EVAL=RUNTIME/'v22e_us_mock_eval.json'
LOG=Path('/tmp/daytrader-v5.log')
PORT=8503


def run(*args,check=True,capture=False):
    print('+',' '.join(map(str,args)),flush=True)
    return subprocess.run(list(map(str,args)),check=check,text=True,capture_output=capture)


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
    if 'def balance_all(self)' not in s:
        anchor='''    def balance(self, symbol: str = "", exchange: str = "NY") -> dict[str, Any]:\n        ex = self._check_exchange(exchange)\n        return self._post("/api/us/acnt", "ust21070", {"stex_tp": ex, "stk_cd": str(symbol).upper().strip()})\n'''
        add=anchor+'''\n    def balance_all(self) -> dict[str, Any]:\n        # ust21070 allows both stex_tp and stk_cd to be omitted for whole-account holdings.\n        return self._post("/api/us/acnt", "ust21070", {})\n\n    def overseas_deposit(self) -> dict[str, Any]:\n        # Official overseas-stock deposit TR. Keep order logic completely separate.\n        return self._post("/api/us/acnt", "ust21100", {})\n'''
        if anchor not in s: raise SystemExit('ABORT broker balance anchor missing')
        s=s.replace(anchor,add,1)
    return s


def patch_runner(s):
    if 'V46_WHOLE_ACCOUNT_BALANCE' in s:
        return s
    marker="V45_REGULAR_OPEN_REEVAL = True"
    if marker not in s: raise SystemExit('ABORT V45 runner marker missing')
    s=s.replace(marker,marker+"\nV46_WHOLE_ACCOUNT_BALANCE = True",1)

    start=s.find('def publish_live_account():')
    end=s.find('\n\ndef refresh_holdings(',start)
    if start<0 or end<0: raise SystemExit('ABORT publish_live_account block missing')
    new_func=r'''def _num(v):
    try:
        return float(str(v).replace(',', '').strip() or 0)
    except Exception:
        return 0.0


def _walk_values(obj, keys):
    vals=[]
    if isinstance(obj, dict):
        for k,v in obj.items():
            if k in keys:
                x=_num(v)
                if x or str(v).strip() in ('0','0.0','0.00'):
                    vals.append(x)
            if isinstance(v,(dict,list)):
                vals.extend(_walk_values(v,keys))
    elif isinstance(obj,list):
        for v in obj:
            vals.extend(_walk_values(v,keys))
    return vals


def publish_live_account():
    # V46: whole-account ust21070 first. Friday's symbol-specific path remains
    # as a conservative fallback for known/evaluated symbols if mock returns no list.
    b=broker(); errors=[]; holdings={}; bal={}
    try:
        bal=b.balance_all()
    except Exception as e:
        errors.append('ust21070_all:'+repr(e)); bal={}

    def ingest(rows, exchange_hint=''):
        for x in rows or []:
            sym=str(x.get('stk_cd') or '').upper().strip()
            qty=i(x.get('poss_qty') or x.get('sell_alowq') or x.get('qty') or 0)
            if not sym or qty<=0: continue
            holdings[sym]={
                'symbol':sym,'exchange':exchange_hint or str(x.get('stex_tp') or ''),
                'name':x.get('frgn_stk_nm') or sym,'qty':qty,
                'sellable_qty':i(x.get('sell_alowq') or qty),
                'avg':f(x.get('frgn_stk_book_uv')),'price':f(x.get('now_pric')),
                'market_value':f(x.get('evlt_amt')),'pnl':f(x.get('pl_amt')),
                'pnl_pct':f(x.get('pl_rt')),'purchase_amount':f(x.get('frgn_stk_book_amt')),
            }
    ingest(bal.get('result_list') or [])

    # Mock fallback: query the symbols the engine/UI actually knows, exactly as
    # the Friday DBB runner did. This is only used if whole-account list is empty.
    if not holdings:
        known=[]
        try:
            d=json.loads(EVAL_PATH.read_text(encoding='utf-8')) if EVAL_PATH.exists() else {}
            known += list((d.get('rows') or {}).keys())
        except Exception: pass
        try: known += list(load_state().keys())
        except Exception: pass
        known += ['SPCX','SOXL','SOXS','SPY','QQQ','TQQQ','SQQQ']
        seen=[]
        for sym in known:
            sym=str(sym or '').upper().strip()
            if sym and sym not in seen: seen.append(sym)
        for sym in seen[:30]:
            ex=settings.exchange_for(sym)
            try:
                r=b.balance(sym,ex); ingest(r.get('result_list') or [],ex)
            except Exception as e:
                errors.append(f'ust21070_{sym}:'+repr(e))
            time.sleep(1.05)  # mock: same TR max 1/sec

    deposit_resp={}
    try:
        deposit_resp=b.overseas_deposit()
    except Exception as e:
        errors.append('ust21100:'+repr(e))

    cash_vals=_walk_values(deposit_resp,{'fc_entra','frgn_entra','entra','cash','cash_amt'})
    order_vals=_walk_values(deposit_resp,{'fc_pymn_alowa','pymn_alowa','ord_psbl_amt','ord_alow_amt','buy_alow_amt'})
    cash=max(cash_vals) if cash_vals else 0.0
    orderable=max(order_vals) if order_vals else cash

    top_eval=f(bal.get('tot_evlt_amt'))
    top_purchase=f(bal.get('tot_prch_amt'))
    top_pnl=f(bal.get('tot_pl_amt'))
    row_value=sum((h.get('market_value') or h.get('qty',0)*h.get('price',0)) for h in holdings.values())
    row_purchase=sum((h.get('purchase_amount') or h.get('qty',0)*h.get('avg',0)) for h in holdings.values())
    stock_value=top_eval or row_value
    purchase=top_purchase or row_purchase
    pnl=top_pnl if top_pnl else sum(h.get('pnl',0) for h in holdings.values())
    payload={
        'broker':'KIWOOM_US_MOCK_ONLY','account_api':'ust21070','cash_api':'ust21100',
        'currency':'USD','cash':cash,'orderable_cash':orderable,
        'stock_value':stock_value,'purchase_amount':purchase,
        'total_assets':stock_value+cash,'pnl':pnl,
        'holdings':list(holdings.values()),'holding_count':len(holdings),
        'balance_top':{k:bal.get(k) for k in ('crnc_code','tot_evlt_amt','tot_prch_amt','tot_pl_amt','tot_pl_rt','tdy_pl_amt','tdy_pl_rt')},
        'deposit_keys':sorted(deposit_resp.keys()) if isinstance(deposit_resp,dict) else [],
        'errors':errors[-12:],'updated_at':datetime.now(timezone.utc).isoformat(),
    }
    try:
        tmp=ACCOUNT_PATH.with_suffix('.tmp'); tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8'); tmp.replace(ACCOUNT_PATH)
    except Exception as e:
        log('ACCOUNT_STATE_WRITE_ERROR',error=repr(e))
    return payload
'''
    s=s[:start]+new_func+s[end:]

    # Use the same whole-account source for trading reconciliation. If blank list
    # is returned, retain the targeted fallback already used by publish_live_account.
    old='''    out = {}\n    b = broker()\n    for idx, ex in enumerate(('NY','ND','NA')):\n        try:\n            r = b.balance('', ex)\n            for x in r.get('result_list') or []:\n                h = parse_holding(x, ex)\n                if h:\n                    out[h['symbol']] = h\n        except Exception as e:\n            log('ACCOUNT_READ_ERROR', exchange=ex, error=repr(e))\n        if idx < 2:\n            time.sleep(0.8)\n'''
    new='''    out = {}\n    b = broker()\n    try:\n        r=b.balance_all()\n        for x in r.get('result_list') or []:\n            h=parse_holding(x, str(x.get('stex_tp') or settings.exchange_for(str(x.get('stk_cd') or ''))))\n            if h: out[h['symbol']]=h\n    except Exception as e:\n        log('ACCOUNT_READ_ERROR', exchange='ALL', error=repr(e))\n    if not out:\n        # Target only currently known broker/engine symbols so the trading loop is not\n        # blocked by a full-universe account scan. Friday path: balance(symbol,exchange).\n        known=list(_holdings_cache) + list(load_state()) + ['SPCX','SOXL','SOXS']\n        seen=[]\n        for sym in known:\n            sym=str(sym or '').upper().strip()\n            if sym and sym not in seen: seen.append(sym)\n        for sym in seen[:8]:\n            ex=settings.exchange_for(sym)\n            try:\n                rr=b.balance(sym,ex)\n                for x in rr.get('result_list') or []:\n                    h=parse_holding(x,ex)\n                    if h: out[h['symbol']]=h\n            except Exception as e:\n                log('ACCOUNT_READ_ERROR', exchange=ex, symbol=sym, error=repr(e))\n            time.sleep(1.05)\n'''
    if old in s:
        s=s.replace(old,new,1)
    else:
        raise SystemExit('ABORT refresh_holdings V45 anchor missing')
    return s


def patch_api(s):
    if 'V46_V22E_FINDER_FALLBACK' in s:
        return s
    anchor="manual_scan_state={'last_started_monotonic':0.0,'last_result':None}"
    helper=r'''

V46_V22E_FINDER_FALLBACK=True
V46_US_EVAL_PATH=Path('/home/ubuntu/day-trader-api/v22e_us_mock_eval.json')

def _v46_usa_eval_finder():
    try:
        d=json.loads(V46_US_EVAL_PATH.read_text(encoding='utf-8')) if V46_US_EVAL_PATH.exists() else {}
        rows=[]
        for sym,r0 in (d.get('rows') or {}).items():
            r=dict(r0 or {}); score=r.get('effective_score') if r.get('effective_score') is not None else r.get('score')
            rows.append({
                'market':'USA','symbol':str(sym).upper(),'name':str(sym).upper(),
                'finder_score':float(score or 0),'price':r.get('price'),
                'direction':'UP' if bool(r.get('enter')) else 'WATCH',
                'finder_reason':r.get('reason') or 'V22E live evaluation',
                'engine5_v22_decision':r,'source':'V22E_EVAL_FALLBACK',
            })
        rows.sort(key=lambda x:x.get('finder_score') or 0,reverse=True)
        for n,r in enumerate(rows[:20],1): r['rank']=n
        return {'rows':rows[:20],'updated_at':d.get('updated_at'),'session':(rows[0].get('engine5_v22_decision') or {}).get('session') if rows else None,'source':'V22E_EVAL_FALLBACK'}
    except Exception:
        return {'rows':[],'updated_at':None,'source':'V22E_EVAL_FALLBACK_ERROR'}
'''
    if anchor not in s: raise SystemExit('ABORT API global anchor missing')
    s=s.replace(anchor,anchor+helper,1)

    old="""def v4_status(market:str):\n    market=market.upper()\n    if market not in ('USA','KOREA'): raise HTTPException(400,'market must be USA or KOREA')\n    return v4.status(market)"""
    new="""def v4_status(market:str):\n    market=market.upper()\n    if market not in ('USA','KOREA'): raise HTTPException(400,'market must be USA or KOREA')\n    out=v4.status(market)\n    if market=='USA' and not ((out.get('finder') or {}).get('rows') or []):\n        fb=_v46_usa_eval_finder()\n        if fb.get('rows'): out['finder']=fb\n    return out"""
    if old not in s: raise SystemExit('ABORT v4_status anchor missing')
    s=s.replace(old,new,1)

    old2="""def v4_finder(market:str):\n    market=market.upper()\n    if market=='USA': return v4.build_usa_finder(screener_rows(db.quotes(),db.daily_metrics(),40),k.discovery,20,db=db)"""
    new2="""def v4_finder(market:str):\n    market=market.upper()\n    if market=='USA':\n        out=v4.build_usa_finder(screener_rows(db.quotes(),db.daily_metrics(),40),k.discovery,20,db=db)\n        return out if (out.get('rows') or []) else _v46_usa_eval_finder()"""
    if old2 in s: s=s.replace(old2,new2,1)
    return s


def patch_app(s):
    # Correct any surviving old US badge. V5 already reads V45 account snapshot.
    s=s.replace("ENGINE5 V22 &nbsp;&nbsp; <span class=\"g\">LIVE</span>","ENGINE5 V22E &nbsp;&nbsp; <span class=\"g\">LIVE</span>")
    s=s.replace('DAY TRADER V5 <small>v45</small>','DAY TRADER V5 <small>v46</small>')
    return s


def safe_live_probe():
    code=r'''import os,sys,json,time\nos.chdir('/home/ubuntu/day-trader-api');sys.path.insert(0,'/home/ubuntu/day-trader-api')\nfrom dotenv import load_dotenv;load_dotenv('/home/ubuntu/day-trader-api/.env')\nfrom live_server.kiwoom_us_mock_broker import KiwoomUSMockBroker\nb=KiwoomUSMockBroker()\ndef view(tag,d):\n print(tag,json.dumps({'return_code':d.get('return_code'),'crnc_code':d.get('crnc_code'),'tot_evlt_amt':d.get('tot_evlt_amt'),'tot_prch_amt':d.get('tot_prch_amt'),'tot_pl_amt':d.get('tot_pl_amt'),'rows':len(d.get('result_list') or []),'symbols':[str(x.get('stk_cd') or '') for x in (d.get('result_list') or [])[:8]],'keys':sorted(d.keys())},ensure_ascii=False))\ntry:view('UST21070_ALL',b.balance_all())\nexcept Exception as e:print('UST21070_ALL_ERROR',repr(e))\nfor ex in ('ND','NY','NA'):\n try:view('UST21070_SPCX_'+ex,b.balance('SPCX',ex))\n except Exception as e:print('UST21070_SPCX_'+ex+'_ERROR',repr(e))\n time.sleep(1.05)\ntry:\n d=b.overseas_deposit(); print('UST21100_KEYS',sorted(d.keys())); print('UST21100_BODY',json.dumps({k:v for k,v in d.items() if k not in ('token','authorization')},ensure_ascii=False)[:3000])\nexcept Exception as e:print('UST21100_ERROR',repr(e))\n'''
    p=run('sudo','-u','ubuntu','-H',RUNTIME/'venv/bin/python','-c',code,check=False,capture=True)
    if p.stdout: print(p.stdout.strip(),flush=True)
    if p.stderr: print('PROBE_STDERR',p.stderr.strip(),flush=True)


def main():
    for p in (BROKER,RUNNER,API,APP,ENV):
        if not p.exists(): raise SystemExit('ABORT missing '+str(p))
    olds={p:p.read_text(encoding='utf-8') for p in (BROKER,RUNNER,API,APP)}
    news={BROKER:patch_broker(olds[BROKER]),RUNNER:None,API:None,APP:None}
    # Runner patch needs the broker method names in text only, no import side effect.
    news[RUNNER]=patch_runner(olds[RUNNER]); news[API]=patch_api(olds[API]); news[APP]=patch_app(olds[APP])
    tmps={}
    try:
        for p,t in news.items(): tmps[p]=compile_text(t,'v46_')
        print('PY_COMPILE=PASS',flush=True)
        for p,tmp in tmps.items():
            bak=Path(str(p)+'.pre_v46')
            if not bak.exists(): run('sudo','cp','-a',p,bak)
            run('sudo','install','-m','0644',tmp,p) if str(p).startswith(str(RUNTIME)) else p.write_text(news[p],encoding='utf-8')
    finally:
        for t in tmps.values(): t.unlink(missing_ok=True)

    safe_live_probe()

    run('sudo','systemctl','restart','day-trader-api'); wait_http('http://127.0.0.1:8000/health',60); print('API_HEALTH=PASS',flush=True)
    run('sudo','systemctl','restart','day-trader-v22e-us'); time.sleep(5)
    if subprocess.check_output(['sudo','systemctl','is-active','day-trader-v22e-us'],text=True).strip()!='active':
        run('sudo','journalctl','-u','day-trader-v22e-us','-n','100','--no-pager',check=False); raise SystemExit('ABORT V22E inactive')
    print('V22E_SERVICE=ACTIVE',flush=True)

    # Give account publisher enough time. Targeted fallback can take ~10s if needed.
    deadline=time.time()+35
    while time.time()<deadline:
        if ACCOUNT.exists():
            try:
                d=json.loads(ACCOUNT.read_text(encoding='utf-8'))
                if d.get('updated_at'): break
            except Exception: pass
        time.sleep(2)
    if ACCOUNT.exists():
        d=json.loads(ACCOUNT.read_text(encoding='utf-8'))
        print('US_MOCK_ACCOUNT',json.dumps({'total_assets':d.get('total_assets'),'cash':d.get('cash'),'orderable_cash':d.get('orderable_cash'),'stock_value':d.get('stock_value'),'holdings':d.get('holding_count'),'symbols':[x.get('symbol') for x in d.get('holdings') or []],'balance_top':d.get('balance_top'),'deposit_keys':d.get('deposit_keys'),'errors':d.get('errors')},ensure_ascii=False),flush=True)

    st=json.loads(urllib.request.urlopen('http://127.0.0.1:8000/api/v4/USA/status',timeout=10).read().decode())
    fr=(st.get('finder') or {}).get('rows') or []
    print('USA_SESSION='+str(st.get('session')),flush=True)
    print('USA_FINDER_ROWS='+str(len(fr)),flush=True)
    print('USA_FINDER_SOURCE='+str((st.get('finder') or {}).get('source') or 'V4_LIVE'),flush=True)
    print('USA_FINDER_SYMBOLS='+','.join(str(x.get('symbol') or '') for x in fr[:20]),flush=True)

    run('sudo','rm','-f',LOG,check=False); run('sudo','-u','ubuntu','touch',LOG); run('sudo','chown','ubuntu:ubuntu',LOG)
    subprocess.run(['sudo','pkill','-f','streamlit run app_v5.py'],check=False); time.sleep(1)
    cmd=f"cd {REPO} && DAYTRADER_API_URL=http://127.0.0.1:8000 nohup {RUNTIME}/venv/bin/python -m streamlit run app_v5.py --server.address=0.0.0.0 --server.port={PORT} --server.headless=true > {LOG} 2>&1 &"
    run('sudo','-u','ubuntu','-H','bash','-lc',cmd); wait_http(f'http://127.0.0.1:{PORT}/',45); print('V5_HTTP=PASS',flush=True)

    print('US_BALANCE_API=ust21070_WHOLE_ACCOUNT_PLUS_TARGETED_FALLBACK',flush=True)
    print('US_CASH_API=ust21100',flush=True)
    print('US_FINDER_ZERO_FALLBACK=V22E_LIVE_EVAL',flush=True)
    print('US_BUY_AUTHORITY=ENGINE5_V22E_USA',flush=True)
    print('US_SELL_AUTHORITY=ENGINE5_V22E_USA',flush=True)
    print('WILLIAMS_US_ORDER_AUTHORITY=DISABLED_UNCHANGED',flush=True)
    print('DEPLOY=PASS',flush=True)

if __name__=='__main__': main()
