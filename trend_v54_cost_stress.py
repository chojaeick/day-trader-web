#!/usr/bin/env python3
"""
TREND V5.4 COST / STRESS VALIDATION

Frozen strategy:
- Entry logic: V4
- PROBE/CONFIRM: V4
- FULL gate: participation >= 1.8
- Exit capture: PEAK50
- Structural exits / debounce / hard risk: V4

This script does NOT optimize trading logic.
It stress-tests the frozen strategy under higher round-trip cost assumptions.

Cost grid is applied post-trade on deployed capital:
0.20% = current baseline
0.25%
0.30%
0.40%
0.50%

Also reports:
- leave-one-date-out robustness
- by-date and by-symbol performance
- max single-date loss
- profit factor
- worst trade / average winner / average loser
"""

import glob
import os
import pandas as pd
import trend_v4_exit_debounce as v4

FULL_PART_MIN = 1.8
COST_GRID = [0.20, 0.25, 0.30, 0.40, 0.50]


def peak50_floor(peak):
    if peak >= 1.0:
        return peak * 0.50
    return None


def trade_one(x, i0):
    r0 = x.loc[i0]
    entry0 = float(x.loc[i0, "open"])
    entry_mfi = float(r0.mfi14)

    legs = [(entry0, v4.PROBE_W, "PROBE")]
    deployed = v4.PROBE_W
    confirm_done = False
    full_done = False
    breakout_i = None
    breakout_high = None

    exit_reason = "TIME"
    exit_px = None
    exit_i = min(i0 + v4.MAX_HOLD, len(x)-1)

    peak_posret = 0.0
    floor_level = None

    below_refs_count = 0
    weak_full_count = 0

    for i in range(i0+1, min(i0+v4.MAX_HOLD, len(x)-1)+1):
        r = x.loc[i]
        sig = float(r.signal_price)
        ema9 = float(r.ema9)
        ema20 = float(r.ema20)
        vwap = float(r.vwap)
        mfi = float(r.mfi14)
        vo = float(r.vo_raw)
        part = float(r.participation)
        px = float(x.loc[i, "open"])

        cur_posret = v4.position_return(legs, sig, deployed)
        peak_posret = max(peak_posret, cur_posret)

        floor = peak50_floor(peak_posret)
        if floor is not None:
            floor_level = floor
            if cur_posret <= floor:
                exit_px = px
                exit_i = i
                exit_reason = "PROFIT_FLOOR"
                break

        if cur_posret <= -1.20:
            exit_px = px
            exit_i = i
            exit_reason = "HARD_RISK"
            break

        below_refs = (sig < ema20 and sig < vwap)
        below_refs_count = below_refs_count + 1 if below_refs else 0

        hard_break = bool(int(r.one_break)) and sig < ema20 and sig < vwap
        if hard_break or below_refs_count >= 2:
            exit_px = px
            exit_i = i
            exit_reason = "STRUCT_2BAR"
            break

        if not confirm_done:
            higher_low = True
            if i >= 3:
                prev = x.loc[i-3:i-1, "signal_low"]
                higher_low = float(r.signal_low) >= float(prev.min())

            confirm_ok = (
                sig > ema9 > ema20 and
                sig >= entry0 * 0.997 and
                mfi >= entry_mfi - 8 and
                higher_low
            )

            if confirm_ok:
                legs.append((px, v4.CONFIRM_W, "CONFIRM"))
                deployed += v4.CONFIRM_W
                confirm_done = True
                peak_posret = max(
                    0.0,
                    v4.position_return(legs, sig, deployed)
                )

        if confirm_done and not full_done and i >= i0+2:
            if breakout_i is None:
                prior_high = float(x.loc[i0:i-1, "signal_high"].max())
                breakout = sig > prior_high
                flow_ok = (mfi >= entry_mfi - 5) and (vo > -1.0)
                trend_ok = sig > ema9 > ema20 and sig > vwap

                if breakout and flow_ok and trend_ok:
                    breakout_i = i
                    breakout_high = prior_high
            else:
                bars_since = i - breakout_i

                hold_ok = (
                    sig >= breakout_high and
                    sig > ema9 > ema20 and
                    sig > vwap and
                    mfi >= entry_mfi - 5 and
                    vo > -1.0
                )

                retest_ok = (
                    float(r.signal_low) <= breakout_high * 1.002 and
                    sig >= breakout_high and
                    sig > ema9 > ema20
                )

                structural_full_ok = (
                    (bars_since >= 2 and hold_ok) or
                    (bars_since >= 1 and retest_ok)
                )

                if structural_full_ok:
                    if part >= FULL_PART_MIN:
                        legs.append((px, v4.FULL_W, "FULL"))
                        deployed += v4.FULL_W
                        full_done = True
                        peak_posret = max(
                            0.0,
                            v4.position_return(legs, sig, deployed)
                        )
                    else:
                        breakout_i = None
                        breakout_high = None

                elif (
                    bars_since >= 4 or
                    sig < ema9 or
                    sig < breakout_high * .995
                ):
                    breakout_i = None
                    breakout_high = None

        if full_done:
            weak_full = sig < ema9 and (sig < vwap or sig < ema20)
            weak_full_count = weak_full_count + 1 if weak_full else 0

            if weak_full_count >= 2:
                exit_px = px
                exit_i = i
                exit_reason = "FULL_FAIL_2BAR"
                break

    if exit_px is None:
        exit_px = float(x.loc[exit_i, "close"])

    gross = v4.weighted_return(legs, exit_px)

    w = x.loc[i0:min(i0+30, len(x)-1)]
    mfe30 = (float(w.high.max())/entry0 - 1)*100
    mae30 = (float(w.low.min())/entry0 - 1)*100

    return dict(
        ENTRY_TIME=str(r0.time),
        EXIT_TIME=str(x.loc[exit_i, "time"]),
        SCORE=int(v4.score_row(x, i0)[0]),
        DEPLOYED=deployed,
        LEGS="+".join(name for _,_,name in legs),
        EXIT_REASON=exit_reason,
        GROSS=gross,
        PEAK_POSRET=peak_posret,
        FLOOR_POSRET=floor_level,
        FINAL_POSRET=v4.position_return(legs, exit_px, deployed),
        MFE30=mfe30,
        MAE30=mae30,
    )


