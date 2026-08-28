from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from live_server.double_bollinger_v2 import DoubleBollingerV2, DoubleBollingerV2Config
from live_server.kiwoom_mock_broker import KiwoomMockBroker
from tools.dbb_kr_shadow_live import (
    db_conn,
    diag_for,
    latest_age_sec,
    load_bars,
    strongest_candidate,
)

STATE_PATH = Path(os.getenv("DBB_KR_LIVE_STATE", "/home/ubuntu/day-trader-api/dbb_kr_mock_live_state.json"))
LOG_PATH = Path(os.getenv("DBB_KR_LIVE_LOG", "/home/ubuntu/day-trader-api/dbb_kr_mock_live.jsonl"))
POLL_SEC = int(os.getenv("DBB_KR_LIVE_POLL_SEC", "30"))
ORDER_QTY = int(os.getenv("DBB_KR_LIVE_QTY", "1"))
STALE_SEC = int(os.getenv("DBB_KR_STALE_SEC", "180"))
NO_ENTRY_MINUTE_KST = int(os.getenv("DBB_KR_NO_ENTRY_MINUTE", str(15 * 60)))
FORCE_FLAT_MINUTE_KST = int(os.getenv("DBB_KR_FORCE_FLAT_MINUTE", str(15 * 60 + 20)))
PROFIT_ARM_PCT = float(os.getenv("DBB_KR_PROFIT_ARM_PCT", "0.004"))
PARTIAL_MIN_GAIN_PCT = float(os.getenv("DBB_KR_PARTIAL_MIN_GAIN_PCT", "0.005"))
PRE_PARTIAL_PULLBACK_PCT = float(os.getenv("DBB_KR_PRE_PARTIAL_PULLBACK_PCT", "0.0035"))
RUNNER_TRAIL_STRONG_PCT = float(os.getenv("DBB_KR_RUNNER_TRAIL_STRONG_PCT", "0.008"))
RUNNER_TRAIL_NORMAL_PCT = float(os.getenv("DBB_KR_RUNNER_TRAIL_NORMAL_PCT", "0.005"))
MOMENTUM_FAIL_BARS = int(os.getenv("DBB_KR_MOMENTUM_FAIL_BARS", "2"))
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


def account_qty(broker: KiwoomMockBroker, symbol: str) -> int:
    r = broker.request_account("kt00004", {"qry_tp": "0", "dmst_stex_tp": "KRX"})
    for p in r.get("stk_acnt_evlt_prst") or []:
        if strip_code(p.get("stk_cd")) == symbol:
            return int(p.get("rmnd_qty") or 0)
    return 0


def sell_all_tracked(broker: KiwoomMockBroker, state: dict, reason: str, price: float | None = None, **extra) -> dict:
    sym = str(state["symbol"])
    tracked = int(state.get("current_qty") or state.get("initial_qty") or 0)
    held = account_qty(broker, sym)
    qty = min(tracked, held)
    if qty <= 0:
        emit("LIVE_FLAT_ALREADY", symbol=sym, reason=reason, tracked_qty=tracked, account_qty=held)
        clear_state()
        return {}
    resp = broker.sell_market(sym, qty)
    emit("LIVE_FULL_EXIT_ORDER", symbol=sym, qty=qty, reason=reason, price=price, order_no=resp.get("ord_no"), **extra)
    time.sleep(2)
    left = account_qty(broker, sym)
    if left > max(0, held - qty):
        emit("LIVE_EXIT_RECONCILE_WARN", symbol=sym, account_qty=left)
    clear_state()
    return {}


