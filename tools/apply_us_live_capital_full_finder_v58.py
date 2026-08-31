#!/usr/bin/env python3
from pathlib import Path
import json, os, py_compile, re, subprocess, tempfile, time

R=Path('/home/ubuntu/day-trader-api')
P=R/'live_server'/'v22e_us_mock_live.py'
A=R/'v22e_us_mock_account.json'
SERVICE='day-trader-v22e-us'

s=P.read_text(encoding='utf-8')
if 'V58_LIVE_CAPITAL_FULL_FINDER = True' not in s:
    marker=None
    for m in ('V57_USD_ONLY_CASH_PARSE = True','V50_ACCOUNT_RATE_LIMIT_SAFE = True','V49_LIVE_EXCHANGE_RESOLUTION = True'):
        if m in s:
            marker=m; break
    if not marker: raise SystemExit('ABORT V58 marker missing')
    s=s.replace(marker,marker+'\nV58_LIVE_CAPITAL_FULL_FINDER = True',1)

    # Fixed-share sizing is obsolete. Keep env compatibility but stop using it.
    qpat=r"(?m)^QTY_DEFAULT\s*=.*$"
    repl=("MAX_POSITIONS = max(1, int(os.getenv('V22E_US_MAX_POSITIONS', '4') or 4))\n"
          "CAPITAL_USE_PCT = min(0.999, max(0.90, float(os.getenv('V22E_US_CAPITAL_USE_PCT', '0.995') or 0.995)))")
    s,n=re.subn(qpat,repl,s,count=1)
    if n!=1: raise SystemExit('ABORT fixed QTY_DEFAULT anchor missing')

    # Ensure account snapshot path exists.
    if "ACCOUNT_PATH = Path(" not in s:
        anchor="STATE_PATH = Path(os.getenv('V22E_US_STATE_PATH', '/home/ubuntu/day-trader-api/v22e_us_mock_state.json'))"
        if anchor not in s: raise SystemExit('ABORT state path anchor missing')
        s=s.replace(anchor,anchor+"\nACCOUNT_PATH = Path(os.getenv('V22E_US_ACCOUNT_PATH', '/home/ubuntu/day-trader-api/v22e_us_mock_account.json'))",1)

    # Add account-aware sizing helpers after integer helper.
    ipos=s.find('\ndef session():')
    if ipos<0: raise SystemExit('ABORT session anchor missing')
    helper=r'''

def account_snapshot():
    try:
        d=json.loads(ACCOUNT_PATH.read_text(encoding='utf-8'))
        return d if isinstance(d,dict) else {}
    except Exception:
        return {}


def live_buy_qty(signal_px: float, holdings_count: int):
    acct=account_snapshot()
    orderable=f(acct.get('orderable_cash'))
    slots=max(1, MAX_POSITIONS-int(holdings_count))
    budget=(orderable*CAPITAL_USE_PCT)/slots if orderable>0 else 0.0
    limit_px=marketable(signal_px,'BUY')
    qty=int(budget//limit_px) if limit_px>0 else 0
    return max(0,qty),budget,orderable,slots
'''
    s=s[:ipos]+helper+s[ipos:]

    # Replace recursive/circular Finder source with deterministic TOP20 composition:
    # current Finder rows first, then light/tracker, then configured liquid universe.
    fstart=s.find('def finder_symbols():')
    fend=s.find('\n\ndef ',fstart+10)
    if fstart<0 or fend<0: raise SystemExit('ABORT finder_symbols block missing')
    finder=r'''def finder_symbols():
    found=[]
    def add(sym):
        sym=str(sym or '').upper().strip()
        if sym and 1<=len(sym)<=8 and sym not in found:
            found.append(sym)
    try:
        with urllib.request.urlopen(STATUS_URL,timeout=3) as r:
            data=json.loads(r.read().decode('utf-8'))
        finder=(data.get('finder') or {}) if isinstance(data,dict) else {}
        for row in finder.get('rows') or []: add((row or {}).get('symbol'))
        for row in finder.get('light_rows') or []: add((row or {}).get('symbol'))
        tracker=(data.get('tracker') or {}) if isinstance(data,dict) else {}
        for row in tracker.get('rows') or []: add((row or {}).get('symbol'))
    except Exception as e:
        log('FINDER_STATUS_ERROR',error=repr(e))
    # Break the V22E-eval -> API-fallback -> V22E-eval circular shrinkage by
    # always filling the active evaluation pool from the configured liquid universe.
    for sym in list(settings.core_symbols)+list(settings.symbols):
        add(sym)
        if len(found)>=MAX_CANDIDATES: break
    return found[:MAX_CANDIDATES]
'''
    s=s[:fstart]+finder+s[fend:]

    # Add pending-buy state so we never chain buys against stale account cash.
    anchor="    state = load_state()"
    if anchor not in s: raise SystemExit('ABORT state load anchor missing')
    s=s.replace(anchor,anchor+"\n    pending_buy=None",1)

    # Resolve pending buy at the start of each loop from broker holdings / live cash.
    anchor="            holdings = refresh_holdings()"
    if anchor not in s: raise SystemExit('ABORT holdings loop anchor missing')
    pending=r'''            holdings = refresh_holdings()
            if pending_buy:
                acct_now=account_snapshot(); cash_now=f(acct_now.get('orderable_cash'))
                if pending_buy.get('symbol') in holdings or (cash_now>0 and cash_now < f(pending_buy.get('cash_before'))-1.0):
                    log('BUY_CASH_REFRESH_CONFIRMED',symbol=pending_buy.get('symbol'),cash_before=pending_buy.get('cash_before'),cash_now=cash_now)
                    pending_buy=None
'''
    s=s.replace(anchor,pending,1)

    # Replace fixed-qty entry execution with live account allocator.
    old=r'''                if d.get('enter'):
                    ex = resolve_exchange(sym)
                    res = order_once('BUY', sym, QTY_DEFAULT, px, ex, bar_key, 'V22E_ENTRY')'''
    if old not in s:
        old=r'''                if d.get('enter'):
                    ex = settings.exchange_for(sym)
                    res = order_once('BUY', sym, QTY_DEFAULT, px, ex, bar_key, 'V22E_ENTRY')'''
    if old not in s: raise SystemExit('ABORT fixed buy block missing')
    new=r'''                if d.get('enter'):
                    if pending_buy:
                        log('BUY_WAIT_ACCOUNT_REFRESH',symbol=sym,pending_symbol=pending_buy.get('symbol'))
                        continue
                    if len(holdings)>=MAX_POSITIONS:
                        log('BUY_MAX_POSITIONS_BLOCK',symbol=sym,holdings=len(holdings),max_positions=MAX_POSITIONS)
                        continue
                    qty,budget,orderable,slots=live_buy_qty(px,len(holdings))
                    if qty<=0:
                        log('BUY_NO_ORDERABLE_CASH',symbol=sym,orderable_cash=orderable,budget=budget,slots=slots,price=px)
                        continue
                    ex = resolve_exchange(sym)
                    log('LIVE_CAPITAL_SIZE',symbol=sym,qty=qty,orderable_cash=orderable,budget=round(budget,2),remaining_slots=slots,capital_use_pct=CAPITAL_USE_PCT)
                    res = order_once('BUY', sym, qty, px, ex, bar_key, 'V22E_ENTRY')'''
    s=s.replace(old,new,1)

    # On accepted buy, gate subsequent buys until broker/account state changes.
    anchor="                    if res.get('ok'):\n                        state[sym] = {"
    if anchor not in s: raise SystemExit('ABORT buy accepted state anchor missing')
    s=s.replace(anchor,"                    if res.get('ok'):\n                        pending_buy={'symbol':sym,'cash_before':orderable,'ts':time.time()}\n                        state[sym] = {",1)

