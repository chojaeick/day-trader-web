from __future__ import annotations
import asyncio, json, logging
from datetime import datetime, timezone, timedelta, date
import requests, websockets
from zoneinfo import ZoneInfo
from .config import Settings
from .db import DB

log = logging.getLogger('kiwoom')

def num(v, default=0.0):
    if v is None: return default
    s = str(v).strip().replace(',', '').lstrip('+')
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
        r=requests.post(self.s.rest_base+'/api/us/mrkcond', headers=self.headers('usa20100'),
                        json={'stex_tp':exchange,'stk_cd':symbol}, timeout=15)
        d=r.json()
        if d.get('return_code') != 0:
            raise RuntimeError(f"quote {symbol}/{exchange}: {d.get('return_code')} {d.get('return_msg')}")
        now=datetime.now(timezone.utc).isoformat()
        q={'symbol':symbol,'exchange':exchange,'price':abs(num(d.get('cur_prc'))),'change_pct':num(d.get('flu_rt')),
           'volume':abs(num(d.get('acc_trde_qty'))),'open':abs(num(d.get('open_pric'))),'high':abs(num(d.get('high_pric'))),
           'low':abs(num(d.get('low_pric'))),'prev_close':abs(num(d.get('base_close_pric'))),'updated_at':now}
        self.db.upsert_quote(q)
        # REST quote snapshots are intentionally NOT stored as intraday ticks.
        # Doing so while the market is closed creates fake flat 1-minute bars.
        return q

    def daily_metrics(self, symbol: str, exchange: str):
        # Official Kiwoom example: usa06012 /api/us/chart (US daily chart)
        start=(datetime.now(timezone.utc)-timedelta(days=18)).strftime('%Y%m%d')
        r=requests.post(self.s.rest_base+'/api/us/chart', headers=self.headers('usa06012'), json={
            'stex_tp':exchange,'stk_cd':symbol,'strt_dt':start,'upd_stkpc_tp':'1','exrt_appl_tp':'0'
        }, timeout=15)
        d=r.json()
        if d.get('return_code') != 0:
            raise RuntimeError(f"daily {symbol}/{exchange}: {d.get('return_code')} {d.get('return_msg')}")
        rows=d.get('result_list') or []
        parsed=[]
        for x in rows:
            close=abs(num(x.get('cur_prc'))); high=abs(num(x.get('high_pric'))); low=abs(num(x.get('low_pric')))
            vol=abs(num(x.get('acc_trde_qty'))); dv=abs(num(x.get('acc_trde_prica')))
            dt=str(x.get('dt') or '')
            if close>0: parsed.append({'dt':dt,'close':close,'high':high,'low':low,'volume':vol,'dollar_volume':dv})
        if not parsed: raise RuntimeError(f'daily {symbol}: empty result')
        parsed=sorted(parsed,key=lambda x:x['dt'],reverse=True)
        today=datetime.now(timezone.utc).strftime('%Y%m%d')
        completed=[x for x in parsed if x['dt'] and x['dt'] != today]
        use=(completed or parsed)[:5]
        if len(use)<3: raise RuntimeError(f'daily {symbol}: insufficient rows={len(use)}')
        closes=[x['close'] for x in use]
        ma5=sum(closes)/len(closes)
        slope=(closes[0]/closes[-1]-1)*100 if closes[-1] else 0
        avg_vol=sum(x['volume'] for x in use)/len(use)
        avg_dv=sum((x['dollar_volume'] if x['dollar_volume']>0 else x['close']*x['volume']) for x in use)/len(use)
        trs=[]
        for i,x in enumerate(use):
            prev=use[i+1]['close'] if i+1<len(use) else x['close']
            trs.append(max(x['high']-x['low'],abs(x['high']-prev),abs(x['low']-prev)))
        atr=sum(trs)/len(trs)
        atr_pct=(atr/ma5*100) if ma5 else 0
        m={'symbol':symbol,'ma5':ma5,'ma5_slope_pct':slope,'avg5_volume':avg_vol,
           'avg5_dollar_volume':avg_dv,'atr5_pct':atr_pct,'updated_at':datetime.now(timezone.utc).isoformat()}
        self.db.upsert_daily_metrics(m); return m


    def minute_chart(self, symbol: str, exchange: str, minutes: int = 1, start_date: str | None = None):
        """Fetch Kiwoom US minute bars (usa06011 /api/us/chart).

        Official fields: cur_prc, trde_qty, open_pric, high_pric, low_pric,
        cntr_tm, bus_dt.  We use this only to warm up indicators after restart;
        live data continues to come from F5 WebSocket trades.
        """
        if start_date is None:
            et = datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York'))
            start_date = (et.date() - timedelta(days=7)).strftime('%Y%m%d')
        r = requests.post(self.s.rest_base + '/api/us/chart', headers=self.headers('usa06011'), json={
            'stex_tp': exchange, 'stk_cd': symbol, 'strt_dt': start_date,
            'tic_scope': str(minutes), 'upd_stkpc_tp': '0', 'exrt_appl_tp': '1'
        }, timeout=20)
        d = r.json()
        if d.get('return_code') != 0:
            raise RuntimeError(f"minute {symbol}/{exchange}: {d.get('return_code')} {d.get('return_msg')}")
        rows = d.get('result_list') or []
        parsed=[]
        etz=ZoneInfo('America/New_York')
        for x in rows:
            close=abs(num(x.get('cur_prc'))); op=abs(num(x.get('open_pric'))); hi=abs(num(x.get('high_pric'))); lo=abs(num(x.get('low_pric')))
            vol=abs(num(x.get('trde_qty')))
            bus=str(x.get('bus_dt') or '').strip(); tm=''.join(ch for ch in str(x.get('cntr_tm') or '') if ch.isdigit())
            if close <= 0 or len(bus) < 8: continue
            tm=(tm+'000000')[:6]
            try:
                local=datetime.strptime(bus[:8]+tm,'%Y%m%d%H%M%S').replace(tzinfo=etz)
                ts=local.astimezone(timezone.utc)
            except Exception:
                continue
            parsed.append({'time':ts,'open':op or close,'high':hi or close,'low':lo or close,'close':close,'volume':vol})
        parsed.sort(key=lambda x:x['time'])
        return parsed

    def backfill_symbol(self, symbol: str, exchange: str, min_bars: int = 80):
        """Seed recent 1-minute history into ticks so signal calculations are ready after restart."""
        rows=self.minute_chart(symbol, exchange, 1)
        if not rows:
            raise RuntimeError(f'backfill {symbol}: empty minute chart')
        rows=rows[-max(min_bars, 30):]
        self.db.delete_zero_qty_ticks(symbol)
        cumulative_by_day={}
        inserted=0
        for bar in rows:
            day=bar['time'].astimezone(ZoneInfo('America/New_York')).date().isoformat()
            running=cumulative_by_day.get(day,0.0)
            barvol=max(0.0,float(bar.get('volume') or 0))
            seq=[bar['open'], bar['low'], bar['high'], bar['close']] if bar['close']>=bar['open'] else [bar['open'],bar['high'],bar['low'],bar['close']]
            portions=[0.20,0.25,0.25,0.30]
            for sec,(price,portion) in enumerate(zip(seq,portions),start=1):
                qty=barvol*portion; running += qty
                ts=(bar['time'].replace(second=0,microsecond=0)+timedelta(seconds=(5,20,40,55)[sec-1])).isoformat()
                inserted += self.db.add_tick_if_missing(symbol,float(price),qty,running,ts)
            cumulative_by_day[day]=running
        return inserted, len(rows)

    async def backfill_forever_once(self):
        # A short, rate-limited warmup pass at service start. Kiwoom US chart limits
        # comfortably allow one request per symbol at this pace.
        await asyncio.sleep(1.0)
        for symbol in self.s.symbols:
            try:
                inserted,bars=self.backfill_symbol(symbol,self.s.exchange_for(symbol),80)
                log.info('minute backfill %s: bars=%s inserted=%s',symbol,bars,inserted)
            except Exception as e:
                log.warning('minute backfill %s: %s',symbol,e)
            await asyncio.sleep(0.35)

    def _extract_f5(self, msg: dict):
        for row in msg.get('data') or []:
            if str(row.get('type')) != 'F5': continue
            symbol = str(row.get('item') or '').upper(); values = row.get('values') or {}
            price = None
            for k in ('10','price','cur_prc','curr_pric','last','12'):
                if k in values and num(values[k]) != 0:
                    price = abs(num(values[k])); break
            cumvol = 0.0
            for k in ('13','volume','acc_trde_qty','cum_volume'):
                if k in values:
                    cumvol = abs(num(values[k])); break
            qty = 0.0
            for k in ('15','qty','trade_qty'):
                if k in values:
                    qty = abs(num(values[k])); break
            if symbol and price: yield symbol, price, qty, cumvol

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
                    await ws.send(json.dumps(reg)); reg_d=json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
                    if reg_d.get('return_code') != 0: raise RuntimeError(f'REG failed: {reg_d}')
                    log.info('WebSocket live: %s', ','.join(self.s.symbols))
                    while True:
                        raw=await ws.recv(); now=datetime.now(timezone.utc).isoformat(); d=json.loads(raw)
                        if d.get('trnm')=='PING': await ws.send(raw); continue
                        self.db.add_raw(raw, now)
                        for symbol,price,qty,cumvol in self._extract_f5(d): self.db.add_tick(symbol,price,qty,cumvol,now)
            except asyncio.CancelledError: raise
            except Exception as e:
                log.exception('websocket reconnect after error: %s', e); await asyncio.sleep(5)

    async def snapshot_poll_forever(self):
        while True:
            for symbol in self.s.symbols:
                try: self.quote(symbol, self.s.exchange_for(symbol))
                except Exception as e: log.warning('snapshot %s: %s', symbol, e)
                await asyncio.sleep(0.22)
            await asyncio.sleep(self.s.poll_seconds)

    async def daily_refresh_forever(self):
        while True:
            for symbol in self.s.symbols:
                try: self.daily_metrics(symbol,self.s.exchange_for(symbol))
                except Exception as e: log.warning('daily metrics %s: %s',symbol,e)
                await asyncio.sleep(0.25)
            await asyncio.sleep(self.s.daily_refresh_seconds)
