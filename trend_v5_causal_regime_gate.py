#!/usr/bin/env python3
"""
TREND V5 causal regime-gate sweep.

Baseline trading behavior is TREND V4 unchanged.
Only the entry permission is changed: before a candidate entry, measure
cross-sectional breadth from the other cached symbols at the same DATE/TIME.

This is a causal gate: it uses only same-time/current-bar information, never
future session outcome. It tests a small predeclared grid and prints both
full-sample and leave-one-date-out robustness.
"""

import glob
import os
from collections import defaultdict

import pandas as pd
import trend_v4_exit_debounce as v4


MIN_PEERS = 3

# Small, predeclared grid. Do not expand after seeing results.
GATES = [
    ("BREADTH_025", 0.25, None),
    ("BREADTH_040", 0.40, None),
    ("BREADTH_050", 0.50, None),
    ("BREADTH_040_MFI", 0.40, 50.0),
    ("BREADTH_050_MFI", 0.50, 50.0),
]


def load_sessions():
    sessions = {}
    needed = {
        "signal_open", "signal_high", "signal_low", "signal_price", "mfi14",
        "vo_raw", "ema9", "ema20", "vwap", "participation", "rp", "one_break"
    }

    for f in sorted(glob.glob(v4.CACHE_GLOB)):
        try:
            x = pd.read_csv(f).reset_index(drop=True)
        except Exception:
            continue

        if not needed.issubset(set(x.columns)):
            continue

        case = os.path.basename(f).replace(".csv", "")
        symbol, date = case.split("_", 1)
        sessions[case] = (symbol, date, x)

    return sessions


def build_regime_map(sessions):
    """
    Same-time cross-sectional regime snapshot.

    A peer is bullish when:
      signal_price > ema9 > ema20
      signal_price > vwap

    For every DATE/TIME:
      breadth = bullish peers / valid peers
      median_mfi = cross-sectional median MFI14
    """
    bucket = defaultdict(list)

    for case, (symbol, date, x) in sessions.items():
        for _, r in x.iterrows():
            try:
                t = str(r.time)
                sig = float(r.signal_price)
                e9 = float(r.ema9)
                e20 = float(r.ema20)
                vw = float(r.vwap)
                mfi = float(r.mfi14)
            except Exception:
                continue

            bullish = (sig > e9 > e20) and (sig > vw)
            bucket[(date, t)].append((symbol, bullish, mfi))

    regime = {}

    for key, vals in bucket.items():
        n = len(vals)
        bull = sum(1 for _, b, _ in vals if b)
        mfis = [m for _, _, m in vals]
        regime[key] = {
            "N_PEERS": n,
            "BREADTH": bull / n if n else 0.0,
            "MEDIAN_MFI": float(pd.Series(mfis).median()) if mfis else float("nan"),
        }

    return regime


def collect_for_gate(sessions, regime, gate_name, min_breadth, min_mfi):
    rows = []
    blocked = []
    eligible = 0

    for case in sorted(sessions):
        symbol, date, x = sessions[case]
        next_i = 0

        for i in range(8, len(x) - 46):
            if i < next_i:
                continue

            r = x.loc[i]
            tstr = str(r.time)

            if tstr >= "11:00":
                break

            score, feat = v4.score_row(x, i)
            if score < 5:
                continue

            eligible += 1
            rs = regime.get((date, tstr), {})
            n_peers = int(rs.get("N_PEERS", 0))
            breadth = float(rs.get("BREADTH", 0.0))
            median_mfi = float(rs.get("MEDIAN_MFI", float("nan")))

            gate_ok = n_peers >= MIN_PEERS and breadth >= min_breadth
            if min_mfi is not None:
                gate_ok = gate_ok and pd.notna(median_mfi) and median_mfi >= min_mfi

            if not gate_ok:
                blocked.append({
                    "CASE": case,
                    "SYMBOL": symbol,
                    "DATE": date,
                    "ENTRY_TIME": tstr,
                    "SCORE": score,
                    "N_PEERS": n_peers,
                    "BREADTH": breadth,
                    "MEDIAN_MFI": median_mfi,
                })
                # Important: blocked candidate consumes no position and no cooldown.
                continue

            trade = v4.trade_one(x, i)
            trade.update(
                CASE=case,
                SYMBOL=symbol,
                DATE=date,
                GATE=gate_name,
                N_PEERS=n_peers,
                BREADTH=breadth,
                MEDIAN_MFI=median_mfi,
                **feat,
            )
            rows.append(trade)

            exit_idx = x.index[x.time.astype(str) == trade["EXIT_TIME"]]
            if len(exit_idx):
                next_i = int(exit_idx[0]) + 1
            else:
                next_i = i + v4.MAX_HOLD + 1

    return pd.DataFrame(rows), pd.DataFrame(blocked), eligible


def summarize(name, z, blocked, eligible):
    if len(z) == 0:
        return {
            "GATE": name, "TRADES": 0, "NET": 0.0, "AVG": 0.0,
            "WIN_RATE": 0.0, "FULL": 0, "BLOCKED": len(blocked),
            "ELIGIBLE": eligible, "JUN12_NET": 0.0, "OTHER_NET": 0.0,
            "POS_DATES": 0,
        }

    d612 = z[z.DATE.astype(str) == "20260612"]
    other = z[z.DATE.astype(str) != "20260612"]
    by_date = z.groupby("DATE").NET.sum()

    return {
        "GATE": name,
        "TRADES": len(z),
        "NET": z.NET.sum(),
        "AVG": z.NET.mean(),
        "WIN_RATE": (z.NET > 0).mean() * 100,
        "FULL": int((z.DEPLOYED >= .999).sum()),
        "BLOCKED": len(blocked),
        "ELIGIBLE": eligible,
        "JUN12_NET": d612.NET.sum(),
        "OTHER_NET": other.NET.sum(),
        "POS_DATES": int((by_date > 0).sum()),
    }


