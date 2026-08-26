from pathlib import Path
import shutil, subprocess, time, urllib.request, json, sqlite3

K=Path('/home/ubuntu/day-trader-api/live_server/kiwoom.py')
API='http://127.0.0.1:8000'
DB='/home/ubuntu/day-trader-api/daytrader.db'
print('=== V195 PATCH FROZEN USA FEED F5->FE + PARSER PARITY ===')
print('STRATEGY_CONSTANT_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('PURPOSE=USE_FE_USA_REALTIME_TRADE_FEED_FOR_FROZEN19_AND_PARSE_F5_FE_COMPATIBLY')
if not K.exists(): raise SystemExit('KIWOOM_NOT_FOUND')
src=K.read_text()
bak=K.with_suffix('.py.bak_v195')
shutil.copy2(K,bak); print('BACKUP',bak)

# Patch only the main USA websocket registration to FE. Dedicated frozen websocket remains disabled in api.py.
old="'data':[{'item':reg_items,'type':['F5']}]"
count=src.count(old)
print('MAIN_REG_F5_COUNT=',count)
if count < 1: raise SystemExit('MAIN_REG_TARGET_NOT_FOUND')
src=src.replace(old,"'data':[{'item':reg_items,'type':['FE']}]",2)

# Parser accepts both historical F5 and FE trade payloads; field 10/13/15 semantics are shared in observed FE payload.
old_filter="if str(row.get('type')) != 'F5': continue"
if old_filter not in src: raise SystemExit('PARSER_FILTER_NOT_FOUND')
src=src.replace(old_filter,"if str(row.get('type')) not in ('F5','FE'): continue",1)
K.write_text(src)

r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(K)])
print('COMPILE_RC=',r.returncode)
if r.returncode: raise SystemExit('COMPILE_FAIL')

# Restart current service using the same unit the project has been using.
r=subprocess.run(['sudo','systemctl','restart','day-trader-api.service'])
print('RESTART_RC=',r.returncode)

ready=False
for i in range(1,41):
    try:
        with urllib.request.urlopen(API+'/api/v4/USA/frozen-paper',timeout=2) as f:
            if f.status==200:
                ready=True; print('API_READY_PROBE',i); break
    except Exception:
        time.sleep(1)
if not ready: raise SystemExit('API_NOT_READY')

# Observe current feed for up to 90s. FE should create second-resolution timestamps for all active frozen symbols.
frozen=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']
con=sqlite3.connect(DB)
def latest(sym):
    return con.execute('select id,ts,price,qty from ticks where symbol=? order by id desc limit 1',(sym,)).fetchone()
base={s:latest(s) for s in frozen}
for sec in (15,30,45,60,75,90):
    time.sleep(15)
    cur={s:latest(s) for s in frozen}
    changed=[s for s in frozen if cur[s] and (not base[s] or cur[s][0]>base[s][0])]
    print('OBSERVE_SEC',sec,'CHANGED',len(changed),changed)
    if len(changed)>=17: break

cur={s:latest(s) for s in frozen}
changed=[s for s in frozen if cur[s] and (not base[s] or cur[s][0]>base[s][0])]
unchanged=[s for s in frozen if s not in changed]
print('FEED_CHANGED_COUNT=',len(changed),changed)
print('FEED_UNCHANGED_COUNT=',len(unchanged),unchanged)

# Verify raw FE delivery exists after patch.
rows=con.execute("select payload,ts from raw_ws order by id desc limit 1000").fetchall()
fe_items=set(); fe_rows=0
for payload,ts in rows:
    try: d=json.loads(payload)
    except Exception: continue
    for row in d.get('data') or []:
        if str(row.get('type'))=='FE':
            fe_rows+=1; fe_items.add(str(row.get('item') or '').upper())
print('RAW_FE_ROWS=',fe_rows)
print('RAW_FE_ITEMS=',sorted(fe_items))

# Frozen-paper endpoint: verify rows exist and show current bar/context status.
try:
    with urllib.request.urlopen(API+'/api/v4/USA/frozen-paper',timeout=5) as f:
        d=json.loads(f.read().decode())
    rows=d.get('rows') or []
    print('FROZEN_ROWS=',len(rows),'ERRORS=',d.get('errors'),'EVAL=',d.get('evaluations'))
    for r in rows:
        if r.get('symbol') in frozen:
            print('FROZEN',r.get('symbol'),'CTX',r.get('ctx'),'REASON',r.get('eval_reason'),'BAR',r.get('bar'),'TICKS',r.get('ticks'))
except Exception as e:
    print('FROZEN_ENDPOINT_ERROR',repr(e))

# Static assertions.
post=K.read_text()
print('MAIN_FE_REG_ENABLED=',"'type':['FE']" in post)
print('PARSER_ACCEPTS_F5_FE=',"not in ('F5','FE')" in post)
print('RUNTIME_LEFT_ON_FE=YES')
print('NEXT=RUN_V196_COMPLETED_1M_BAR_ONCE_PER_BAR_AND_CTX_PARITY_AUDIT')
