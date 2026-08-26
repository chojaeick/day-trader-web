#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
K=Path('/home/ubuntu/day-trader-api/live_server/kiwoom.py')
print('=== V178 PATCH DEDICATED FROZEN19 WS SESSION ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')

# patch kiwoom.py with dedicated websocket method if absent
text=K.read_text()
marker='async def frozen19_websocket_forever(self):'
if marker not in text:
    backup=K.with_suffix('.py.bak_v178')
    shutil.copy2(K,backup)
    insert='''\n    async def frozen19_websocket_forever(self):\n        """Dedicated USA frozen-19 F5 feed. DB ticks only; no strategy/order authority."""\n        import websockets, json, asyncio\n        frozen=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']\n        while True:\n            try:\n                if not self.token:\n                    await asyncio.to_thread(self.get_token)\n                async with websockets.connect(self.s.ws_url, ping_interval=None, close_timeout=5) as ws:\n                    await ws.send(json.dumps({'trnm':'LOGIN','token':self.token}))\n                    raw=await asyncio.wait_for(ws.recv(),timeout=10)\n                    try:\n                        self.db.add_raw(raw, __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat())\n                    except Exception:\n                        pass\n                    items=self._ws_items(frozen)\n                    if not items:\n                        raise RuntimeError('frozen19 no valid ws items')\n                    await ws.send(json.dumps({'trnm':'REG','grp_no':'F19','refresh':'0','data':[{'item':items,'type':['F5']}]}))\n                    log.info('Frozen19 WebSocket live: %s',','.join(f"{x['jmcode']}/{x['stex_tp']}" for x in items))\n                    while True:\n                        try:\n                            raw=await asyncio.wait_for(ws.recv(),timeout=20)\n                        except asyncio.TimeoutError:\n                            continue\n                        now=datetime.now(timezone.utc).isoformat()\n                        self.db.add_raw(raw,now)\n                        d=json.loads(raw)\n                        if d.get('trnm')=='PING':\n                            await ws.send(raw); continue\n                        for symbol,price,qty,cumvol in self._extract_f5(d):\n                            if symbol in frozen:\n                                self.db.add_tick(symbol,price,qty,cumvol,now)\n            except Exception as e:\n                log.warning('Frozen19 WebSocket reconnect: %s',e)\n                await asyncio.sleep(3)\n'''
    # insert before main websocket_forever if found; else before EOF
    pos=text.find('    async def websocket_forever(')
    if pos<0:
        text=text.rstrip()+"\n"+insert+"\n"
    else:
        text=text[:pos]+insert+'\n'+text[pos:]
    K.write_text(text)
    print('PATCHED',K)
    print('BACKUP',backup)
else:
    print('KIWOOM_ALREADY_PATCHED=YES')

# patch api startup task list to include dedicated loop, without touching existing loop
atext=API.read_text()
if 'k.frozen19_websocket_forever()' not in atext:
    backup=API.with_suffix('.py.bak_v178')
    shutil.copy2(API,backup)
    # safest targeted insertion after existing websocket task if present
    old='asyncio.create_task(k.websocket_forever())'
    if old in atext:
        atext=atext.replace(old,old+',\n                       asyncio.create_task(k.frozen19_websocket_forever())',1)
    else:
        # fallback: add into gathered create_task list near discovery_forever
        old='asyncio.create_task(k.discovery_forever())'
        if old not in atext:
            raise SystemExit('API_TASK_INSERTION_POINT_NOT_FOUND')
        atext=atext.replace(old,old+',\n                       asyncio.create_task(k.frozen19_websocket_forever())',1)
    API.write_text(atext)
    print('PATCHED',API)
    print('BACKUP',backup)
else:
    print('API_ALREADY_PATCHED=YES')

for p in (API,K):
    r=subprocess.run(['python3','-m','py_compile',str(p)],capture_output=True,text=True)
    print('PY_COMPILE',p.name,'PASS' if r.returncode==0 else 'FAIL',r.stderr.strip())
    if r.returncode!=0: raise SystemExit(1)
print('DEDICATED_FROZEN19_WS=YES')
print('DB_TICKS_ONLY=YES')
print('REAL_BROKER_CALLS_ADDED=NONE')
print('NEXT=V179_RESTART_AND_VERIFY_DEDICATED_FROZEN19_F5')
