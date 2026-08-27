from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class USMockBrokerConfig:
    app_key: str
    app_secret: str
    rest_base: str = "https://mockapi.kiwoom.com"
    order_enable: bool = False

    @classmethod
    def from_env(cls) -> "USMockBrokerConfig":
        key = os.getenv("KIWOOM_MOCK_APP_KEY", "").strip()
        secret = os.getenv("KIWOOM_MOCK_APP_SECRET", "").strip()
        base = os.getenv("KIWOOM_MOCK_REST_BASE", "https://mockapi.kiwoom.com").strip().rstrip("/")
        enabled = os.getenv("KIWOOM_MOCK_US_ORDER_ENABLE", "0").lower() in ("1", "true", "yes", "on")
        if not key or not secret:
            raise RuntimeError("KIWOOM_MOCK_APP_KEY / KIWOOM_MOCK_APP_SECRET not set")
        if "mockapi.kiwoom.com" not in base:
            raise RuntimeError(f"Refusing non-mock REST base: {base}")
        return cls(key, secret, base, enabled)


class KiwoomUSMockBroker:
    """Kiwoom US-stock mock-investment REST adapter.

    Safety invariants:
      * mock credentials only
      * mockapi.kiwoom.com only
      * orders blocked unless KIWOOM_MOCK_US_ORDER_ENABLE=1
      * US order endpoint only (/api/us/ordr)
    """

    def __init__(self, config: USMockBrokerConfig | None = None):
        self.cfg = config or USMockBrokerConfig.from_env()
        self.token: str | None = None

    def get_token(self) -> str:
        r = requests.post(
            self.cfg.rest_base + "/oauth2/token",
            json={
                "grant_type": "client_credentials",
                "appkey": self.cfg.app_key,
                "secretkey": self.cfg.app_secret,
            },
            headers={"Content-Type": "application/json;charset=UTF-8"},
            timeout=15,
        )
        r.raise_for_status()
        d = r.json()
        if d.get("return_code") not in (None, 0) or not d.get("token"):
            raise RuntimeError(f"mock token failed: {d}")
        self.token = d["token"]
        return self.token

    def _headers(self, api_id: str) -> dict[str, str]:
        if not self.token:
            self.get_token()
        return {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.token}",
            "api-id": api_id,
        }

    def _post(self, path: str, api_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        r = requests.post(
            self.cfg.rest_base + path,
            headers=self._headers(api_id),
            json=body or {},
            timeout=15,
        )
        r.raise_for_status()
        d = r.json()
        if d.get("return_code") not in (None, 0):
            raise RuntimeError(f"{api_id} failed: {d}")
        return d

    def _ensure_order_enabled(self) -> None:
        if not self.cfg.order_enable:
            raise RuntimeError("US mock orders disabled. Set KIWOOM_MOCK_US_ORDER_ENABLE=1 for the round-trip test only.")

    @staticmethod
    def _check_exchange(exchange: str) -> str:
        ex = str(exchange).upper().strip()
        if ex not in {"NY", "ND", "NA"}:
            raise ValueError(f"unsupported US exchange code: {ex}")
        return ex

    def balance(self, symbol: str = "", exchange: str = "NY") -> dict[str, Any]:
        ex = self._check_exchange(exchange)
        return self._post("/api/us/acnt", "ust21070", {"stex_tp": ex, "stk_cd": str(symbol).upper().strip()})

    def buy_market(self, symbol: str, qty: int = 1, exchange: str = "NY") -> dict[str, Any]:
        self._ensure_order_enabled()
        ex = self._check_exchange(exchange)
        if int(qty) <= 0:
            raise ValueError("qty must be > 0")
        return self._post("/api/us/ordr", "ust20000", {
            "stex_tp": ex,
            "stk_cd": str(symbol).upper().strip(),
            "ord_qty": str(int(qty)),
            "ord_uv": "",
            "trde_tp": "03",
        })

    def sell_market(self, symbol: str, qty: int = 1, exchange: str = "NY") -> dict[str, Any]:
        self._ensure_order_enabled()
        ex = self._check_exchange(exchange)
        if int(qty) <= 0:
            raise ValueError("qty must be > 0")
        return self._post("/api/us/ordr", "ust20001", {
            "stex_tp": ex,
            "stk_cd": str(symbol).upper().strip(),
            "ord_qty": str(int(qty)),
            "ord_uv": "",
            "trde_tp": "03",
        })