def main():
    sessions = load_sessions()
    regime = build_regime_map(sessions)

    print("===== TREND V5 CAUSAL REGIME GATE =====")
    print("SESSIONS", len(sessions))
    print("MIN_PEERS", MIN_PEERS)
    print("V4 BASELINE NET -0.078%")
    print()

    summaries = []
    results = {}

    for name, min_breadth, min_mfi in GATES:
        z, blocked, eligible = collect_for_gate(
            sessions, regime, name, min_breadth, min_mfi
        )
        results[name] = (z, blocked)
        summaries.append(summarize(name, z, blocked, eligible))

    s = pd.DataFrame(summaries)
    print("===== FULL SAMPLE SWEEP =====")
    print(s.round(3).sort_values("NET", ascending=False).to_string(index=False))

    # Save all variants.
    for name, (z, blocked) in results.items():
        z.to_csv(f"/tmp/trend_v5_{name.lower()}_trades.csv", index=False)
        blocked.to_csv(f"/tmp/trend_v5_{name.lower()}_blocked.csv", index=False)

    # Select full-sample leader, but robustness is evaluated separately.
    best_name = s.sort_values(["NET", "AVG"], ascending=False).iloc[0]["GATE"]
    best = results[best_name][0]

    print()
    print("===== BEST FULL-SAMPLE GATE =====")
    print("GATE", best_name)
    if len(best):
        print("TRADES", len(best))
        print("NET", f"{best.NET.sum():+.3f}%")
        print("AVG", f"{best.NET.mean():+.3f}%")
        print("WIN RATE", f"{(best.NET > 0).mean()*100:.1f}%")
        print("FULL", int((best.DEPLOYED >= .999).sum()))

        print()
        print("===== BEST GATE BY DATE =====")
        print(
            best.groupby("DATE").agg(
                N=("NET", "size"),
                NET=("NET", "sum"),
                AVG=("NET", "mean"),
                WIN_RATE=("NET", lambda x: (x > 0).mean() * 100),
            ).round(3).sort_index().to_string()
        )

        print()
        print("===== BEST GATE BY SYMBOL =====")
        print(
            best.groupby("SYMBOL").agg(
                N=("NET", "size"),
                NET=("NET", "sum"),
                AVG=("NET", "mean"),
            ).round(3).sort_values("NET", ascending=False).to_string()
        )

    # Leave-one-date-out: no refitting. Apply each fixed gate and report
    # remaining-date net after excluding each date.
    print()
    print("===== LEAVE-ONE-DATE-OUT ROBUSTNESS =====")
    loo_rows = []
    all_dates = sorted({date for _, date, _ in sessions.values()})

    for name, (z, _) in results.items():
        if len(z) == 0:
            continue
        for holdout in all_dates:
            train_like = z[z.DATE.astype(str) != str(holdout)]
            loo_rows.append({
                "GATE": name,
                "EXCLUDED_DATE": holdout,
                "NET_REMAINING": train_like.NET.sum(),
                "TRADES_REMAINING": len(train_like),
            })

    loo = pd.DataFrame(loo_rows)
    if len(loo):
        robust = loo.groupby("GATE").agg(
            LOO_MIN_NET=("NET_REMAINING", "min"),
            LOO_AVG_NET=("NET_REMAINING", "mean"),
            LOO_POSITIVE=("NET_REMAINING", lambda x: int((x > 0).sum())),
            LOO_N=("NET_REMAINING", "size"),
        ).reset_index()
        robust["LOO_POS_RATE"] = robust.LOO_POSITIVE / robust.LOO_N * 100
        print(
            robust.round(3)
            .sort_values(["LOO_MIN_NET", "LOO_AVG_NET"], ascending=False)
            .to_string(index=False)
        )
    else:
        robust = pd.DataFrame()

    print()
    print("===== CAUSAL DECISION =====")

    best_row = s.sort_values(["NET", "AVG"], ascending=False).iloc[0]
    improvement = float(best_row.NET) - (-0.078)

    robust_ok = False
    if len(robust):
        rr = robust[robust.GATE == best_name]
        if len(rr):
            rr = rr.iloc[0]
            robust_ok = (
                float(rr.LOO_MIN_NET) > 0
                and float(rr.LOO_POS_RATE) >= 75.0
            )

    sample_ok = int(best_row.TRADES) >= 25
    economics_ok = float(best_row.NET) >= 2.0 and improvement >= 2.0

    print("BEST_GATE", best_name)
    print("IMPROVEMENT_VS_V4", f"{improvement:+.3f}%")
    print("SAMPLE_OK", sample_ok)
    print("ECONOMICS_OK", economics_ok)
    print("ROBUST_OK", robust_ok)

    if sample_ok and economics_ok and robust_ok:
        print("DECISION: REGIME_GATE PASSES -> next test FULL_CONFIRMATION on top of fixed gate.")
    else:
        print("DECISION: REGIME_GATE NOT YET ROBUST -> do not stack another optimization.")
        print("NEXT: inspect blocked-vs-allowed causal characteristics, then either simplify gate or reject regime hypothesis.")

    print()
    print("NOTE: No symbol was excluded and V4 trade/exit logic was not changed.")
    print("NOTE: Gate uses same DATE/TIME cross-sectional data only; no future-session label is used.")


if __name__ == "__main__":
    main()
