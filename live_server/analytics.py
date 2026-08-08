from __future__ import annotations
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import math
import pandas as pd
from trader.signals import intraday_signal, position_signal
from trader.indicators import enrich_intraday
from trader.config import TradingConfig

CFG=TradingConfig()

def ticks_to_bars(ticks: list[dict], minutes: int=1) -> pd.DataFrame:
    if not ticks: return pd.DataFrame(columns=['time','open','high','low','close','volume'])
    df=pd.DataFrame(ticks); df['ts']=pd.to_datetime(df['ts'], utc=True); df['price']=pd.to_numeric(df['price'], errors='coerce')
    df['cum_volume']=pd.to_numeric(df.get('cum_volume',0), errors='coerce').fillna(0); df=df.dropna(subset=['price']).set_index('ts')
    rule=f'{minutes}min'; ohlc=df['price'].resample(rule).ohlc()
    if df['cum_volume'].max() > 0: vol=df['cum_volume'].resample(rule).last().diff().clip(lower=0).fillna(0)
    else: vol=pd.to_numeric(df.get('qty',0), errors='coerce').fillna(0).resample(rule).sum()
    return ohlc.join(vol.rename('volume')).dropna(subset=['close']).reset_index().rename(columns={'ts':'time'})

def market_minutes_elapsed() -> int:
    et=datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York'))
    mins=(et.hour*60+et.minute)-(9*60+30)
    return max(1,min(390,mins))

def screener_rows(quotes: list[dict], metrics: list[dict], top_n: int=10) -> list[dict]:
    mm={m['symbol']:m for m in metrics}; qmap={q['symbol']:q for q in quotes}
    qqq=qmap.get('QQQ',{}); smh=qmap.get('SMH',{})
    index_strength=float(qqq.get('change_pct') or 0); semi_strength=float(smh.get('change_pct') or 0)
    elapsed=market_minutes_elapsed(); progress=max(0.08,elapsed/390)
    out=[]
    for q in quotes:
        sym=q['symbol']; m=mm.get(sym,{})
        price=float(q.get('price') or 0); vol=float(q.get('volume') or 0); day=float(q.get('change_pct') or 0)
        ma5=float(m.get('ma5') or 0); slope=float(m.get('ma5_slope_pct') or 0); avg5vol=float(m.get('avg5_volume') or 0)
        avg5dv=float(m.get('avg5_dollar_volume') or 0); atr=float(m.get('atr5_pct') or 0)
        dollar=price*vol
        # Time-adjusted RVOL: current cumulative vs expected fraction of 5-day avg volume.
        rvol=(vol/max(avg5vol*progress,1)) if avg5vol>0 else 0
        score=0; parts={}
        def add(name,pts):
            nonlocal score; pts=max(0,min(pts,20)); score+=pts; parts[name]=round(pts,1)
        add('MA5', 12 if (ma5>0 and price>ma5) else 0)
        add('MA5 slope', min(8,max(0,slope*4)))
        add('Liquidity', min(15,15*dollar/150_000_000))
        add('RVOL', min(15,15*rvol/2.5))
        add('ATR', min(10,10*atr/4.0))
        add('Momentum', min(12,12*abs(day)/6.0))
        sector=semi_strength if sym in {'SOXL','SOXS','SMH','NVDA','AMD','AVGO','MU','ARM','TSM','ASML','INTC','QCOM'} else index_strength
        directional_day = -day if sym in {'SOXS','SQQQ'} else day
        directional_sector = -sector if sym in {'SOXS','SQQQ'} else sector
        add('Market/Sector', min(8,max(0,directional_sector)*2.0))
        # room from intraday high: reward strength near high but not >~8% extended.
        high=float(q.get('high') or 0); room=(price/high if high>0 else 0)
        add('Price action', 10 if 0.985<=room<=1.003 and abs(day)<=10 else (5 if room>=0.97 else 0))
        score=min(100,round(score))
        bias='LONG' if directional_day+0.4*directional_sector>=0 else 'SHORT'
        # Required base filter: current price above 5-day avg for long candidate. Inverse ETFs are treated on their own price trend.
        eligible=(price>=5 and dollar>=20_000_000 and (ma5<=0 or price>ma5))
        out.append({'symbol':sym,'score':score,'bias':bias,'price':price,'change_pct':day,'volume':vol,
                    'dollar_volume':dollar,'exchange':q.get('exchange'),'ma5':ma5,'ma5_slope_pct':slope,'rvol':rvol,
                    'atr_pct':atr,'avg5_dollar_volume':avg5dv,'eligible':eligible,'parts':parts})
    rows=[r for r in out if r['eligible']]
    rows.sort(key=lambda r:(r['score'],r['dollar_volume']),reverse=True)
    return rows[:top_n]

def multi_timeframe_signal(symbol: str, ticks: list[dict], market_bias: float=.5):
    b1=ticks_to_bars(ticks,1); b5=ticks_to_bars(ticks,5)
    if len(b1)<20:
        return {'symbol':symbol,'state':'WARMING','score':0,'reason':f'1분봉 {len(b1)}/20 수집 중','bars1':len(b1),'bars5':len(b5)}
    s1=intraday_signal(symbol,b1,market_bias=market_bias,cfg=CFG)
    conf=0; reasons=[]
    if len(b5)>=3:
        e5=enrich_intraday(b5); r=e5.iloc[-1]; prev=e5.iloc[-2]
        if r.close>r.vwap: conf+=7; reasons.append('5M>VWAP')
        if r.ema9>r.ema20: conf+=7; reasons.append('5M EMA9>20')
        if r.close>=prev.close: conf+=4; reasons.append('5M 상승')
        if r.rsi14>=50: conf+=2
    final=min(100,round(s1.score*0.82+conf))
    state='WAIT'
    if final>=CFG.trigger_score and conf>=10: state='TRIGGER'
    elif final>=CFG.setup_score: state='SETUP'
    elif final>=CFG.watch_score: state='WATCH'
    return {'symbol':symbol,'state':state,'score':final,'score_1m':s1.score,'confirm_5m':conf,'price':s1.price,
            'bias':s1.bias,'reason':', '.join([s1.reason]+reasons),'invalidation':s1.invalidation,
            'target1':s1.target1,'target2':s1.target2,'critical':s1.critical,'bars1':len(b1),'bars5':len(b5)}

def position_from_ticks(symbol:str,ticks:list[dict],entry:float):
    b=ticks_to_bars(ticks,1)
    if len(b)<5: return {'symbol':symbol,'state':'WARMING','reason':'포지션 분석 데이터 수집 중'}
    s=position_signal(symbol,b,entry,CFG)
    pnl=(s.price/entry-1)*100 if entry else 0
    return {'symbol':symbol,'state':s.state,'price':s.price,'entry':entry,'pnl_pct':pnl,'reason':s.reason,
            'invalidation':s.invalidation,'critical':s.critical}