def manage_open(engine: DoubleBollingerV2, broker: KiwoomMockBroker, state: dict) -> dict:
    sym = str(state["symbol"])
    with db_conn() as con:
        bars = load_bars(con, sym)
    if len(bars) < 40 or latest_age_sec(bars) > STALE_SEC:
        emit("LIVE_OPEN_WAIT_DATA", symbol=sym, bars=len(bars), age_sec=round(latest_age_sec(bars), 1) if len(bars) else None)
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

    now = now_kst()
    minute = now.hour * 60 + now.minute
    if minute >= FORCE_FLAT_MINUTE_KST:
        return sell_all_tracked(broker, state, "SESSION_FORCE_FLAT", price, kst=str(now))
    if bar_low <= stop:
        return sell_all_tracked(broker, state, "INITIAL_STOP_INTRABAR_LOW", stop, bar_low=bar_low)

    rsi_slope = float(d.get("rsi_slope1") or 0.0)
    gap_delta = float(d.get("macd_gap_delta") or 0.0)
    inner_u = float(d.get("inner_upper") or price)
    mid = float(d.get("mid") or price)
    strong = bool(rsi_slope > 0.0 and gap_delta > 0.0 and price >= inner_u)
    weak = bool(rsi_slope < 0.0 and gap_delta < 0.0)
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
            emit("LIVE_PARTIAL_EXIT_ORDER", symbol=sym, qty=sell_qty, remaining_qty=state["current_qty"], reason="PROFIT_ARM_MOMENTUM_SOFTEN", order_no=resp.get("ord_no"), gain_pct=round(gain * 100, 4))
            save_state(state)
            return state
        if high_gain >= PROFIT_ARM_PCT and drawdown >= PRE_PARTIAL_PULLBACK_PCT:
            return sell_all_tracked(broker, state, "PRE_PARTIAL_HIGH_WATER_PULLBACK", price, gain_pct=round(gain * 100, 4))
        if int(state.get("momentum_fail_count") or 0) >= MOMENTUM_FAIL_BARS:
            return sell_all_tracked(broker, state, "PRE_PARTIAL_MOMENTUM_FAIL", price)
        emit("LIVE_HOLD", symbol=sym, reason="HOLD_STRONG_TREND" if high_gain >= PARTIAL_MIN_GAIN_PCT and strong else "HOLD_PRE_PARTIAL", price=price, gain_pct=round(gain * 100, 4), strong=strong)
        save_state(state)
        return state

    trail = RUNNER_TRAIL_STRONG_PCT if strong else RUNNER_TRAIL_NORMAL_PCT
    if drawdown >= trail:
        return sell_all_tracked(broker, state, "RUNNER_HIGH_WATER_TRAIL", price, trail_pct=trail * 100)
    if weak and price < inner_u:
        return sell_all_tracked(broker, state, "RUNNER_MOMENTUM_BREAK", price)
    if price <= mid:
        return sell_all_tracked(broker, state, "RUNNER_MID_TOUCH", price)
    emit("LIVE_HOLD", symbol=sym, reason="RUNNER_STRONG" if strong else "RUNNER_NORMAL", price=price, gain_pct=round(gain * 100, 4), strong=strong)
    save_state(state)
    return state


def main() -> None:
    if ORDER_QTY <= 0:
        raise RuntimeError("DBB_KR_LIVE_QTY must be > 0")
    broker = KiwoomMockBroker()
    account = broker.validate_account()
    digits = "".join(c for c in account if c.isdigit())
    engine = DoubleBollingerV2(DoubleBollingerV2Config(open_bonus_score=0.0))
    emit("START", mode="KOREA_MOCK_LIVE", account_base8=digits[:8], qty=ORDER_QTY, poll_sec=POLL_SEC)

    while True:
        try:
            state = load_state()
            if state:
                manage_open(engine, broker, state)
            else:
                now = now_kst()
                minute = now.hour * 60 + now.minute
                if minute >= NO_ENTRY_MINUTE_KST:
                    emit("NO_NEW_ENTRY_TIME", kst=str(now))
                else:
                    with db_conn() as con:
                        best, bars = strongest_candidate(engine, con)
                    if best is None or bars is None:
                        emit("NO_LIVE_CANDIDATE")
                    elif bool(best.get("early") or best.get("confirm")):
                        sym = str(best["symbol"])
                        price = float(best["price"])
                        resp = broker.buy_market(sym, ORDER_QTY)
                        state = {
                            "symbol": sym,
                            "entry_price": price,
                            "stop_price": price * (1.0 - engine.cfg.fallback_risk_pct),
                            "opened_at": now_utc().isoformat(),
                            "entry_score": float(best.get("score") or 0.0),
                            "entry_stage": str(best.get("stage") or ""),
                            "high_watermark": price,
                            "partial_exit_done": False,
                            "initial_qty": ORDER_QTY,
                            "current_qty": ORDER_QTY,
                            "momentum_fail_count": 0,
                            "last_bar": str(bars.iloc[-1]["time"]),
                            "entry_order_no": resp.get("ord_no"),
                        }
                        save_state(state)
                        emit("LIVE_ENTER_ORDER", symbol=sym, qty=ORDER_QTY, price=price, stop=state["stop_price"], score=best.get("score"), stage=best.get("stage"), order_no=resp.get("ord_no"))
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
