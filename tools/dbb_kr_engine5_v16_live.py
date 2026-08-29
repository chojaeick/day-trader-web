from __future__ import annotations

import json
import math
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from live_server.config import Settings
from live_server.db import DB
from live_server.kiwoom import KiwoomClient
from live_server.korea import KoreaMarketAdapter
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from live_server.double_bollinger_engine5_v16 import Engine5V16Runtime
from live_server.kiwoom_mock_broker import KiwoomMockBroker

KST = ZoneInfo("Asia/Seoul")
STATE_PATH = Path(os.getenv("DBB_KR_ENGINE5_V16_STATE", "/home/ubuntu/day-trader-api/dbb_kr_engine5_v16_state.json"))
LOG_PATH = Path(os.getenv("DBB_KR_ENGINE5_V16_LOG", "/home/ubuntu/day-trader-api/dbb_kr_engine5_v16.jsonl"))
POLL_SEC = int(os.getenv("DBB_KR_ENGINE5_V16_POLL_SEC", "10"))
ORDER_QTY = int(os.getenv("DBB_KR_ENGINE5_V16_QTY", "1"))
FINDER_TOP = int(os.getenv("DBB_KR_ENGINE5_V16_FINDER_TOP", "10"))
MIN_1M_BARS = int(os.getenv("DBB_KR_ENGINE5_V16_MIN_1M_BARS", "80"))
NO_ENTRY_MINUTE = int(os.getenv("DBB_KR_ENGINE5_V16_NO_ENTRY_MINUTE", str(15 * 60)))
FORCE_FLAT_MINUTE = int(os.getenv("DBB_KR_ENGINE5_V16_FORCE_FLAT_MINUTE", str(15 * 60 + 20)))
ENTRY_THRESHOLD = float(os.getenv("DBB_KR_ENGINE5_V16_THRESHOLD", "50"))


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
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def clear_state() -> None:
    STATE_PATH.unlink(missing_ok=True)


def strip_code(v: str) -> str:
    s = str(v or "").strip()
    if s.startswith("A") and len(s) >= 7:
        s = s[1:7]
    if "_" in s:
        s = s.split("_", 1)[0]
    return s[:6]


def account_qty(broker: KiwoomMockBroker, symbol: str) -> int:
    sym = strip_code(symbol)
    r = broker.request_account("kt00004", {"qry_tp": "0", "dmst_stex_tp": "KRX"})
    for p in r.get("stk_acnt_evlt_prst") or []:
        if strip_code(p.get("stk_cd")) == sym:
            return int(p.get("rmnd_qty") or 0)
    return 0


def normalize_finder_rows(korea: KoreaMarketAdapter) -> list[dict]:
    d = korea.discover(max(20, FINDER_TOP * 3))
    rows = d.get("rows") if isinstance(d, dict) else None
    rows = rows or []
    out = []
    seen = set()
    for r in rows:
        sym = strip_code(r.get("symbol") or r.get("stk_cd"))
        if len(sym) != 6 or not sym.isdigit() or sym in seen:
            continue
        seen.add(sym)
        out.append({
            "symbol": sym,
            "name": r.get("name") or r.get("stk_nm"),
            "finder_score": float(r.get("score") or r.get("current_score") or r.get("gamma_score") or 0.0),
            "market": r.get("market"),
        })
        if len(out) >= FINDER_TOP:
            break
    return out


def fetch_1m(korea: KoreaMarketAdapter, symbol: str) -> pd.DataFrame:
    d = korea.canonical_minute_bars(symbol, max_pages=3)
    rows = d.get("bars") or []
    if not rows:
        return pd.DataFrame()
    f = pd.DataFrame(rows)
    f["time"] = pd.to_datetime(f["time"], format="%Y%m%d%H%M%S", errors="coerce")
    f = f.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)
    for c in ("open", "high", "low", "close", "volume"):
        f[c] = pd.to_numeric(f[c], errors="coerce")
    return f.dropna(subset=["open", "high", "low", "close"])


