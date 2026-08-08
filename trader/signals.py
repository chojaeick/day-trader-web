from __future__ import annotations
from dataclasses import dataclass, asdict
import math
import pandas as pd
from .config import TradingConfig
from .indicators import enrich_intraday

@dataclass
class Signal:
    symbol: str
    state: str
    score: int
    price: float
    bias: str
    reason: str
    invalidation: float | None = None
    target1: float | None = None
    target2: float | None = None
    critical: bool = False
    risks: str = ''
    long_score: int = 0
    short_score: int = 0
    components: dict | None = None

    def to_dict(self):
        return asdict(self)


def _finite(v, default=0.0):
    try:
        x=float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _score_intraday(r: pd.Series, prev: pd.Series, market_bias: float, sector_bias: float):
    long=0; short=0; lp={}; sp={}; good=[]; risks=[]
    price=_finite(r.close); vwap=_finite(r.vwap, price); e9=_finite(r.ema9, price); e20=_finite(r.ema20, price); e50=_finite(r.ema50, price)
    rsi=_finite(r.rsi14,50); rv=_finite(r.rvol,0); atr=_finite(r.atr14,0)
    atr_pct=(atr/price*100) if price else 0

    def L(name, pts, text=None):
        nonlocal long; long += pts; lp[name]=pts
        if text: good.append(text)
    def S(name, pts, text=None):
        nonlocal short; short += pts; sp[name]=pts
        if text: good.append(text)

    if price > vwap: L('VWAP',15,'가격>VWAP')
    elif price < vwap: S('VWAP',15,'가격<VWAP')
    if e9 > e20: L('EMA9/20',13,'EMA9>EMA20')
    elif e9 < e20: S('EMA9/20',13,'EMA9<EMA20')
    if e20 > e50: L('EMA20/50',8,'EMA20>EMA50')
    elif e20 < e50: S('EMA20/50',8,'EMA20<EMA50')

    if rv >= 3: L('RVOL',16,f'RVOL {rv:.1f}x'); S('RVOL',16)
    elif rv >= 2: L('RVOL',13,f'RVOL {rv:.1f}x'); S('RVOL',13)
    elif rv >= 1.5: L('RVOL',9,f'RVOL {rv:.1f}x'); S('RVOL',9)
    elif rv > 0 and rv < .7: risks.append(f'거래량 약함 {rv:.1f}x')

    if 55 <= rsi <= 72: L('RSI',10,f'RSI {rsi:.0f}')
    elif 45 <= rsi < 55: L('RSI',4); S('RSI',4)
    elif 28 <= rsi <= 45: S('RSI',10,f'RSI {rsi:.0f}')
    elif rsi > 80: long -= 8; risks.append(f'RSI 과열 {rsi:.0f}')
    elif rsi < 20: short -= 8; risks.append(f'RSI 과매도 {rsi:.0f}')

    ph=_finite(r.get('prev_high20'),0); pl=_finite(r.get('prev_low20'),0)
    if ph and price > ph: L('Breakout',15,'20봉 고점 돌파')
    elif ph and price >= ph*0.997: L('Near breakout',6,'직전 고점 근접')
    if pl and price < pl: S('Breakdown',15,'20봉 저점 이탈')
    elif pl and price <= pl*1.003: S('Near breakdown',6,'직전 저점 근접')

    ret3=_finite(r.get('ret3_pct'),0)
    if ret3 > .20: L('3m momentum',5,f'3분 +{ret3:.2f}%')
    elif ret3 < -.20: S('3m momentum',5,f'3분 {ret3:.2f}%')

    if market_bias > .15: L('Market',min(8,round(market_bias*8)),'시장 동조')
    elif market_bias < -.15: S('Market',min(8,round(abs(market_bias)*8)),'시장 하락 동조')
    if sector_bias > .15: L('Sector',min(7,round(sector_bias*7)),'섹터 동조')
    elif sector_bias < -.15: S('Sector',min(7,round(abs(sector_bias)*7)),'섹터 하락 동조')

    # Penalize chasing when extended far above/below VWAP relative to ATR.
    if atr > 0:
        ext=(price-vwap)/atr
        if ext > 1.6: long -= 8; risks.append(f'VWAP 대비 과도한 확장 {ext:.1f} ATR')
        if ext < -1.6: short -= 8; risks.append(f'VWAP 대비 과도한 하락 {abs(ext):.1f} ATR')
    if atr_pct < .15: risks.append('단기 변동성 부족')

    return max(0,min(100,round(long))), max(0,min(100,round(short))), lp, sp, good, risks


