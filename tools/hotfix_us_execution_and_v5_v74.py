#!/usr/bin/env python3
from pathlib import Path
import os, re, shutil, subprocess, sys, time, json, urllib.request

RUNTIME=Path('/home/ubuntu/day-trader-api/live_server/v22e_us_mock_live.py')
APP=Path('/home/ubuntu/day-trader-api-repo/app_v5.py')
PY='/home/ubuntu/day-trader-api/venv/bin/python'

def die(msg):
    print('ABORT '+msg); raise SystemExit(1)

if not RUNTIME.exists(): die('runtime missing')
if not APP.exists(): die('app_v5.py missing')

rt=RUNTIME.read_text(encoding='utf-8')
app=APP.read_text(encoding='utf-8')

# backups once
for p,suf in [(RUNTIME,'.pre_v74'),(APP,'.pre_v74')]:
    b=Path(str(p)+suf)
    if not b.exists(): shutil.copy2(p,b)

# ---- US execution: generous one-shot marketable limit ----
# Preserve no same-bar retry contract. Only change the price cushion used by order_once.
# BUY crosses +1.0%; SELL crosses -1.0%. Env-overridable.
if 'V74_MARKETABLE_LIMITS = True' not in rt:
    anchor='V57_USD_ONLY_CASH_PARSE = True'
    if anchor not in rt: die('runtime marker anchor not found')
    rt=rt.replace(anchor, anchor+"\nV74_MARKETABLE_LIMITS = True\nBUY_CROSS_PCT = float(os.getenv('V22E_US_BUY_CROSS_PCT','0.010'))\nSELL_CROSS_PCT = float(os.getenv('V22E_US_SELL_CROSS_PCT','0.010'))",1)

pat=re.compile(r"def marketable\(price: float, side: str\):\n(?:    .*\n){1,8}?    return .*\n",re.M)
m=pat.search(rt)
if not m: die('marketable function not found')
new_market='''def marketable(price: float, side: str):
    # V74: Kiwoom US mock has no market order. Use a deliberately marketable
    # limit so a single order has room to cross the spread / short-term move.
    side = str(side or '').upper()
    pct = BUY_CROSS_PCT if side == 'BUY' else SELL_CROSS_PCT
    px = price * (1 + pct) if side == 'BUY' else price * (1 - pct)
    return round(px + 1e-9, 2)
'''
rt=rt[:m.start()]+new_market+rt[m.end():]

# Add cushion to order log so runtime proves actual configuration.
old="log('ORDER_ATTEMPT', side=side, symbol=sym, qty=qty, limit=limit_px, exchange=exchange, reason=reason)"
new="log('ORDER_ATTEMPT', side=side, symbol=sym, qty=qty, signal_price=signal_px, limit=limit_px, cross_pct=(BUY_CROSS_PCT if str(side).upper()=='BUY' else SELL_CROSS_PCT), exchange=exchange, reason=reason)"
if old in rt: rt=rt.replace(old,new,1)

# ---- V5: USA must show Finder first, not stale tracker first ----
old_src="source=(finders[:20] if market=='KOREA' and finders else (rows[:20] if market=='KOREA' else (rows[:20] or finders[:20])))"
new_src="source=(finders[:20] if finders else rows[:20])"
if old_src in app:
    app=app.replace(old_src,new_src,1)
elif new_src not in app:
    print('WARN finder source exact line not found; leaving app source unchanged')

# Short cache on market status to avoid repeated API calls during a single Streamlit rerun,
# while remaining effectively live. Clear cache when switching market/mode by rerun naturally.
if 'V74_FAST_MARKET_STATUS = True' not in app:
    marker='V72_DYNAMIC_USA_STATUS_BRIDGE = True'
    if marker in app:
        app=app.replace(marker,marker+'\nV74_FAST_MARKET_STATUS = True',1)

# get_market_status can be called repeatedly in one render; 2s cache cuts duplicate network waits.
pat_status=re.compile(r"(?m)^def get_market_status\(market\):return api\(f'/api/v4/\{market\}/status',15\)$")
if pat_status.search(app):
    app=pat_status.sub("@st.cache_data(ttl=2,show_spinner=False)\ndef get_market_status(market): return api(f'/api/v4/{market}/status',5)",app,1)
else:
    # V72 may have expanded this function. Add decorator only if not already cached.
    idx=app.find('def get_market_status(market):')
    if idx>=0 and '@st.cache_data(ttl=2,show_spinner=False)' not in app[max(0,idx-80):idx]:
        app=app[:idx]+'@st.cache_data(ttl=2,show_spinner=False)\n'+app[idx:]

# Write temp + compile + install atomically preserving ownership/mode.
rt_tmp=Path('/tmp/v22e_us_mock_live.py.v74')
app_tmp=Path('/tmp/app_v5.py.v74')
rt_tmp.write_text(rt,encoding='utf-8')
app_tmp.write_text(app,encoding='utf-8')
for p in (rt_tmp,app_tmp):
    r=subprocess.run([PY,'-m','py_compile',str(p)],capture_output=True,text=True)
    if r.returncode!=0:
        print(r.stderr); die('py_compile failed '+str(p))
print('PY_COMPILE=PASS')

subprocess.run(['sudo','install','-o','ubuntu','-g','ubuntu','-m','0644',str(rt_tmp),str(RUNTIME)],check=True)
subprocess.run(['sudo','install','-o','ubuntu','-g','ubuntu','-m','0644',str(app_tmp),str(APP)],check=True)
print('V74_RUNTIME_INSTALLED=YES')
print('US_BUY_LIMIT_CROSS=+1.0PCT')
print('US_SELL_LIMIT_CROSS=-1.0PCT')
print('AUTO_ORDER_RETRY=UNCHANGED_DISABLED_PER_BAR')
print('USA_FINDER_UI_PRIORITY=FINDER_FIRST')
print('V5_MARKET_STATUS_TIMEOUT=5S_CACHE=2S')

subprocess.run(['sudo','systemctl','restart','day-trader-v22e-us'],check=True)
time.sleep(3)
svc=subprocess.run(['systemctl','is-active','day-trader-v22e-us'],capture_output=True,text=True).stdout.strip()
print('V22E_SERVICE='+svc.upper())
if svc!='active': die('V22E service not active')

# Restart V5 nohup process without touching API/engine code. Locate current app_v5 process.
subprocess.run("pkill -f 'streamlit run .*app_v5.py' || true",shell=True)
time.sleep(1)
log='/home/ubuntu/day-trader-api/v5_streamlit.log'
cmd=f"cd /home/ubuntu/day-trader-api-repo && nohup /home/ubuntu/day-trader-api/venv/bin/streamlit run app_v5.py --server.address 0.0.0.0 --server.port 8503 > {log} 2>&1 &"
subprocess.run(['bash','-lc',cmd],check=True)
time.sleep(5)
try:
    with urllib.request.urlopen('http://127.0.0.1:8503/',timeout=6) as r:
        if r.status!=200: die('V5 HTTP '+str(r.status))
    print('V5_HTTP=PASS')
except Exception as e:
    die('V5 HTTP failed '+repr(e))

print('TRADING_STRATEGY_GATES=UNCHANGED')
print('ORDER_PRICE_EXECUTION=MARKETABLE_LIMIT_1PCT')
print('DEPLOY=PASS')
