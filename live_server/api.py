from __future__ import annotations
import asyncio, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from .config import Settings
from .db import DB
from .kiwoom import KiwoomClient
from .analytics import ticks_to_bars, signal_from_ticks

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
s=Settings(); db=DB(s.db_path); k=KiwoomClient(s,db); tasks=[]

@asynccontextmanager
async def lifespan(app: FastAPI):
    if not s.app_key or not s.app_secret:
        logging.error('KIWOOM_APP_KEY / KIWOOM_APP_SECRET missing')
    else:
        tasks.extend([asyncio.create_task(k.websocket_forever()), asyncio.create_task(k.snapshot_poll_forever())])
    yield
    for t in tasks: t.cancel()

app=FastAPI(title='DAY TRADER LIVE API',version='1.1',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_credentials=False,allow_methods=['GET'],allow_headers=['*'])

@app.get('/health')
def health():
    qs=db.quotes()
    return {'ok':True,'mode':'LIVE','symbols':s.symbols,'quotes':len(qs),'db':s.db_path}

@app.get('/api/quotes')
def quotes(): return db.quotes()

@app.get('/api/quote/{symbol}')
def quote(symbol:str):
    q=db.quote(symbol)
    if not q: raise HTTPException(404,'quote not available yet')
    return q

@app.get('/api/bars/{symbol}')
def bars(symbol:str, minutes:int=Query(1,ge=1,le=60), limit:int=Query(200,ge=10,le=1000)):
    out=ticks_to_bars(db.ticks(symbol,20000),minutes).tail(limit)
    return {'symbol':symbol.upper(),'minutes':minutes,'data':out.assign(time=out['time'].astype(str)).to_dict('records')}

@app.get('/api/signal/{symbol}')
def signal(symbol:str):
    return signal_from_ticks(symbol.upper(),db.ticks(symbol,20000))

@app.get('/api/raw')
def raw(limit:int=20): return db.raw(min(limit,100))
