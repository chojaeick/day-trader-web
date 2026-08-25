#!/usr/bin/env python3
from live_server.paper_trading import PaperBroker


def show(label, x):
    print(label, x)


if __name__ == "__main__":
    b = PaperBroker("daytrader.db")
    print("=== PAPER TRADING V95 SMOKE ===")
    print("NO REAL BROKER ORDERS. DB-only paper fills.")

    show("RESET_KOREA", b.reset("KOREA"))
    show("RESET_USA", b.reset("USA"))

    show("KR_BUY", b.enter("KOREA", "TESTKR", 10000, support=9800))
    b.mark("KOREA", "TESTKR", 10200, support=9900, support_updates=1, state="HOLD")
    show("KR_AFTER_MARK", b.account("KOREA"))
    show("KR_SELL", b.exit("KOREA", "TESTKR", 9900, support=9900))
    show("KR_FINAL", b.account("KOREA"))

    show("US_BUY", b.enter("USA", "TESTUS", 50.0, support=49.0))
    b.mark("USA", "TESTUS", 51.0, support=49.5, support_updates=1, state="HOLD")
    show("US_AFTER_MARK", b.account("USA"))
    show("US_SELL", b.exit("USA", "TESTUS", 49.4, support=49.5))
    show("US_FINAL", b.account("USA"))

    print("PASS: paper account schema / buy / mark / support-ratchet / exit ledger executed")
