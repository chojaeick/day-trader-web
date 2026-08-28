from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from live_server.double_bollinger_v2 import DoubleBollingerV2, DoubleBollingerV2Config

DB_PATH = Path(os.getenv("DBB_KR_DB", "/home/ubuntu/day-trader-api/daytrader.db"))
STATE_PATH = Path(os.getenv("DBB_KR_STATE", "/home/ubuntu/day-trader-api/dbb_kr_shadow_state.json"))
LOG_PATH = Path(os.getenv("DBB_KR_LOG", "/home/ubuntu/day-trader-api/dbb_kr_shadow_live.jsonl"))
POLL_SEC = int(os.getenv("DBB_KR_POLL_SEC", "30"))
CANDIDATE_LIMIT = int(os.getenv("DBB_KR_CANDIDATE_LIMIT", "20"))
TICK_LIMIT = int(os.getenv("DBB_KR_TICK_LIMIT", "30000"))
MIN_BARS = int(os.getenv("DBB_KR_MIN_BARS", "40"))
STALE_SEC = int(os.getenv("DBB_KR_STALE_SEC", "180"))
NO_ENTRY_MINUTE_KST = int(os.getenv("DBB_KR_NO_ENTRY_MINUTE", str(15 * 60)))
FORCE_FLAT_MINUTE_KST = int(os.getenv("DBB_KR_FORCE_FLAT_MINUTE", str(15 * 60 + 20)))

# Adaptive profit management being tested in Korea shadow mode.
PROFIT_ARM_PCT = float(os.getenv("DBB_KR_PROFIT_ARM_PCT", "0.004"))
PARTIAL_MIN_GAIN_PCT = float(os.getenv("DBB_KR_PARTIAL_MIN_GAIN_PCT", "0.005"))
PRE_PARTIAL_PULLBACK_PCT = float(os.getenv("DBB_KR_PRE_PARTIAL_PULLBACK_PCT", "0.0035"))
RUNNER_TRAIL_STRONG_PCT = float(os.getenv("DBB_KR_RUNNER_TRAIL_STRONG_PCT", "0.008"))
RUNNER_TRAIL_NORMAL_PCT = float(os.getenv("DBB_KR_RUNNER_TRAIL_NORMAL_PCT", "0.005"))
MOMENTUM_FAIL_BARS = int(os.getenv("DBB_KR_MOMENTUM_FAIL_BARS", "2"))

KST = ZoneInfo("Asia/Seoul")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def kst_now() -> datetime:
    return datetime.now(KST)


def emit(event: str, **kwargs) -> None:
    row = {"ts": utc_now().isoformat().replace("+00:00", "Z"), "event": event, **kwargs}
    line = json.dumps(row, ensure_ascii=False, default=str)
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_state() -> None:
    STATE_PATH.unlink(missing_ok=True)


def db_conn() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH), timeout=5)
    con.row_factory = sqlite3.Row
    return con


def candidate_symbols(con: sqlite3.Connection) -> list[str]:
    # Prefer symbols with the newest tick ids; Korean common-stock/ETF codes are six digits.
    rows = con.execute(
        """
        SELECT symbol, MAX(id) AS max_id, COUNT(*) AS n
        FROM ticks
        WHERE length(symbol)=6
          AND symbol GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        GROUP BY symbol
        ORDER BY max_id DESC
        LIMIT ?
        """,
        (CANDIDATE_LIMIT,),
    ).fetchall()
    return [str(r["symbol"]) for r in rows]


