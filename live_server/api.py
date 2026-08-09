from __future__ import annotations
import time
import asyncio, logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from .config import Settings, FALLBACK_UNIVERSE, _symbols
from .db import DB
from .kiwoom import KiwoomClient
from .analytics import ticks_to_bars, multi_timeframe_signal, position_from_ticks, screener_rows, context_for
from .validation import HistoricalValidator, LiveTop10Validator
from .archive import RankingArchive

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
s=Settings(); db=DB(s.db_path); k=KiwoomClient(s,db); validator=HistoricalValidator(k,s.db_path); live_validator=LiveTop10Validator(s.db_path); archive=RankingArchive(s.db_path); tasks=[]
manual_scan_state={'last_started_monotonic':0.0,'last_result':None}

async def checkpoint_forever():
    done=set()
    while True:
        now=datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York'))
        day=now.strftime('%Y-%m-%d'); minute=now.hour*60+now.minute
        targets={'T-10':9*60+20,'T-1':9*60+29,'T+7':9*60+37,'T+30':10*60,'T+60':10*60+30,'CLOSE':15*60+59}
        for label,target in targets.items():
            key=(day,label)
            if key not in done and target<=minute<=target+1 and now.weekday()<5:
                rows=screener_rows(db.quotes(),db.daily_metrics(),10)
                if rows:
                    captured=datetime.now(timezone.utc).isoformat()
                    db.save_ranking_snapshot(day,label,rows,captured)
                    qmap={q.get('symbol'):q for q in db.quotes()}
                    archive.save(day,label,'CURRENT',rows,captured,
                                 float((qmap.get('QQQ') or {}).get('change_pct') or 0),
                                 float((qmap.get('SMH') or {}).get('change_pct') or 0))
                    done.add(key)
        await asyncio.sleep(20)

@asynccontextmanager
async def lifespan(app: FastAPI):
    if not s.app_key or not s.app_secret:
        logging.error('KIWOOM_APP_KEY / KIWOOM_APP_SECRET missing')
    else:
        try:
            await asyncio.to_thread(k.discover_universe)
        except Exception as e:
            logging.warning('startup universe discovery failed; using fallback universe: %s', e)
        tasks.extend([asyncio.create_task(k.websocket_forever()),asyncio.create_task(k.snapshot_poll_forever()),
                      asyncio.create_task(k.daily_refresh_forever()),asyncio.create_task(k.backfill_forever_once()),
                      asyncio.create_task(k.discovery_forever()),asyncio.create_task(checkpoint_forever())])
    yield
    for t in tasks: t.cancel()

app=FastAPI(title='DAY TRADER LIVE API',version='1.8',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_credentials=False,allow_methods=['GET','POST'],allow_headers=['*'])

@app.get('/health')
def health():
    qs=db.quotes()
    return {'ok':True,'mode':'LIVE','version':'1.8','hotfix':'scan-3','symbols':s.symbols,'quotes':len(qs),'daily_metrics':len(db.daily_metrics()),'db':s.db_path}

@app.get('/api/quotes')
def quotes(): return db.quotes()

@app.get('/api/quote/{symbol}')
def quote(symbol:str):
    q=db.quote(symbol)
    if not q: raise HTTPException(404,'quote not available yet')
    m=db.daily_metric(symbol) or {}; return {**q,**m}

@app.get('/api/market-context/{symbol}')
def market_context(symbol:str):
    _,_,ctx=context_for(symbol.upper(),db.quotes()); return ctx

@app.get('/api/screener')
def screener(top_n:int=Query(10,ge=1,le=30)):
    return {'data':screener_rows(db.quotes(),db.daily_metrics(),top_n),'updated_at':datetime.now(timezone.utc).isoformat()}

@app.get('/api/ranking-history')
def ranking_history(): return {'data':db.ranking_history()}


@app.get('/api/archive/dates')
def archive_dates(limit:int=Query(120,ge=1,le=500)):
    return {'data':archive.dates(limit)}

@app.get('/api/archive/snapshots')
def archive_snapshots(trade_date:str):
    return {'data':archive.snapshots(trade_date)}

@app.get('/api/archive/ranking')
def archive_ranking(trade_date:str,label:str,model:str='CURRENT'):
    x=archive.ranking(trade_date,label,model)
    if not x: raise HTTPException(404,'archived ranking not found')
    return x

@app.get('/api/archive/recent')
def archive_recent(limit:int=Query(50,ge=1,le=500)):
    return {'data':archive.recent(limit)}

