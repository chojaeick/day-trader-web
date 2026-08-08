from __future__ import annotations
import asyncio, json, logging, re
from datetime import datetime, timezone
import requests, websockets
from .config import Settings
from .db import DB

log = logging.getLogger('kiwoom')


def num(v, default=0.0):
    if v is None: return default
    s = str(v).strip().replace(',', '')
    s = s.lstrip('+')
    try: return float(s)
    except Exception: return default

class KiwoomClient:
    def __init__(self, settings: Settings, db: DB):
        self.s, self.db = settings, db
        self.token = None

    def get_token(self):
        r = requests.post(self.s.rest_base + '/oauth2/token', json={
            'grant_type':'client_credentials','appkey':self.s.app_key,'secretkey':self.s.app_secret
        }, headers={'Content-Type':'application/json;charset=UTF-8'}, timeout=15)
        r.raise_for_status(); d=r.json()
        if not d.get('token'): raise RuntimeError(f"token failed: {d}")
        self.token=d['token']; return self.token

    def headers(self, api_id: str):
        if not self.token: self.get_token()
        return {'authorization':f'Bearer {self.token}','api-id':api_id,'Content-Type':'application/json;charset=UTF-8'}

    def quote(self, symbol: str, exchange: str):
        # This exact request shape was verified against the user's account during setup.
        r=requests.post(self.s.rest_base+'/api/us/mrkcond', headers=self.headers('usa20100'),
                        json={'stex_tp':exchange,'stk_cd':symbol}, timeout=15)
        d=r.json()
        if d.get('return_code') != 0:
            # token may have expired: refresh once
            if d.get('return_code') in {-1, 100013}:
                self.get_token()
            raise RuntimeError(f"quote {symbol}/{exchange}: {d.get('return_code')} {d.get('return_msg')}")
        now=datetime.now(timezone.utc).isoformat()
        q={'symbol':symbol,'exchange':exchange,'price':num(d.get('cur_prc')),'change_pct':num(d.get('flu_rt')),
           'volume':num(d.get('acc_trde_qty')),'open':num(d.get('open_pric')),'high':num(d.get('high_pric')),
           'low':num(d.get('low_pric')),'prev_close':num(d.get('base_close_pric')),'updated_at':now}
        self.db.upsert_quote(q)
        # REST snapshot tick is also stored. The WebSocket worker will add true realtime ticks while market is open.
        if q['price'] > 0: self.db.add_tick(symbol, q['price'], 0, q['volume'], now)
        return q

    def _extract_f5(self, msg: dict):
        # Kiwoom realtime messages arrive as {trnm:'REAL', data:[{type:'F5', item:'SOXL', values:{...}}]}.
        # Field IDs may evolve. We persist every raw message and use conservative price/volume aliases.
        for row in msg.get('data') or []:
            if str(row.get('type')) != 'F5': continue
            symbol = str(row.get('item') or '').upper()
            values = row.get('values') or {}
            # Common aliases + numeric field IDs seen in Kiwoom realtime protocols.
            price = None
            for k in ('10','price','cur_prc','curr_pric','last','12'):
                if k in values and num(values[k]) > 0:
                    price = abs(num(values[k])); break
            cumvol = 0.0
            for k in ('13','volume','acc_trde_qty','cum_volume','15'):
                if k in values:
                    cumvol = abs(num(values[k])); break
            qty = 0.0
            for k in ('15','qty','trade_qty'):
                if k in values:
                    qty = abs(num(values[k])); break
            if symbol and price:
                yield symbol, price, qty, cumvol

    async def websocket_forever(self):
        while True:
            try:
                token=self.get_token()
                async with websockets.connect(self.s.ws_url, ping_interval=None, close_timeout=5) as ws:
                    await ws.send(json.dumps({'trnm':'LOGIN','token':token}))
                    while True:
                        d=json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
                        if d.get('trnm')=='LOGIN':
                            if d.get('return_code')!=0: raise RuntimeError(f'LOGIN failed: {d}')
                            break
                    reg={'trnm':'REG','grp_no':'1','refresh':'1','data':[{'item':self.s.symbols,'type':['F5']}]}
                    await ws.send(json.dumps(reg))
                    reg_d=json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
                    if reg_d.get('return_code') != 0: raise RuntimeError(f'REG failed: {reg_d}')
                    log.info('WebSocket live: %s', ','.join(self.s.symbols))
                    while True:
                        raw=await ws.recv(); now=datetime.now(timezone.utc).isoformat()
                        d=json.loads(raw)
                        if d.get('trnm')=='PING':
                            await ws.send(raw); continue
                        self.db.add_raw(raw, now)
                        for symbol,price,qty,cumvol in self._extract_f5(d):
                            self.db.add_tick(symbol,price,qty,cumvol,now)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception('websocket reconnect after error: %s', e)
                await asyncio.sleep(5)

    async def snapshot_poll_forever(self):
        while True:
            for symbol in self.s.symbols:
                try:
                    self.quote(symbol, self.s.exchange_for(symbol))
                except Exception as e:
                    log.warning('snapshot %s: %s', symbol, e)
                await asyncio.sleep(0.3)
            await asyncio.sleep(self.s.poll_seconds)
