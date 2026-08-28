from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from live_server.double_bollinger_v2 import DoubleBollingerV2, DoubleBollingerV2Config
from live_server.kiwoom_mock_broker import KiwoomMockBroker

STATE_PATH = Path(os.getenv("DBB_KR_LIVE_STATE", "/home/ubuntu/day-trader-api/dbb_kr_mock_live_state.json"))
LOG_PATH = Path(os.getenv("DBB_KR_LIVE_LOG", "/home/ubuntu/day-trader-api/dbb_kr_mock_live.jsonl"))
POLL_SEC = int(os.getenv("DBB_KR_LIVE_POLL_SEC", "30"))
ORDER_QTY = int(os.getenv("DBB_KR_LIVE_QTY", "1"))
STALE_SEC = int(os.getenv("DBB_KR_STALE_SEC", "180"))
MIN_BARS = int(os.getenv("DBB_KR_MIN_BARS", "40"))
NO_ENTRY_MINUTE_KST = int(os.getenv("DBB_KR_NO_ENTRY_MINUTE", str(15 * 60)))
FORCE_FLAT_MINUTE_KST = int(os.getenv("DBB_KR_FORCE_FLAT_MINUTE", str(15 * 60 + 20)))
PROFIT_ARM_PCT = float(os.getenv("DBB_KR_PROFIT_ARM_PCT", "0.004"))
PARTIAL_MIN_GAIN_PCT = float(os.getenv("DBB_KR_PARTIAL_MIN_GAIN_PCT", "0.005"))
PRE_PARTIAL_PULLBACK_PCT = float(os.getenv("DBB_KR_PRE_PARTIAL_PULLBACK_PCT", "0.0035"))
RUNNER_TRAIL_STRONG_PCT = float(os.getenv("DBB_KR_RUNNER_TRAIL_STRONG_PCT", "0.008"))
RUNNER_TRAIL_NORMAL_PCT = float(os.getenv("DBB_KR_RUNNER_TRAIL_NORMAL_PCT", "0.005"))
MOMENTUM_FAIL_BARS = int(os.getenv("DBB_KR_MOMENTUM_FAIL_BARS", "2"))

DEFAULT_UNIVERSE = ("122630", "252670")
ALLOWED_SYMBOLS = tuple(s.strip() for s in os.getenv("DBB_KR_ALLOWED_SYMBOLS", ",".join(DEFAULT_UNIVERSE)).split(",") if s.strip())
if not ALLOWED_SYMBOLS or any(len(s) != 6 or not s.isdigit() for s in ALLOWED_SYMBOLS):
    raise RuntimeError("DBB_KR_ALLOWED_SYMBOLS must be six-digit KR symbols")

KST = ZoneInfo("Asia/Seoul")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_kst() -> datetime:
    return datetime.now(KST)


def emit(event: str, **kwargs) -> None:
    row = {"ts": now_utc().isoformat().replace("+00:00", "Z"), "event": event, **kwargs}
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


def strip_code(v: str) -> str:
    s = str(v or "")
    return s[1:] if s.startswith("A") else s


def ensure_allowed(symbol: str) -> str:
    sym = strip_code(symbol)
    if sym not in ALLOWED_SYMBOLS:
        raise RuntimeError(f"KR live safety block: {sym} not in {ALLOWED_SYMBOLS}")
    return sym


def account_qty(broker: KiwoomMockBroker, symbol: str) -> int:
    sym = ensure_allowed(symbol)
    r = broker.request_account("kt00004", {"qry_tp": "0", "dmst_stex_tp": "KRX"})
    for p in r.get("stk_acnt_evlt_prst") or []:
        if strip_code(p.get("stk_cd")) == sym:
            return int(p.get("rmnd_qty") or 0)
    return 0


def _n(v) -> float:
    try:
        return abs(float(str(v or "0").replace(",", "").replace("+", "")))
    except Exception:
        return 0.0


def fetch_bars(broker: KiwoomMockBroker, symbol: str) -> pd.DataFrame:
    sym = ensure_allowed(symbol)
    d = broker._post("/api/dostk/chart", "ka10080", {"stk_cd": sym, "tic_scope": "1", "upd_stkpc_tp": "1"})
    rows = []
    for x in d.get("stk_min_pole_chart_qry") or []:
        tm = str(x.get("cntr_tm") or "").strip()
        close = _n(x.get("cur_prc"))
        if len(tm) < 12 or close <= 0:
            continue
        rows.append({
            "time": pd.to_datetime(tm[:14], format="%Y%m%d%H%M%S", errors="coerce").tz_localize(KST).tz_convert("UTC"),
            "open": _n(x.get("open_pric")),
            "high": _n(x.get("high_pric")),
            "low": _n(x.get("low_pric")),
            "close": close,
            "volume": _n(x.get("trde_qty")),
        })
    if not rows:
        return pd.DataFrame()
    bars = pd.DataFrame(rows).dropna(subset=["time"]).drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    return bars


