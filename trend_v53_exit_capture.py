#!/usr/bin/env python3
"""
TREND V5.3 EXIT CAPTURE causal test.

Frozen from V5.2:
- Entry SCORE: V4 unchanged
- PROBE / CONFIRM: V4 unchanged
- FULL gate: participation >= 1.8
- Structural exits / 2-bar debounce / HARD_RISK: V4 unchanged

Only profit-capture policy changes.

Variants:
BASE    : V4 profit floors (control, must reproduce V5.2 PART18 ~ +2.981%)
LOOSE   : lower floors, lets winners breathe more
DELAYED : no floor until peak >= 1.5%
TIGHT   : locks profit earlier
PEAK50  : after peak >=1%, protect 50% of peak return

No date/symbol exclusion.
"""

import glob
import os
import pandas as pd
import trend_v4_exit_debounce as v4

FULL_PART_MIN = 1.8

VARIANTS = [
    "BASE",
    "LOOSE",
    "DELAYED",
    "TIGHT",
    "PEAK50",
]


def profit_floor(variant, peak):
    if variant == "BASE":
        if peak >= 3.0:
            return 1.80
        if peak >= 2.0:
            return 1.00
        if peak >= 1.5:
            return 0.60
        if peak >= 1.0:
            return 0.10
        return None

    if variant == "LOOSE":
        if peak >= 3.0:
            return 1.50
        if peak >= 2.0:
            return 0.80
        if peak >= 1.5:
            return 0.40
        if peak >= 1.0:
            return 0.00
        return None

    if variant == "DELAYED":
        if peak >= 3.0:
            return 1.70
        if peak >= 2.0:
            return 0.90
        if peak >= 1.5:
            return 0.40
        return None

    if variant == "TIGHT":
        if peak >= 3.0:
            return 2.00
        if peak >= 2.0:
            return 1.30
        if peak >= 1.5:
            return 0.80
        if peak >= 1.0:
            return 0.25
        return None

    if variant == "PEAK50":
        if peak >= 1.0:
            return peak * 0.50
        return None

    raise ValueError(variant)


def trade_one(x, i0, variant):
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
    full_gate_blocks = 0

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

        floor = profit_floor(variant, peak_posret)
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
                        full_gate_blocks += 1
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
    cost = v4.COST_RT * deployed
    net = gross - cost
    final_posret = v4.position_return(legs, exit_px, deployed)

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
        COST=cost,
        NET=net,
        PEAK_POSRET=peak_posret,
        FLOOR_POSRET=floor_level,
        FINAL_POSRET=final_posret,
        MFE30=mfe30,
        MAE30=mae30,
        FULL_GATE_BLOCKS=full_gate_blocks,
    )


