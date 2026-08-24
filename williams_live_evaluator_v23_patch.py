#!/usr/bin/env python3
"""
Williams live evaluator V23 patch.
Adds a validation-candidate live evaluator without enabling trading or selection.

Rule:
1) Williams raw arm:
   trigger = day_open + 0.5 * (prev_day_high - prev_day_low)
   CrossUp(current_price, trigger)
   RSI(2) > 50
2) Within 30 minutes after raw arm, Finder rank <= 20 confirms.
3) Signal entry candidate at first 1m bar after Finder confirmation.
4) One signal per symbol per KST trading day.
5) Validation status only; no order submission.
"""
from pathlib import Path
import re

TARGET = Path("live_server/v4_engine.py")
if not TARGET.exists():
    raise SystemExit(f"TARGET_NOT_FOUND {TARGET}")

src = TARGET.read_text()

marker = "# === WILLIAMS LIVE EVALUATOR V23 ==="
if marker in src:
    print("ALREADY_PATCHED")
    raise SystemExit(0)

append = r'''

# === WILLIAMS LIVE EVALUATOR V23 ===
from datetime import datetime as _dt, timezone as _tz, timedelta as _td
from collections import defaultdict as _dd

_WILLIAMS_KST = _tz(_td(hours=9))
_WILLIAMS_STATE = _dd(dict)

def _williams_rsi2(closes):
    if len(closes) < 3:
        return None
    gains = []
    losses = []
    for i in range(1, 3):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains) / 2.0
    al = sum(losses) / 2.0
    r = 100.0 if al == 0 else 100.0 - (100.0 / (1.0 + ag / al))
    for i in range(3, len(closes)):
        d = closes[i] - closes[i-1]
        g = max(d, 0.0)
        l = max(-d, 0.0)
        ag = (ag + g) / 2.0
        al = (al + l) / 2.0
        r = 100.0 if al == 0 else 100.0 - (100.0 / (1.0 + ag / al))
    return r

def williams_live_evaluate_v23(
    symbol,
    prev_day_high,
    prev_day_low,
    day_open,
    prev_price,
    current_price,
    recent_closes,
    finder_rank=None,
    now=None,
):
    """
    Returns a pure evaluation dict.
    Does not place orders and does not mutate broker state.
    """
    now = now or _dt.now(_WILLIAMS_KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_WILLIAMS_KST)
    else:
        now = now.astimezone(_WILLIAMS_KST)

    day_key = now.strftime("%Y%m%d")
    trigger = float(day_open) + 0.5 * (float(prev_day_high) - float(prev_day_low))
    rsi2 = _williams_rsi2([float(x) for x in recent_closes])

    st = _WILLIAMS_STATE[(str(symbol), day_key)]
    armed_at = st.get("armed_at")
    sent = bool(st.get("signal_sent"))

    raw_cross = (
        float(prev_price) <= trigger < float(current_price)
        and rsi2 is not None
        and rsi2 > 50.0
    )

    if raw_cross and armed_at is None and not sent:
        armed_at = now
        st["armed_at"] = now

    age_min = None
    if armed_at is not None:
        age_min = (now - armed_at).total_seconds() / 60.0
        if age_min > 30.0 and not sent:
            st.pop("armed_at", None)
            armed_at = None
            age_min = None

    finder_ok = finder_rank is not None and int(finder_rank) <= 20
    signal = bool(
        armed_at is not None
        and age_min is not None
        and 0.0 <= age_min <= 30.0
        and finder_ok
        and not sent
    )

    if signal:
        st["signal_sent"] = True
        st["confirmed_at"] = now

    if sent:
        stage = "SIGNAL_SENT"
    elif signal:
        stage = "ENTRY_CANDIDATE"
    elif armed_at is not None:
        stage = "READY"
    else:
        stage = "WATCH"

    return {
        "engine_id": "williams",
        "engine_name": "윌리암스",
        "status": "VALIDATION_CANDIDATE",
        "selectable": False,
        "orders_enabled": False,
        "symbol": str(symbol),
        "trigger": trigger,
        "rsi2": rsi2,
        "raw_cross": raw_cross,
        "finder_rank": finder_rank,
        "finder_confirmed": finder_ok,
        "armed_at": armed_at.isoformat() if armed_at else None,
        "age_min": age_min,
        "stage": stage,
        "signal": signal,
        "rule": "CrossUp(day_open+0.5*(prev_high-prev_low)) & RSI2>50 -> Finder rank<=20 within 30m -> first next 1m bar entry candidate -> 5m validation hold",
        "max_one_signal_per_symbol_day": True,
    }

'''

TARGET.write_text(src + append)
print("PATCH_OK", TARGET)
