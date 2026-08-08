from __future__ import annotations

import math
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Day Trader Web v1.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

UNIVERSE = [
    ("SOXL", "Direxion Daily Semiconductor Bull 3X", "ETF"),
    ("NVDA", "NVIDIA", "Semiconductor"),
    ("PLTR", "Palantir", "Software"),
    ("AMD", "AMD", "Semiconductor"),
    ("TQQQ", "ProShares UltraPro QQQ", "ETF"),
    ("AVGO", "Broadcom", "Semiconductor"),
    ("TSLA", "Tesla", "Auto/AI"),
    ("META", "Meta Platforms", "Internet"),
    ("AMZN", "Amazon", "Internet"),
    ("MU", "Micron", "Semiconductor"),
    ("SOXS", "Direxion Daily Semiconductor Bear 3X", "ETF"),
    ("SQQQ", "ProShares UltraPro Short QQQ", "ETF"),
]

BASE_PRICE = {
    "SOXL": 142.6, "NVDA": 182.2, "PLTR": 154.8, "AMD": 179.1,
    "TQQQ": 96.4, "AVGO": 312.7, "TSLA": 327.9, "META": 721.4,
    "AMZN": 232.1, "MU": 143.8, "SOXS": 7.3, "SQQQ": 16.5,
}

start_ts = time.time()
selected_symbol = "SOXL"
position = {"symbol": None, "entry": None, "amount_krw": 0, "side": "LONG", "opened_at": None}


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def market_clock() -> Dict:
    # Demo market clock. Real version will use exchange schedule/time-zone aware logic.
    now = datetime.now(timezone.utc)
    return {
        "utc": now.isoformat(),
        "mode": "DEMO",
        "message": "실시간 증권 API 연결 전 데모 데이터",
    }


