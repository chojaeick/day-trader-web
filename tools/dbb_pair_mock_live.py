#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from live_server.api import db, ticks_to_bars
from live_server.double_bollinger_v2 import DoubleBollingerV2, DoubleBollingerPairV2
from live_server.strategy_core_v1 import Action, PositionPhase, PositionState
from live_server.kiwoom_us_mock_broker import KiwoomUSMockBroker

LIMITS = {"SOXL": 200000, "SOXS": 80000}
EXCHANGE = {"SOXL": "NY", "SOXS": "NY"}
STATE_PATH = Path(os.getenv("DBB_MOCK_STATE_PATH", str(ROOT / "dbb_pair_mock_state.json")))
LOG_PATH = Path(os.getenv("DBB_MOCK_LOG_PATH", str(ROOT / "dbb_pair_mock_live.jsonl")))
LOCK_PATH = Path(os.getenv("DBB_MOCK_LOCK_PATH", "/tmp/dbb_pair_mock_live.lock"))
RECON_SEC = max(30, int(os.getenv("DBB_MOCK_RECON_SEC", "60") or 60))


def truthy(name: str, default: str = "0") -> bool:
    return str(os.getenv(name, default)).lower() in {"1", "true", "yes", "on"}


def log(event: str, **payload: Any) -> None:
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event, **payload}
    text = json.dumps(row, ensure_ascii=False, default=str)
    print(text, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(text + "\n")


def save_state(d: dict[str, Any]) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def completed_bars(sym: str):
    ticks = db.ticks(sym, LIMITS[sym])
    bars = ticks_to_bars(ticks, 1)
    if bars is None or len(bars) < 40:
        return None
    x = bars.copy().reset_index(drop=True)
    try:
        import pandas as pd
        ts = pd.to_datetime(x["time"], errors="coerce", utc=True)
        now_min = pd.Timestamp.now(tz="UTC").floor("min")
        if len(x) > 1 and pd.notna(ts.iloc[-1]) and ts.iloc[-1].floor("min") >= now_min:
            x = x.iloc[:-1].reset_index(drop=True)
    except Exception:
        pass
    return x if len(x) >= 40 else None


def balance_with_backoff(broker: KiwoomUSMockBroker, sym: str, retries: int = 4):
    delay = 2.0
    last = None
    for attempt in range(1, retries + 1):
        try:
            return broker.balance(sym, EXCHANGE[sym])
        except Exception as e:
            last = e
            if "429" not in repr(e) or attempt >= retries:
                raise
            log("ACCOUNT_429_BACKOFF", symbol=sym, attempt=attempt, sleep_sec=delay)
            time.sleep(delay)
            delay *= 2.0
    raise last


def holding(broker: KiwoomUSMockBroker, sym: str) -> dict[str, Any] | None:
    r = balance_with_backoff(broker, sym)
    for x in r.get("result_list") or []:
        if str(x.get("stk_cd") or "").upper() == sym:
            qty = int(str(x.get("sell_alowq") or x.get("poss_qty") or "0") or "0")
            if qty > 0:
                return {
                    "symbol": sym,
                    "qty": qty,
                    "avg": float(x.get("frgn_stk_book_uv") or 0),
                    "price": float(x.get("now_pric") or 0),
                    "raw": x,
                }
    return None


def pair_holdings(broker: KiwoomUSMockBroker):
    out = []
    for idx, sym in enumerate(("SOXL", "SOXS")):
        h = holding(broker, sym)
        if h:
            out.append(h)
        if idx == 0:
            time.sleep(1.2)
    return out


def marketable_price(price: float, side: str) -> float:
    cross = max(0.001, float(os.getenv("DBB_MOCK_CROSS_PCT", "0.01") or 0.01))
    px = price * (1.0 + cross) if side == "BUY" else price * (1.0 - cross)
    return round(px, 2 if px >= 1 else 4)


def order_with_retry(broker: KiwoomUSMockBroker, side: str, sym: str, qty: int, px: float):
    """Send order through the same proven broker path as the round-trip test.
    One fresh-token retry is allowed for transient Kiwoom mock context errors.
    """
    last = None
    for attempt in (1, 2):
        try:
            if side == "BUY":
                return broker.buy_limit(sym, qty, px, EXCHANGE[sym]), broker
            return broker.sell_limit(sym, qty, px, EXCHANGE[sym]), broker
        except Exception as e:
            last = e
            log("ORDER_RETRY", side=side, symbol=sym, qty=qty, limit=px, attempt=attempt, error=repr(e))
            if attempt == 2:
                raise
            time.sleep(1.5)
            broker = KiwoomUSMockBroker()  # new instance => fresh token/context
    raise last


def state_from_dict(d: dict[str, Any]) -> PositionState:
    st = PositionState(symbol=str(d["symbol"]))
    st.phase = PositionPhase(str(d.get("phase") or "OPEN"))
    st.entry_price = float(d["entry_price"])
    st.stop_price = float(d["stop_price"])
    st.qty_fraction = float(d.get("qty_fraction", 1.0))
    st.high_watermark = float(d.get("high_watermark") or d["entry_price"])
    st.partial_exit_done = bool(d.get("partial_exit_done", False))
    st.opened_at = d.get("opened_at")
    return st


def state_to_dict(st: PositionState, initial_qty: int, current_qty: int, last_bar: str | None = None):
    return {
        "symbol": st.symbol,
        "phase": st.phase.value,
        "entry_price": st.entry_price,
        "stop_price": st.stop_price,
        "qty_fraction": st.qty_fraction,
        "high_watermark": st.high_watermark,
        "partial_exit_done": st.partial_exit_done,
        "opened_at": st.opened_at,
        "initial_qty": int(initial_qty),
        "current_qty": int(current_qty),
        "last_bar": last_bar,
    }


def rebuild_from_holding(h: dict[str, Any], bar_key: str, qty_default: int) -> dict[str, Any]:
    avg = float(h["avg"] or h["price"])
    st = PositionState(symbol=h["symbol"])
    st.open(avg, avg * 0.99, opened_at=bar_key)
    d = state_to_dict(st, max(qty_default, h["qty"]), h["qty"], bar_key)
    save_state(d)
    log("STATE_REBUILT", holding=h)
    return d


def main() -> int:
    if not truthy("DBB_MOCK_AUTO"):
        raise SystemExit("DBB_MOCK_AUTO is not enabled")
    if truthy("WILLIAMS_KIWOOM_US_MOCK_AUTO"):
        raise SystemExit("REFUSE_DUAL_AUTHORITY: disable WILLIAMS_KIWOOM_US_MOCK_AUTO first")
    if not truthy("KIWOOM_MOCK_US_ORDER_ENABLE"):
        raise SystemExit("KIWOOM_MOCK_US_ORDER_ENABLE is not enabled")

    lockf = LOCK_PATH.open("w")
    try:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("another DBB mock runner is already active")

    broker = KiwoomUSMockBroker()
    eng = DoubleBollingerV2()
    pair = DoubleBollingerPairV2(eng)
    qty_default = max(2, int(os.getenv("DBB_MOCK_QTY", "2") or 2))
    persisted = load_state()
    last_eval_bar = None
    last_recon = 0.0

    log("START", qty=qty_default, recon_sec=RECON_SEC, state=str(STATE_PATH), log=str(LOG_PATH), account_polling="60S_RECON_PLUS_EVENT")

    while True:
        try:
            bars = {s: completed_bars(s) for s in ("SOXL", "SOXS")}
            if any(v is None for v in bars.values()):
                log("WAIT_DATA", soxl=bars["SOXL"] is not None, soxs=bars["SOXS"] is not None)
                time.sleep(5)
                continue

            bar_key = max(str(bars[s].iloc[-1]["time"]) for s in bars)

            # Reconcile real mock-account holdings every 60s. This catches manual
            # trades without hammering Kiwoom on every loop.
            if time.monotonic() - last_recon >= RECON_SEC:
                hs = pair_holdings(broker)
                last_recon = time.monotonic()
                if len(hs) > 1:
                    log("ERROR_BOTH_SIDES_HELD", holdings=hs)
                    time.sleep(5)
                    continue
                if hs:
                    h0 = hs[0]
                    if not persisted or persisted.get("symbol") != h0["symbol"] or int(persisted.get("current_qty") or 0) != h0["qty"]:
                        persisted = rebuild_from_holding(h0, bar_key, qty_default)
                elif persisted:
                    persisted = {}
                    STATE_PATH.unlink(missing_ok=True)
                    log("ACCOUNT_FLAT_STATE_CLEARED")
                else:
                    log("ACCOUNT_FLAT_CONFIRMED")

            if bar_key == last_eval_bar:
                time.sleep(3)
                continue
            last_eval_bar = bar_key

            if not persisted:
                r = pair.evaluate_flat_pair(bars)
                log("FLAT_EVAL", bar=bar_key, symbol=r.symbol, action=r.action.value, reason=r.reason, price=r.price, score=r.score)
                if r.action != Action.ENTER:
                    continue

                sym = r.symbol
                px = marketable_price(float(r.price), "BUY")
                ack, broker = order_with_retry(broker, "BUY", sym, qty_default, px)
                log("BUY_SENT", symbol=sym, qty=qty_default, limit=px, score=r.score, reason=r.reason, ack=ack)
                time.sleep(3)
                h = holding(broker, sym)
                if h:
                    stop = float(r.stop or (h["avg"] * 0.99))
                    st = PositionState(symbol=sym)
                    st.open(h["avg"], stop, opened_at=bar_key)
                    persisted = state_to_dict(st, qty_default, h["qty"], bar_key)
                    save_state(persisted)
                    log("BUY_FILLED", holding=h, stop=stop)
                else:
                    log("BUY_NOT_YET_VISIBLE", symbol=sym, ack=ack)
                continue

            st = state_from_dict(persisted)
            sym = st.symbol.upper()
            r = eng.evaluate_open(st, bars[sym])
            current_qty = int(persisted.get("current_qty") or persisted.get("initial_qty") or qty_default)
            log("OPEN_EVAL", bar=bar_key, symbol=sym, qty=current_qty, action=r.action.value, reason=r.reason, price=r.price)

            if r.action == Action.PARTIAL_EXIT:
                h = holding(broker, sym)
                if not h:
                    persisted = {}
                    STATE_PATH.unlink(missing_ok=True)
                    log("POSITION_ALREADY_FLAT", symbol=sym)
                    continue
                sell_qty = max(1, min(h["qty"] - 1 if h["qty"] > 1 else 1, int(round(h["qty"] * float(r.exit_fraction or 0.5)))))
                px = marketable_price(float(r.price), "SELL")
                ack, broker = order_with_retry(broker, "SELL", sym, sell_qty, px)
                log("PARTIAL_SELL_SENT", symbol=sym, qty=sell_qty, limit=px, reason=r.reason, ack=ack)
                time.sleep(3)
                h2 = holding(broker, sym)
                if h2:
                    st.partial_exit(float(r.exit_fraction or 0.5))
                    persisted = state_to_dict(st, int(persisted.get("initial_qty") or qty_default), h2["qty"], bar_key)
                    save_state(persisted)
                    log("PARTIAL_SELL_FILLED", holding=h2)
                else:
                    persisted = {}
                    STATE_PATH.unlink(missing_ok=True)
                    log("POSITION_CLOSED_AFTER_PARTIAL", symbol=sym)

            elif r.action == Action.FULL_EXIT:
                h = holding(broker, sym)
                if not h:
                    persisted = {}
                    STATE_PATH.unlink(missing_ok=True)
                    log("POSITION_ALREADY_FLAT", symbol=sym)
                    continue
                px = marketable_price(float(r.price), "SELL")
                ack, broker = order_with_retry(broker, "SELL", sym, h["qty"], px)
                log("FULL_SELL_SENT", symbol=sym, qty=h["qty"], limit=px, reason=r.reason, ack=ack)
                time.sleep(3)
                h2 = holding(broker, sym)
                if not h2:
                    persisted = {}
                    STATE_PATH.unlink(missing_ok=True)
                    log("FULL_SELL_FILLED", symbol=sym)
                else:
                    persisted["current_qty"] = h2["qty"]
                    save_state(persisted)
                    log("FULL_SELL_PENDING", holding=h2)
            else:
                persisted["high_watermark"] = st.high_watermark
                persisted["last_bar"] = bar_key
                save_state(persisted)

        except KeyboardInterrupt:
            raise
        except Exception as e:
            log("LOOP_ERROR", error=repr(e))
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