def previous_close_and_gap(bars: pd.DataFrame) -> tuple[float | None, float | None]:
    if bars.empty:
        return None, None
    d = bars.copy()
    d["date"] = d["time"].dt.date
    today = d.iloc[-1]["date"]
    cur = d[d["date"] == today]
    prev = d[d["date"] < today]
    if cur.empty or prev.empty:
        return None, None
    prev_close = float(prev.iloc[-1]["close"])
    today_open = float(cur.iloc[0]["open"])
    if prev_close <= 0:
        return prev_close, None
    return prev_close, (today_open / prev_close - 1.0) * 100.0


def minute_bucket_5m(bars: pd.DataFrame) -> pd.DataFrame:
    if bars.empty:
        return bars.copy()
    f = bars.copy()
    t = f["time"]
    f["bucket"] = t.dt.floor("5min") + pd.Timedelta(minutes=5)
    out = f.groupby("bucket", as_index=False).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"), volume=("volume", "sum")
    ).rename(columns={"bucket": "time"})
    return out.sort_values("time").reset_index(drop=True)


def strict_r_from_latest_5m(enriched: pd.DataFrame) -> float | None:
    if enriched.empty:
        return None
    r = enriched.iloc[-1]
    try:
        iu = float(r["inner_upper"]); il = float(r["inner_lower"])
        risk = iu - il
        return risk if np.isfinite(risk) and risk > 0 else None
    except Exception:
        return None


def strategy_owned_qty(broker: KiwoomMockBroker, state: dict) -> int:
    held = account_qty(broker, state["symbol"])
    base = int(state.get("base_qty_before") or 0)
    return max(0, held - base)


def close_tracked(broker: KiwoomMockBroker, state: dict, reason: str, price: float | None = None) -> dict:
    qty = min(int(state.get("current_qty") or 0), strategy_owned_qty(broker, state))
    if qty <= 0:
        emit("FLAT_ALREADY", symbol=state.get("symbol"), reason=reason)
        clear_state()
        return {}
    resp = broker.sell_market(state["symbol"], qty)
    emit("FULL_EXIT_ORDER", symbol=state["symbol"], qty=qty, reason=reason, price=price, order_no=resp.get("ord_no"))
    time.sleep(2)
    clear_state()
    return {}