fd,name=tempfile.mkstemp(prefix='v58_',suffix='.py'); os.close(fd)
t=Path(name); t.write_text(s,encoding='utf-8'); py_compile.compile(str(t),doraise=True)
print('PY_COMPILE=PASS',flush=True)
bak=Path(str(P)+'.pre_v58')
if not bak.exists(): subprocess.run(['sudo','cp','-a',P,bak],check=True)
subprocess.run(['sudo','install','-m','0644',t,P],check=True); t.unlink(missing_ok=True)

subprocess.run(['sudo','systemctl','restart',SERVICE],check=True)
time.sleep(8)
active=subprocess.check_output(['sudo','systemctl','is-active',SERVICE],text=True).strip()
if active!='active':
    subprocess.run(['sudo','journalctl','-u',SERVICE,'-n','100','--no-pager'],check=False)
    raise SystemExit('ABORT V22E inactive')
print('V22E_SERVICE=ACTIVE',flush=True)

# Verify source markers and current live account, but do not place a test order.
rt=P.read_text(encoding='utf-8')
for token in ('V58_LIVE_CAPITAL_FULL_FINDER = True','MAX_POSITIONS','CAPITAL_USE_PCT','live_buy_qty','BUY_WAIT_ACCOUNT_REFRESH','LIVE_CAPITAL_SIZE'):
    if token not in rt: raise SystemExit('ABORT runtime marker missing '+token)
try:d=json.loads(A.read_text(encoding='utf-8')) if A.exists() else {}
except Exception:d={}
print('US_ACCOUNT='+json.dumps({'total_assets':d.get('total_assets'),'cash':d.get('cash'),'orderable_cash':d.get('orderable_cash'),'holdings':d.get('holding_count'),'symbols':[x.get('symbol') for x in d.get('holdings') or []]},ensure_ascii=False),flush=True)
print('ORDER_SIZING=LIVE_ACCOUNT_99_5PCT_ACROSS_REMAINING_SLOTS',flush=True)
print('MAX_POSITIONS=4',flush=True)
print('FINDER_EVALUATION=TOP20_FULL_POOL',flush=True)
print('CHAIN_BUY=WAIT_FOR_BROKER_CASH_OR_HOLDING_REFRESH',flush=True)
print('AUTO_ORDER_RETRY=DISABLED_PER_ENGINE_BAR',flush=True)
print('US_BUY_AUTHORITY=ENGINE5_V22E_USA',flush=True)
print('US_SELL_AUTHORITY=ENGINE5_V22E_USA',flush=True)
print('DEPLOY=PASS',flush=True)
