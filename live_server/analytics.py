from __future__ import annotations
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pandas as pd
from trader.signals import intraday_signal, position_signal
from trader.indicators import enrich_intraday
from trader.config import TradingConfig

CFG=TradingConfig()
LEVERAGED={'SOXL','SOXS','TQQQ','SQQQ'}
INVERSE={'SOXS','SQQQ'}
SEMI={'SOXL','SOXS','SMH','NVDA','AMD','AVGO','MU','ARM','TSM','ASML','INTC','QCOM'}

def ticks_to_bars(ticks: list[dict], minutes: int=1) -> pd.DataFrame:
    if not ticks: return pd.DataFrame(columns=['time','open','high','low','close','volume'])
    df=pd.DataFrame(ticks); df['ts']=pd.to_datetime(df['ts'], utc=True); df['price']=pd.to_numeric(df['price'], errors='coerce')
    df['cum_volume']=pd.to_numeric(df.get('cum_volume',0), errors='coerce').fillna(0); df=df.dropna(subset=['price']).set_index('ts')
    rule=f'{minutes}min'; ohlc=df['price'].resample(rule).ohlc()
    if df['cum_volume'].max() > 0:
        last=df['cum_volume'].resample(rule).last(); vol=last.diff().clip(lower=0).fillna(0)
        if 'qty' in df.columns:
            q=pd.to_numeric(df['qty'],errors='coerce').fillna(0).resample(rule).sum(); vol=vol.where(vol>0,q)
    else:
        vol=pd.to_numeric(df.get('qty',0), errors='coerce').fillna(0).resample(rule).sum()
    return ohlc.join(vol.rename('volume')).dropna(subset=['close']).reset_index().rename(columns={'ts':'time'})

def market_minutes_elapsed() -> int:
    et=datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York'))
    mins=(et.hour*60+et.minute)-(9*60+30)
    return max(1,min(390,mins))

def rvol_progress() -> float:
    # RVOL session rule:
    # - regular session: scale by elapsed fraction of 390-minute session
    # - outside regular session: compare completed/full-day volume with avg daily volume
    et=datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York'))
    if et.weekday() < 5:
        minute=et.hour*60+et.minute
        if 9*60+30 <= minute < 16*60:
            return max(0.08, market_minutes_elapsed()/390)
    return 1.0