def collect():
    rows = []
    sessions = 0

    for f in sorted(glob.glob(v4.CACHE_GLOB)):
        try:
            x = pd.read_csv(f)
        except Exception:
            continue

        needed = {
            "signal_open","signal_high","signal_low","signal_price","mfi14",
            "vo_raw","ema9","ema20","vwap","participation","rp","one_break"
        }
        if not needed.issubset(set(x.columns)):
            continue

        sessions += 1
        case = os.path.basename(f).replace(".csv","")
        symbol, date = case.split("_", 1)
        next_i = 0

        for i in range(8, len(x)-46):
            if i < next_i:
                continue

            r = x.loc[i]
            if str(r.time) >= "11:00":
                break

            score, feat = v4.score_row(x, i)
            if score < 5:
                continue

            t = trade_one(x, i)
            t.update(
                CASE=case,
                SYMBOL=symbol,
                DATE=date,
                **feat
            )
            rows.append(t)

            exit_idx = x.index[
                x.time.astype(str) == t["EXIT_TIME"]
            ]

            if len(exit_idx):
                next_i = int(exit_idx[0]) + 1
            else:
                next_i = i + v4.MAX_HOLD + 1

    return pd.DataFrame(rows), sessions


def add_cost(z, cost_rt):
    x = z.copy()
    x["COST_RT"] = cost_rt
    x["COST"] = cost_rt * x.DEPLOYED
    x["NET"] = x.GROSS - x.COST
    return x


def stats(z):
    wins = z[z.NET > 0]
    losses = z[z.NET <= 0]

    gross_profit = wins.NET.sum()
    gross_loss = -losses.NET.sum()

    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    by_date = z.groupby("DATE").NET.sum()

    return dict(
        TRADES=len(z),
        NET=z.NET.sum(),
        AVG=z.NET.mean(),
        WIN_RATE=(z.NET > 0).mean()*100,
        PROFIT_FACTOR=pf,
        AVG_WIN=wins.NET.mean() if len(wins) else 0,
        AVG_LOSS=losses.NET.mean() if len(losses) else 0,
        WORST_TRADE=z.NET.min(),
        BEST_TRADE=z.NET.max(),
        WORST_DATE=by_date.min(),
        POS_DATES=int((by_date > 0).sum()),
        DATES=len(by_date),
    )


