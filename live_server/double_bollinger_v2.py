from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

from live_server.strategy_core_v1 import Action, PositionPhase, PositionState, SignalResult, enforce_long_stop


@dataclass(frozen=True)
class DoubleBollingerV2Config:
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bb_period: int = 20
    inner_sigma: float = 0.5
    outer_sigma: float = 3.0
    volume_period: int = 20

    # Early-entry logic: direction/slope matters more than waiting for fixed crossings.
    rsi_floor: float = 38.0
    rsi_slope1_full: float = 3.0
    rsi_slope3_full: float = 7.0
    macd_gap_convergence_full: float = 0.025
    inner_distance_full_pct: float = 0.0035
    strong_volume_ratio: float = 1.5
    bb_width_expand_full: float = 0.03

    # 0..100 entry score.
    early_entry_score: float = 65.0
    confirm_entry_score: float = 80.0
    open_bonus_score: float = 5.0
    open_bonus_minutes: int = 60
    minimum_ready_bars: int = 40

    # Risk / exits.
    fallback_risk_pct: float = 0.012
    partial_fraction: float = 0.5
    runner_exit_mode: str = "adaptive"  # adaptive|inner_upper|mid|inner_lower|momentum
    runner_trail_pct: float = 0.010


class DoubleBollingerV2:
    """Slope-first double-Bollinger engine for SOXL/SOXS.

    Design goals:
      * Do not require RSI 50 or MACD golden-cross confirmation before an entry can exist.
      * Reward steep RSI acceleration and fast MACD-gap convergence.
      * Reward price pressure toward/through the inner upper band.
      * Reward expanding volatility and volume; suppress flat compression.
      * Take 50% at the first outer-upper touch, then manage a runner.
      * Apply exactly the same long logic independently to SOXL and SOXS.

    The class only emits SignalResult decisions; broker authority remains elsewhere.
    """

    ALLOWED_SYMBOLS = {"SOXL", "SOXS"}

    def __init__(self, config: DoubleBollingerV2Config = DoubleBollingerV2Config()):
        self.cfg = config

    @staticmethod
    def _f(v, default: float = 0.0) -> float:
        try:
            x = float(v)
            return x if np.isfinite(x) else default
        except Exception:
            return default

    @staticmethod
    def _clip(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
        return max(lo, min(hi, float(v)))

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> pd.Series:
        c = pd.to_numeric(close, errors="coerce").astype(float)
        d = c.diff()
        gain = d.clip(lower=0.0)
        loss = -d.clip(upper=0.0)
        ag = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
        al = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
        rs = ag / al.mask(al == 0.0, np.nan)
        return (100.0 - 100.0 / (1.0 + rs)).astype(float)

    def _macd(self, close: pd.Series):
        c = pd.to_numeric(close, errors="coerce").astype(float)
        m = c.ewm(span=self.cfg.macd_fast, adjust=False).mean() - c.ewm(span=self.cfg.macd_slow, adjust=False).mean()
        s = m.ewm(span=self.cfg.macd_signal, adjust=False).mean()
        return m, s, m - s

    def _bands(self, close: pd.Series):
        c = pd.to_numeric(close, errors="coerce").astype(float)
        mid = c.rolling(self.cfg.bb_period).mean()
        std = c.rolling(self.cfg.bb_period).std(ddof=0)
        return {
            "mid": mid,
            "inner_upper": mid + self.cfg.inner_sigma * std,
            "inner_lower": mid - self.cfg.inner_sigma * std,
            "outer_upper": mid + self.cfg.outer_sigma * std,
            "outer_lower": mid - self.cfg.outer_sigma * std,
            "width": (2.0 * self.cfg.outer_sigma * std) / mid.replace(0.0, np.nan),
        }

    def _prepare(self, bars_1m: pd.DataFrame):
        if bars_1m is None or bars_1m.empty:
            return None
        need = {"time", "open", "high", "low", "close", "volume"}
        if not need.issubset(set(bars_1m.columns)):
            return None
        x = bars_1m.copy().reset_index(drop=True)
        if len(x) < self.cfg.minimum_ready_bars:
            return None
        c = pd.to_numeric(x["close"], errors="coerce").astype(float)
        h = pd.to_numeric(x["high"], errors="coerce").astype(float)
        l = pd.to_numeric(x["low"], errors="coerce").astype(float)
        v = pd.to_numeric(x["volume"], errors="coerce").fillna(0.0).astype(float)
        rsi = self._rsi(c, self.cfg.rsi_period)
        macd, sig, hist = self._macd(c)
        bb = self._bands(c)
        if any(pd.isna(s.iloc[-1]) for s in (rsi, macd, sig, bb["mid"], bb["inner_upper"], bb["outer_upper"], bb["width"])):
            return None
        return x, c, h, l, v, rsi, macd, sig, hist, bb

    def _open_session_bonus(self, ts) -> Tuple[float, Optional[int]]:
        try:
            t = pd.Timestamp(ts)
            if t.tzinfo is None:
                t = t.tz_localize("UTC")
            et = t.tz_convert("America/New_York")
            minute = et.hour * 60 + et.minute
            since_open = minute - (9 * 60 + 30)
            if 0 <= since_open < self.cfg.open_bonus_minutes:
                return self.cfg.open_bonus_score, int(since_open)
            return 0.0, int(since_open)
        except Exception:
            return 0.0, None

    def entry_diagnostics(self, symbol: str, bars_1m: pd.DataFrame) -> Dict[str, object]:
        symbol = str(symbol).upper()
        if symbol not in self.ALLOWED_SYMBOLS:
            raise ValueError(f"unsupported symbol: {symbol}")
        p = self._prepare(bars_1m)
        if p is None:
            return {"symbol": symbol, "ready": False, "reason": "INSUFFICIENT_BARS"}
        x, c, h, l, v, rsi, macd, sig, hist, bb = p
        i = len(x) - 1
        price = self._f(c.iloc[i])

        rsi_now = self._f(rsi.iloc[i])
        rsi_prev = self._f(rsi.iloc[i - 1])
        rsi_3 = self._f(rsi.iloc[max(0, i - 3)])
        rsi_slope1 = rsi_now - rsi_prev
        rsi_slope3 = rsi_now - rsi_3
        rsi_accel = rsi_slope1 - (rsi_prev - self._f(rsi.iloc[i - 2]))
        rsi_cross50 = bool(self._f(rsi.iloc[i - 1]) <= 50.0 < rsi_now)

        gap = self._f(macd.iloc[i] - sig.iloc[i])
        gap_prev = self._f(macd.iloc[i - 1] - sig.iloc[i - 1])
        gap_prev2 = self._f(macd.iloc[i - 2] - sig.iloc[i - 2])
        gap_delta = gap - gap_prev
        gap_accel = gap_delta - (gap_prev - gap_prev2)
        golden_cross = bool(gap_prev <= 0.0 < gap)
        macd_bull = bool(gap > 0.0)
        macd_approaching = bool(gap <= 0.0 and gap_delta > 0.0)

        inner_u = self._f(bb["inner_upper"].iloc[i])
        outer_u = self._f(bb["outer_upper"].iloc[i])
        mid = self._f(bb["mid"].iloc[i])
        inner_distance_pct = (price - inner_u) / price if price > 0 else -1.0
        inner_cross = bool(self._f(c.iloc[i - 1]) <= self._f(bb["inner_upper"].iloc[i - 1]) and price > inner_u)
        above_inner = bool(price >= inner_u)
        price_slope3 = (price - self._f(c.iloc[max(0, i - 3)])) / max(price, 1e-9)

        width = self._f(bb["width"].iloc[i])
        width_prev = self._f(bb["width"].iloc[i - 1])
        width_prev3 = self._f(bb["width"].iloc[max(0, i - 3)])
        width_slope1 = width - width_prev
        width_slope3 = width - width_prev3
        width_expanding = bool(width_slope1 > 0.0 and width_slope3 > 0.0)

        vol_avg = self._f(v.iloc[max(0, i - self.cfg.volume_period):i].mean()) if i > 0 else 0.0
        volume_ratio = self._f(v.iloc[i]) / vol_avg if vol_avg > 0 else 0.0

        # RSI 25 points: slope and acceleration matter; RSI 50 is only a bonus.
        rsi_score = 0.0
        if rsi_now >= self.cfg.rsi_floor:
            rsi_score += 5.0
        rsi_score += 10.0 * self._clip(rsi_slope1 / max(self.cfg.rsi_slope1_full, 1e-9))
        rsi_score += 7.0 * self._clip(rsi_slope3 / max(self.cfg.rsi_slope3_full, 1e-9))
        if rsi_accel > 0.0:
            rsi_score += 3.0
        if rsi_cross50:
            rsi_score += 2.0
        rsi_score = min(25.0, rsi_score)

        # MACD 25 points: allow pre-cross entries when the negative gap is closing fast.
        macd_score = 0.0
        if macd_bull:
            macd_score += 12.0
        elif macd_approaching:
            macd_score += 7.0
        macd_score += 10.0 * self._clip(gap_delta / max(self.cfg.macd_gap_convergence_full, 1e-9))
        if gap_accel > 0.0:
            macd_score += 3.0
        if golden_cross:
            macd_score += 3.0
        macd_score = min(25.0, macd_score)

        # Price/band 20 points.
        band_score = 0.0
        if above_inner:
            band_score += 12.0
        else:
            distance_to_inner = (inner_u - price) / max(price, 1e-9)
            band_score += 8.0 * (1.0 - self._clip(distance_to_inner / max(self.cfg.inner_distance_full_pct, 1e-9)))
        if inner_cross:
            band_score += 4.0
        if price_slope3 > 0.0:
            band_score += 4.0
        band_score = min(20.0, band_score)

        # Volume 15 points.
        volume_score = 15.0 * self._clip(volume_ratio / max(self.cfg.strong_volume_ratio, 1e-9))

        # Volatility regime 15 points. Compression is not rewarded; fresh expansion is.
        vol_score = 0.0
        if width_expanding:
            rel_expand = width_slope3 / max(abs(width_prev3), 1e-9)
            vol_score = 15.0 * self._clip(rel_expand / max(self.cfg.bb_width_expand_full, 1e-9))

        open_bonus, minutes_from_open = self._open_session_bonus(x.iloc[i]["time"])
        raw_score = rsi_score + macd_score + band_score + volume_score + vol_score + open_bonus
        score = min(100.0, raw_score)

        # Anti-chase: do not enter when momentum is rolling over even if price remains high.
        momentum_rising = bool(rsi_slope1 > 0.0 and gap_delta > 0.0)
        not_chasing = bool(price_slope3 <= 0.015 or inner_cross or not above_inner)
        early = bool(score >= self.cfg.early_entry_score and momentum_rising and not_chasing)
        confirm = bool(score >= self.cfg.confirm_entry_score and (above_inner or inner_cross) and (macd_bull or golden_cross) and rsi_now >= 45.0)
        stage = "CONFIRM_ENTRY" if confirm else ("EARLY_ENTRY" if early else ("PRE_ENTRY" if score >= 50.0 else "NO_TRADE"))

        return {
            "symbol": symbol,
            "ready": True,
            "time": str(x.iloc[i]["time"]),
            "price": price,
            "score": round(score, 3),
            "stage": stage,
            "early": early,
            "confirm": confirm,
            "rsi": rsi_now,
            "rsi_slope1": rsi_slope1,
            "rsi_slope3": rsi_slope3,
            "rsi_accel": rsi_accel,
            "rsi_cross50": rsi_cross50,
            "macd": self._f(macd.iloc[i]),
            "signal": self._f(sig.iloc[i]),
            "macd_gap": gap,
            "macd_gap_delta": gap_delta,
            "macd_gap_accel": gap_accel,
            "golden_cross": golden_cross,
            "macd_bull": macd_bull,
            "macd_approaching": macd_approaching,
            "mid": mid,
            "inner_upper": inner_u,
            "outer_upper": outer_u,
            "inner_cross": inner_cross,
            "above_inner": above_inner,
            "price_slope3": price_slope3,
            "bb_width": width,
            "bb_width_slope1": width_slope1,
            "bb_width_slope3": width_slope3,
            "bb_expanding": width_expanding,
            "volume_ratio": volume_ratio,
            "minutes_from_open": minutes_from_open,
            "component_scores": {
                "rsi": round(rsi_score, 3),
                "macd": round(macd_score, 3),
                "band": round(band_score, 3),
                "volume": round(volume_score, 3),
                "volatility": round(vol_score, 3),
                "open_bonus": round(open_bonus, 3),
            },
        }

    def evaluate_flat(self, symbol: str, bars_1m: pd.DataFrame) -> SignalResult:
        d = self.entry_diagnostics(symbol, bars_1m)
        if not d.get("ready"):
            return SignalResult(str(symbol).upper(), Action.HOLD, str(d.get("reason") or "NOT_READY"), 0.0, diagnostics=d)
        price = self._f(d.get("price"))
        score = self._f(d.get("score"))
        if not (d.get("early") or d.get("confirm")):
            return SignalResult(str(symbol).upper(), Action.HOLD, str(d.get("stage") or "NO_TRADE"), price, score=score, diagnostics=d)
        stop = enforce_long_stop(price, None, self.cfg.fallback_risk_pct)
        reason = "DBV2_CONFIRM_ENTRY" if d.get("confirm") else "DBV2_EARLY_ENTRY"
        return SignalResult(str(symbol).upper(), Action.ENTER, reason, price, score=score, stop=stop, diagnostics=d)

    def evaluate_open(self, state: PositionState, bars_1m: pd.DataFrame) -> SignalResult:
        if state.symbol.upper() not in self.ALLOWED_SYMBOLS:
            raise ValueError(f"unsupported symbol: {state.symbol}")
        if state.entry_price is None or state.stop_price is None or state.phase not in {PositionPhase.OPEN, PositionPhase.RUNNER}:
            raise ValueError("open evaluation requires a valid open PositionState")
        p = self._prepare(bars_1m)
        if p is None:
            return SignalResult(state.symbol, Action.HOLD, "HOLD_NOT_READY", float(state.entry_price), stop=state.stop_price)
        x, c, h, l, v, rsi, macd, sig, hist, bb = p
        i = len(x) - 1
        price = self._f(c.iloc[i])
        high = self._f(h.iloc[i])
        state.update_high(high)
        if price <= float(state.stop_price):
            return SignalResult(state.symbol, Action.FULL_EXIT, "INITIAL_STOP", price, stop=state.stop_price, exit_fraction=1.0)

        inner_u = self._f(bb["inner_upper"].iloc[i])
        inner_l = self._f(bb["inner_lower"].iloc[i])
        mid = self._f(bb["mid"].iloc[i])
        outer_u = self._f(bb["outer_upper"].iloc[i])
        rsi_slope = self._f(rsi.iloc[i] - rsi.iloc[i - 1])
        gap = self._f(macd.iloc[i] - sig.iloc[i])
        gap_delta = gap - self._f(macd.iloc[i - 1] - sig.iloc[i - 1])
        momentum_weak = bool(rsi_slope < 0.0 and gap_delta < 0.0)

        diag = {
            "price": price,
            "high": high,
            "inner_upper": inner_u,
            "mid": mid,
            "inner_lower": inner_l,
            "outer_upper": outer_u,
            "rsi": self._f(rsi.iloc[i]),
            "rsi_slope": rsi_slope,
            "macd_gap": gap,
            "macd_gap_delta": gap_delta,
            "momentum_weak": momentum_weak,
            "high_watermark": state.high_watermark,
        }

        # First touch of outer upper band: take half off.
        if not state.partial_exit_done and high >= outer_u:
            return SignalResult(state.symbol, Action.PARTIAL_EXIT, "FIRST_OUTER_UPPER_TOUCH", price, stop=state.stop_price, exit_fraction=self.cfg.partial_fraction, diagnostics=diag)

        if state.partial_exit_done or state.phase == PositionPhase.RUNNER:
            mode = self.cfg.runner_exit_mode.lower().strip()
            trail_hit = bool(state.high_watermark and price <= float(state.high_watermark) * (1.0 - self.cfg.runner_trail_pct))
            if mode == "inner_upper" and price < inner_u:
                return SignalResult(state.symbol, Action.FULL_EXIT, "RUNNER_INNER_UPPER_BREAK", price, stop=state.stop_price, exit_fraction=1.0, diagnostics=diag)
            if mode == "mid" and price <= mid:
                return SignalResult(state.symbol, Action.FULL_EXIT, "RUNNER_MID_TOUCH", price, stop=state.stop_price, exit_fraction=1.0, diagnostics=diag)
            if mode == "inner_lower" and price <= inner_l:
                return SignalResult(state.symbol, Action.FULL_EXIT, "RUNNER_INNER_LOWER_TOUCH", price, stop=state.stop_price, exit_fraction=1.0, diagnostics=diag)
            if mode == "momentum" and momentum_weak and price < inner_u:
                return SignalResult(state.symbol, Action.FULL_EXIT, "RUNNER_MOMENTUM_BREAK", price, stop=state.stop_price, exit_fraction=1.0, diagnostics=diag)
            if mode == "adaptive" and ((momentum_weak and price < inner_u) or price <= mid or trail_hit):
                reason = "RUNNER_TRAIL" if trail_hit else ("RUNNER_MID_TOUCH" if price <= mid else "RUNNER_ADAPTIVE_MOMENTUM_BREAK")
                return SignalResult(state.symbol, Action.FULL_EXIT, reason, price, stop=state.stop_price, exit_fraction=1.0, diagnostics=diag)

        return SignalResult(state.symbol, Action.HOLD, "HOLD_TREND", price, stop=state.stop_price, diagnostics=diag)


class DoubleBollingerPairV2:
    """SOXL/SOXS selector. Exactly one side may be selected while flat."""

    def __init__(self, engine: Optional[DoubleBollingerV2] = None):
        self.engine = engine or DoubleBollingerV2()

    def evaluate_flat_pair(self, bars_by_symbol: Dict[str, pd.DataFrame]) -> SignalResult:
        results = []
        for sym in ("SOXL", "SOXS"):
            bars = bars_by_symbol.get(sym)
            if bars is None:
                continue
            results.append(self.engine.evaluate_flat(sym, bars))
        enters = [r for r in results if r.action == Action.ENTER]
        if enters:
            enters.sort(key=lambda r: (float(r.score), r.symbol == "SOXS"), reverse=True)
            return enters[0]
        if results:
            results.sort(key=lambda r: float(r.score), reverse=True)
            best = results[0]
            return SignalResult(best.symbol, Action.HOLD, "PAIR_WAIT", best.price, score=best.score, diagnostics={"best": best.diagnostics, "all": {r.symbol: r.diagnostics for r in results}})
        return SignalResult("PAIR", Action.HOLD, "PAIR_NO_DATA", 0.0)