def _score_row(q:dict,m:dict,index_strength:float,semi_strength:float,progress:float):
    sym=q['symbol']; price=float(q.get('price') or 0); vol=float(q.get('volume') or 0); day=float(q.get('change_pct') or 0)
    ma5=float(m.get('ma5') or 0); slope=float(m.get('ma5_slope_pct') or 0); avg5vol=float(m.get('avg5_volume') or 0)
    avg5dv=float(m.get('avg5_dollar_volume') or 0); atr=float(m.get('atr5_pct') or 0); high=float(q.get('high') or 0)
    dollar=price*vol; rvol=(vol/max(avg5vol*progress,1)) if avg5vol>0 else 0
    score=0.; parts={}; penalties=[]
    def add(name,pts):
        nonlocal score; score+=pts; parts[name]=round(pts,1)

    if ma5>0 and price>ma5: add('Price>MA5',14)
    else: add('Price>MA5',-18); penalties.append('현재가≤MA5')

    if slope>=2: add('MA5 slope',14)
    elif slope>0: add('MA5 slope',7+min(7,slope*3.5))
    elif slope<=-5: add('MA5 slope',-14); penalties.append('MA5 급하락')
    else: add('MA5 slope',slope*2.2); penalties.append('MA5 하락')

    # V1.4.3: stronger liquidity quality for final TOP10.
    if dollar>=1_000_000_000: add('Liquidity',18)
    elif dollar>=500_000_000: add('Liquidity',15)
    elif dollar>=150_000_000: add('Liquidity',12)
    elif dollar>=50_000_000: add('Liquidity',7)
    elif dollar>=30_000_000: add('Liquidity',3)
    elif dollar<20_000_000: add('Liquidity',-12); penalties.append('거래대금 부족')

    if rvol>=3: add('RVOL',16)
    elif rvol>=2: add('RVOL',13)
    elif rvol>=1.5: add('RVOL',10)
    elif rvol>=1: add('RVOL',5)
    elif rvol<.6: add('RVOL',-4); penalties.append('RVOL 약함')

    if 3<=atr<=8: add('ATR',12)
    elif 1.5<=atr<3 or 8<atr<=10: add('ATR',8)
    elif 1<=atr<1.5: add('ATR',4)
    elif atr>12: add('ATR',-4); penalties.append('변동성 과다')

    # Momentum with symmetric chase protection.
    abs_day=abs(day)
    extreme=abs_day>=30
    if extreme:
        add('Momentum',-28); penalties.append('EXTREME ±30%')
    elif day>0:
        if 3<=day<=10: add('Momentum',14)
        elif 1<=day<3: add('Momentum',9)
        elif 0<day<1: add('Momentum',4)
        elif 10<day<=15: add('Momentum',7); penalties.append('추격 주의')
        elif day>15: add('Momentum',-8); penalties.append('과도한 급등')
    else:
        if -3<=day<0: add('Momentum',-5); penalties.append('당일 약세')
        elif -10<=day<-3: add('Momentum',-9); penalties.append('당일 급락')
        else: add('Momentum',-15); penalties.append('과도한 급락')

    sector=semi_strength if sym in SEMI else index_strength
    own_direction=-1 if sym in INVERSE else 1
    aligned=sector*own_direction
    if aligned>=1.5: add('Market/Sector',10)
    elif aligned>0: add('Market/Sector',min(8,aligned*4))
    elif aligned<-1: add('Market/Sector',-7); penalties.append('시장/섹터 역행')

    near_high=price/high if high>0 else 0
    if 0.992<=near_high<=1.003 and day<=12: add('Price action',10)
    elif near_high>=.98: add('Price action',6)
    elif near_high<.95: add('Price action',-3)

    if sym in LEVERAGED:
        add('Liquid leveraged ETF',5)
    elif sym in {'NVDA','AAPL','MSFT','AMZN','META','TSLA','AMD','PLTR'}:
        add('Core liquidity',2)

    score=max(0,min(100,round(score)))
    bias='LONG' if day>=0 and price>=ma5 else ('SHORT' if day<0 and slope<0 else 'NEUTRAL')
    # Extreme movers are visible in discovery, but excluded from the regular TOP10.
    eligible=(price>=5 and dollar>=20_000_000 and ma5>0 and price>ma5 and not extreme)
    return {'symbol':sym,'score':score,'bias':bias,'price':price,'change_pct':day,'volume':vol,'dollar_volume':dollar,
            'exchange':q.get('exchange'),'ma5':ma5,'ma5_slope_pct':slope,'rvol':rvol,'atr_pct':atr,
            'avg5_dollar_volume':avg5dv,'eligible':eligible,'parts':parts,'penalties':penalties,'extreme':extreme}

def screener_rows(quotes: list[dict], metrics: list[dict], top_n: int=10) -> list[dict]:
    mm={m['symbol']:m for m in metrics}; qmap={q['symbol']:q for q in quotes}
    index_strength=float((qmap.get('QQQ') or {}).get('change_pct') or 0)
    semi_strength=float((qmap.get('SMH') or {}).get('change_pct') or 0)
    progress=rvol_progress()

    # V4.3.5 market regime. This is a routing aid, not a buy signal.
    breadth_proxy=(index_strength+semi_strength)/2
    if index_strength<=-1.2 and semi_strength<=-1.5:
        regime='STRONG_BEAR'
    elif index_strength<=-0.5 and semi_strength<=-0.7:
        regime='BEAR'
    elif index_strength>=1.2 and semi_strength>=1.5:
        regime='STRONG_BULL'
    elif index_strength>=0.5 and semi_strength>=0.7:
        regime='BULL'
    else:
        regime='NEUTRAL'

    out=[_score_row(q,mm.get(q['symbol'],{}),index_strength,semi_strength,progress) for q in quotes]
    for r in out:
        r['market_regime']=regime
        r['market_proxy_pct']=round(breadth_proxy,3)

        # In a bearish tape, explicitly evaluate the liquid inverse pair instead
        # of letting a long-only style ranking crowd them out.
        if regime in ('BEAR','STRONG_BEAR') and r['symbol'] in INVERSE:
            inverse_alignment=max(0.0,-(semi_strength if r['symbol']=='SOXS' else index_strength))
            live_ok=(r['price']>=5 and r['dollar_volume']>=20_000_000 and
                     r['change_pct']>0 and r['ma5']>0 and r['price']>=r['ma5']*0.995)
            if live_ok:
                boost=min(22,8+inverse_alignment*5)
                r['score']=min(100,round(r['score']+boost))
                r['eligible']=True
                r['parts']['Bear inverse rotation']=round(boost,1)

    # This app is manual long-entry only. A falling common stock is useful context,
    # but should not outrank a rising long/inverse candidate simply because |change| is large.
    for r in out:
        sym=str(r.get('symbol') or '').upper()
        chg=float(r.get('change_pct') or 0)
        if sym not in INVERSE and chg < -0.30:
            penalty=min(30.0, abs(chg)*3.5)
            r['score']=max(0, round(float(r.get('score') or 0)-penalty))
            r['parts']['Long-only down penalty']=-round(penalty,1)

        # Mild leadership reward: positive movers with actual RVOL get preference over flat names.
        if chg > 0.50 and float(r.get('rvol') or 0) >= 1.0:
            leadership=min(10.0, chg*0.7 + min(float(r.get('rvol') or 0),4)*1.2)
            r['score']=min(100, round(float(r.get('score') or 0)+leadership))
            r['parts']['Live leadership']=round(leadership,1)

    rows=[r for r in out if r['eligible']]
    rows.sort(key=lambda r:(r['score'],r['dollar_volume']),reverse=True)

    # In BEAR regimes reserve visibility for the best qualified inverse ETF.
    if regime in ('BEAR','STRONG_BEAR'):
        inverse=sorted(
            [r for r in rows if r['symbol'] in INVERSE],
            key=lambda r:(r['score'],r['dollar_volume']),reverse=True
        )
        selected=rows[:top_n]
        if inverse and inverse[0]['symbol'] not in {r['symbol'] for r in selected}:
            selected=(selected[:max(0,top_n-1)]+[inverse[0]])
            selected.sort(key=lambda r:(r['score'],r['dollar_volume']),reverse=True)
        return selected[:top_n]

    return rows[:top_n]

