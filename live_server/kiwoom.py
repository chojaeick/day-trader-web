from __future__ import annotations
import asyncio, json, logging
from datetime import datetime, timezone, timedelta, date
import requests, websockets
from zoneinfo import ZoneInfo
from .config import Settings
from .db import DB
from .scanner import merge_rankings

log = logging.getLogger('kiwoom')

def num(v, default=0.0):
    if v is None: return default
    s = str(v).strip().replace(',', '').lstrip('+')
    try: return float(s)
    except Exception: return default

class KiwoomClient:
    def __init__(self, settings: Settings, db: DB):
        self.manual_scan_lock=asyncio.Lock()
        self.last_manual_scan_at=None
        self.s, self.db = settings, db
        self.token = None
        self.discovery = {'symbols': list(settings.symbols), 'rows': [], 'updated_at': None, 'count': len(settings.symbols), 'core': list(settings.core_symbols), 'exchanges': {}}

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
        return q

    def daily_metrics(self, symbol: str, exchange: str):
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
            vol=abs(num(x.get('acc_trde_qty'))); dv=abs(num(x.get('acc_trde_prica'))); dt=str(x.get('dt') or '')
            if close>0: parsed.append({'dt':dt,'close':close,'high':high,'low':low,'volume':vol,'dollar_volume':dv})
        if not parsed: raise RuntimeError(f'daily {symbol}: empty result')
        parsed=sorted(parsed,key=lambda x:x['dt'],reverse=True)
        today=datetime.now(timezone.utc).strftime('%Y%m%d')
        completed=[x for x in parsed if x['dt'] and x['dt'] != today]
        use=(completed or parsed)[:5]
        if len(use)<3: raise RuntimeError(f'daily {symbol}: insufficient rows={len(use)}')
        closes=[x['close'] for x in use]; ma5=sum(closes)/len(closes)
        slope=(closes[0]/closes[-1]-1)*100 if closes[-1] else 0
        avg_vol=sum(x['volume'] for x in use)/len(use)
        avg_dv=sum((x['dollar_volume'] if x['dollar_volume']>0 else x['close']*x['volume']) for x in use)/len(use)
        trs=[]
        for i,x in enumerate(use):
            prev=use[i+1]['close'] if i+1<len(use) else x['close']
            trs.append(max(x['high']-x['low'],abs(x['high']-prev),abs(x['low']-prev)))
        atr=sum(trs)/len(trs); atr_pct=(atr/ma5*100) if ma5 else 0
        m={'symbol':symbol,'ma5':ma5,'ma5_slope_pct':slope,'avg5_volume':avg_vol,
           'avg5_dollar_volume':avg_dv,'atr5_pct':atr_pct,'updated_at':datetime.now(timezone.utc).isoformat()}
        self.db.upsert_daily_metrics(m); return m


    def ranking_today_volume(self, sort_mode: str='0') -> list[dict]:
        """Kiwoom usa20530, broadened across ALL/NYSE/NASDAQ/AMEX."""
        combined=[]
        seen=set()
        for stex in ('0','1','2','3'):
            body={'stex_tp':stex,'inds_cd':'','stk_tp':'0','trde_qty_tp':'0',
                  'qry_tp':str(sort_mode),'stk_cnd':'0','pric_cnd':'0','trde_prica_cnd':'0'}
            r=requests.post(self.s.rest_base+'/api/us/rkinfo',
                            headers=self.headers('usa20530'),json=body,timeout=20)
            d=r.json()
            if d.get('return_code') not in (None,0):
                log.warning('ranking usa20530/%s/%s: %s %s',
                            sort_mode,stex,d.get('return_code'),d.get('return_msg'))
                continue
            for row in d.get('result_list') or []:
                sym=str(row.get('stk_cd') or '').upper().strip()
                key=(sym, str(row.get('stex_tp') or stex))
                if sym and key not in seen:
                    combined.append(row)
                    seen.add(key)
            # Keep API cadence conservative.
            import time
            time.sleep(0.18)
        return combined

    def ranking_change_rate(self, sort_tp: str='1') -> list[dict]:
        """Kiwoom usa20910: 1=gainers, 4=losers."""
        combined=[]
        seen=set()
        import time
        for stex in ('0','1','2','3'):
            body={
                'stex_tp':stex,'inds_cd':'','inds_cls_tp':'0','sort_tp':str(sort_tp),
                'stk_tp':'0','stk_cnd':'0','pric_cnd':'0',
                'trde_prica_cnd':'0','trde_qty_tp':''
            }
            r=requests.post(
                self.s.rest_base+'/api/us/rkinfo',
                headers=self.headers('usa20910'),
                json=body,timeout=20
            )
            d=r.json()
            if d.get('return_code') not in (None,0):
                log.warning('ranking usa20910/%s/%s: %s %s',
                            sort_tp,stex,d.get('return_code'),d.get('return_msg'))
                continue
            for row in d.get('result_list') or []:
                sym=str(row.get('stk_cd') or '').upper().strip()
                if sym and sym not in seen:
                    combined.append(row); seen.add(sym)
            time.sleep(0.18)
        return combined

    def ranking_volume_surge(self) -> list[dict]:
        """Kiwoom usa20520: volume surge vs 5-day average."""
        combined=[]
        seen=set()
        import time
        for stex in ('0','1','2','3'):
            body={
                'stex_tp':stex,'inds_cd':'','tm':'5','stk_tp':'0',
                'stk_cnd':'0','pric_cnd':'0','trde_prica_cnd':'0','trde_qty_tp':'0'
            }
            r=requests.post(
                self.s.rest_base+'/api/us/stkinfo',
                headers=self.headers('usa20520'),
                json=body,timeout=20
            )
            d=r.json()
            if d.get('return_code') not in (None,0):
                log.warning('ranking usa20520/%s: %s %s',
                            stex,d.get('return_code'),d.get('return_msg'))
                continue
            for row in d.get('result_list') or []:
                sym=str(row.get('stk_cd') or '').upper().strip()
                if sym and sym not in seen:
                    combined.append(row); seen.add(sym)
            time.sleep(0.18)
        return combined

    def discover_universe(self) -> dict:
        volume=self.ranking_today_volume('0')
        dollar=self.ranking_today_volume('1')
        gainers=self.ranking_change_rate('1')
        losers=self.ranking_change_rate('4')
        surge=self.ranking_volume_surge()
        result=merge_rankings(
            volume,dollar,self.s.core_symbols,self.s.discovery_limit,
            self.s.discovery_min_price,self.s.discovery_min_dollar,
            gainers=gainers,losers=losers,volume_surge=surge
        )
        exchanges={r['symbol']:r.get('exchange') for r in result.rows if r.get('exchange')}
        self.s.symbols=list(result.symbols)
        self.discovery={
            'symbols':list(result.symbols),'rows':result.rows,'updated_at':result.updated_at,
            'count':len(result.symbols),'core':list(self.s.core_symbols),'exchanges':exchanges,
            'auto_count':len([x for x in result.symbols if x not in self.s.core_symbols]),
            'sources':['volume','dollar','gainer','loser','surge'],
            'extreme_count':len(result.extreme_rows),'extreme_rows':result.extreme_rows,
            'quality_risk_rows':result.quality_risk_rows,
            'quality_reject_rows':result.quality_reject_rows,
            'quality_counts':{
                'A':len([r for r in result.rows if r.get('quality_grade')=='A']),
                'B_EVENT':len([r for r in result.rows if r.get('quality_grade')=='B_EVENT']),
                'C_HIGH_RISK':len(result.quality_risk_rows),
                'REJECT':len(result.quality_reject_rows)
            },
            'quality_gate':'QUALITY_GATE_USA_V1'
        }
        log.info('universe discovery: %s symbols',len(result.symbols))
        return self.discovery

    def active_symbols(self) -> list[str]:
        """Return the currently active dynamic universe."""
        return list(self.s.symbols)

    def active_exchange(self, symbol:str) -> str:
        return (self.discovery.get('exchanges') or {}).get(symbol.upper()) or self.s.exchange_for(symbol)

    def _ws_items(self, symbols) -> list[dict]:
        """Build Kiwoom USA websocket REG items using the documented symbol+exchange map."""
        items=[]
        for raw_symbol in symbols:
            symbol=str(raw_symbol or '').upper().strip()
            if not symbol:
                continue
            exchange=str(self.active_exchange(symbol) or '').upper().strip()
            # Preserve the project's already-verified AMEX normalization.
            if exchange == 'AM':
                exchange='NA'
            if exchange not in ('ND','NY','NA'):
                log.warning('WebSocket skip %s: unsupported exchange=%s',symbol,exchange)
                continue
            items.append({'jmcode':symbol,'stex_tp':exchange})
        return items


    async def manual_discover_now(self):
        """Force a fresh market-wide discovery and prime newly added symbols."""
        async with self.manual_scan_lock:
            before=list(self.active_symbols())
            before_set=set(before)
            started=datetime.now(timezone.utc)
            result=await asyncio.to_thread(self.discover_universe)
            after=list(self.active_symbols())
            after_set=set(after)

            added=sorted(after_set-before_set)
            removed=sorted(before_set-after_set)

            # Prime only genuinely new symbols. Keep this bounded so a manual click stays responsive.
            for sym in added[:12]:
                try:
                    ex=self.active_exchange(sym)
                    await asyncio.to_thread(self.quote, sym, ex)
                    await asyncio.to_thread(self.daily_metrics, sym, ex)
                    await asyncio.to_thread(self.backfill_symbol, sym, ex, 80)
                except Exception as e:
                    log.warning('manual prime discovered %s: %s', sym, e)
                await asyncio.sleep(0.15)

            self.last_manual_scan_at=datetime.now(timezone.utc)
            return {
                'ok':True,
                'started_at':started.isoformat(),
                'finished_at':self.last_manual_scan_at.isoformat(),
                'before_count':len(before),
                'after_count':len(after),
                'added':added,
                'removed':removed,
                'result':result
            }

    async def discovery_forever(self):
        while True:
            try:
                old=set(self.s.symbols)
                await asyncio.to_thread(self.discover_universe)
                added=[x for x in self.s.symbols if x not in old]
                for sym in added:
                    try:
                        ex=self.active_exchange(sym)
                        await asyncio.to_thread(self.quote,sym,ex)
                        await asyncio.to_thread(self.daily_metrics,sym,ex)
                        await asyncio.to_thread(self.backfill_symbol,sym,ex,80)
                    except Exception as e:
                        log.warning('prime discovered %s: %s',sym,e)
                    await asyncio.sleep(0.25)
                if set(self.s.symbols)!=old:
                    log.info('universe changed: +%s -%s',sorted(set(self.s.symbols)-old),sorted(old-set(self.s.symbols)))
            except Exception as e:
                log.warning('universe discovery failed; keeping current universe: %s',e)
            await asyncio.sleep(self.s.discovery_seconds)

    def minute_chart(self, symbol: str, exchange: str, minutes: int = 1, start_date: str | None = None):
        if start_date is None:
            # usa06011 is date-scoped: request the current New York trading date.
            et = datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York'))
            start_date = et.date().strftime('%Y%m%d')
        def _request(date_str:str):
            r = requests.post(self.s.rest_base + '/api/us/chart', headers=self.headers('usa06011'), json={
                'stex_tp': exchange, 'stk_cd': symbol, 'strt_dt': date_str,
                'tic_scope': str(minutes), 'upd_stkpc_tp': '0', 'exrt_appl_tp': '1'
            }, timeout=20)
            return r.json()

        d = _request(start_date)
        if d.get('return_code') != 0:
            raise RuntimeError(f"minute {symbol}/{exchange}: {d.get('return_code')} {d.get('return_msg')}")

        rows = d.get('result_list') or []
        if not rows and start_date:
            base = datetime.strptime(start_date, '%Y%m%d').date()
            for delta_days in range(1, 8):
                candidate = base - timedelta(days=delta_days)
                if candidate.weekday() >= 5:
                    continue
                d2 = _request(candidate.strftime('%Y%m%d'))
                if d2.get('return_code') == 0 and (d2.get('result_list') or []):
                    d = d2
                    rows = d2.get('result_list') or []
                    break

        parsed=[]
        for x in rows:
            close=abs(num(x.get('cur_prc'))); op=abs(num(x.get('open_pric'))); hi=abs(num(x.get('high_pric'))); lo=abs(num(x.get('low_pric')))
            vol=abs(num(x.get('trde_qty')))
            bus=str(x.get('bus_dt') or '').strip()
            tm=''.join(ch for ch in str(x.get('cntr_tm') or '') if ch.isdigit())
            if close <= 0 or len(bus) < 8: continue
            tp=tm[8:14] if len(tm)>=14 and tm[:8]==bus[:8] else (('000000'+tm)[-6:] if tm else '000000')
            try:
                hh,mm,ss=int(tp[:2]),int(tp[2:4]),int(tp[4:6])
                base=datetime.strptime(bus[:8],'%Y%m%d').replace(tzinfo=ZoneInfo('Asia/Seoul'))
                local=base+timedelta(hours=hh,minutes=mm,seconds=ss)
                ts=local.astimezone(timezone.utc)
            except Exception:
                continue
            parsed.append({'time':ts,'open':op or close,'high':hi or close,'low':lo or close,'close':close,'volume':vol})
        parsed.sort(key=lambda x:x['time'])
        return parsed


    def premarket_probe(self, symbol:str, exchange:str) -> dict:
        """
        Inspect actual 1-minute chart timestamps instead of assuming the REST quote is live.
        A value is called PREMARKET_LIVE only when the latest minute bar belongs to today
        (America/New_York) and is recent enough during the 04:00-09:29 ET premarket window.
        """
        now_utc=datetime.now(timezone.utc)
        et=now_utc.astimezone(ZoneInfo('America/New_York'))
        phase='CLOSED'
        if et.weekday()<5:
            minute=et.hour*60+et.minute
            if 4*60 <= minute < 9*60+30:
                phase='PREMARKET'
            elif 9*60+30 <= minute < 16*60:
                phase='REGULAR'
            elif 16*60 <= minute < 20*60:
                phase='AFTER_HOURS'

        rows=self.minute_chart(symbol,exchange,1)
        latest=rows[-1] if rows else None
        latest_et=latest['time'].astimezone(ZoneInfo('America/New_York')) if latest else None
        age_min=((now_utc-latest['time']).total_seconds()/60.0) if latest else None
        same_day=bool(latest_et and latest_et.date()==et.date())

        fresh=bool(
            phase=='PREMARKET' and same_day and age_min is not None
            and -2 <= age_min <= 15
            and latest_et.hour*60+latest_et.minute >= 4*60
            and latest_et.hour*60+latest_et.minute < 9*60+30
        )
        mode='PREMARKET_LIVE' if fresh else ('LIVE_OTHER_SESSION' if same_day and age_min is not None and age_min<=15 else 'LAST_SESSION')

        pm_rows=[]
        if rows:
            for r in rows:
                rt=r['time'].astimezone(ZoneInfo('America/New_York'))
                rm=rt.hour*60+rt.minute
                if rt.date()==et.date() and 4*60 <= rm < 9*60+30:
                    pm_rows.append(r)

        q=self.db.get_quote(symbol) if hasattr(self.db,'get_quote') else None
        prev_close=float((q or {}).get('prev_close') or 0)
        pm_price=float(latest['close']) if fresh and latest else 0.0
        pm_change=((pm_price/prev_close-1)*100) if fresh and pm_price>0 and prev_close>0 else None
        pm_volume=sum(float(x.get('volume') or 0) for x in pm_rows) if fresh else None

        return {
            'symbol':symbol,
            'phase':phase,
            'data_mode':mode,
            'is_fresh_premarket':fresh,
            'checked_at':now_utc.isoformat(),
            'latest_bar_at':latest['time'].isoformat() if latest else None,
            'latest_bar_et':latest_et.isoformat() if latest_et else None,
            'latest_age_minutes':round(age_min,2) if age_min is not None else None,
            'premarket_price':pm_price if fresh else None,
            'premarket_change_pct':round(pm_change,4) if pm_change is not None else None,
            'premarket_volume':pm_volume,
            'premarket_bar_count':len(pm_rows) if fresh else 0,
        }

    def backfill_symbol(self, symbol: str, exchange: str, min_bars: int = 80):
        rows=self.minute_chart(symbol, exchange, 1)
        if not rows: raise RuntimeError(f'backfill {symbol}: empty minute chart')
        rows=rows[-max(min_bars, 30):]; self.db.delete_zero_qty_ticks(symbol)
        cumulative_by_day={}; inserted=0
        for bar in rows:
            day=bar['time'].astimezone(ZoneInfo('America/New_York')).date().isoformat()
            running=cumulative_by_day.get(day,0.0); barvol=max(0.0,float(bar.get('volume') or 0))
            seq=[bar['open'],bar['low'],bar['high'],bar['close']] if bar['close']>=bar['open'] else [bar['open'],bar['high'],bar['low'],bar['close']]
            portions=[0.20,0.25,0.25,0.30]
            for sec,(price,portion) in enumerate(zip(seq,portions),start=1):
                qty=barvol*portion; running += qty
                ts=(bar['time'].replace(second=0,microsecond=0)+timedelta(seconds=(5,20,40,55)[sec-1])).isoformat()
                inserted += self.db.add_tick_if_missing(symbol,float(price),qty,running,ts)
            cumulative_by_day[day]=running
        return inserted, len(rows)

    async def backfill_forever_once(self):
        """V4.3.3 periodic live-bar recovery for USA regular session."""
        await asyncio.sleep(1.0)
        last_regular_run=0.0
        closed_bootstrap_done=False

        while True:
            try:
                et=datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York'))
                regular=(et.weekday()<5 and (et.hour,et.minute)>=(9,30) and (et.hour,et.minute)<(16,0))

                if not regular:
                    if not closed_bootstrap_done:
                        for symbol in tuple(self.s.symbols)[:8]:
                            try:
                                inserted,bars=await asyncio.to_thread(
                                    self.backfill_symbol,symbol,self.active_exchange(symbol),80
                                )
                                log.info('minute backfill %s: bars=%s inserted=%s',symbol,bars,inserted)
                            except Exception as e:
                                log.warning('minute backfill %s: %s',symbol,e)
                            await asyncio.sleep(0.35)
                        closed_bootstrap_done=True
                    await asyncio.sleep(30)
                    continue

                closed_bootstrap_done=False
                now_ts=asyncio.get_running_loop().time()
                if last_regular_run and now_ts-last_regular_run < 55:
                    await asyncio.sleep(5)
                    continue

                # Keep the fallback intentionally small to limit REST load.
                for symbol in tuple(self.s.symbols)[:8]:
                    try:
                        inserted,bars=await asyncio.to_thread(
                            self.backfill_symbol,symbol,self.active_exchange(symbol),40
                        )
                        log.info('live minute recovery %s: bars=%s inserted=%s',symbol,bars,inserted)
                    except Exception as e:
                        log.warning('live minute recovery %s: %s',symbol,e)
                    await asyncio.sleep(0.25)

                last_regular_run=now_ts

            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception('live minute recovery loop error: %s',e)

            await asyncio.sleep(5)

    def _extract_f5(self, msg: dict):
        for row in msg.get('data') or []:
            if str(row.get('type')) != 'F5': continue
            symbol = str(row.get('item') or '').upper(); values = row.get('values') or {}; price=None
            for k in ('10','price','cur_prc','curr_pric','last','12'):
                if k in values and num(values[k]) != 0: price=abs(num(values[k])); break
            cumvol=0.0
            for k in ('13','volume','acc_trde_qty','cum_volume'):
                if k in values: cumvol=abs(num(values[k])); break
            qty=0.0
            for k in ('15','qty','trade_qty'):
                if k in values: qty=abs(num(values[k])); break
            if symbol and price: yield symbol,price,qty,cumvol

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
                    registered=tuple(self.s.symbols)
                    reg_items=self._ws_items(registered)
                    if not reg_items:
                        raise RuntimeError('REG failed: no valid USA websocket items')
                    await ws.send(json.dumps({'trnm':'REG','grp_no':'1','refresh':'1','data':[{'item':reg_items,'type':['F5']}]}))
                    reg_d=json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
                    self.db.add_raw(json.dumps(reg_d,ensure_ascii=False), datetime.now(timezone.utc).isoformat())
                    if reg_d.get('return_code') != 0: raise RuntimeError(f'REG failed: {reg_d}')
                    log.info('WebSocket live: %s', ','.join(f"{x['jmcode']}/{x['stex_tp']}" for x in reg_items))
                    while True:
                        current=tuple(self.s.symbols)
                        if current != registered:
                            reg_items=self._ws_items(current)
                            if reg_items:
                                await ws.send(json.dumps({'trnm':'REG','grp_no':'1','refresh':'1','data':[{'item':reg_items,'type':['F5']}]}))
                                registered=current
                                log.info('WebSocket universe refreshed: %s',','.join(f"{x['jmcode']}/{x['stex_tp']}" for x in reg_items))
                            else:
                                log.warning('WebSocket universe refresh skipped: no valid USA websocket items')
                        try:
                            raw=await asyncio.wait_for(ws.recv(),timeout=15)
                        except asyncio.TimeoutError:
                            continue
                        now=datetime.now(timezone.utc).isoformat(); d=json.loads(raw)
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
                try: self.quote(symbol,self.active_exchange(symbol))
                except Exception as e: log.warning('snapshot %s: %s',symbol,e)
                await asyncio.sleep(0.22)
            await asyncio.sleep(self.s.poll_seconds)

    async def daily_refresh_forever(self):
        while True:
            for symbol in self.s.symbols:
                try: self.daily_metrics(symbol,self.active_exchange(symbol))
                except Exception as e: log.warning('daily metrics %s: %s',symbol,e)
                await asyncio.sleep(0.25)
            await asyncio.sleep(self.s.daily_refresh_seconds)
