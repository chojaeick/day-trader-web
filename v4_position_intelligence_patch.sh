#!/usr/bin/env bash
set -euo pipefail

cd "${HOME}/day-trader-api-repo"

ENGINE="live_server/v4_engine.py"
MOD="live_server/position_intelligence.py"
CFG="live_server/position_portfolio.json"
TS="$(date +%Y%m%d_%H%M%S)"

test -f "$ENGINE" || { echo "ERROR: $ENGINE not found"; exit 1; }

cp "$ENGINE" "${ENGINE}.pre_position_intelligence_${TS}.bak"

cat > "$MOD" <<'PY'
from __future__ import annotations
import json
from pathlib import Path

CFG_PATH = Path(__file__).with_name("position_portfolio.json")

DEFAULT_CFG = {
    "total_capital": 0.0,
    "available_cash": None,
    "max_position_pct": 15.0,
    "max_add_pct": 5.0,
    "risk_per_trade_pct": 0.75,
    "average_down_enabled": False,
}

def _f(v, d=0.0):
    try:
        return float(v)
    except Exception:
        return float(d)

def _clamp(v, lo, hi):
    return max(lo, min(hi, v))

def load_portfolio_config():
    cfg = dict(DEFAULT_CFG)
    try:
        if CFG_PATH.exists():
            raw = json.loads(CFG_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cfg.update(raw)
    except Exception:
        pass
    return cfg

def save_portfolio_config(payload):
    cfg = load_portfolio_config()
    for k in DEFAULT_CFG:
        if k in payload:
            cfg[k] = payload[k]
    CFG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg

def build_position_intelligence(
    *,
    price,
    entry,
    qty,
    power,
    power_delta,
    entry_power=0,
    peak_power=None,
    current_floor=0,
    warning_floor=0,
    hard_floor=0,
    high_watermark=0,
    target1=0,
    target2=0,
    vwap=0,
    total_capital=0,
    available_cash=None,
    max_position_pct=15.0,
    max_add_pct=5.0,
    risk_per_trade_pct=0.75,
    average_down_enabled=False,
):
    price = _f(price)
    entry = _f(entry)
    qty = _f(qty)
    power = _f(power)
    dpower = _f(power_delta)
    entry_power = _f(entry_power, power)
    peak_power = _f(peak_power, max(power, entry_power))
    high = max(_f(high_watermark, price), price)
    hard = _f(hard_floor)
    warn = _f(warning_floor)
    old_floor = max(_f(current_floor), hard)

    if not price or not entry or qty <= 0:
        return {"enabled": False, "reason": "position data incomplete"}

    R = max(entry - hard, entry * 0.004) if hard else entry * 0.004
    pnl_pct = (price / entry - 1.0) * 100.0
    pnl_usd = (price - entry) * qty
    position_value = price * qty

    # ---- Dynamic Ceiling -------------------------------------------------
    # Strong/rising power -> allow expansion.
    # Weak/falling power -> bring the ceiling closer to current price.
    if power >= 70 and dpower >= 0:
        ceiling_mode = "EXPAND_FAST"
        ceiling = max(_f(target2), price + 1.50 * R, high + 0.75 * R)
    elif power >= 55 and dpower >= -3:
        ceiling_mode = "EXPAND"
        ceiling = max(_f(target1), price + 1.00 * R)
    elif power >= 40 and dpower > -8:
        ceiling_mode = "NORMAL"
        base = _f(target1, price + 0.80 * R)
        ceiling = max(price + 0.55 * R, min(base, price + 1.00 * R))
    else:
        ceiling_mode = "COMPRESS"
        ceiling = max(price + 0.25 * R, min(_f(target1, price + 0.50 * R), price + 0.55 * R))

    # Peak-power loss accelerates profit protection.
    power_drop = max(0.0, peak_power - power)

    # ---- Dynamic Flooring ------------------------------------------------
    if power >= 70 and dpower >= 0 and power_drop < 10:
        floor_mode = "RUN"
        candidate_floor = high - 1.00 * R
    elif power >= 55 and dpower >= -5 and power_drop < 15:
        floor_mode = "TRAIL"
        candidate_floor = high - 0.75 * R
    elif power >= 40 and dpower > -10 and power_drop < 20:
        floor_mode = "PROTECT"
        candidate_floor = high - 0.50 * R
    else:
        floor_mode = "TIGHTEN"
        candidate_floor = high - 0.30 * R

    if pnl_pct > 0:
        candidate_floor = max(candidate_floor, entry - 0.10 * R)
    if pnl_pct >= 0.50:
        candidate_floor = max(candidate_floor, entry + 0.10 * R)

    # Floor may rise, never loosen downward.
    dynamic_floor = max(old_floor, candidate_floor)
    if warn:
        dynamic_warning = max(warn, dynamic_floor)
    else:
        dynamic_warning = dynamic_floor

    # ---- Portfolio sizing ------------------------------------------------
    total_capital = _f(total_capital)
    max_position_pct = _clamp(_f(max_position_pct, 15), 1, 100)
    max_add_pct = _clamp(_f(max_add_pct, 5), 0, 100)
    risk_per_trade_pct = _clamp(_f(risk_per_trade_pct, .75), .05, 10)

    if available_cash is None:
        cash = max(0.0, total_capital - position_value) if total_capital > 0 else 0.0
    else:
        cash = max(0.0, _f(available_cash))

    max_position_value = total_capital * max_position_pct / 100.0 if total_capital > 0 else 0.0
    exposure_pct = position_value / total_capital * 100.0 if total_capital > 0 else None
    exposure_room = max(0.0, max_position_value - position_value) if total_capital > 0 else 0.0
    add_cap = min(
        exposure_room,
        cash,
        total_capital * max_add_pct / 100.0 if total_capital > 0 else 0.0,
    )
    risk_budget = total_capital * risk_per_trade_pct / 100.0 if total_capital > 0 else 0.0
    risk_qty_cap = int(risk_budget / max(price - dynamic_floor, R * 0.20)) if risk_budget > 0 else 0

    # ---- Action logic -----------------------------------------------------
    above_vwap = (not vwap) or price >= _f(vwap)
    below_entry = price < entry
    breakout_strength = power >= 65 and dpower >= 3 and above_vwap and not below_entry
    reclaim_strength = power >= 55 and dpower >= 8 and above_vwap and below_entry

    add_winner_qty = 0
    avg_down_qty = 0

    if breakout_strength and add_cap > 0:
        add_winner_qty = max(0, min(int(add_cap / price), risk_qty_cap or int(add_cap / price)))

    if average_down_enabled and reclaim_strength and add_cap > 0 and price > hard:
        avg_down_qty = max(0, min(int((add_cap * 0.50) / price), risk_qty_cap or int((add_cap * 0.50) / price)))

    distance_to_ceiling_pct = (ceiling / price - 1.0) * 100.0
    distance_to_floor_pct = (price / dynamic_floor - 1.0) * 100.0 if dynamic_floor > 0 else None

    if hard and price <= hard:
        action = "HARD_EXIT"
        exit_pct = 100
        reason = "Hard floor broken"
    elif price <= dynamic_floor:
        action = "EXIT"
        exit_pct = 100
        reason = "Dynamic floor broken"
    elif (power < 35 and dpower <= -10) or power_drop >= 25:
        action = "PROFIT_PROTECT" if pnl_pct > 0 else "EXIT_WATCH"
        exit_pct = 50 if pnl_pct > 0 else 0
        reason = "Power loss / peak-power drawdown"
    elif distance_to_ceiling_pct <= 0.25 and dpower < 0:
        action = "TAKE_PROFIT"
        exit_pct = 30 if power >= 50 else 50
        reason = "Ceiling near + momentum weakening"
    elif add_winner_qty > 0:
        action = "ADD_WINNER"
        exit_pct = 0
        reason = "Strong/rising power + capital room"
    elif avg_down_qty > 0:
        action = "AVERAGE_DOWN"
        exit_pct = 0
        reason = "Reclaim confirmed + portfolio room"
    else:
        action = "HOLD"
        exit_pct = 0
        reason = "Structure remains inside dynamic floor/ceiling"

    return {
        "enabled": True,
        "action": action,
        "reason": reason,
        "entry": round(entry, 4),
        "price": round(price, 4),
        "qty": qty,
        "pnl_usd": round(pnl_usd, 2),
        "pnl_pct": round(pnl_pct, 3),
        "power": round(power, 1),
        "power_delta": round(dpower, 1),
        "power_drop_from_peak": round(power_drop, 1),
        "ceiling": round(ceiling, 4),
        "ceiling_mode": ceiling_mode,
        "floor": round(dynamic_floor, 4),
        "warning_floor": round(dynamic_warning, 4),
        "hard_floor": round(hard, 4) if hard else None,
        "floor_mode": floor_mode,
        "distance_to_ceiling_pct": round(distance_to_ceiling_pct, 3),
        "distance_to_floor_pct": round(distance_to_floor_pct, 3) if distance_to_floor_pct is not None else None,
        "portfolio": {
            "total_capital": round(total_capital, 2),
            "available_cash": round(cash, 2),
            "position_value": round(position_value, 2),
            "exposure_pct": round(exposure_pct, 2) if exposure_pct is not None else None,
            "max_position_pct": max_position_pct,
            "risk_budget": round(risk_budget, 2),
        },
        "orders": {
            "exit_pct": exit_pct,
            "add_winner_qty": add_winner_qty,
            "add_winner_usd": round(add_winner_qty * price, 2),
            "average_down_enabled": bool(average_down_enabled),
            "average_down_qty": avg_down_qty,
            "average_down_usd": round(avg_down_qty * price, 2),
        },
        "guards": {
            "manual_order_only": True,
            "auto_order": False,
            "above_vwap": bool(above_vwap),
        },
    }
PY

if [ ! -f "$CFG" ]; then
cat > "$CFG" <<'JSON'
{
  "total_capital": 0,
  "available_cash": null,
  "max_position_pct": 15,
  "max_add_pct": 5,
  "risk_per_trade_pct": 0.75,
  "average_down_enabled": false
}
JSON
fi

python3 - <<'PY'
from pathlib import Path

p = Path("live_server/v4_engine.py")
s = p.read_text(encoding="utf-8")

imp = "from .position_intelligence import build_position_intelligence, load_portfolio_config\n"
if imp not in s:
    lines = s.splitlines(True)
    insert_at = 0
    for i, line in enumerate(lines):
        if line.startswith("from .") or line.startswith("import ") or line.startswith("from "):
            insert_at = i + 1
    lines.insert(insert_at, imp)
    s = "".join(lines)

marker = "        return {'market':'USA','symbol':sym"
if marker not in s:
    raise SystemExit("SAFE STOP: USA tracker return marker not found. Engine left backed up.")

if "position_intelligence=build_position_intelligence(" not in s:
    block = """        position_intelligence=None
        if pos and price:
            try:
                _pcfg=load_portfolio_config()
                position_intelligence=build_position_intelligence(
                    price=price,
                    entry=_f(pos.get('avg_entry')),
                    qty=_f(pos.get('qty')),
                    power=power,
                    power_delta=delta,
                    entry_power=_f(pos.get('entry_power'),power),
                    peak_power=max(_f(pos.get('entry_power'),power),power),
                    current_floor=_f((position_gate or {}).get('current_floor') or (position_gate or {}).get('hard_floor')),
                    warning_floor=_f((position_gate or {}).get('warning_floor')),
                    hard_floor=_f((position_gate or {}).get('hard_floor')),
                    high_watermark=_f(pos.get('high_watermark'),price),
                    target1=t1,
                    target2=t2,
                    vwap=vwap,
                    total_capital=_pcfg.get('total_capital',0),
                    available_cash=_pcfg.get('available_cash'),
                    max_position_pct=_pcfg.get('max_position_pct',15),
                    max_add_pct=_pcfg.get('max_add_pct',5),
                    risk_per_trade_pct=_pcfg.get('risk_per_trade_pct',.75),
                    average_down_enabled=_pcfg.get('average_down_enabled',False),
                )
            except Exception as _pie:
                position_intelligence={'enabled':False,'error':str(_pie)}
"""
    s = s.replace(marker, block + marker, 1)

needle = "'position_gate':position_gate,"
if needle in s and "'position_intelligence':position_intelligence," not in s:
    s = s.replace(
        needle,
        needle + "'position_intelligence':position_intelligence,",
        1,
    )
else:
    if "'position_intelligence':position_intelligence," not in s:
        raise SystemExit("SAFE STOP: position_gate return field not found; backup retained.")

p.write_text(s, encoding="utf-8")
print("PATCHED:", p)
PY

python3 -m py_compile "$MOD" "$ENGINE"

echo
echo "=== V4 POSITION INTELLIGENCE PATCH OK ==="
echo "Backup: ${ENGINE}.pre_position_intelligence_${TS}.bak"
echo "Config: $CFG"
echo
echo "Set total capital example:"
echo "  python3 - <<'PY'"
echo "from live_server.position_intelligence import save_portfolio_config"
echo "print(save_portfolio_config({'total_capital':100000,'available_cash':50000}))"
echo "PY"
echo
echo "Then restart the existing API service using the SAME command/service you normally use."
