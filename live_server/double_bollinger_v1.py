from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from live_server.strategy_core_v1 import Action, PositionPhase, PositionState, SignalResult, enforce_long_stop


@dataclass(frozen=True)
class DoubleBollingerConfig:
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bb_period: int = 20
    inner_sigma: float = 0.5
    outer_sigma: float = 3.0
    rsi_levels: Tuple[float, ...] = (30.0, 50.0, 70.0)
    rsi_event_lookback_5m: int = 3
    inner_break_lookback_5m: int = 2
    volume_period: int = 20
    strong_volume_ratio: float = 1.5
    fallback_risk_pct: float = 0.01
    max_swing_risk_pct: float = 0.025
    swing_left: int = 2
    swing_right: int = 2
    profit_check_pct: Optional[float] = None
    normal_partial_fraction: float = 0.5


class DoubleBollingerV1:
    ALLOWED_SYMBOLS = {"SOXL", "SOXS"}

    def __init__(self, config: DoubleBollingerConfig = DoubleBollingerConfig()):
        self.cfg = config

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> pd.Series:
        d = pd.to_numeric(close, errors="coerce").astype(float).diff()
        gain = d.clip(lower=0)
        loss = -d.clip(upper=0)
        ag = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        al = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        rs = ag / al.mask(al == 0.0, np.nan)
        return (100.0 - 100.0 / (1.0 + rs)).astype(float)

    def _macd(self, close: pd.Series):
        c = pd.to_numeric(close, errors="coerce").astype(float)
        macd = c.ewm(span=self.cfg.macd_fast, adjust=False).mean() - c.ewm(span=self.cfg.macd_slow, adjust=False).mean()
        sig = macd.ewm(span=self.cfg.macd_signal, adjust=False).mean()
        return macd, sig, macd - sig

    def _bands(self, close: pd.Series):
        c = pd.to_numeric(close, errors="coerce").astype(float)
        mid = c.rolling(self.cfg.bb_period).mean()
        std = c.rolling(self.cfg.bb_period).std(ddof=0)
        return {"mid": mid, "inner_upper": mid + self.cfg.inner_sigma * std, "inner_lower": mid - self.cfg.inner_sigma * std, "outer_upper": mid + self.cfg.outer_sigma * std, "outer_lower": mid - self.cfg.outer_sigma * std}

    def _resample_5m_completed(self, bars_1m: pd.DataFrame) -> pd.DataFrame:
        empty = pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
        if bars_1m.empty:
            return empty
        x = bars_1m.copy()
        if "time" not in x.columns:
            raise ValueError("bars_1m requires time")
        dt = pd.to_datetime(x["time"], errors="coerce", utc=True)
        if dt.isna().all():
            dt = pd.to_datetime(x["time"].astype(str).str[:14], format="%Y%m%d%H%M%S", errors="coerce", utc=True)
        x = x.assign(_dt=dt).dropna(subset=["_dt"]).sort_values("_dt")
        if x.empty:
            return empty
        current_bucket = x["_dt"].iloc[-1].floor("5min")
        complete = x[x["_dt"] < current_bucket].set_index("_dt")
        if complete.empty:
            return empty
        agg = complete.resample("5min").agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"}).dropna(subset=["open","high","low","close"])
        return agg.reset_index().rename(columns={"_dt":"time"})

    def _confirmed_swing_low(self, bars: pd.DataFrame) -> Optional[float]:
        if len(bars) < self.cfg.swing_left + self.cfg.swing_right + 1:
            return None
        low = pd.to_numeric(bars["low"], errors="coerce").astype(float).reset_index(drop=True)
        found = None
        for i in range(self.cfg.swing_left, len(low) - self.cfg.swing_right):
            v = low.iloc[i]
            if pd.isna(v):
                continue
            if v < low.iloc[i-self.cfg.swing_left:i].min() and v <= low.iloc[i+1:i+1+self.cfg.swing_right].min():
                found = float(v)
        return found

    def _entry_stop(self, entry: float, bars_1m: pd.DataFrame) -> float:
        swing = self._confirmed_swing_low(bars_1m)
        if swing is not None:
            risk_pct = (entry - swing) / entry
            if 0 < risk_pct <= self.cfg.max_swing_risk_pct:
                return enforce_long_stop(entry, swing, self.cfg.fallback_risk_pct)
        return enforce_long_stop(entry, None, self.cfg.fallback_risk_pct)

    def _five_min_setup(self, b5: pd.DataFrame):
        required = max(self.cfg.bb_period + 2, self.cfg.macd_slow + 2)
        if b5.empty or "close" not in b5.columns or "volume" not in b5.columns or len(b5) < required:
            return False, 0.0, {"reason":"INSUFFICIENT_5M", "bars5": int(len(b5))}
        c = pd.to_numeric(b5["close"], errors="coerce").astype(float)
        v = pd.to_numeric(b5["volume"], errors="coerce").fillna(0.0).astype(float)
        rsi = self._rsi(c, self.cfg.rsi_period)
        macd, sig, hist = self._macd(c)
        bb = self._bands(c)
        rsi_events=[]
        start=max(1,len(c)-self.cfg.rsi_event_lookback_5m)
        for i in range(start,len(c)):
            for level in self.cfg.rsi_levels:
                if pd.notna(rsi.iloc[i-1]) and pd.notna(rsi.iloc[i]) and rsi.iloc[i-1] <= level < rsi.iloc[i]:
                    rsi_events.append((i,level,float(rsi.iloc[i]-rsi.iloc[i-1])))
        rsi_event=rsi_events[-1] if rsi_events else None
        macd_cross_now=bool(macd.iloc[-2] <= sig.iloc[-2] and macd.iloc[-1] > sig.iloc[-1])
        macd_bull=bool(macd.iloc[-1] > sig.iloc[-1] and hist.iloc[-1] >= hist.iloc[-2])
        break_found=False
        for i in range(max(1,len(c)-self.cfg.inner_break_lookback_5m),len(c)):
            iu0,iu1=bb["inner_upper"].iloc[i-1],bb["inner_upper"].iloc[i]
            if pd.notna(iu0) and pd.notna(iu1) and c.iloc[i-1] <= iu0 and c.iloc[i] > iu1:
                break_found=True
        above_inner=bool(pd.notna(bb["inner_upper"].iloc[-1]) and c.iloc[-1] > bb["inner_upper"].iloc[-1])
        inner_confirm=bool(break_found or above_inner)
        rsi_slope=float(rsi.iloc[-1]-rsi.iloc[-2]) if pd.notna(rsi.iloc[-1]) and pd.notna(rsi.iloc[-2]) else 0.0
        hist_accel=float(hist.iloc[-1]-hist.iloc[-2]) if pd.notna(hist.iloc[-1]) and pd.notna(hist.iloc[-2]) else 0.0
        avg_vol=float(v.iloc[-self.cfg.volume_period-1:-1].mean()) if len(v)>self.cfg.volume_period else 0.0
        volume_ratio=float(v.iloc[-1]/avg_vol) if avg_vol>0 else 0.0
        score=float(bool(rsi_event))+float(rsi_slope>0)+float(macd_cross_now or macd_bull)+float(hist_accel>0)+float(inner_confirm)+float(volume_ratio>=self.cfg.strong_volume_ratio)
        setup=bool(rsi_event and (macd_cross_now or macd_bull) and inner_confirm)
        return setup,score,{"rsi":float(rsi.iloc[-1]),"rsi_prev":float(rsi.iloc[-2]),"rsi_event":rsi_event,"rsi_slope":rsi_slope,"macd":float(macd.iloc[-1]),"signal":float(sig.iloc[-1]),"hist":float(hist.iloc[-1]),"hist_accel":hist_accel,"inner_upper":float(bb["inner_upper"].iloc[-1]),"outer_upper":float(bb["outer_upper"].iloc[-1]),"volume_ratio":volume_ratio,"break_found":break_found,"above_inner":above_inner}

    def _one_min_timing(self,bars_1m:pd.DataFrame):
        c=pd.to_numeric(bars_1m["close"],errors="coerce").astype(float)
        if len(c)<self.cfg.macd_slow+3:
            return False,{"reason":"INSUFFICIENT_1M"}
        rsi=self._rsi(c,self.cfg.rsi_period); macd,sig,hist=self._macd(c)
        rsi_up=bool(rsi.iloc[-1]>=rsi.iloc[-2]) if pd.notna(rsi.iloc[-1]) and pd.notna(rsi.iloc[-2]) else False
        hist_up=bool(hist.iloc[-1]>=hist.iloc[-2]); macd_ok=bool(macd.iloc[-1]>sig.iloc[-1] or (macd.iloc[-2]<=sig.iloc[-2] and macd.iloc[-1]>sig.iloc[-1]))
        rsi70_exit=bool(pd.notna(rsi.iloc[-2]) and pd.notna(rsi.iloc[-1]) and rsi.iloc[-2]>=70>rsi.iloc[-1])
        return bool((rsi_up or hist_up) and macd_ok and not rsi70_exit),{"rsi1":float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else None,"rsi1_up":rsi_up,"macd1":float(macd.iloc[-1]),"signal1":float(sig.iloc[-1]),"hist1":float(hist.iloc[-1]),"hist1_up":hist_up,"rsi70_exit":rsi70_exit}

    def evaluate_flat(self,symbol:str,bars_1m:pd.DataFrame)->SignalResult:
        symbol=symbol.upper()
        if symbol not in self.ALLOWED_SYMBOLS: raise ValueError(f"unsupported symbol: {symbol}")
        if bars_1m.empty: return SignalResult(symbol,Action.HOLD,"NO_BARS",0.0)
        price=float(pd.to_numeric(bars_1m["close"],errors="coerce").iloc[-1])
        b5=self._resample_5m_completed(bars_1m); setup,score,d5=self._five_min_setup(b5)
        if not setup: return SignalResult(symbol,Action.HOLD,"WATCH_5M_SETUP",price,score=score,diagnostics={"five":d5})
        timing,d1=self._one_min_timing(bars_1m)
        if not timing: return SignalResult(symbol,Action.HOLD,"WAIT_1M_TIMING",price,score=score,diagnostics={"five":d5,"one":d1})
        stop=self._entry_stop(price,bars_1m.iloc[:-1])
        if not (0<stop<price): return SignalResult(symbol,Action.HOLD,"INVALID_STOP_BLOCK",price,score=score,diagnostics={"five":d5,"one":d1})
        return SignalResult(symbol,Action.ENTER,"RSI_MACD_INNER_BAND_CONFIRM",price,score=score,stop=stop,diagnostics={"five":d5,"one":d1,"risk_pct":(price-stop)/price})

    def evaluate_open(self,state:PositionState,bars_1m:pd.DataFrame)->SignalResult:
        if state.entry_price is None or state.stop_price is None or state.phase not in {PositionPhase.OPEN,PositionPhase.RUNNER}: raise ValueError("open evaluation requires valid state")
        price=float(pd.to_numeric(bars_1m["close"],errors="coerce").iloc[-1]); state.update_high(price)
        if price<=state.stop_price: return SignalResult(state.symbol,Action.FULL_EXIT,"INITIAL_STOP",price,stop=state.stop_price,exit_fraction=1.0)
        b5=self._resample_5m_completed(bars_1m)
        if b5.empty or "close" not in b5.columns or len(b5)<max(self.cfg.bb_period+2,self.cfg.macd_slow+2):
            return SignalResult(state.symbol,Action.HOLD,"HOLD_INSUFFICIENT_5M",price,stop=state.stop_price)
        c=pd.to_numeric(b5["close"],errors="coerce").astype(float); rsi=self._rsi(c,self.cfg.rsi_period); macd,sig,hist=self._macd(c); bb=self._bands(c)
        close5=float(c.iloc[-1]); inner=float(bb["inner_upper"].iloc[-1]); outer=float(bb["outer_upper"].iloc[-1]); outer_expansion=close5>=outer; inner_hold=close5>=inner; hist_up=bool(hist.iloc[-1]>=hist.iloc[-2]); rsi_up=bool(pd.notna(rsi.iloc[-1]) and pd.notna(rsi.iloc[-2]) and rsi.iloc[-1]>=rsi.iloc[-2]); macd_bull=bool(macd.iloc[-1]>=sig.iloc[-1]); strength_points=int(inner_hold)+int(hist_up)+int(macd_bull); trend="STRONG" if outer_expansion or strength_points>=3 else ("NORMAL" if strength_points>=2 else "WEAK")
        diag={"trend":trend,"close5":close5,"inner_upper":inner,"outer_upper":outer,"outer_expansion":outer_expansion,"rsi5":float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else None,"rsi_up":rsi_up,"hist5":float(hist.iloc[-1]),"hist_up":hist_up,"macd_bull":macd_bull}
        if not inner_hold and (not macd_bull or not hist_up): return SignalResult(state.symbol,Action.FULL_EXIT,"INNER_BAND_TREND_BREAK",price,stop=state.stop_price,exit_fraction=1.0,diagnostics=diag)
        return SignalResult(state.symbol,Action.HOLD,"HOLD_TREND",price,stop=state.stop_price,diagnostics=diag)
