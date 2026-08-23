from pathlib import Path

MOD=Path('live_server/fujimoto.py')
API=Path('live_server/api.py')

MODULE=r'''from __future__ import annotations
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def _ema(values, span):
    if not values: return []
    a=2.0/(span+1.0); out=[float(values[0])]
    for v in values[1:]: out.append(a*float(v)+(1-a)*out[-1])
    return out


def _rsi(values, period=14):
    n=len(values)
    if n<period+2: return [None]*n
    out=[None]*n
    gains=[]; losses=[]
    for i in range(1,period+1):
        d=float(values[i])-float(values[i-1]); gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains)/period; al=sum(losses)/period
    out[period]=100.0 if al==0 else 100.0-(100.0/(1.0+ag/al))
    for i in range(period+1,n):
        d=float(values[i])-float(values[i-1]); g=max(d,0); l=max(-d,0)
        ag=(ag*(period-1)+g)/period; al=(al*(period-1)+l)/period
        out[i]=100.0 if al==0 else 100.0-(100.0/(1.0+ag/al))
    return out


def _cross_up(series, level=0.0, lookback=1):
    if len(series)<2: return None
    start=max(1,len(series)-1-int(lookback))
    found=[]
    for i in range(start,len(series)):
        a=series[i-1]; b=series[i]
        if a is not None and b is not None and a<=level<b: found.append(len(series)-1-i)
    return min(found) if found else None


def _cross_down(series, level=0.0, lookback=1):
    if len(series)<2: return None
    start=max(1,len(series)-1-int(lookback))
    found=[]
    for i in range(start,len(series)):
        a=series[i-1]; b=series[i]
        if a is not None and b is not None and a>=level>b: found.append(len(series)-1-i)
    return min(found) if found else None


def _pair_cross_up(a,b,lookback=1):
    found=[]
    start=max(1,len(a)-1-int(lookback))
    for i in range(start,min(len(a),len(b))):
        if a[i-1]<=b[i-1] and a[i]>b[i]: found.append(len(a)-1-i)
    return min(found) if found else None


def _pair_cross_down(a,b,lookback=1):
    found=[]
    start=max(1,len(a)-1-int(lookback))
    for i in range(start,min(len(a),len(b))):
        if a[i-1]>=b[i-1] and a[i]<b[i]: found.append(len(a)-1-i)
    return min(found) if found else None


def _local_lows(values, window=2):
    out=[]
    for i in range(window,len(values)-window):
        v=values[i]
        if v is None: continue
        if all(v<=values[j] for j in range(i-window,i+window+1) if j!=i and values[j] is not None): out.append(i)
    return out


def _local_highs(values, window=2):
    out=[]
    for i in range(window,len(values)-window):
        v=values[i]
        if v is None: continue
        if all(v>=values[j] for j in range(i-window,i+window+1) if j!=i and values[j] is not None): out.append(i)
    return out


def _divergence(prices,rsi):
    bull=bear=False
    lows=_local_lows(prices[-60:]); highs=_local_highs(prices[-60:]); off=max(0,len(prices)-60)
    lows=[x+off for x in lows if rsi[x+off] is not None]
    highs=[x+off for x in highs if rsi[x+off] is not None]
    if len(lows)>=2:
        a,b=lows[-2],lows[-1]
        bull=bool(prices[b]<=prices[a] and rsi[b]>rsi[a]+2.0)
    if len(highs)>=2:
        a,b=highs[-2],highs[-1]
        bear=bool(prices[b]>=prices[a] and rsi[b]<rsi[a]-2.0)
    return bull,bear


def _state(score):
    if score>=90:return 'VERY_STRONG'
    if score>=75:return 'STRONG_ENTRY'
    if score>=60:return 'ENTRY_READY'
    if score>=40:return 'PREPARE'
    return 'WATCH'


def evaluate_fujimoto_v1(bars):
    bars=[b for b in (bars or []) if float(b.get('close') or 0)>0]
    if len(bars)<40:
        return {'ok':False,'reason':f'insufficient_1m_bars:{len(bars)}','score':None,'state':'DATA_INVALID'}
    closes=[float(b['close']) for b in bars]
    rsi=_rsi(closes,14)
    e12=_ema(closes,12); e26=_ema(closes,26); macd=[a-b for a,b in zip(e12,e26)]; signal=_ema(macd,9); hist=[a-b for a,b in zip(macd,signal)]
    ma20=sum(closes[-20:])/20.0

    rsi30up=_cross_up(rsi,30,5); rsi50up=_cross_up(rsi,50,3); rsi50down=_cross_down(rsi,50,2); rsi70down=_cross_down(rsi,70,2)
    macd_zero_up=_cross_up(macd,0,5); golden=_pair_cross_up(macd,signal,3); dead=_pair_cross_down(macd,signal,2)
    bull_div,bear_div=_divergence(closes,rsi)
    rv=float(rsi[-1] or 0); mv=float(macd[-1]); sv=float(signal[-1]); hv=float(hist[-1])
    rsi_rising=all((rsi[-i] or -1)>(rsi[-i-1] or -1) for i in (1,2))
    hist_rising=hist[-1]>hist[-2]>hist[-3]
    hist_falling=hist[-1]<hist[-2]<hist[-3]

    score=20; reasons=[]; penalties=[]
    def add(n,label):
        nonlocal score; score+=n; reasons.append({'points':n,'reason':label})
    def sub(n,label):
        nonlocal score; score-=n; penalties.append({'points':-n,'reason':label})

    if rsi30up is not None:add(12,'RSI_30_RECLAIM')
    if rsi50up is not None:add(12,'RSI_50_CROSS_UP')
    if rv>=50:add(8,'RSI_ABOVE_50')
    if rsi_rising:add(6,'RSI_RISING_3')
    if bull_div:add(12,'RSI_BULLISH_DIVERGENCE')
    if golden is not None:add(14,'MACD_GOLDEN_CROSS')
    if mv>sv:add(8,'MACD_ABOVE_SIGNAL')
    if hist_rising:add(8,'MACD_HISTOGRAM_RISING')
    if macd_zero_up is not None:add(10,'MACD_ZERO_CROSS_UP')
    if mv>0:add(6,'MACD_ABOVE_ZERO')
    if rv>=50 and mv>sv and hv>0:add(10,'RSI_MACD_CONFIRMATION')

    if rsi50down is not None:sub(10,'RSI_50_CROSS_DOWN')
    if rsi70down is not None:sub(12,'RSI_70_EXIT_DOWN')
    if dead is not None:sub(15,'MACD_DEAD_CROSS')
    if hist_falling:sub(8,'MACD_HISTOGRAM_FALLING')
    if bear_div:sub(12,'RSI_BEARISH_DIVERGENCE')

    score=max(0,min(100,int(round(score))))
    now=datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Seoul'))
    regular=bool(now.weekday()<5 and (now.hour*60+now.minute)>=540 and (now.hour*60+now.minute)<930)
    return {
        'ok':True,'version':'FUJIMOTO_SCORE_V1','timeframe':'1m','score':score,'state':_state(score),'actionable':regular,
        'rsi':round(rv,2),'macd':round(mv,6),'macd_signal':round(sv,6),'macd_hist':round(hv,6),
        'ma20':round(ma20,6),'price':closes[-1],'price_above_ma20':bool(closes[-1]>ma20),
        'signals':{
            'rsi_30_reclaim_bars_ago':rsi30up,'rsi_50_cross_up_bars_ago':rsi50up,'rsi_50_cross_down_bars_ago':rsi50down,
            'rsi_70_cross_down_bars_ago':rsi70down,'rsi_rising_3':rsi_rising,'bullish_divergence':bull_div,'bearish_divergence':bear_div,
            'macd_golden_cross_bars_ago':golden,'macd_dead_cross_bars_ago':dead,'macd_zero_cross_up_bars_ago':macd_zero_up,
            'macd_above_signal':bool(mv>sv),'macd_above_zero':bool(mv>0),'histogram_rising_3':hist_rising,'histogram_falling_3':hist_falling,
        },
        'positive_reasons':reasons,'penalties':penalties,'bar_count':len(bars),'latest_bar_time':bars[-1].get('time'),
        'scoring_note':'Core score uses RSI(14)+MACD(12,26,9). MA20 is diagnostic context only in v1.'
    }
'''

API_PATCH=r'''

from .fujimoto import evaluate_fujimoto_v1

@app.get('/api/v5/fujimoto-score/KOREA/{symbol}')
async def v5_fujimoto_score_korea(symbol:str,max_pages:int=2):
    def _run():
        d=korea.canonical_minute_bars(symbol,max_pages=max(1,min(int(max_pages),3)))
        out=evaluate_fujimoto_v1(d.get('bars') or [])
        out['symbol']=symbol
        out['source']='KIWOOM_KA10080_CANONICAL_1M'
        return out
    return await asyncio.to_thread(_run)
'''

def main():
    MOD.write_text(MODULE)
    a=API.read_text()
    if '/api/v5/fujimoto-score/KOREA/{symbol}' not in a:
        anchor="app=FastAPI(title='DAY TRADER LIVE API',version='3.5',lifespan=lifespan)\n"
        if anchor not in a: raise SystemExit('PATCH_TARGET_NOT_FOUND: FastAPI app')
        a=a.replace(anchor,anchor+API_PATCH+'\n',1)
        API.write_text(a)
    print('FUJIMOTO_SCORE_V1_PATCH_OK')

if __name__=='__main__': main()
