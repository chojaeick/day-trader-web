#!/usr/bin/env python3

import glob
import os
import pandas as pd

import trend_v4_exit_debounce as v4


def collect_trades():
    rows = []
    sessions = 0

    for f in sorted(glob.glob(v4.CACHE_GLOB)):
        try:
            x = pd.read_csv(f)
        except Exception:
            continue

        needed = {
            "signal_open",
            "signal_high",
            "signal_low",
            "signal_price",
            "mfi14",
            "vo_raw",
            "ema9",
            "ema20",
            "vwap",
            "participation",
            "rp",
            "one_break",
        }

        if not needed.issubset(set(x.columns)):
            continue

        sessions += 1
        case = os.path.basename(f).replace(".csv", "")
        symbol, date = case.split("_", 1)
        next_i = 0

        for i in range(8, len(x) - 46):
            if i < next_i:
                continue

            r = x.loc[i]
            if str(r.time) >= "11:00":
                break

            score, feat = v4.score_row(x, i)
            if score < 5:
                continue

            t = v4.trade_one(x, i)
            t.update(CASE=case, SYMBOL=symbol, DATE=date, **feat)
            rows.append(t)

            exit_idx = x.index[x.time.astype(str) == t["EXIT_TIME"]]
            if len(exit_idx):
                next_i = int(exit_idx[0]) + 1
            else:
                next_i = i + v4.MAX_HOLD + 1

    return pd.DataFrame(rows), sessions


def print_block(title, z, cols):
    print()
    print("=" * 90)
    print(title)
    print("=" * 90)
    print("N", len(z))

    if len(z) == 0:
        print("NONE")
        return

    print("NET", f"{z.NET.sum():+.3f}%")
    print("AVG", f"{z.NET.mean():+.3f}%")
    print()
    print(z[cols].round(3).to_string(index=False))


