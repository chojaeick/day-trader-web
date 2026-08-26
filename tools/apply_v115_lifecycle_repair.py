#!/usr/bin/env python3
"""Apply DAY TRADER V115 mock lifecycle safety repair to the current runtime tree.

This script intentionally patches the files already present on the server instead of
replacing them with the older GitHub branch copies.

V115 scope (mock investment only):
- process-shared OAuth token lock + double-checked reuse
- restore mock holdings from Kiwoom kt00004/ka10076 once per process
- count actual restored holdings toward the 5-position limit
- block duplicate BUY after restart because restored symbols are in_pos
- make the -1.5% emergency stop independent of Williams EXIT_READY
- retain the >=5 minute hold for ordinary structural exits

The script makes .bak_v115 backups and never starts/restarts systemd services.
"""
from __future__ import annotations

from pathlib import Path
import py_compile
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "live_server" / "v4_engine.py"
BROKER = ROOT / "live_server" / "kiwoom_mock_broker.py"


def fail(msg: str) -> None:
    raise SystemExit(f"V115_ABORT: {msg}")


def backup(path: Path) -> None:
    dst = path.with_name(path.name + ".bak_v115")
    if not dst.exists():
        shutil.copy2(path, dst)
        print(f"BACKUP {dst.relative_to(ROOT)}")


def patch_broker() -> None:
    if not BROKER.exists():
        fail(f"missing {BROKER}")
    backup(BROKER)
    s = BROKER.read_text()

    if "_token_lock = threading.Lock()" in s and "with cls._token_lock:" in s:
        print("BROKER_ALREADY_V115")
        return

    if "import threading\n" not in s:
        anchor = "import requests\n"
        if anchor not in s:
            fail("broker import anchor not found")
        s = s.replace(anchor, anchor + "import threading\n", 1)

    # V114 may have comments/spacing/type annotation differences around
    # _shared_token. Insert the lock immediately after that declaration.
    if "_token_lock = threading.Lock()" not in s:
        pat = re.compile(r"^(\s+_shared_token\s*(?::[^=\n]+)?=\s*None\s*)$", re.M)
        m = pat.search(s)
        if not m:
            fail("broker _shared_token declaration not found")
        indent = re.match(r"\s*", m.group(1)).group(0)
        s = s[:m.end()] + f"\n{indent}_token_lock = threading.Lock()" + s[m.end():]

    # Replace only the get_token method body, bounded by the next method.
    if "with cls._token_lock:" not in s:
        start = s.find("    def get_token(self) -> str:\n")
        if start < 0:
            fail("broker get_token start not found")
        end = s.find("\n    def ", start + 5)
        if end < 0:
            fail("broker get_token end not found")
        new = '''    def get_token(self) -> str:\n        cls = type(self)\n\n        if self.token:\n            return self.token\n        if cls._shared_token:\n            self.token = cls._shared_token\n            return self.token\n\n        with cls._token_lock:\n            if cls._shared_token:\n                self.token = cls._shared_token\n                return self.token\n\n            r = requests.post(\n                self.cfg.rest_base + "/oauth2/token",\n                json={\n                    "grant_type": "client_credentials",\n                    "appkey": self.cfg.app_key,\n                    "secretkey": self.cfg.app_secret,\n                },\n                headers={"Content-Type": "application/json;charset=UTF-8"},\n                timeout=15,\n            )\n            r.raise_for_status()\n            d = r.json()\n            if d.get("return_code") not in (None, 0) or not d.get("token"):\n                raise RuntimeError(f"mock token failed: {d}")\n            self.token = d["token"]\n            cls._shared_token = self.token\n            return self.token\n'''
        s = s[:start] + new + s[end+1:]

    BROKER.write_text(s)
    print("BROKER_PATCHED")


