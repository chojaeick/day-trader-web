from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class MockBrokerConfig:
    app_key: str
    app_secret: str
    rest_base: str = "https://mockapi.kiwoom.com"
    order_enable: bool = False
    expected_account_last4: str = "2300"

    @classmethod
    def from_env(cls) -> "MockBrokerConfig":
        key = os.getenv("KIWOOM_KR_MOCK_APP_KEY", "").strip()
        secret = os.getenv("KIWOOM_KR_MOCK_APP_SECRET", "").strip()
        base = os.getenv("KIWOOM_KR_MOCK_REST_BASE", os.getenv("KIWOOM_MOCK_REST_BASE", "https://mockapi.kiwoom.com")).strip().rstrip("/")
        enabled = os.getenv("KIWOOM_MOCK_ORDER_ENABLE", "0").lower() in ("1", "true", "yes", "on")
        expected = os.getenv("KIWOOM_KR_MOCK_EXPECTED_ACCOUNT_LAST4", "2300").strip()
        if not key or not secret:
            raise RuntimeError("KIWOOM_KR_MOCK_APP_KEY / KIWOOM_KR_MOCK_APP_SECRET not set")
        if "mockapi.kiwoom.com" not in base:
            raise RuntimeError(f"Refusing non-mock REST base: {base}")
        return cls(key, secret, base, enabled, expected)


class KiwoomMockBroker:
    """Kiwoom domestic-stock mock-investment broker adapter.

    Safety invariants:
      * only KIWOOM_KR_MOCK_* credentials are accepted
      * only mockapi.kiwoom.com is accepted
      * order placement is disabled unless KIWOOM_MOCK_ORDER_ENABLE=1
      * domestic mock orders are forced to KRX
      * domestic account identity is checked before every order
    """

    def __init__(self, config: MockBrokerConfig | None = None):
        self.cfg = config or MockBrokerConfig.from_env()
        self.token: str | None = None
        self._validated_account: str | None = None

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
            raise RuntimeError(f"KR mock token failed: {d}")
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

    def account_number(self) -> str:
        d = self._post("/api/dostk/acnt", "ka00001", {})
        acct = str(d.get("acctNo") or d.get("acct_no") or "").strip()
        if not acct:
            raise RuntimeError(f"KR mock account number missing: {d}")
        return acct

    def validate_account(self) -> str:
        if self._validated_account:
            return self._validated_account
        acct = self.account_number()
        digits = "".join(ch for ch in acct if ch.isdigit())
        expected = "".join(ch for ch in self.cfg.expected_account_last4 if ch.isdigit())[-4:]
        if expected and digits[-4:] != expected:
            raise RuntimeError(
                f"KR mock account mismatch: expected last4={expected}, got last4={digits[-4:]}"
            )
        self._validated_account = acct
        return acct

    def request_account(self, api_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Low-level safe account-query helper for documented domestic account TRs."""
        self.validate_account()
        return self._post("/api/dostk/acnt", api_id, body or {})

    def _ensure_order_enabled(self) -> None:
        if not self.cfg.order_enable:
            raise RuntimeError(
                "KR mock order placement is disabled. Set KIWOOM_MOCK_ORDER_ENABLE=1 only after account-query validation."
            )
        self.validate_account()

    def buy_market(self, symbol: str, qty: int) -> dict[str, Any]:
        self._ensure_order_enabled()
        if int(qty) <= 0:
            raise ValueError("qty must be > 0")
        return self._post(
            "/api/dostk/ordr",
            "kt10000",
            {
                "dmst_stex_tp": "KRX",
                "stk_cd": str(symbol).zfill(6),
                "ord_qty": str(int(qty)),
                "ord_uv": "",
                "trde_tp": "3",
            },
        )

    def sell_market(self, symbol: str, qty: int) -> dict[str, Any]:
        self._ensure_order_enabled()
        if int(qty) <= 0:
            raise ValueError("qty must be > 0")
        return self._post(
            "/api/dostk/ordr",
            "kt10001",
            {
                "dmst_stex_tp": "KRX",
                "stk_cd": str(symbol).zfill(6),
                "ord_qty": str(int(qty)),
                "ord_uv": "",
                "trde_tp": "3",
            },
        )