def calc_row(symbol: str, idx: int) -> Dict:
    elapsed = time.time() - start_ts
    phase = elapsed / 24.0 + idx * 0.73
    seed = sum(ord(c) for c in symbol)
    rnd = random.Random(seed + int(elapsed // 8))

    # deterministic-ish moving demo market
    drift = math.sin(phase) * 1.8 + math.sin(phase / 2.3) * 1.2
    impulse = (rnd.random() - 0.45) * 2.2
    change = max(-9.9, min(12.0, drift + impulse + (1.0 if symbol in {"SOXL", "NVDA", "PLTR"} else 0)))
    price = BASE_PRICE[symbol] * (1 + change / 100)
    rvol = max(0.6, 1.1 + abs(change) / 3.2 + rnd.random() * 1.7)
    ma5_delta = change * 0.38 + rnd.uniform(-0.7, 0.9)
    atr_pct = max(0.6, 1.0 + abs(change) * 0.35 + rnd.random() * 1.6)
    momentum = max(-5, min(5, change * 0.55 + rnd.uniform(-1.2, 1.4)))
    spread = max(0.02, 0.04 + rnd.random() * 0.16)
    news = rnd.choice([0, 0, 0, 2, 4, -3])
    sector = 2 if symbol in {"SOXL", "NVDA", "AMD", "AVGO", "MU"} else rnd.choice([0, 1, 2])

    score = 50
    score += 8 if price > BASE_PRICE[symbol] * 0.995 else -5
    score += max(-5, min(7, ma5_delta * 2.3))
    score += max(0, min(15, (rvol - 0.8) * 7.2))
    score += max(0, min(10, atr_pct * 2.2))
    score += max(-5, min(8, momentum * 1.5))
    score += sector
    score += news
    score += 5 if spread < 0.12 else 1
    score = int(max(35, min(99, round(score))))

    bias_raw = change * 0.7 + momentum * 0.8 + sector * 0.5 + news * 0.4
    long_bias = int(35 + sigmoid(bias_raw / 3.0) * 50)
    long_bias = max(5, min(95, long_bias))
    short_bias = 100 - long_bias
    bias = "LONG" if long_bias >= 58 else "SHORT" if short_bias >= 58 else "NEUTRAL"
    status = "TRIGGER" if score >= 90 else "SETUP" if score >= 82 else "WATCH" if score >= 70 else "WAIT"

    return {
        "symbol": symbol,
        "name": next(n for s, n, _ in UNIVERSE if s == symbol),
        "sector": next(sec for s, _, sec in UNIVERSE if s == symbol),
        "price": round(price, 2),
        "change_pct": round(change, 2),
        "rvol": round(rvol, 2),
        "ma5_delta": round(ma5_delta, 2),
        "atr_pct": round(atr_pct, 2),
        "momentum": round(momentum, 2),
        "spread_pct": round(spread, 2),
        "news_score": news,
        "score": score,
        "long_bias": long_bias,
        "short_bias": short_bias,
        "bias": bias,
        "status": status,
    }


def snapshot() -> List[Dict]:
    rows = [calc_row(sym, i) for i, (sym, _, _) in enumerate(UNIVERSE)]
    rows.sort(key=lambda x: x["score"], reverse=True)
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return rows


def selected_detail(symbol: str) -> Dict:
    rows = snapshot()
    row = next((r for r in rows if r["symbol"] == symbol), rows[0])
    px = row["price"]
    # coherent demo indicators around current price
    trend = 1 if row["bias"] == "LONG" else -1 if row["bias"] == "SHORT" else 0
    vwap = px * (1 - trend * 0.003)
    ema9 = px * (1 - trend * 0.002)
    ema20 = px * (1 - trend * 0.005)
    ema50 = px * (1 - trend * 0.009)
    rsi = max(20, min(82, 50 + row["momentum"] * 5.5))
    trigger = px * (1.0025 if row["bias"] != "SHORT" else 0.9975)
    technical_stop = px * (0.989 if row["bias"] != "SHORT" else 1.011)

    if position["symbol"] == symbol and position["entry"]:
        pnl_pct = (px / position["entry"] - 1) * 100
        if position["side"] == "SHORT":
            pnl_pct *= -1
        pos_status = "EXIT" if pnl_pct <= -2 else "TRIM 30%" if pnl_pct >= 3 else "TRIM 30%" if pnl_pct >= 1.8 else "HOLD"
    else:
        pnl_pct = None
        pos_status = None

    return {
        **row,
        "vwap": round(vwap, 2),
        "ema9": round(ema9, 2),
        "ema20": round(ema20, 2),
        "ema50": round(ema50, 2),
        "rsi": round(rsi, 1),
        "trigger": round(trigger, 2),
        "technical_stop": round(technical_stop, 2),
        "hard_stop_pct": -2.0,
        "one_min": "LONG" if row["long_bias"] >= 60 else "SHORT" if row["short_bias"] >= 60 else "NEUTRAL",
        "five_min": "LONG" if row["score"] >= 82 and row["long_bias"] >= 55 else "SHORT" if row["score"] >= 82 and row["short_bias"] >= 55 else "NEUTRAL",
        "position": position if position["symbol"] == symbol else None,
        "pnl_pct": None if pnl_pct is None else round(pnl_pct, 2),
        "position_signal": pos_status,
    }


class SelectRequest(BaseModel):
    symbol: str


class PositionRequest(BaseModel):
    symbol: str
    entry: float
    amount_krw: int = 15_000_000
    side: str = "LONG"


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/market")
def api_market():
    rows = snapshot()
    bull = int(sum(r["long_bias"] for r in rows[:8]) / 8)
    return {"clock": market_clock(), "market": {"nasdaq_bull": bull, "nasdaq_bear": 100-bull}, "top10": rows[:10]}


@app.get("/api/symbol/{symbol}")
def api_symbol(symbol: str):
    return selected_detail(symbol.upper())


@app.post("/api/select")
def api_select(req: SelectRequest):
    global selected_symbol
    selected_symbol = req.symbol.upper()
    return {"ok": True, "selected": selected_symbol}


@app.post("/api/position")
def api_position(req: PositionRequest):
    global position
    position = {
        "symbol": req.symbol.upper(),
        "entry": req.entry,
        "amount_krw": req.amount_krw,
        "side": req.side.upper(),
        "opened_at": datetime.now(timezone.utc).isoformat(),
    }
    return {"ok": True, "position": position}


@app.delete("/api/position")
def api_close_position():
    global position
    position = {"symbol": None, "entry": None, "amount_krw": 0, "side": "LONG", "opened_at": None}
    return {"ok": True}


@app.get("/health")
def health():
    return {"ok": True, "service": "day-trader-web-v1"}