def intraday_signal(symbol: str, bars: pd.DataFrame, market_bias: float = 0.0, sector_bias: float = 0.0,
                    cfg: TradingConfig | None = None) -> Signal:
    cfg = cfg or TradingConfig()
    x = enrich_intraday(bars)
    r = x.iloc[-1]; prev=x.iloc[-2] if len(x)>1 else r
    long_score,short_score,lp,sp,good,risks=_score_intraday(r,prev,market_bias,sector_bias)
    if long_score >= short_score + 8:
        bias='LONG'; base=long_score
    elif short_score >= long_score + 8:
        bias='SHORT'; base=short_score
    else:
        bias='NEUTRAL'; base=max(long_score,short_score)

    state='WAIT'
    if base>=cfg.trigger_score: state='TRIGGER'
    elif base>=cfg.setup_score: state='SETUP'
    elif base>=cfg.watch_score: state='WATCH'

    price=_finite(r.close); vwap=_finite(r.vwap,price); e20=_finite(r.ema20,price); atr=_finite(r.atr14,price*.004)
    minrisk=max(price*.004,atr*.60)
    if bias=='SHORT':
        invalid=max(vwap,e20,price+minrisk)
        risk=max(invalid-price,minrisk)
        t1=price-1.5*risk; t2=price-2.5*risk
    else:
        invalid=min(vwap,e20,price-minrisk)
        risk=max(price-invalid,minrisk)
        t1=price+1.5*risk; t2=price+2.5*risk

    critical=False
    ret3=_finite(r.get('ret3_pct'),0); rv=_finite(r.rvol,0)
    if abs(ret3)>=3 or rv>=5:
        critical=True; risks.append(f'급변동 경보: 3분 {ret3:+.1f}% / RVOL {rv:.1f}x')

    chosen=lp if bias!='SHORT' else sp
    reason=' · '.join(good[:7]) if good else '조건 대기'
    return Signal(symbol,state,int(base),price,bias,reason,float(invalid),float(t1),float(t2),critical,
                  ' · '.join(risks),long_score,short_score,{'LONG':lp,'SHORT':sp,'selected':chosen})


def position_signal(symbol: str, bars: pd.DataFrame, entry_price: float, side: str='LONG',
                    market_bias: float=0.0, sector_bias: float=0.0, cfg: TradingConfig | None=None) -> Signal:
    cfg=cfg or TradingConfig(); side=side.upper(); x=enrich_intraday(bars); r=x.iloc[-1]
    current=_finite(r.close)
    pnl=((current/entry_price-1)*100) if side=='LONG' else ((entry_price/current-1)*100)
    hard_stop=entry_price*(1+cfg.hard_stop_pct/100) if side=='LONG' else entry_price/(1+cfg.hard_stop_pct/100)
    live=intraday_signal(symbol,bars,market_bias,sector_bias,cfg)
    state='HOLD'; critical=False; reason=f'{side} 진입 대비 {pnl:+.2f}%'
    vwap=_finite(r.vwap,current); e9=_finite(r.ema9,current); e20=_finite(r.ema20,current); rv=_finite(r.rvol,0)

    if side=='SHORT':
        technical_stop=min(hard_stop,max(vwap,e20))
        stop_hit=current>=technical_stop
        trend_ok=current<e9 and e9<e20
        signal_aligned=live.bias=='SHORT' and live.score>=cfg.watch_score
    else:
        technical_stop=max(hard_stop,min(vwap,e20))
        stop_hit=current<=technical_stop
        trend_ok=current>e9 and e9>e20
        signal_aligned=live.bias=='LONG' and live.score>=cfg.watch_score

    if stop_hit:
        state='EXIT'; critical=True; reason += ' · 기술적/하드 스톱 도달'
    elif pnl >= cfg.trim2_pct:
        if trend_ok:
            state='TRIM_30_RUNNER'; critical=True; reason += ' · 2차 30% 익절 + 나머지 Runner'
        else:
            state='TRIM_MORE'; critical=True; reason += ' · +3% 이상 & 추세 약화, 추가 익절 우선'
    elif pnl >= cfg.trim1_pct:
        state='TRIM_30'; reason += ' · 1차 30% 분할익절 구간'
    elif pnl > .5 and signal_aligned and trend_ok and rv>=1.3:
        state='ADD'; reason += ' · 이익 상태 + 신호 유지, 추가진입 후보'
    elif pnl > 0 and trend_ok:
        state='HOLD_RUNNER'; reason += ' · 추세 유지'
    elif pnl < 0 and not signal_aligned:
        state='HOLD_CAUTION'; reason += ' · 손실 중 추가매수 금지, 신호 약화'

    return Signal(symbol,state,live.score,current,side,reason,float(technical_stop),critical=critical,
                  risks=live.risks,long_score=live.long_score,short_score=live.short_score,components=live.components)