def patch_engine() -> None:
    if not ENGINE.exists():
        fail(f"missing {ENGINE}")
    backup(ENGINE)
    s = ENGINE.read_text()

    if "self._williams_mock_account_synced=False" not in s:
        old_init = "self._kr_gate_cache={}; self._lock=threading.RLock(); self.paper=PaperBroker(db_path)"
        new_init = "self._kr_gate_cache={}; self._lock=threading.RLock(); self._williams_mock_account_synced=False; self.paper=PaperBroker(db_path)"
        if old_init not in s:
            fail("engine __init__ V114 anchor not found")
        s = s.replace(old_init, new_init, 1)

    helper_marker = "    def _williams_mock_auto_step(self, row):\n"
    if "    def _williams_mock_sync_account(self, broker):\n" not in s:
        helper = '''    def _williams_mock_sync_account(self, broker):\n        """V115: restore current Kiwoom mock holdings once per process."""\n        if getattr(self, "_williams_mock_account_synced", False):\n            return\n\n        from datetime import datetime as _dt\n\n        bal = broker.request_account(\n            "kt00004",\n            {"qry_tp":"0", "dmst_stex_tp":"KRX"},\n        )\n        fills = broker.request_account(\n            "ka10076",\n            {"qry_tp":"0", "sell_tp":"0", "stex_tp":"1"},\n        )\n\n        latest_buy = {}\n        for x in fills.get("cntr", []) or []:\n            if "+매수" not in str(x.get("io_tp_nm") or ""):\n                continue\n            sym = str(x.get("stk_cd") or "").replace("A", "").zfill(6)\n            tm = str(x.get("ord_tm") or "").strip()\n            if sym and tm and sym not in latest_buy:\n                latest_buy[sym] = tm\n\n        now = _dt.now(_WILLIAMS_KST)\n        restored = 0\n        for x in bal.get("stk_acnt_evlt_prst", []) or []:\n            sym = str(x.get("stk_cd") or "").replace("A", "").zfill(6)\n            try:\n                qty = int(str(x.get("rmnd_qty") or "0").replace(",", ""))\n            except Exception:\n                qty = 0\n            if not sym or qty <= 0:\n                continue\n            try:\n                avg = float(str(x.get("avg_prc") or "0").replace(",", ""))\n            except Exception:\n                avg = 0.0\n\n            entered_ts = 0.0\n            tm = latest_buy.get(sym)\n            if tm and len(tm) >= 6:\n                try:\n                    entered = now.replace(\n                        hour=int(tm[0:2]),\n                        minute=int(tm[2:4]),\n                        second=int(tm[4:6]),\n                        microsecond=0,\n                    )\n                    entered_ts = entered.timestamp()\n                except Exception:\n                    entered_ts = 0.0\n\n            self._last[("WILLIAMS_MOCK", sym)] = {\n                "in_pos": True,\n                "qty": qty,\n                "entry_price": avg,\n                "entered_ts": entered_ts,\n                "synced_from_account": True,\n            }\n            restored += 1\n\n        self._williams_mock_account_synced = True\n        import logging as _logging\n        _logging.warning("WILLIAMS_MOCK_ACCOUNT_SYNC open_positions=%s", restored)\n\n'''
        if helper_marker not in s:
            fail("engine auto-step marker not found")
        s = s.replace(helper_marker, helper + helper_marker, 1)

    old_broker = '''            b=KiwoomMockBroker()\n            if not b.cfg.order_enable:\n                return\n            key=("WILLIAMS_MOCK",sym)\n'''
    new_broker = '''            b=KiwoomMockBroker()\n            if not b.cfg.order_enable:\n                return\n            self._williams_mock_sync_account(b)\n            key=("WILLIAMS_MOCK",sym)\n'''
    if "self._williams_mock_sync_account(b)" not in s:
        if old_broker not in s:
            fail("engine broker creation anchor not found")
        s = s.replace(old_broker, new_broker, 1)

    old_exit = '''            elif exit_ready and in_pos:\n                import time as _time\n                qty=max(1,int(_f(st.get("qty"),1)))\n                entry_price=_f(st.get("entry_price"))\n                price=_f(row.get("price"))\n                entered_ts=_f(st.get("entered_ts"))\n                hold_sec=(_time.time()-entered_ts) if entered_ts else 999999.0\n                hard_stop=bool(entry_price and price and price<=entry_price*0.985)\n\n                # STRUCT0 support may include bars formed before this fresh entry.\n                # Give the post-entry 1m structure five minutes to form; emergency stop remains live.\n                if hold_sec < 300.0 and not hard_stop:\n                    return\n\n                r=b.sell_market(sym,qty)\n'''
    new_exit = '''            elif in_pos:\n                import time as _time\n                qty=max(1,int(_f(st.get("qty"),1)))\n                entry_price=_f(st.get("entry_price"))\n                price=_f(row.get("price"))\n                entered_ts=_f(st.get("entered_ts"))\n                hold_sec=(_time.time()-entered_ts) if entered_ts else 999999.0\n                hard_stop=bool(entry_price and price and price<=entry_price*0.985)\n\n                # V115: emergency -1.5% stop is independent of EXIT_READY.\n                # Ordinary structural exits still require EXIT_READY and >=5m hold.\n                if not hard_stop:\n                    if not exit_ready:\n                        return\n                    if hold_sec < 300.0:\n                        return\n\n                r=b.sell_market(sym,qty)\n'''
    if "# V115: emergency -1.5% stop is independent of EXIT_READY." not in s:
        if old_exit not in s:
            fail("engine V113 exit block not found")
        s = s.replace(old_exit, new_exit, 1)

    ENGINE.write_text(s)
    print("ENGINE_PATCHED")


def main() -> None:
    patch_broker()
    patch_engine()
    py_compile.compile(str(BROKER), doraise=True)
    py_compile.compile(str(ENGINE), doraise=True)
    print("V115_PATCH_OK")
    print("SERVICE_NOT_STARTED")


if __name__ == "__main__":
    main()
