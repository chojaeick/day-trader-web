from __future__ import annotations
from dataclasses import dataclass
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


def intraday_signal(symbol: str, bars: pd.DataFrame, market_bias: float = 0.0,
                    cfg: TradingConfig | None = None) -> Signal:
    cfg = cfg or TradingConfig()
    x = enrich_intraday(bars)
    r = x.iloc[-1]
    prev = x.iloc[-2] if len(x) > 1 else r
    score = 0; why=[]

    if r.close > r.vwap: score += 18; why.append('VWAP 위')
    if r.ema9 > r.ema20: score += 14; why.append('EMA9>EMA20')
    if r.ema20 > r.ema50: score += 10; why.append('EMA20>EMA50')
    if r.rvol >= 2: score += 18; why.append(f'RVOL {r.rvol:.1f}')
    elif r.rvol >= 1.3: score += 10
    if 55 <= r.rsi14 <= 75: score += 10; why.append(f'RSI {r.rsi14:.0f}')
    if pd.notna(r.prev_high20) and r.close > r.prev_high20: score += 18; why.append('20봉 고점 돌파')
    if r.close > prev.close: score += 5
    if market_bias > 0: score += min(7, round(market_bias * 7)); why.append('시장/섹터 동조')

    bias = 'LONG' if score >= 50 else 'NEUTRAL'
    state = 'WAIT'
    if score >= cfg.trigger_score: state='TRIGGER'
    elif score >= cfg.setup_score: state='SETUP'
    elif score >= cfg.watch_score: state='WATCH'

    # Technical invalidation based on VWAP / EMA20, capped by hard stop reference later.
    invalid = float(min(r.vwap, r.ema20)) if pd.notna(r.vwap) else float(r.close * 0.99)
    risk = max(float(r.close - invalid), float(r.close * 0.004))
    t1 = float(r.close + 1.5*risk)
    t2 = float(r.close + 2.5*risk)

    # Emergency move detector (approximate using latest 1-min bars)
    critical=False
    if len(x) >= 4:
        ret3 = (r.close / x.iloc[-4].close - 1) * 100
        if abs(ret3) >= 3 or (pd.notna(r.rvol) and r.rvol >= 5):
            critical=True
            why.append(f'3분 변동 {ret3:+.1f}%/급증거래량')

    return Signal(symbol, state, min(score,100), float(r.close), bias,
                  ', '.join(why) or '조건 대기', invalid, t1, t2, critical)


def position_signal(symbol: str, bars: pd.DataFrame, entry_price: float, cfg: TradingConfig | None=None) -> Signal:
    cfg = cfg or TradingConfig(); x=enrich_intraday(bars); r=x.iloc[-1]
    pnl=(r.close/entry_price-1)*100
    state='HOLD'; critical=False; reason=f'진입 대비 {pnl:+.2f}%'
    hard_stop=entry_price*(1+cfg.hard_stop_pct/100)
    technical_stop=max(hard_stop, min(float(r.vwap), float(r.ema20)))

    if r.close <= technical_stop:
        state='EXIT'; critical=True; reason += ', 기술적/하드 스톱 도달'
    elif pnl >= cfg.trim2_pct:
        state='TRIM_30'; critical=True; reason += ', 2차 분할익절 구간'
    elif pnl >= cfg.trim1_pct:
        state='TRIM_30'; critical=False; reason += ', 1차 분할익절 구간'
    elif pnl > 0 and r.close > r.ema9 and r.ema9 > r.ema20:
        state='HOLD_RUNNER'; reason += ', 추세 유지'
    elif pnl > 0.5 and r.close > r.vwap and r.rvol >= 1.5:
        state='ADD'; reason += ', 이익 상태에서 거래량 동반 지속'

    return Signal(symbol,state,0,float(r.close),'LONG',reason,technical_stop,critical=critical)