def latest_age_sec(bars: pd.DataFrame) -> float:
    if bars.empty:
        return 1e18
    t = pd.Timestamp(bars.iloc[-1]["time"])
    return max(0.0, (pd.Timestamp.now(tz="UTC") - t).total_seconds())


def diag_for(engine: DoubleBollingerV2, symbol: str, bars: pd.DataFrame) -> dict:
    engine.ALLOWED_SYMBOLS = {symbol}
    return engine.entry_diagnostics(symbol, bars)


def strongest_allowed_candidate(engine: DoubleBollingerV2, broker: KiwoomMockBroker):
    ranked = []
    data_status = []
    for sym in ALLOWED_SYMBOLS:
        try:
            bars = fetch_bars(broker, sym)
            age = latest_age_sec(bars)
            data_status.append({"symbol": sym, "bars": len(bars), "age_sec": round(age, 1) if age < 1e17 else None})
            if len(bars) < MIN_BARS or age > STALE_SEC:
                continue
            d = diag_for(engine, sym, bars)
            if d.get("ready"):
                ranked.append((float(d.get("score") or 0.0), d, bars))
        except Exception as e:
            emit("DATA_ERROR", symbol=sym, error=repr(e))
    emit("DATA_STATUS", rows=data_status)
    ranked.sort(key=lambda z: z[0], reverse=True)
    if not ranked:
        return None, None
    emit("SCAN", top=[{"symbol": d.get("symbol"), "score": d.get("score"), "stage": d.get("stage"), "price": d.get("price")} for _, d, _ in ranked])
    return ranked[0][1], ranked[0][2]


def strategy_owned_qty(broker: KiwoomMockBroker, state: dict) -> int:
    held = account_qty(broker, state["symbol"])
    base = int(state.get("base_qty_before") or 0)
    return max(0, held - base)


def sell_all_tracked(broker: KiwoomMockBroker, state: dict, reason: str, price: float | None = None, **extra) -> dict:
    sym = ensure_allowed(state["symbol"])
    qty = min(int(state.get("current_qty") or 0), strategy_owned_qty(broker, state))
    if qty <= 0:
        emit("LIVE_FLAT_ALREADY", symbol=sym, reason=reason)
        clear_state()
        return {}
    resp = broker.sell_market(sym, qty)
    emit("LIVE_FULL_EXIT_ORDER", symbol=sym, qty=qty, reason=reason, price=price, order_no=resp.get("ord_no"), **extra)
    time.sleep(2)
    clear_state()
    return {}