def context_for(symbol:str, quotes:list[dict]):
    qmap={q['symbol']:q for q in quotes}; qqq=float((qmap.get('QQQ') or {}).get('change_pct') or 0); smh=float((qmap.get('SMH') or {}).get('change_pct') or 0)
    sym=symbol.upper(); sector=smh if sym in SEMI else qqq
    market=max(-1,min(1,qqq/4)); sector_n=max(-1,min(1,sector/4))
    if sym in INVERSE:
        market=-market; sector_n=-sector_n
    return market,sector_n,{'qqq_pct':qqq,'smh_pct':smh,'market_bias':market,'sector_bias':sector_n}

def multi_timeframe_signal(symbol: str, ticks: list[dict], quotes:list[dict]):
    b1=ticks_to_bars(ticks,1); b5=ticks_to_bars(ticks,5); market_bias,sector_bias,ctx=context_for(symbol,quotes)
    if len(b1)<20:
        return {'symbol':symbol,'state':'DATA WARMUP','score':0,'bias':'NEUTRAL','reason':f'지표 준비 중 · 1분봉 {len(b1)}/20',
                'risks':'','bars1':len(b1),'bars5':len(b5),'warmup_required':20,'warmup_progress':min(100,round(len(b1)/20*100)),'context':ctx}
    s1=intraday_signal(symbol,b1,market_bias=market_bias,sector_bias=sector_bias,cfg=CFG)
    conf=0; conf_reason=[]
    if len(b5)>=3:
        e5=enrich_intraday(b5); r=e5.iloc[-1]; prev=e5.iloc[-2]
        if s1.bias=='LONG':
            if r.close>r.vwap: conf+=8; conf_reason.append('5M>VWAP')
            if r.ema9>r.ema20: conf+=8; conf_reason.append('5M EMA9>20')
            if r.close>=prev.close: conf+=5; conf_reason.append('5M 상승')
            if r.rsi14>=50: conf+=4
        elif s1.bias=='SHORT':
            if r.close<r.vwap: conf+=8; conf_reason.append('5M<VWAP')
            if r.ema9<r.ema20: conf+=8; conf_reason.append('5M EMA9<20')
            if r.close<=prev.close: conf+=5; conf_reason.append('5M 하락')
            if r.rsi14<=50: conf+=4
    final=min(100,round(s1.score*.78+conf))
    state='WAIT'
    if s1.bias!='NEUTRAL' and final>=CFG.trigger_score and conf>=13: state='TRIGGER'
    elif s1.bias!='NEUTRAL' and final>=CFG.setup_score: state='SETUP'
    elif s1.bias!='NEUTRAL' and final>=CFG.watch_score: state='WATCH'
    reason=' · '.join([x for x in [s1.reason,' · '.join(conf_reason)] if x])
    d=s1.to_dict(); d.update({'state':state,'score':final,'score_1m':s1.score,'confirm_5m':conf,'reason':reason,
                              'bars1':len(b1),'bars5':len(b5),'context':ctx})
    e1=enrich_intraday(b1); rr=e1.iloc[-1]
    d['indicators']={'vwap':float(rr.vwap) if pd.notna(rr.vwap) else None,'ema9':float(rr.ema9),'ema20':float(rr.ema20),
                     'ema50':float(rr.ema50),'rsi14':float(rr.rsi14),'rvol':float(rr.rvol) if pd.notna(rr.rvol) else 0,
                     'prev_high20':float(rr.prev_high20) if pd.notna(rr.prev_high20) else None,
                     'prev_low20':float(rr.prev_low20) if pd.notna(rr.prev_low20) else None}
    return d

