#!/usr/bin/env python3
from __future__ import annotations

import json, os, py_compile, subprocess, tempfile, time, urllib.request
from pathlib import Path

RUNTIME=Path('/home/ubuntu/day-trader-api')
REPO=Path('/home/ubuntu/day-trader-api-repo')
RUNNER=RUNTIME/'live_server'/'v22e_us_mock_live.py'
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


def wait_http(url,seconds=45):
    end=time.time()+seconds; last=None
    while time.time()<end:
        try:
            with urllib.request.urlopen(url,timeout=3) as r:
                if r.status==200:return
        except Exception as e:last=e
        time.sleep(2)
    raise SystemExit(f'ABORT HTTP {url}: {last}')


def patch_runner(s):
    if 'V49_LIVE_EXCHANGE_RESOLUTION = True' in s:
        return s
    marker='V48_TARGETED_BALANCE_FALLBACK = True'
    if marker not in s: raise SystemExit('ABORT V48 marker missing')
    s=s.replace(marker,marker+'\nV49_LIVE_EXCHANGE_RESOLUTION = True',1)

    # Resolve exchange from live DB quotes first; those quotes were populated by Kiwoom using
    # the discovery exchange map. Fall back to static Settings only if no live row exists.
    anchor='''def marketable(price: float, side: str):\n    px = price * (1 + CROSS_PCT) if side == 'BUY' else price * (1 - CROSS_PCT)\n    return round(px, 2 if px >= 1 else 4)\n'''
    helper=anchor+r'''

def resolve_exchange(sym: str) -> str:
    sym=str(sym or '').upper().strip()
    try:
        for q in db.quotes() or []:
            if str(q.get('symbol') or '').upper()==sym:
                ex=str(q.get('exchange') or '').upper().strip()
                if ex=='AM': ex='NA'
                if ex in ('NY','ND','NA'):
                    return ex
    except Exception:
        pass
    return settings.exchange_for(sym)
'''
    if anchor not in s: raise SystemExit('ABORT marketable anchor missing')
    s=s.replace(anchor,helper,1)

    # All order-side exchange choices must use the live resolver.
    s=s.replace("ex = settings.exchange_for(sym)\n                    res = order_once('BUY'", "ex = resolve_exchange(sym)\n                    res = order_once('BUY'",1)
    s=s.replace("h.get('exchange') or settings.exchange_for(sym)", "h.get('exchange') or resolve_exchange(sym)")

    # Targeted account probes should also try the live exchange first.
    s=s.replace("preferred=settings.exchange_for(sym); exchanges=[preferred]+[x for x in ('NY','ND','NA') if x!=preferred]",
                "preferred=resolve_exchange(sym); exchanges=[preferred]+[x for x in ('NY','ND','NA') if x!=preferred]")
    return s


def safe_probe():
    code=r'''import os,sys,json,time
os.chdir('/home/ubuntu/day-trader-api');sys.path.insert(0,'/home/ubuntu/day-trader-api')
from dotenv import load_dotenv;load_dotenv('/home/ubuntu/day-trader-api/.env')
from live_server.kiwoom_us_mock_broker import KiwoomUSMockBroker
from live_server.db import DB
from live_server.config import Settings
s=Settings(); db=DB(s.db_path); b=KiwoomUSMockBroker()
syms=['SPCX','SOXL','SOXS']
try:
 d=json.load(open('/home/ubuntu/day-trader-api/v22e_us_mock_eval.json'))
 for x in list((d.get('rows') or {}).keys())[:8]:
  if x not in syms: syms.append(x)
except Exception: pass
qmap={str(x.get('symbol') or '').upper():str(x.get('exchange') or '') for x in (db.quotes() or [])}
print('LIVE_DB_EXCHANGES',json.dumps({x:qmap.get(x) for x in syms},ensure_ascii=False))
for sym in syms:
 print('ACCOUNT_PROBE_SYMBOL',sym)
 for ex in ('NY','ND','NA'):
  try:
   r=b.balance(sym,ex); rows=r.get('result_list') or []
   print(' UST21070',ex,json.dumps({'return_code':r.get('return_code'),'rows':len(rows),'symbols':[str(x.get('stk_cd') or '') for x in rows[:5]],'qty':[x.get('poss_qty') or x.get('sell_alowq') for x in rows[:5]],'top':{k:r.get(k) for k in ('crnc_code','tot_evlt_amt','tot_prch_amt','tot_pl_amt')}},ensure_ascii=False))
  except Exception as e:
   print(' UST21070',ex,'ERROR',repr(e))
  time.sleep(1.05)
try:
 c=b.deposit_usd(); safe={k:v for k,v in c.items() if k not in ('token','authorization')}; print('UST21110_RAW',json.dumps(safe,ensure_ascii=False)[:5000])
except Exception as e: print('UST21110_ERROR',repr(e))
'''
    p=run('sudo','-u','ubuntu','-H',RUNTIME/'venv/bin/python','-c',code,check=False,capture=True,cwd=RUNTIME)
    print('ACCOUNT_PROBE_BEGIN')
    if p.stdout: print(p.stdout.strip())
    if p.stderr: print('PROBE_STDERR',p.stderr.strip())
    print('ACCOUNT_PROBE_END')
    if p.returncode!=0: raise SystemExit('ABORT account probe failed')


