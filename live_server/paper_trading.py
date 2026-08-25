from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class PaperConfig:
    market: str
    currency: str
    initial_cash: float
    max_positions: int = 3
    max_position_fraction: float = 1.0 / 3.0
    fee_bps_each_side: float = 5.0
    slippage_bps_each_side: float = 5.0


DEFAULT_CONFIGS = {
    "KOREA": PaperConfig("KOREA", "KRW", 1_000_000.0),
    "USA": PaperConfig("USA", "USD", 1_000.0),
}


class PaperBroker:
    """Small deterministic paper broker for live engine validation.

    Design goals:
      * zero real broker/order side effects
      * fixed starting equity (KRW 1m / USD 1k by default)
      * max 3 simultaneous positions, max 1/3 equity per symbol
      * explicit fees + slippage on both sides
      * persistent SQLite ledger
      * idempotent one-open-position-per-symbol behavior

    This module does not decide *when* to trade. It only executes paper fills
    requested by a strategy and records the resulting account state.
    """

    def __init__(self, db_path: str = "daytrader.db", configs: Optional[Dict[str, PaperConfig]] = None):
        self.db_path = str(Path(db_path))
        self.configs = dict(DEFAULT_CONFIGS)
        if configs:
            self.configs.update({k.upper(): v for k, v in configs.items()})
        self._lock = threading.RLock()
        self._init_db()
        for market, cfg in self.configs.items():
            self._ensure_account(market, cfg)

    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def _init_db(self) -> None:
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS paper_accounts(
                    market TEXT PRIMARY KEY,
                    currency TEXT NOT NULL,
                    initial_cash REAL NOT NULL,
                    cash REAL NOT NULL,
                    realized_pnl REAL NOT NULL DEFAULT 0,
                    fees REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS paper_positions(
                    market TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    qty REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    entry_fill_price REAL NOT NULL,
                    entry_time TEXT NOT NULL,
                    last_price REAL NOT NULL,
                    support REAL,
                    support_updates INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL DEFAULT 'HOLD',
                    entry_reason TEXT,
                    PRIMARY KEY(market, symbol)
                );

                CREATE TABLE IF NOT EXISTS paper_trades(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    qty REAL NOT NULL,
                    signal_price REAL NOT NULL,
                    fill_price REAL NOT NULL,
                    gross_value REAL NOT NULL,
                    fee REAL NOT NULL,
                    slippage REAL NOT NULL,
                    realized_pnl REAL,
                    reason TEXT,
                    support REAL,
                    ts TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_paper_trades_market_ts
                    ON paper_trades(market, ts);
                """
            )

    def _ensure_account(self, market: str, cfg: PaperConfig) -> None:
        market = market.upper()
        with self._conn() as c:
            c.execute(
                """INSERT OR IGNORE INTO paper_accounts
                   (market,currency,initial_cash,cash,realized_pnl,fees,updated_at)
                   VALUES(?,?,?,?,0,0,?)""",
                (market, cfg.currency, cfg.initial_cash, cfg.initial_cash, _now()),
            )

    def reset(self, market: str) -> Dict[str, Any]:
        market = market.upper()
        cfg = self.configs[market]
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM paper_positions WHERE market=?", (market,))
            c.execute("DELETE FROM paper_trades WHERE market=?", (market,))
            c.execute(
                """UPDATE paper_accounts
                   SET initial_cash=?,cash=?,realized_pnl=0,fees=0,updated_at=?
                   WHERE market=?""",
                (cfg.initial_cash, cfg.initial_cash, _now(), market),
            )
        return self.account(market)

    def _costs(self, cfg: PaperConfig, signal_price: float, qty: float, side: str):
        slip_rate = cfg.slippage_bps_each_side / 10_000.0
        fee_rate = cfg.fee_bps_each_side / 10_000.0
        fill = signal_price * (1.0 + slip_rate if side == "BUY" else 1.0 - slip_rate)
        gross = fill * qty
        fee = gross * fee_rate
        slippage = abs(fill - signal_price) * qty
        return fill, gross, fee, slippage

    def enter(
        self,
        market: str,
        symbol: str,
        signal_price: float,
        strategy_id: str = "WILLIAMS_STRUCT0",
        reason: str = "WILLIAMS_ENTRY",
        support: Optional[float] = None,
    ) -> Dict[str, Any]:
        market, symbol = market.upper(), str(symbol)
        cfg = self.configs[market]
        signal_price = float(signal_price)
        if signal_price <= 0:
            return {"ok": False, "reason": "INVALID_PRICE"}

        with self._lock, self._conn() as c:
            existing = c.execute(
                "SELECT 1 FROM paper_positions WHERE market=? AND symbol=?", (market, symbol)
            ).fetchone()
            if existing:
                return {"ok": False, "reason": "ALREADY_OPEN"}

            npos = c.execute(
                "SELECT COUNT(*) n FROM paper_positions WHERE market=?", (market,)
            ).fetchone()["n"]
            if npos >= cfg.max_positions:
                return {"ok": False, "reason": "MAX_POSITIONS"}

            acct = c.execute("SELECT * FROM paper_accounts WHERE market=?", (market,)).fetchone()
            cash = float(acct["cash"])
            equity = self._equity_locked(c, market, cash)
            budget = min(cash, equity * cfg.max_position_fraction)
            if budget <= 0:
                return {"ok": False, "reason": "NO_CASH"}

            unit_est = signal_price * (1 + (cfg.slippage_bps_each_side + cfg.fee_bps_each_side) / 10_000.0)
            if market == "KOREA":
                qty = int(budget // unit_est)
            else:
                qty = int(budget // unit_est)  # whole-share paper fills for both markets
            if qty <= 0:
                return {"ok": False, "reason": "BUDGET_TOO_SMALL", "budget": budget}

            fill, gross, fee, slippage = self._costs(cfg, signal_price, qty, "BUY")
            debit = gross + fee
            while qty > 0 and debit > cash + 1e-9:
                qty -= 1
                fill, gross, fee, slippage = self._costs(cfg, signal_price, qty, "BUY")
                debit = gross + fee
            if qty <= 0:
                return {"ok": False, "reason": "NO_CASH"}

            ts = _now()
            c.execute(
                "UPDATE paper_accounts SET cash=cash-?,fees=fees+?,updated_at=? WHERE market=?",
                (debit, fee, ts, market),
            )
            c.execute(
                """INSERT INTO paper_positions
                   (market,symbol,strategy_id,qty,entry_price,entry_fill_price,entry_time,last_price,
                    support,support_updates,state,entry_reason)
                   VALUES(?,?,?,?,?,?,?,?,?,0,'HOLD',?)""",
                (market, symbol, strategy_id, qty, signal_price, fill, ts, signal_price, support, reason),
            )
            c.execute(
                """INSERT INTO paper_trades
                   (market,symbol,strategy_id,side,qty,signal_price,fill_price,gross_value,fee,slippage,
                    realized_pnl,reason,support,ts)
                   VALUES(?,?,?,?,?,?,?,?,?,?,NULL,?,?,?)""",
                (market, symbol, strategy_id, "BUY", qty, signal_price, fill, gross, fee, slippage, reason, support, ts),
            )

        return {"ok": True, "action": "BUY", "market": market, "symbol": symbol, "qty": qty,
                "signal_price": signal_price, "fill_price": round(fill, 6), "fee": round(fee, 6),
                "slippage": round(slippage, 6), "reason": reason}

    def mark(
        self,
        market: str,
        symbol: str,
        price: float,
        support: Optional[float] = None,
        support_updates: Optional[int] = None,
        state: Optional[str] = None,
    ) -> bool:
        market, symbol = market.upper(), str(symbol)
        price = float(price)
        with self._lock, self._conn() as c:
            row = c.execute(
                "SELECT support,support_updates FROM paper_positions WHERE market=? AND symbol=?",
                (market, symbol),
            ).fetchone()
            if not row:
                return False
            old_support = row["support"]
            new_support = old_support
            if support is not None:
                support = float(support)
                if old_support is None or support > float(old_support):
                    new_support = support
            new_updates = int(row["support_updates"] or 0)
            if support_updates is not None:
                new_updates = max(new_updates, int(support_updates))
            c.execute(
                """UPDATE paper_positions
                   SET last_price=?,support=?,support_updates=?,state=COALESCE(?,state)
                   WHERE market=? AND symbol=?""",
                (price, new_support, new_updates, state, market, symbol),
            )
        return True

    def exit(
        self,
        market: str,
        symbol: str,
        signal_price: float,
        reason: str = "SUPPORT_BREAK_EXIT",
        support: Optional[float] = None,
    ) -> Dict[str, Any]:
        market, symbol = market.upper(), str(symbol)
        cfg = self.configs[market]
        signal_price = float(signal_price)
        with self._lock, self._conn() as c:
            pos = c.execute(
                "SELECT * FROM paper_positions WHERE market=? AND symbol=?", (market, symbol)
            ).fetchone()
            if not pos:
                return {"ok": False, "reason": "NO_POSITION"}
            qty = float(pos["qty"])
            fill, gross, fee, slippage = self._costs(cfg, signal_price, qty, "SELL")
            entry_cost = float(pos["entry_fill_price"]) * qty
            realized = gross - fee - entry_cost
            ts = _now()
            c.execute(
                """UPDATE paper_accounts
                   SET cash=cash+?,realized_pnl=realized_pnl+?,fees=fees+?,updated_at=?
                   WHERE market=?""",
                (gross - fee, realized, fee, ts, market),
            )
            c.execute(
                """INSERT INTO paper_trades
                   (market,symbol,strategy_id,side,qty,signal_price,fill_price,gross_value,fee,slippage,
                    realized_pnl,reason,support,ts)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (market, symbol, pos["strategy_id"], "SELL", qty, signal_price, fill, gross, fee, slippage,
                 realized, reason, support if support is not None else pos["support"], ts),
            )
            c.execute("DELETE FROM paper_positions WHERE market=? AND symbol=?", (market, symbol))

        return {"ok": True, "action": "SELL", "market": market, "symbol": symbol, "qty": qty,
                "signal_price": signal_price, "fill_price": round(fill, 6), "fee": round(fee, 6),
                "slippage": round(slippage, 6), "realized_pnl": round(realized, 6), "reason": reason}

    def _equity_locked(self, c: sqlite3.Connection, market: str, cash: float) -> float:
        rows = c.execute(
            "SELECT qty,last_price FROM paper_positions WHERE market=?", (market,)
        ).fetchall()
        return cash + sum(float(r["qty"]) * float(r["last_price"]) for r in rows)

    def account(self, market: str) -> Dict[str, Any]:
        market = market.upper()
        with self._lock, self._conn() as c:
            a = c.execute("SELECT * FROM paper_accounts WHERE market=?", (market,)).fetchone()
            if not a:
                raise KeyError(market)
            positions = [dict(r) for r in c.execute(
                "SELECT * FROM paper_positions WHERE market=? ORDER BY entry_time", (market,)
            ).fetchall()]
            cash = float(a["cash"])
            market_value = sum(float(p["qty"]) * float(p["last_price"]) for p in positions)
            equity = cash + market_value
            initial = float(a["initial_cash"])
            unrealized = sum(
                (float(p["last_price"]) - float(p["entry_fill_price"])) * float(p["qty"])
                for p in positions
            )
            return {
                "market": market,
                "currency": a["currency"],
                "initial_cash": initial,
                "cash": round(cash, 6),
                "market_value": round(market_value, 6),
                "equity": round(equity, 6),
                "return_pct": round((equity / initial - 1) * 100, 4) if initial else 0.0,
                "realized_pnl": round(float(a["realized_pnl"]), 6),
                "unrealized_pnl": round(unrealized, 6),
                "fees": round(float(a["fees"]), 6),
                "positions": positions,
                "position_count": len(positions),
                "updated_at": a["updated_at"],
            }

    def trades(self, market: str, limit: int = 100) -> List[Dict[str, Any]]:
        market = market.upper()
        limit = max(1, min(int(limit), 1000))
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM paper_trades WHERE market=? ORDER BY id DESC LIMIT ?", (market, limit)
            ).fetchall()]

    def status(self) -> Dict[str, Any]:
        return {m: self.account(m) for m in self.configs}