@app.get('/api/archive/save-now')
def archive_save_now(label:str='MANUAL'):
    now=datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York'))
    day=now.strftime('%Y-%m-%d')
    rows=screener_rows(db.quotes(),db.daily_metrics(),10)
    if not rows: raise HTTPException(409,'screener rows not ready')
    captured=datetime.now(timezone.utc).isoformat()
    qmap={q.get('symbol'):q for q in db.quotes()}
    meta_id=archive.save(day,label,'CURRENT',rows,captured,
                         float((qmap.get('QQQ') or {}).get('change_pct') or 0),
                         float((qmap.get('SMH') or {}).get('change_pct') or 0))
    return {'ok':True,'id':meta_id,'trade_date':day,'label':label.upper(),'model':'CURRENT','rows':len(rows)}

@app.get('/api/bars/{symbol}')
def bars(symbol:str,minutes:int=Query(1,ge=1,le=60),limit:int=Query(200,ge=10,le=1000)):
    out=ticks_to_bars(db.ticks(symbol,40000),minutes).tail(limit)
    return {'symbol':symbol.upper(),'minutes':minutes,'data':out.assign(time=out['time'].astype(str)).to_dict('records')}

@app.get('/api/signal/{symbol}')
def signal(symbol:str):
    return multi_timeframe_signal(symbol.upper(),db.ticks(symbol,40000),db.quotes())

@app.get('/api/position/{symbol}')
def position(symbol:str,entry:float=Query(...,gt=0),side:str=Query('LONG',pattern='^(LONG|SHORT)$')):
    return position_from_ticks(symbol.upper(),db.ticks(symbol,40000),entry,side,db.quotes())

@app.get('/api/raw')
def raw(limit:int=20): return db.raw(min(limit,100))


@app.get('/api/universe')
def universe():
    return k.discovery



@app.post('/api/scan/market')
async def scan_market_now():
    cooldown=int(getattr(s,'manual_scan_cooldown_seconds',45))
    now=time.monotonic()
    elapsed=now-float(manual_scan_state.get('last_started_monotonic') or 0)
    if elapsed < cooldown:
        return {
            'ok':False,'cooldown':True,
            'retry_after':round(cooldown-elapsed,1),
            'last_result':manual_scan_state.get('last_result')
        }

    manual_scan_state['last_started_monotonic']=now
    before_top=screener_rows(db.quotes(),db.daily_metrics(),10)
    before_syms=[x.get('symbol') for x in before_top]

    res=await k.manual_discover_now()

    # Recalculate with whatever data is available after priming.
    after_top=screener_rows(db.quotes(),db.daily_metrics(),10)
    after_syms=[x.get('symbol') for x in after_top]
    changed=[x for x in after_syms if x not in before_syms]

    ny=datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York'))
    label='MANUAL_SCAN_'+ny.strftime('%H%M')
    captured=datetime.now(timezone.utc).isoformat()
    qmap={q.get('symbol'):q for q in db.quotes()}
    if after_top:
        archive.save(
            ny.strftime('%Y-%m-%d'),label,'CURRENT',after_top,captured,
            float((qmap.get('QQQ') or {}).get('change_pct') or 0),
            float((qmap.get('SMH') or {}).get('change_pct') or 0)
        )

    out={
        **res,
        'cooldown_seconds':cooldown,
        'archive_label':label,
        'top10_before':before_syms,
        'top10_after':after_syms,
        'top10_new':changed
    }
    manual_scan_state['last_result']=out
    return out

@app.get('/api/scan/status')
def scan_status():
    cooldown=int(getattr(s,'manual_scan_cooldown_seconds',45))
    elapsed=time.monotonic()-float(manual_scan_state.get('last_started_monotonic') or 0)
    return {
        'cooldown_seconds':cooldown,
        'retry_after':max(0,round(cooldown-elapsed,1)),
        'last_result':manual_scan_state.get('last_result'),
        'last_manual_scan_at':getattr(k,'last_manual_scan_at',None)
    }

@app.get('/api/validation/runs')
def validation_runs(limit:int=Query(20,ge=1,le=100)):
    return {'data':validator.store.runs(limit)}

@app.get('/api/validation/result/{run_id}')
def validation_result(run_id:int):
    x=validator.store.result(run_id)
    if not x: raise HTTPException(404,'validation run not found')
    return x

@app.get('/api/validation/run')
def validation_run(days:int=Query(60,ge=20,le=260), max_symbols:int=Query(20,ge=8,le=32)):
    base=_symbols(FALLBACK_UNIVERSE)
    ordered=[]
    for sym in ['QQQ','SMH']+base:
        if sym not in ordered: ordered.append(sym)
    research=[x for x in ordered if x not in ('QQQ','SMH')][:max_symbols]
    return validator.run(['QQQ','SMH']+research,days)


@app.get('/api/validation/live')
def validation_live(trade_date:str|None=None):
    return live_validator.evaluate(trade_date)