def manage_open(broker: KiwoomMockBroker, korea: KoreaMarketAdapter, state: dict) -> dict:
    sym = strip_code(state["symbol"])
    bars = fetch_1m(korea, sym)
    if bars.empty:
        emit("OPEN_WAIT_DATA", symbol=sym)
        return state
    last = bars.iloc[-1]
    price, low, high = float(last.close), float(last.low), float(last.high)
    minute = now_kst().hour * 60 + now_kst().minute
    if minute >= FORCE_FLAT_MINUTE:
        return close_tracked(broker, state, "SESSION_FORCE_FLAT", price)
    if low <= float(state["stop_price"]):
        return close_tracked(broker, state, "INITIAL_1R_STOP", float(state["stop_price"]))
    if not state.get("tp1_done") and high >= float(state["tp1_price"]):
        qty = int(state.get("current_qty") or 0)
        sell_qty = max(1, qty // 2) if qty >= 2 else qty
        if sell_qty > 0:
            resp = broker.sell_market(sym, sell_qty)
            state["current_qty"] = qty - sell_qty
            state["tp1_done"] = True
            state["tp1_time"] = now_utc().isoformat()
            emit("TP1_ORDER", symbol=sym, qty=sell_qty, remaining=state["current_qty"], price=state["tp1_price"], order_no=resp.get("ord_no"))
            if state["current_qty"] <= 0:
                clear_state(); return {}
            save_state(state); return state
    emit("HOLD", symbol=sym, price=price, tp1_done=bool(state.get("tp1_done")), stop=state.get("stop_price"), tp1=state.get("tp1_price"))
    save_state(state)
    return state


def scan(runtime: Engine5V16Runtime, korea: KoreaMarketAdapter) -> tuple[dict | None, dict | None]:
    rows = normalize_finder_rows(korea)
    emit("FINDER", rows=rows)
    ranked = []
    for meta in rows:
        sym = meta["symbol"]
        try:
            bars = fetch_1m(korea, sym)
            if len(bars) < MIN_1M_BARS:
                emit("SKIP_SHORT_DATA", symbol=sym, bars=len(bars))
                continue
            five = minute_bucket_5m(bars)
            decision = runtime.evaluate(sym, five, bars)
            _, gap = previous_close_and_gap(bars)
            decision["gap_pct"] = gap
            decision["finder_score"] = meta["finder_score"]
            decision["name"] = meta.get("name")
            emit("ENGINE5_DECISION", **decision)
            if decision.get("action") == "BUY" and float(decision.get("score") or 0) >= ENTRY_THRESHOLD:
                ranked.append((float(decision.get("score") or 0), float(meta["finder_score"]), decision, bars, five))
        except Exception as e:
            emit("SCAN_ERROR", symbol=sym, error=repr(e))
    if not ranked:
        return None, None
    ranked.sort(key=lambda x: (x[0], x[1]), reverse=True)
    _, _, d, bars, five = ranked[0]
    d["risk_abs"] = strict_r_from_latest_5m(runtime.last_enriched.get(d["symbol"], pd.DataFrame()))
    return d, bars


def main() -> None:
    if ORDER_QTY <= 0:
        raise RuntimeError("DBB_KR_ENGINE5_V16_QTY must be > 0")
    settings = Settings()
    db = DB(settings.db_path)
    kc = KiwoomClient(settings, db)
    korea = KoreaMarketAdapter(kc)
    broker = KiwoomMockBroker()
    cfg = DoubleBollingerEngine5Config(macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)
    runtime = Engine5V16Runtime(cfg=cfg, gap_pct=4.0, open_minute=9 * 60 + 10, micro_end_minute=10 * 60)
    emit("START", mode="KR_ENGINE5_V16_FINDER", poll_sec=POLL_SEC, qty=ORDER_QTY, threshold=ENTRY_THRESHOLD, finder_top=FINDER_TOP, cfg=asdict(cfg))
    while True:
        try:
            state = load_state()
            if state:
                manage_open(broker, korea, state)
            else:
                now = now_kst(); minute = now.hour * 60 + now.minute
                if minute < 9 * 60 + 10:
                    emit("OPENING_BLOCK", kst=str(now))
                elif minute >= NO_ENTRY_MINUTE:
                    emit("NO_NEW_ENTRY_TIME", kst=str(now))
                else:
                    best, bars = scan(runtime, korea)
                    if best is None or bars is None:
                        emit("NO_BUY")
                    else:
                        sym = best["symbol"]
                        price = float(best["price"])
                        risk = best.get("risk_abs")
                        if not risk or not np.isfinite(float(risk)) or float(risk) <= 0:
                            emit("NO_BUY_BAD_R", symbol=sym, risk=risk)
                        else:
                            base_qty = account_qty(broker, sym)
                            resp = broker.buy_market(sym, ORDER_QTY)
                            time.sleep(2)
                            owned = max(0, account_qty(broker, sym) - base_qty)
                            if owned <= 0:
                                emit("ENTRY_RECONCILE_FAIL", symbol=sym, order_no=resp.get("ord_no"))
                            else:
                                risk = float(risk)
                                state = {
                                    "symbol": sym,
                                    "entry_price": price,
                                    "risk_abs": risk,
                                    "stop_price": price - risk,
                                    "tp1_price": price + 2.0 * risk,
                                    "opened_at": now_utc().isoformat(),
                                    "entry_score": float(best.get("score") or 0.0),
                                    "entry_reason": best.get("reason"),
                                    "initial_qty": owned,
                                    "current_qty": owned,
                                    "base_qty_before": base_qty,
                                    "tp1_done": False,
                                    "entry_order_no": resp.get("ord_no"),
                                }
                                save_state(state)
                                emit("ENTER_ORDER", symbol=sym, qty=owned, price=price, stop=state["stop_price"], tp1=state["tp1_price"], score=state["entry_score"], reason=state["entry_reason"], order_no=resp.get("ord_no"))
        except KeyboardInterrupt:
            emit("STOP", reason="KEYBOARD_INTERRUPT")
            break
        except Exception as e:
            emit("ERROR", error=repr(e))
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