def position_from_ticks(symbol:str,ticks:list[dict],entry:float,side:str,quotes:list[dict]):
    b=ticks_to_bars(ticks,1)
    if len(b)<5: return {'symbol':symbol,'state':'DATA WARMUP','reason':'포지션 분석 데이터 수집 중'}
    mb,sb,ctx=context_for(symbol,quotes); s=position_signal(symbol,b,entry,side,mb,sb,CFG)
    pnl=(s.price/entry-1)*100 if side.upper()=='LONG' else (entry/s.price-1)*100
    return {'symbol':symbol,'state':s.state,'side':side.upper(),'price':s.price,'entry':entry,'pnl_pct':pnl,'reason':s.reason,
            'invalidation':s.invalidation,'critical':s.critical,'risks':s.risks,'signal_score':s.score,'context':ctx}


# V1.9 experimental live Shadow Model.
# This is intentionally NOT the production score. It applies candidate-style
# weights to the same live features already used by CURRENT so both models can
# be compared on identical real-time data without changing trading operations.
SHADOW_WEIGHTS = {
    'Price>MA5': 1.15,
    'MA5 slope': 1.15,
    'Liquidity': 0.65,
    'RVOL': 0.95,
    'ATR': 0.95,
    'Momentum': 0.65,
    'Market/Sector': 0.90,
    'Price action': 1.15,
    'Liquid leveraged ETF': 1.15,
    'Core liquidity': 0.75,
}

def _shadow_score(parts:dict) -> float:
    total=0.0
    for name, value in (parts or {}).items():
        total += float(value) * SHADOW_WEIGHTS.get(name,1.0)
    return max(0,min(100,round(total,2)))

def shadow_screener_rows(quotes:list[dict], metrics:list[dict], top_n:int=10) -> list[dict]:
    mm={m['symbol']:m for m in metrics}; qmap={q['symbol']:q for q in quotes}
    index_strength=float((qmap.get('QQQ') or {}).get('change_pct') or 0)
    semi_strength=float((qmap.get('SMH') or {}).get('change_pct') or 0)
    progress=max(0.08,market_minutes_elapsed()/390)
    out=[]
    for q in quotes:
        r=_score_row(q,mm.get(q['symbol'],{}),index_strength,semi_strength,progress)
        if not r.get('eligible'):
            continue
        x=dict(r)
        x['current_score']=x.get('score')
        x['shadow_score']=_shadow_score(x.get('parts') or {})
        x['score']=x['shadow_score']
        x['model']='SHADOW'
        out.append(x)
    out.sort(key=lambda r:(r['score'],r['dollar_volume']),reverse=True)
    return out[:top_n]

def compare_current_shadow(quotes:list[dict], metrics:list[dict], top_n:int=10) -> dict:
    current=screener_rows(quotes,metrics,top_n)
    shadow=shadow_screener_rows(quotes,metrics,top_n)
    cr={r['symbol']:i for i,r in enumerate(current,1)}
    sr={r['symbol']:i for i,r in enumerate(shadow,1)}
    cm={r['symbol']:r for r in current}
    sm={r['symbol']:r for r in shadow}
    syms=[]
    for r in current+shadow:
        if r['symbol'] not in syms:
            syms.append(r['symbol'])
    rows=[]
    for sym in syms:
        c=cm.get(sym) or {}
        s=sm.get(sym) or {}
        rows.append({
            'symbol':sym,
            'current_rank':cr.get(sym),
            'shadow_rank':sr.get(sym),
            'rank_delta':(cr[sym]-sr[sym]) if sym in cr and sym in sr else None,
            'current_score':c.get('score'),
            'shadow_score':s.get('score'),
            'bias':(s or c).get('bias'),
            'price':(s or c).get('price'),
            'change_pct':(s or c).get('change_pct'),
            'in_current':sym in cr,
            'in_shadow':sym in sr,
        })
    overlap=sorted(set(cr)&set(sr),key=lambda x:sr[x])
    return {
        'model':'LIVE_CANDIDATE_V1',
        'experimental':True,
        'top_n':top_n,
        'overlap_count':len(overlap),
        'overlap_symbols':overlap,
        'current_only':[x for x in cr if x not in sr],
        'shadow_only':[x for x in sr if x not in cr],
        'rows':rows,
        'current':current,
        'shadow':shadow,
    }