def main():
    print("===== TREND V5.4 COST / STRESS VALIDATION =====")
    print("FROZEN: PART18 + PEAK50")
    print()

    base, sessions = collect()

    print("SESSIONS", sessions)
    print("TRADES", len(base))
    print()

    rows = []
    cost_results = {}

    for c in COST_GRID:
        z = add_cost(base, c)
        cost_results[c] = z
        m = stats(z)
        m["COST_RT"] = c
        rows.append(m)

        z.to_csv(
            f"/tmp/trend_v54_cost_{int(c*100):02d}.csv",
            index=False
        )

    s = pd.DataFrame(rows)

    print("===== COST SWEEP =====")
    print(
        s[
            [
                "COST_RT","TRADES","NET","AVG","WIN_RATE",
                "PROFIT_FACTOR","AVG_WIN","AVG_LOSS",
                "WORST_TRADE","BEST_TRADE",
                "WORST_DATE","POS_DATES","DATES"
            ]
        ]
        .round(3)
        .to_string(index=False)
    )

    z20 = cost_results[0.20]

    print()
    print("===== CONTROL CHECK @ 0.20 COST =====")
    print("NET", f"{z20.NET.sum():+.3f}%")
    print("EXPECTED V5.3 PEAK50 approx +3.973%")

    print()
    print("===== BY DATE @ BASE COST =====")
    print(
        z20.groupby("DATE").agg(
            N=("NET","size"),
            NET=("NET","sum"),
            AVG=("NET","mean"),
            WIN_RATE=("NET", lambda x:(x>0).mean()*100)
        ).round(3).sort_index().to_string()
    )

    print()
    print("===== BY SYMBOL @ BASE COST =====")
    print(
        z20.groupby("SYMBOL").agg(
            N=("NET","size"),
            NET=("NET","sum"),
            AVG=("NET","mean"),
            WIN_RATE=("NET", lambda x:(x>0).mean()*100)
        ).round(3).sort_values("NET", ascending=False).to_string()
    )

    print()
    print("===== LEAVE-ONE-DATE-OUT BY COST =====")

    dates = sorted(z20.DATE.astype(str).unique())
    loo_rows = []

    for c, z in cost_results.items():
        for d in dates:
            r = z[z.DATE.astype(str) != d]
            loo_rows.append({
                "COST_RT": c,
                "EXCLUDED_DATE": d,
                "NET_REMAINING": r.NET.sum(),
                "TRADES_REMAINING": len(r),
            })

    loo = pd.DataFrame(loo_rows)

    robust = loo.groupby("COST_RT").agg(
        LOO_MIN_NET=("NET_REMAINING","min"),
        LOO_AVG_NET=("NET_REMAINING","mean"),
        LOO_POSITIVE=("NET_REMAINING", lambda x:int((x>0).sum())),
        LOO_N=("NET_REMAINING","size"),
    ).reset_index()

    robust["LOO_POS_RATE"] = (
        robust.LOO_POSITIVE / robust.LOO_N * 100
    )

    print(
        robust.round(3).to_string(index=False)
    )

    print()
    print("===== STRESS DECISION =====")

    control_ok = abs(float(z20.NET.sum()) - 3.973) <= 0.05

    z30 = cost_results[0.30]
    z40 = cost_results[0.40]
    z50 = cost_results[0.50]

    r30 = robust[robust.COST_RT == 0.30].iloc[0]
    r40 = robust[robust.COST_RT == 0.40].iloc[0]

    base_pass = (
        control_ok and
        z20.NET.sum() > 0 and
        float(
            robust[robust.COST_RT == 0.20].iloc[0].LOO_MIN_NET
        ) > 0
    )

    moderate_pass = (
        z30.NET.sum() > 0 and
        r30.LOO_MIN_NET > 0 and
        r30.LOO_POS_RATE >= 75.0
    )

    harsh_pass = (
        z40.NET.sum() > 0 and
        r40.LOO_MIN_NET > 0 and
        r40.LOO_POS_RATE >= 75.0
    )

    extreme_positive = z50.NET.sum() > 0

    print("CONTROL_OK", control_ok)
    print("BASE_PASS_0.20", base_pass)
    print("MODERATE_PASS_0.30", moderate_pass)
    print("HARSH_PASS_0.40", harsh_pass)
    print("EXTREME_NET_POSITIVE_0.50", extreme_positive)

    if base_pass and moderate_pass and harsh_pass:
        print("DECISION: COST/STRESS VALIDATION PASSES.")
        print("NEXT: freeze TREND candidate = PART18 + PEAK50 and run out-of-sample/session expansion.")
    elif base_pass and moderate_pass:
        print("DECISION: CONDITIONAL PASS.")
        print("NEXT: freeze candidate, but require realistic live cost <= 0.30% and run out-of-sample expansion.")
    else:
        print("DECISION: COST/STRESS VALIDATION FAILS.")
        print("NEXT: do not promote candidate; reassess edge vs transaction cost.")

    print()
    print("NOTE: No strategy parameter was optimized in this test.")


if __name__ == "__main__":
    main()