def manage_open(engine: DoubleBollingerV2, broker: KiwoomMockBroker, state: dict) -> dict:
    sym = ensure_allowed(state["symbol"])
    bars = fetch_bars(broker, sym)
    age = latest_age_sec(bars)
    if len(bars) < MIN_BARS or age > STALE_SEC:
        emit("LIVE_OPEN_WAIT_DATA", symbol=sym, bars=len(bars), age_sec=round(age, 1) if age < 1e17 else None)
        return state
    d = diag_for(engine, sym, bars)
    last = bars.iloc[-1]
    price, bar_high, bar_low = float(last["close"]), float(last["high"]), float(last["low"])
    entry, stop = float(state["entry_price"]), float(state["stop_price"])
    hwm = max(float(state.get("high_watermark") or entry), bar_high)
    state["high_watermark"] = hwm
    state["last_bar"] = str(last["time"])
    minute = now_kst().hour * 60 + now_kst().minute
    if minute >= FORCE_FLAT_MINUTE_KST:
        return sell_all_tracked(broker, state, "SESSION_FORCE_FLAT", price)
    if bar_low <= stop:
        return sell_all_tracked(broker, state, "INITIAL_STOP_INTRABAR_LOW", stop, bar_low=bar_low)
    rsi_slope = float(d.get("rsi_slope1") or 0.0)
    gap_delta = float(d.get("macd_gap_delta") or 0.0)
    inner_u = float(d.get("inner_upper") or price)
    mid = float(d.get("mid") or price)
    strong = rsi_slope > 0 and gap_delta > 0 and price >= inner_u
    weak = rsi_slope < 0 and gap_delta < 0
    state["momentum_fail_count"] = int(state.get("momentum_fail_count") or 0) + 1 if weak else 0
    gain = price / entry - 1.0
    high_gain = hwm / entry - 1.0
    drawdown = 1.0 - price / hwm if hwm > 0 else 0.0
    qty = int(state.get("current_qty") or 0)
    if not state.get("partial_exit_done"):
        if high_gain >= PARTIAL_MIN_GAIN_PCT and not strong and qty >= 2:
            sell_qty = max(1, qty // 2)
            resp = broker.sell_market(sym, sell_qty)
            state["current_qty"] = qty - sell_qty
            state["partial_exit_done"] = True
            emit("LIVE_PARTIAL_EXIT_ORDER", symbol=sym, qty=sell_qty, remaining_qty=state["current_qty"], order_no=resp.get("ord_no"))
            save_state(state)
            return state
        if high_gain >= PROFIT_ARM_PCT and drawdown >= PRE_PARTIAL_PULLBACK_PCT:
            return sell_all_tracked(broker, state, "PRE_PARTIAL_HIGH_WATER_PULLBACK", price)
        if state["momentum_fail_count"] >= MOMENTUM_FAIL_BARS:
            return sell_all_tracked(broker, state, "PRE_PARTIAL_MOMENTUM_FAIL", price)
        emit("LIVE_HOLD", symbol=sym, price=price, gain_pct=round(gain * 100, 4), strong=strong)
        save_state(state)
        return state
    trail = RUNNER_TRAIL_STRONG_PCT if strong else RUNNER_TRAIL_NORMAL_PCT
    if drawdown >= trail:
        return sell_all_tracked(broker, state, "RUNNER_HIGH_WATER_TRAIL", price)
    if weak and price < inner_u:
        return sell_all_tracked(broker, state, "RUNNER_MOMENTUM_BREAK", price)
    if price <= mid:
        return sell_all_tracked(broker, state, "RUNNER_MID_TOUCH", price)
    emit("LIVE_HOLD", symbol=sym, price=price, gain_pct=round(gain * 100, 4), strong=strong)
    save_state(state)
    return state


def main() -> None:
    if ORDER_QTY <= 0:
        raise RuntimeError("DBB_KR_LIVE_QTY must be > 0")
    broker = KiwoomMockBroker()
    account = broker.validate_account()
    engine = DoubleBollingerV2(DoubleBollingerV2Config(open_bonus_score=0.0))
    emit("START", mode="KOREA_MOCK_LIVE_DIRECT_KA10080", qty=ORDER_QTY, poll_sec=POLL_SEC, allowed_symbols=list(ALLOWED_SYMBOLS), account_base8="".join(c for c in account if c.isdigit())[:8])
    while True:
        try:
            state = load_state()
            if state:
                ensure_allowed(state.get("symbol", ""))
                manage_open(engine, broker, state)
            else:
                now = now_kst()
                minute = now.hour * 60 + now.minute
                if minute >= NO_ENTRY_MINUTE_KST:
                    emit("NO_NEW_ENTRY_TIME", kst=str(now))
                else:
                    best, bars = strongest_allowed_candidate(engine, broker)
                    if best is None or bars is None:
                        emit("NO_LIVE_CANDIDATE", allowed_symbols=list(ALLOWED_SYMBOLS))
                    elif bool(best.get("early") or best.get("confirm")):
                        sym = ensure_allowed(best["symbol"])
                        price = float(best["price"])
                        base_qty = account_qty(broker, sym)
                        resp = broker.buy_market(sym, ORDER_QTY)
                        time.sleep(2)
                        owned = max(0, account_qty(broker, sym) - base_qty)
                        if owned <= 0:
                            emit("ENTRY_RECONCILE_FAIL", symbol=sym, order_no=resp.get("ord_no"))
                        else:
                            state = {"symbol": sym, "entry_price": price, "stop_price": price * (1.0 - engine.cfg.fallback_risk_pct), "opened_at": now_utc().isoformat(), "entry_score": float(best.get("score") or 0.0), "entry_stage": str(best.get("stage") or ""), "high_watermark": price, "partial_exit_done": False, "initial_qty": owned, "current_qty": owned, "base_qty_before": base_qty, "momentum_fail_count": 0, "last_bar": str(bars.iloc[-1]["time"]), "entry_order_no": resp.get("ord_no")}
                            save_state(state)
                            emit("LIVE_ENTER_ORDER", symbol=sym, qty=owned, price=price, stop=state["stop_price"], score=best.get("score"), stage=best.get("stage"), order_no=resp.get("ord_no"))
                    else:
                        emit("LIVE_WAIT", symbol=best.get("symbol"), price=best.get("price"), score=best.get("score"), stage=best.get("stage"))
        except KeyboardInterrupt:
            emit("STOP", reason="KEYBOARD_INTERRUPT")
            break
        except Exception as e:
            emit("ERROR", error=repr(e))
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