def main():
    z, sessions = collect_trades()

    print()
    print("===== TREND V4 FAILURE ATTRIBUTION AUDIT =====")
    print("SESSIONS", sessions)
    print("TRADES", len(z))

    if len(z) == 0:
        return

    total_net = z.NET.sum()

    print("TOTAL NET", f"{total_net:+.3f}%")
    print("WIN RATE", f"{(z.NET > 0).mean()*100:.1f}%")
    print("AVG NET", f"{z.NET.mean():+.3f}%")

    missed = z[(z.MFE30 >= 2.0) & (z.NET <= 0)].copy()
    false_full = z[(z.DEPLOYED >= 0.999) & (z.NET <= 0)].copy()
    good_full = z[(z.DEPLOYED >= 0.999) & (z.NET > 0)].copy()

    cols = [
        "CASE",
        "ENTRY_TIME",
        "EXIT_TIME",
        "SCORE",
        "DEPLOYED",
        "LEGS",
        "EXIT_REASON",
        "NET",
        "PEAK_POSRET",
        "FINAL_POSRET",
        "MFE30",
        "MAE30",
        "p3",
        "p5",
        "mfi3",
        "close_pos",
        "ema9_gap",
        "rp",
        "part",
    ]

    print_block(
        "1. MISSED BIG OPPORTUNITIES : MFE30 >= 2% AND NET <= 0",
        missed,
        cols
    )

    print_block(
        "2. FALSE FULL : FULL DEPLOYED AND NET <= 0",
        false_full,
        cols
    )

    print_block(
        "3. SUCCESSFUL FULL : FULL DEPLOYED AND NET > 0",
        good_full,
        cols
    )

    print()
    print("===== MISSED BIG BY EXIT REASON =====")

    if len(missed):
        print(
            missed.groupby("EXIT_REASON")
            .agg(
                N=("NET", "size"),
                NET=("NET", "sum"),
                AVG_NET=("NET", "mean"),
                AVG_PEAK=("PEAK_POSRET", "mean"),
                AVG_MFE=("MFE30", "mean"),
                AVG_MAE=("MAE30", "mean"),
            )
            .round(3)
            .sort_values("NET")
            .to_string()
        )
    else:
        print("NONE")

    print()
    print("===== FALSE FULL BY EXIT REASON =====")

    if len(false_full):
        print(
            false_full.groupby("EXIT_REASON")
            .agg(
                N=("NET", "size"),
                NET=("NET", "sum"),
                AVG_NET=("NET", "mean"),
                AVG_PEAK=("PEAK_POSRET", "mean"),
                AVG_MFE=("MFE30", "mean"),
                AVG_MAE=("MAE30", "mean"),
            )
            .round(3)
            .sort_values("NET")
            .to_string()
        )
    else:
        print("NONE")

    rows = []

    for date, g in z.groupby("DATE"):
        ff = false_full[false_full.DATE == date]
        mb = missed[missed.DATE == date]

        rows.append({
            "DATE": date,
            "N": len(g),
            "NET": g.NET.sum(),
            "AVG": g.NET.mean(),
            "WIN_RATE": (g.NET > 0).mean() * 100,
            "LOSERS": int((g.NET <= 0).sum()),
            "FULL": int((g.DEPLOYED >= .999).sum()),
            "FALSE_FULL": len(ff),
            "MISSED_BIG": len(mb),
        })

    by_date = pd.DataFrame(rows)

    print()
    print("===== 4. DATE / REGIME ATTRIBUTION =====")
    print(
        by_date
        .sort_values("NET")
        .round(3)
        .to_string(index=False)
    )

    rows = []

    for symbol, g in z.groupby("SYMBOL"):
        ff = false_full[false_full.SYMBOL == symbol]
        mb = missed[missed.SYMBOL == symbol]

        rows.append({
            "SYMBOL": symbol,
            "N": len(g),
            "NET": g.NET.sum(),
            "AVG": g.NET.mean(),
            "WIN_RATE": (g.NET > 0).mean() * 100,
            "FULL": int((g.DEPLOYED >= .999).sum()),
            "FALSE_FULL": len(ff),
            "MISSED_BIG": len(mb),
        })

    by_symbol = pd.DataFrame(rows)

    print()
    print("===== 5. SYMBOL ATTRIBUTION =====")
    print(
        by_symbol
        .sort_values("NET")
        .round(3)
        .to_string(index=False)
    )

    z2 = z.copy()
    z2["OUTCOME"] = z2.NET.gt(0).map({
        True: "WIN",
        False: "LOSS"
    })

    feat_cols = [
        "p3",
        "p5",
        "mfi3",
        "close_pos",
        "ema9_gap",
        "rp",
        "part",
        "MFE30",
        "MAE30",
    ]

    print()
    print("===== 6. ENTRY FEATURE MEANS : WIN VS LOSS =====")
    print(
        z2.groupby("OUTCOME")[feat_cols]
        .mean()
        .round(3)
        .to_string()
    )

    full = z[z.DEPLOYED >= .999].copy()

    if len(full):
        full["OUTCOME"] = full.NET.gt(0).map({
            True: "WIN_FULL",
            False: "LOSS_FULL"
        })

        print()
        print("===== 7. FULL ENTRY FEATURE MEANS =====")
        print(
            full.groupby("OUTCOME")[feat_cols]
            .mean()
            .round(3)
            .to_string()
        )

    d612 = z[z.DATE.astype(str) == "20260612"].copy()

    if len(d612):
        print_block(
            "8. 20260612 LOSS CLUSTER DETAIL",
            d612,
            cols
        )

    z.to_csv("/tmp/trend_v4_all_trades.csv", index=False)
    missed.to_csv("/tmp/trend_v4_missed_big.csv", index=False)
    false_full.to_csv("/tmp/trend_v4_false_full.csv", index=False)
    by_date.to_csv("/tmp/trend_v4_by_date.csv", index=False)
    by_symbol.to_csv("/tmp/trend_v4_by_symbol.csv", index=False)

    total_negative = -z.loc[z.NET < 0, "NET"].sum()
    false_full_loss = -false_full.loc[false_full.NET < 0, "NET"].sum()
    missed_loss = -missed.loc[missed.NET < 0, "NET"].sum()

    worst = by_date.sort_values("NET").iloc[0]
    other_net = total_net - float(worst.NET)

    false_full_share = (
        false_full_loss / total_negative
        if total_negative > 0
        else 0
    )

    missed_share = (
        missed_loss / total_negative
        if total_negative > 0
        else 0
    )

    regime_flag = (
        float(worst.NET) <= -2.0
        and other_net > 0
    )

    full_flag = (
        len(false_full) >= 5
        and false_full_share >= 0.40
    )

    capture_flag = (
        len(missed) >= 5
        and missed_share >= 0.25
    )

    print()
    print("=" * 90)
    print("===== 9. AUTOMATIC V5 DIRECTION =====")
    print("=" * 90)

    print("TOTAL_NEGATIVE", f"{total_negative:.3f}%")
    print(
        "FALSE_FULL_LOSS",
        f"{false_full_loss:.3f}%",
        "SHARE",
        f"{false_full_share*100:.1f}%"
    )
    print(
        "MISSED_BIG_LOSS",
        f"{missed_loss:.3f}%",
        "SHARE",
        f"{missed_share*100:.1f}%"
    )
    print(
        "WORST_DATE",
        str(worst.DATE),
        "NET",
        f"{float(worst.NET):+.3f}%"
    )
    print(
        "OTHER_DATES_NET",
        f"{other_net:+.3f}%"
    )

    print()

    candidates = []

    if regime_flag:
        candidates.append(
            (
                1,
                "REGIME_GATE",
                "Losses are heavily concentrated in one bad market/session regime."
            )
        )

    if full_flag:
        candidates.append(
            (
                2,
                "FULL_CONFIRMATION",
                "Too much loss occurs after escalating to FULL."
            )
        )

    if capture_flag:
        candidates.append(
            (
                3,
                "EXIT_CAPTURE",
                "Many trades had large future MFE but failed to monetize it."
            )
        )

    if not candidates:
        candidates.append(
            (
                4,
                "ENTRY_SELECTOR",
                "Losses are diffuse; entry quality becomes the next hypothesis."
            )
        )

    candidates.sort()

    for rank, name, reason in candidates:
        print(f"PRIORITY {rank}: {name} - {reason}")

    print()
    print("PRIMARY V5 TARGET:", candidates[0][1])

    print()
    print("IMPORTANT: Do not exclude INTC/SMCI merely because their historical V4 totals are negative.")
    print("IMPORTANT: V5 should change ONE causal component only.")

    print()
    print("CSV SAVED:")
    print("/tmp/trend_v4_all_trades.csv")
    print("/tmp/trend_v4_missed_big.csv")
    print("/tmp/trend_v4_false_full.csv")
    print("/tmp/trend_v4_by_date.csv")
    print("/tmp/trend_v4_by_symbol.csv")


if __name__ == "__main__":
    main()