def load_bars(con: sqlite3.Connection, symbol: str) -> pd.DataFrame:
    rows = con.execute(
        """
        SELECT price, qty, cum_volume, ts
        FROM ticks
        WHERE symbol=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (symbol, TICK_LIMIT),
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    x = pd.DataFrame([dict(r) for r in reversed(rows)])
    x["time"] = pd.to_datetime(x["ts"], utc=True, errors="coerce")
    x["price"] = pd.to_numeric(x["price"], errors="coerce")
    x["qty"] = pd.to_numeric(x["qty"], errors="coerce").fillna(0.0).abs()
    x = x.dropna(subset=["time", "price"])
    x = x[x["price"] > 0]
    if x.empty:
        return pd.DataFrame()
    x = x.set_index("time").sort_index()
    bars = x.resample("1min").agg(
        open=("price", "first"),
        high=("price", "max"),
        low=("price", "min"),
        close=("price", "last"),
        volume=("qty", "sum"),
    ).dropna(subset=["open", "high", "low", "close"])
    bars = bars.reset_index()
    return bars.tail(500).reset_index(drop=True)


def latest_age_sec(bars: pd.DataFrame) -> float:
    if bars.empty:
        return 1e18
    t = pd.Timestamp(bars.iloc[-1]["time"])
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return max(0.0, (pd.Timestamp.now(tz="UTC") - t).total_seconds())


def diag_for(engine: DoubleBollingerV2, symbol: str, bars: pd.DataFrame) -> dict:
    engine.ALLOWED_SYMBOLS = {symbol}
    return engine.entry_diagnostics(symbol, bars)


def strongest_candidate(engine: DoubleBollingerV2, con: sqlite3.Connection) -> tuple[dict | None, pd.DataFrame | None]:
    ranked: list[tuple[float, dict, pd.DataFrame]] = []
    for sym in candidate_symbols(con):
        bars = load_bars(con, sym)
        if len(bars) < MIN_BARS or latest_age_sec(bars) > STALE_SEC:
            continue
        try:
            d = diag_for(engine, sym, bars)
        except Exception as e:
            emit("DIAG_ERROR", symbol=sym, error=repr(e))
            continue
        if not d.get("ready"):
            continue
        ranked.append((float(d.get("score") or 0.0), d, bars))
    ranked.sort(key=lambda z: z[0], reverse=True)
    if not ranked:
        return None, None
    top = ranked[:5]
    emit(
        "SCAN",
        top=[{
            "symbol": d.get("symbol"),
            "score": d.get("score"),
            "stage": d.get("stage"),
            "price": d.get("price"),
            "rsi_slope1": round(float(d.get("rsi_slope1") or 0.0), 4),
            "macd_gap_delta": round(float(d.get("macd_gap_delta") or 0.0), 6),
        } for _, d, _ in top],
    )
    return ranked[0][1], ranked[0][2]


def exit_full(state: dict, price: float, reason: str, **extra) -> dict:
    entry = float(state["entry_price"])
    remaining = float(state.get("remaining_fraction", 1.0))
    realized = float(state.get("realized_return_fraction", 0.0))
    realized += remaining * (price / entry - 1.0)
    emit(
        "SHADOW_FULL_EXIT",
        symbol=state["symbol"],
        price=price,
        reason=reason,
        trade_return_pct=round(realized * 100.0, 4),
        **extra,
    )
    clear_state()
    return {}


def manage_open(engine: DoubleBollingerV2, con: sqlite3.Connection, state: dict) -> dict:
    sym = str(state["symbol"])
    bars = load_bars(con, sym)
    if len(bars) < MIN_BARS:
        emit("OPEN_WAIT_DATA", symbol=sym, bars=len(bars))
        return state
    if latest_age_sec(bars) > STALE_SEC:
        emit("OPEN_STALE", symbol=sym, age_sec=round(latest_age_sec(bars), 1))
        return state

    d = diag_for(engine, sym, bars)
    last = bars.iloc[-1]
    price = float(last["close"])
    bar_high = float(last["high"])
    bar_low = float(last["low"])
    entry = float(state["entry_price"])
    stop = float(state["stop_price"])
    hwm = max(float(state.get("high_watermark") or entry), bar_high)
    state["high_watermark"] = hwm
    state["last_bar"] = str(last["time"])

    # Intrabar-low hard stop for the Korea shadow experiment.
    if bar_low <= stop:
        return exit_full(state, stop, "INITIAL_STOP_INTRABAR_LOW", bar_low=bar_low)

    rsi_slope = float(d.get("rsi_slope1") or 0.0)
    gap_delta = float(d.get("macd_gap_delta") or 0.0)
    inner_u = float(d.get("inner_upper") or price)
    mid = float(d.get("mid") or price)
    strong = bool(rsi_slope > 0.0 and gap_delta > 0.0 and price >= inner_u)
    weak = bool(rsi_slope < 0.0 and gap_delta < 0.0)

    fail_count = int(state.get("momentum_fail_count") or 0)
    state["momentum_fail_count"] = fail_count + 1 if weak else 0

    gain = price / entry - 1.0
    high_gain = hwm / entry - 1.0
    drawdown = 1.0 - price / hwm if hwm > 0 else 0.0

    now = kst_now()
    minute = now.hour * 60 + now.minute
    if minute >= FORCE_FLAT_MINUTE_KST:
        return exit_full(state, price, "SESSION_FORCE_FLAT", kst=str(now))

    if not bool(state.get("partial_exit_done")):
        # Core idea being tested: +0.5% is an ARM, not a forced take-profit.
        # If momentum stays strong, keep 100% and let the high-water mark rise.
        if high_gain >= PARTIAL_MIN_GAIN_PCT and not strong:
            frac = 0.5
            state["partial_exit_done"] = True
            state["remaining_fraction"] = 0.5
            state["realized_return_fraction"] = float(state.get("realized_return_fraction") or 0.0) + frac * gain
            emit(
                "SHADOW_PARTIAL_EXIT",
                symbol=sym,
                price=price,
                fraction=frac,
                reason="PROFIT_ARM_MOMENTUM_SOFTEN",
                gain_pct=round(gain * 100.0, 4),
                high_gain_pct=round(high_gain * 100.0, 4),
                strong=strong,
            )
            save_state(state)
            return state

        if high_gain >= PROFIT_ARM_PCT and drawdown >= PRE_PARTIAL_PULLBACK_PCT:
            return exit_full(
                state,
                price,
                "PRE_PARTIAL_HIGH_WATER_PULLBACK",
                gain_pct=round(gain * 100.0, 4),
                high_gain_pct=round(high_gain * 100.0, 4),
                drawdown_pct=round(drawdown * 100.0, 4),
            )

        if int(state.get("momentum_fail_count") or 0) >= MOMENTUM_FAIL_BARS:
            return exit_full(state, price, "PRE_PARTIAL_MOMENTUM_FAIL_2BARS")

        reason = "HOLD_STRONG_TREND" if high_gain >= PARTIAL_MIN_GAIN_PCT and strong else "HOLD_PRE_PARTIAL"
        emit(
            "SHADOW_OPEN_EVAL",
            symbol=sym,
            action="HOLD",
            reason=reason,
            price=price,
            gain_pct=round(gain * 100.0, 4),
            high_gain_pct=round(high_gain * 100.0, 4),
            drawdown_pct=round(drawdown * 100.0, 4),
            strong=strong,
            momentum_fail_count=state["momentum_fail_count"],
        )
        save_state(state)
        return state

    # Runner: widen the trail while RSI/MACD/inner-band trend remains strong.
    trail = RUNNER_TRAIL_STRONG_PCT if strong else RUNNER_TRAIL_NORMAL_PCT
    if drawdown >= trail:
        return exit_full(state, price, "RUNNER_HIGH_WATER_TRAIL", trail_pct=trail * 100.0)
    if weak and price < inner_u:
        return exit_full(state, price, "RUNNER_MOMENTUM_BREAK")
    if price <= mid:
        return exit_full(state, price, "RUNNER_MID_TOUCH")

    emit(
        "SHADOW_OPEN_EVAL",
        symbol=sym,
        action="HOLD",
        reason="RUNNER_STRONG" if strong else "RUNNER_NORMAL",
        price=price,
        gain_pct=round(gain * 100.0, 4),
        high_gain_pct=round(high_gain * 100.0, 4),
        drawdown_pct=round(drawdown * 100.0, 4),
        trail_pct=trail * 100.0,
        strong=strong,
    )
    save_state(state)
    return state


def main() -> None:
    # Disable the US-open bonus; Korea test uses the same indicator score without NY-session bias.
    engine = DoubleBollingerV2(DoubleBollingerV2Config(open_bonus_score=0.0))
    emit(
        "START",
        mode="KOREA_SHADOW_NO_BROKER",
        db=str(DB_PATH),
        poll_sec=POLL_SEC,
        no_entry_minute_kst=NO_ENTRY_MINUTE_KST,
        force_flat_minute_kst=FORCE_FLAT_MINUTE_KST,
        profit_arm_pct=PROFIT_ARM_PCT,
        partial_min_gain_pct=PARTIAL_MIN_GAIN_PCT,
        pre_partial_pullback_pct=PRE_PARTIAL_PULLBACK_PCT,
        runner_trail_strong_pct=RUNNER_TRAIL_STRONG_PCT,
        runner_trail_normal_pct=RUNNER_TRAIL_NORMAL_PCT,
    )

    while True:
        try:
            state = load_state()
            with db_conn() as con:
                if state:
                    manage_open(engine, con, state)
                else:
                    now = kst_now()
                    minute = now.hour * 60 + now.minute
                    if minute >= NO_ENTRY_MINUTE_KST:
                        emit("NO_NEW_ENTRY_TIME", kst=str(now))
                    else:
                        best, bars = strongest_candidate(engine, con)
                        if best is None or bars is None:
                            emit("NO_LIVE_CANDIDATE")
                        elif bool(best.get("early") or best.get("confirm")):
                            sym = str(best["symbol"])
                            price = float(best["price"])
                            stop = price * (1.0 - engine.cfg.fallback_risk_pct)
                            state = {
                                "symbol": sym,
                                "entry_price": price,
                                "stop_price": stop,
                                "opened_at": utc_now().isoformat(),
                                "entry_score": float(best.get("score") or 0.0),
                                "entry_stage": str(best.get("stage") or ""),
                                "high_watermark": price,
                                "partial_exit_done": False,
                                "remaining_fraction": 1.0,
                                "realized_return_fraction": 0.0,
                                "momentum_fail_count": 0,
                                "last_bar": str(bars.iloc[-1]["time"]),
                            }
                            save_state(state)
                            emit(
                                "SHADOW_ENTER",
                                symbol=sym,
                                price=price,
                                stop=stop,
                                score=best.get("score"),
                                stage=best.get("stage"),
                                rsi_slope1=best.get("rsi_slope1"),
                                macd_gap_delta=best.get("macd_gap_delta"),
                            )
                        else:
                            emit(
                                "SHADOW_WAIT",
                                symbol=best.get("symbol"),
                                price=best.get("price"),
                                score=best.get("score"),
                                stage=best.get("stage"),
                            )
        except KeyboardInterrupt:
            emit("STOP", reason="KEYBOARD_INTERRUPT")
            break
        except Exception as e:
            emit("LOOP_ERROR", error=repr(e))
        time.sleep(max(5, POLL_SEC))


if __name__ == "__main__":
    main()
