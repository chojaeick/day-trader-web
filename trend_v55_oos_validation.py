#!/usr/bin/env python3
"""
TREND V5.5 temporal OOS validation.

Frozen strategy imported from trend_v54_cost_stress:
  FULL gate = participation >= 1.8
  Exit capture = PEAK50
  All other logic = V4

Important:
- Uses ONLY dates >= 20260722.
- No parameter fitting is performed here.
- Reports 0.20 / 0.25 / 0.30 round-trip cost.
"""

import pandas as pd
import trend_v54_cost_stress as frozen

OOS_MIN_DATE = "20260722"
COSTS = [0.20, 0.25, 0.30]


def stats(z):
    if len(z) == 0:
        return {
            "TRADES": 0, "NET": 0.0, "AVG": 0.0, "WIN_RATE": 0.0,
            "PF": 0.0, "POS_DATES": 0, "DATES": 0, "WORST_DATE": 0.0
        }

    wins = z[z.NET > 0]
    losses = z[z.NET <= 0]

    gp = wins.NET.sum()
    gl = -losses.NET.sum()
    pf = gp / gl if gl > 0 else float("inf")

    bd = z.groupby("DATE").NET.sum()

    return {
        "TRADES": len(z),
        "NET": z.NET.sum(),
        "AVG": z.NET.mean(),
        "WIN_RATE": (z.NET > 0).mean() * 100,
        "PF": pf,
        "POS_DATES": int((bd > 0).sum()),
        "DATES": len(bd),
        "WORST_DATE": bd.min(),
    }


def main():
    print("===== TREND V5.5 TEMPORAL OOS VALIDATION =====")
    print("FROZEN STRATEGY: PART18 + PEAK50")
    print("OOS_MIN_DATE", OOS_MIN_DATE)
    print()

    all_trades, sessions = frozen.collect()

    z0 = all_trades[
        all_trades.DATE.astype(str) >= OOS_MIN_DATE
    ].copy()

    oos_sessions = sorted(
        {
            (str(r.SYMBOL), str(r.DATE))
            for _, r in z0.iterrows()
        }
    )

    print("ALL_CACHE_SESSIONS", sessions)
    print("OOS_TRADE_SESSIONS", len(oos_sessions))
    print("OOS_TRADES_GROSS", len(z0))

    if len(z0) == 0:
        print("DECISION: NO OOS TRADES. Check cache generation.")
        return

    rows = []
    results = {}

    for c in COSTS:
        z = frozen.add_cost(z0, c)
        results[c] = z
        m = stats(z)
        m["COST_RT"] = c
        rows.append(m)
        z.to_csv(
            f"/tmp/trend_v55_oos_cost_{int(c*100):02d}.csv",
            index=False
        )

    s = pd.DataFrame(rows)

    print()
    print("===== OOS COST SWEEP =====")
    print(
        s[
            ["COST_RT","TRADES","NET","AVG","WIN_RATE","PF",
             "POS_DATES","DATES","WORST_DATE"]
        ].round(3).to_string(index=False)
    )

    z20 = results[0.20]

    print()
    print("===== OOS BY DATE @ 0.20 =====")
    print(
        z20.groupby("DATE").agg(
            N=("NET","size"),
            NET=("NET","sum"),
            AVG=("NET","mean"),
            WIN_RATE=("NET", lambda x: (x > 0).mean()*100),
        ).round(3).sort_index().to_string()
    )

    print()
    print("===== OOS BY SYMBOL @ 0.20 =====")
    print(
        z20.groupby("SYMBOL").agg(
            N=("NET","size"),
            NET=("NET","sum"),
            AVG=("NET","mean"),
            WIN_RATE=("NET", lambda x: (x > 0).mean()*100),
        ).round(3).sort_values("NET", ascending=False).to_string()
    )

    print()
    print("===== OOS LEAVE-ONE-DATE-OUT @ 0.20 =====")
    dates = sorted(z20.DATE.astype(str).unique())
    loo = []

    for d in dates:
        r = z20[z20.DATE.astype(str) != d]
        loo.append({
            "EXCLUDED_DATE": d,
            "NET_REMAINING": r.NET.sum(),
            "TRADES_REMAINING": len(r),
        })

    loo = pd.DataFrame(loo)

    if len(loo):
        print(loo.round(3).to_string(index=False))
        loo_min = float(loo.NET_REMAINING.min())
        loo_pos = float((loo.NET_REMAINING > 0).mean()*100)
    else:
        loo_min = float(z20.NET.sum())
        loo_pos = 100.0 if loo_min > 0 else 0.0

    base = s[s.COST_RT == 0.20].iloc[0]
    c25 = s[s.COST_RT == 0.25].iloc[0]
    c30 = s[s.COST_RT == 0.30].iloc[0]

    sample_ok = int(base.TRADES) >= 25 and int(base.DATES) >= 6
    base_edge = (
        float(base.NET) > 0
        and float(base.PF) > 1.10
        and float(base.POS_DATES) / max(float(base.DATES), 1) >= 0.50
    )
    robust_ok = loo_min > 0 and loo_pos >= 75.0
    cost25_ok = float(c25.NET) > 0
    cost30_positive = float(c30.NET) > 0

    print()
    print("===== OOS DECISION =====")
    print("SAMPLE_OK", sample_ok)
    print("BASE_EDGE_020", base_edge)
    print("LOO_MIN_NET_020", f"{loo_min:+.3f}%")
    print("LOO_POS_RATE_020", f"{loo_pos:.1f}%")
    print("ROBUST_OK", robust_ok)
    print("COST25_POSITIVE", cost25_ok)
    print("COST30_POSITIVE", cost30_positive)

    if sample_ok and base_edge and robust_ok and cost25_ok:
        print("DECISION: TEMPORAL OOS PASSES.")
        print("NEXT: promote PART18 + PEAK50 to TREND candidate and integrate shadow/live replay.")
    elif sample_ok and base_edge and cost25_ok:
        print("DECISION: TEMPORAL OOS CONDITIONAL PASS.")
        print("NEXT: expand more OOS dates before live promotion.")
    else:
        print("DECISION: TEMPORAL OOS FAILS.")
        print("NEXT: do not retune on OOS; reject/freeze result and revisit architecture with a new training split.")

    print()
    print("IMPORTANT: Do not tune PART18 or PEAK50 using these OOS results.")


if __name__ == "__main__":
    main()
