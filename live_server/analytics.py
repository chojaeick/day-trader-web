from __future__ import annotations
from datetime import datetime
import pandas as pd
from trader.signals import intraday_signal
from trader.config import TradingConfig


def ticks_to_bars(ticks: list[dict], minutes: int=1) -> pd.DataFrame:
    if not ticks: return pd.DataFrame(columns=['time','open','high','low','close','volume'])
    df=pd.DataFrame(ticks)
    df['ts']=pd.to_datetime(df['ts'], utc=True)
    df['price']=pd.to_numeric(df['price'], errors='coerce')
    df['cum_volume']=pd.to_numeric(df.get('cum_volume',0), errors='coerce').fillna(0)
    df=df.dropna(subset=['price']).set_index('ts')
    rule=f'{minutes}min'
    ohlc=df['price'].resample(rule).ohlc()
    # Prefer cumulative-volume deltas. If unavailable, sum qty.
    if df['cum_volume'].max() > 0:
        vol=df['cum_volume'].resample(rule).last().diff().clip(lower=0).fillna(0)
    else:
        vol=pd.to_numeric(df.get('qty',0), errors='coerce').fillna(0).resample(rule).sum()
    out=ohlc.join(vol.rename('volume')).dropna(subset=['close']).reset_index().rename(columns={'ts':'time'})
    return out


def signal_from_ticks(symbol: str, ticks: list[dict], market_bias: float=.5):
    bars=ticks_to_bars(ticks,1)
    if len(bars) < 55:
        return {'symbol':symbol,'state':'WARMING','score':0,'reason':f'1분봉 {len(bars)}/55 수집 중','bars':len(bars)}
    s=intraday_signal(symbol,bars,market_bias=market_bias,cfg=TradingConfig())
    return {'symbol':symbol,'state':s.state,'score':s.score,'price':s.price,'bias':s.bias,'reason':s.reason,
            'invalidation':s.invalidation,'target1':s.target1,'target2':s.target2,'critical':s.critical,'bars':len(bars)}