def collect_variant(variant):
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

            t = trade_one(x, i, variant)
            t.update(
                CASE=case,
                SYMBOL=symbol,
                DATE=date,
                VARIANT=variant,
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


def metrics(variant, z):
    full = z[z.DEPLOYED >= .999]

    return dict(
        VARIANT=variant,
        TRADES=len(z),
        NET=z.NET.sum(),
        AVG=z.NET.mean(),
        WIN_RATE=(z.NET > 0).mean()*100,
        FULL=len(full),
        FULL_NET=full.NET.sum(),
        PROFIT_EXITS=int((z.EXIT_REASON == "PROFIT_FLOOR").sum()),
        HARD_RISK=int((z.EXIT_REASON == "HARD_RISK").sum()),
        STRUCT=int((z.EXIT_REASON == "STRUCT_2BAR").sum()),
        FULL_FAIL=int((z.EXIT_REASON == "FULL_FAIL_2BAR").sum()),
        JUN12_NET=z[z.DATE.astype(str)=="20260612"].NET.sum(),
        OTHER_NET=z[z.DATE.astype(str)!="20260612"].NET.sum(),
    )


def main():
    print("===== TREND V5.3 EXIT CAPTURE TEST =====")
    print("FROZEN FULL GATE participation >=", FULL_PART_MIN)

    results = {}
    summaries = []
    sessions = None

    for variant in VARIANTS:
        z, sessions = collect_variant(variant)
        results[variant] = z
        summaries.append(metrics(variant, z))

        z.to_csv(
            f"/tmp/trend_v53_{variant.lower()}_trades.csv",
            index=False
        )

    print("SESSIONS", sessions)
    print()

    s = pd.DataFrame(summaries)

    print("===== FULL SAMPLE SWEEP =====")
    print(
        s.round(3)
        .sort_values(["NET","AVG"], ascending=False)
        .to_string(index=False)
    )

    base = s[s.VARIANT=="BASE"].iloc[0]
    candidates = s[s.VARIANT!="BASE"].copy()
    candidates["IMPROVEMENT"] = candidates.NET - float(base.NET)

    print()
    print("===== CONTROL CHECK =====")
    print("BASE NET", f"{float(base.NET):+.3f}%")
    print("EXPECTED V5.2 PART18 approx +2.981%")

    print()
    print("===== IMPROVEMENT VS FROZEN PART18 BASE =====")
    print(
        candidates[
            [
                "VARIANT","TRADES","NET","IMPROVEMENT","AVG","WIN_RATE",
                "FULL","FULL_NET","PROFIT_EXITS","HARD_RISK",
                "STRUCT","FULL_FAIL","JUN12_NET","OTHER_NET"
            ]
        ]
        .round(3)
        .sort_values("NET", ascending=False)
        .to_string(index=False)
    )

    print()
    print("===== LEAVE-ONE-DATE-OUT =====")

    dates = sorted(
        {
            d
            for z in results.values()
            for d in z.DATE.astype(str).unique()
        }
    )

    loo_rows = []

    for variant, z in results.items():
        for holdout in dates:
            r = z[z.DATE.astype(str) != holdout]
            loo_rows.append({
                "VARIANT": variant,
                "EXCLUDED_DATE": holdout,
                "NET_REMAINING": r.NET.sum(),
                "TRADES_REMAINING": len(r),
            })

    loo = pd.DataFrame(loo_rows)

    robust = loo.groupby("VARIANT").agg(
        LOO_MIN_NET=("NET_REMAINING","min"),
        LOO_AVG_NET=("NET_REMAINING","mean"),
        LOO_POSITIVE=("NET_REMAINING", lambda x: int((x>0).sum())),
        LOO_N=("NET_REMAINING","size"),
    ).reset_index()

    robust["LOO_POS_RATE"] = (
        robust.LOO_POSITIVE / robust.LOO_N * 100
    )

    print(
        robust.round(3)
        .sort_values(
            ["LOO_MIN_NET","LOO_AVG_NET"],
            ascending=False
        )
        .to_string(index=False)
    )

    print()
    print("===== BEST VARIANT DETAIL =====")

    merged = s.merge(robust, on="VARIANT", how="left")
    nonbase = merged[merged.VARIANT!="BASE"].copy()

    best = nonbase.sort_values(
        ["NET","LOO_MIN_NET"],
        ascending=False
    ).iloc[0]

    best_name = best.VARIANT
    bz = results[best_name]

    print("BEST_VARIANT", best_name)

    print()
    print("BY DATE")
    print(
        bz.groupby("DATE").agg(
            N=("NET","size"),
            NET=("NET","sum"),
            AVG=("NET","mean"),
            WIN_RATE=("NET", lambda x:(x>0).mean()*100)
        ).round(3).sort_index().to_string()
    )

    print()
    print("BY SYMBOL")
    print(
        bz.groupby("SYMBOL").agg(
            N=("NET","size"),
            NET=("NET","sum"),
            AVG=("NET","mean")
        ).round(3).sort_values("NET", ascending=False).to_string()
    )

    print()
    print("===== CAUSAL DECISION =====")

    base_net = float(base.NET)
    improvement = float(best.NET) - base_net

    control_ok = abs(base_net - 2.981) <= 0.05
    sample_ok = int(best.TRADES) >= 40
    robust_ok = (
        float(best.LOO_MIN_NET) > 0 and
        float(best.LOO_POS_RATE) >= 75.0
    )

    # For a second-stage refinement, require a meaningful incremental gain
    # over already-positive PART18 baseline.
    incremental_ok = improvement >= 0.75

    print("CONTROL_OK", control_ok)
    print("BASE_NET", f"{base_net:+.3f}%")
    print("BEST_VARIANT", best_name)
    print("BEST_NET", f"{float(best.NET):+.3f}%")
    print("INCREMENTAL_GAIN", f"{improvement:+.3f}%")
    print("SAMPLE_OK", sample_ok)
    print("ROBUST_OK", robust_ok)
    print("INCREMENTAL_OK", incremental_ok)

    if control_ok and sample_ok and robust_ok and incremental_ok:
        print("DECISION: EXIT_CAPTURE PASSES.")
        print("NEXT: freeze PART18 + selected exit policy and run cost/stress validation.")
    else:
        print("DECISION: KEEP BASE EXIT.")
        print("NEXT: freeze PART18 + V4 exit and run cost/stress validation.")

    print()
    print("NOTE: only profit-capture policy changed.")
    print("NOTE: FULL participation gate remains fixed at 1.8.")


if __name__ == "__main__":
    main()