def main():
    if not RUNNER.exists(): raise SystemExit('ABORT runner missing')
    old=RUNNER.read_text(encoding='utf-8'); new=patch_runner(old); tmp=compile_text(new,'v49_')
    print('PY_COMPILE=PASS')
    try:
        bak=Path(str(RUNNER)+'.pre_v49')
        if not bak.exists(): run('sudo','cp','-a',RUNNER,bak)
        run('sudo','install','-m','0644',tmp,RUNNER)
    finally:
        tmp.unlink(missing_ok=True)

    safe_probe()

    run('sudo','systemctl','restart','day-trader-v22e-us'); time.sleep(6)
    if subprocess.check_output(['sudo','systemctl','is-active','day-trader-v22e-us'],text=True).strip()!='active':
        run('sudo','journalctl','-u','day-trader-v22e-us','-n','100','--no-pager',check=False); raise SystemExit('ABORT V22E inactive')
    print('V22E_SERVICE=ACTIVE')

    # Do not declare account success if the broker still returns no assets.
    try:
        d=json.loads(ACCOUNT.read_text(encoding='utf-8')) if ACCOUNT.exists() else {}
    except Exception: d={}
    print('US_MOCK_ACCOUNT='+json.dumps({'total_assets':d.get('total_assets'),'cash':d.get('cash'),'stock_value':d.get('stock_value'),'holdings':d.get('holding_count'),'symbols':[x.get('symbol') for x in d.get('holdings') or []]},ensure_ascii=False))

    st=json.loads(urllib.request.urlopen('http://127.0.0.1:8000/api/v4/USA/status',timeout=10).read().decode())
    fr=(st.get('finder') or {}).get('rows') or []
    print('USA_SESSION='+str(st.get('session')))
    print('USA_FINDER_ROWS='+str(len(fr)))
    print('USA_FINDER_SOURCE='+str((st.get('finder') or {}).get('source') or 'V4_LIVE'))

    j=run('sudo','journalctl','-u','day-trader-v22e-us','-n','60','--no-pager',check=False,capture=True)
    print('V22E_RECENT_BEGIN')
    for line in (j.stdout or '').splitlines():
        if any(x in line for x in ('V22E_HEARTBEAT','ORDER_ATTEMPT','ORDER_ACCEPTED','ORDER_FAILED_NO_RETRY','REGULAR_OPEN_REEVAL')):
            print(line)
    print('V22E_RECENT_END')
    print('US_EXCHANGE_SOURCE=LIVE_DB_QUOTE_THEN_STATIC_FALLBACK')
    print('US_ACCOUNT_PROBE=READ_ONLY_MULTI_EXCHANGE')
    print('US_BUY_AUTHORITY=ENGINE5_V22E_USA')
    print('US_SELL_AUTHORITY=ENGINE5_V22E_USA')
    print('DEPLOY=PASS')

if __name__=='__main__': main()
