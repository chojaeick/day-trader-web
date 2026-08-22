#!/usr/bin/env python3
"""
TREND V5.1 causal FULL confirmation sweep.

Baseline = TREND V4.
Only the transition CONFIRM -> FULL is changed.

Idea:
- V4 confirms PROBE -> CONFIRM as before.
- FULL deployment is allowed only if the breakout/hold/retest is supported by
  stronger contemporaneous flow evidence.
- Exit logic, entry score, PROBE sizing, CONFIRM sizing, debounce, hard risk,
  and profit floor remain unchanged.

Predeclared grid:
  BASE        : exact V4 behavior (control)
  MFI0        : FULL requires current MFI >= entry MFI
  MFI5        : FULL requires current MFI >= entry MFI + 5
  PART15      : FULL requires participation >= 1.5
  FLOW_COMBO  : FULL requires MFI >= entry MFI AND participation >= 1.5

No symbol/date exclusion.
"""

import glob
import os
import pandas as pd

import trend_v4_exit_debounce as v4


VARIANTS = [
    "BASE",
    "MFI0",
    "MFI5",
    "PART15",
    "FLOW_COMBO",
]


def full_gate_ok(variant, r, entry_mfi):
    mfi = float(r.mfi14)
    part = float(r.participation)

    if variant == "BASE":
        return True
    if variant == "MFI0":
        return mfi >= entry_mfi
    if variant == "MFI5":
        return mfi >= entry_mfi + 5.0
    if variant == "PART15":
        return part >= 1.5
    if variant == "FLOW_COMBO":
        return (mfi >= entry_mfi) and (part >= 1.5)

    raise ValueError(variant)


def trade_one_variant(x, i0, variant):
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

    full_gate_passes = 0
    full_gate_blocks = 0

    for i in range(i0+1, min(i0+v4.MAX_HOLD, len(x)-1)+1):
        r = x.loc[i]
        sig = float(r.signal_price)
        ema9 = float(r.ema9)
        ema20 = float(r.ema20)
        vwap = float(r.vwap)
        mfi = float(r.mfi14)
        vo = float(r.vo_raw)
        px = float(x.loc[i, "open"])

        cur_posret = v4.position_return(legs, sig, deployed)
        peak_posret = max(peak_posret, cur_posret)

        floor = None
        if peak_posret >= 3.0:
            floor = 1.80
        elif peak_posret >= 2.0:
            floor = 1.00
        elif peak_posret >= 1.5:
            floor = 0.60
        elif peak_posret >= 1.0:
            floor = 0.10

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
                peak_posret = max(0.0, v4.position_return(legs, sig, deployed))

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
                    if full_gate_ok(variant, r, entry_mfi):
                        full_gate_passes += 1
                        legs.append((px, v4.FULL_W, "FULL"))
                        deployed += v4.FULL_W
                        full_done = True
                        peak_posret = max(
                            0.0,
                            v4.position_return(legs, sig, deployed)
                        )
                    else:
                        full_gate_blocks += 1
                        # Re-arm breakout search. We do not force FULL later
                        # off the same stale breakout.
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
        FULL_GATE_PASSES=full_gate_passes,
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

            t = trade_one_variant(x, i, variant)
            t.update(
                CASE=case,
                SYMBOL=symbol,
                DATE=date,
                VARIANT=variant,
                **feat
            )
            rows.append(t)

            exit_idx = x.index[x.time.astype(str) == t["EXIT_TIME"]]
            if len(exit_idx):
                next_i = int(exit_idx[0]) + 1
            else:
                next_i = i + v4.MAX_HOLD + 1

    return pd.DataFrame(rows), sessions


def metrics(variant, z):
    if len(z) == 0:
        return dict(
            VARIANT=variant, TRADES=0, NET=0, AVG=0, WIN_RATE=0,
            FULL=0, CONFIRM_ONLY=0, PROBE_ONLY=0,
            FULL_LOSERS=0, FULL_NET=0, GATE_BLOCKS=0,
            JUN12_NET=0, OTHER_NET=0
        )

    full = z[z.DEPLOYED >= .999]
    return dict(
        VARIANT=variant,
        TRADES=len(z),
        NET=z.NET.sum(),
        AVG=z.NET.mean(),
        WIN_RATE=(z.NET > 0).mean()*100,
        FULL=int((z.DEPLOYED >= .999).sum()),
        CONFIRM_ONLY=int(((z.DEPLOYED > .10) & (z.DEPLOYED < .999)).sum()),
        PROBE_ONLY=int((z.DEPLOYED <= .10).sum()),
        FULL_LOSERS=int(((z.DEPLOYED >= .999) & (z.NET <= 0)).sum()),
        FULL_NET=full.NET.sum(),
        GATE_BLOCKS=int(z.FULL_GATE_BLOCKS.sum()),
        JUN12_NET=z[z.DATE.astype(str) == "20260612"].NET.sum(),
        OTHER_NET=z[z.DATE.astype(str) != "20260612"].NET.sum(),
    )


def main():
    print("===== TREND V5.1 FULL CONFIRMATION CAUSAL TEST =====")

    results = {}
    summaries = []
    sessions_seen = None

    for variant in VARIANTS:
        z, sessions = collect_variant(variant)
        sessions_seen = sessions
        results[variant] = z
        summaries.append(metrics(variant, z))
        z.to_csv(
            f"/tmp/trend_v51_{variant.lower()}_trades.csv",
            index=False
        )

    print("SESSIONS", sessions_seen)
    print()
    s = pd.DataFrame(summaries)

    print("===== FULL SAMPLE SWEEP =====")
    print(
        s.round(3)
        .sort_values(["NET","AVG"], ascending=False)
        .to_string(index=False)
    )

    base = s[s.VARIANT == "BASE"].iloc[0]

    print()
    print("===== CONTROL CHECK =====")
    print("BASE NET", f"{float(base.NET):+.3f}%")
    print("EXPECTED V4 NET approx -0.078%")

    candidates = s[s.VARIANT != "BASE"].copy()
    candidates["IMPROVEMENT"] = candidates.NET - float(base.NET)

    print()
    print("===== IMPROVEMENT VS V4 CONTROL =====")
    print(
        candidates[
            [
                "VARIANT","TRADES","NET","IMPROVEMENT","AVG","WIN_RATE",
                "FULL","FULL_LOSERS","FULL_NET","GATE_BLOCKS",
                "JUN12_NET","OTHER_NET"
            ]
        ]
        .round(3)
        .sort_values("NET", ascending=False)
        .to_string(index=False)
    )

    best_name = candidates.sort_values(
        ["NET","AVG"], ascending=False
    ).iloc[0].VARIANT

    best = results[best_name]

    print()
    print("===== BEST VARIANT BY DATE =====")
    print("VARIANT", best_name)
    print(
        best.groupby("DATE").agg(
            N=("NET","size"),
            NET=("NET","sum"),
            AVG=("NET","mean"),
            WIN_RATE=("NET", lambda x: (x>0).mean()*100),
            FULL=("DEPLOYED", lambda x: int((x>=.999).sum())),
        ).round(3).sort_index().to_string()
    )

    print()
    print("===== BEST VARIANT BY SYMBOL =====")
    print(
        best.groupby("SYMBOL").agg(
            N=("NET","size"),
            NET=("NET","sum"),
            AVG=("NET","mean"),
            FULL=("DEPLOYED", lambda x: int((x>=.999).sum())),
        ).round(3).sort_values("NET", ascending=False).to_string()
    )

    print()
    print("===== LEAVE-ONE-DATE-OUT ROBUSTNESS =====")

    loo_rows = []
    dates = sorted(
        set(
            d
            for z in results.values()
            for d in z.DATE.astype(str).unique()
        )
    )

    for variant in VARIANTS:
        z = results[variant]
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
    print("===== CAUSAL DECISION =====")

    best_row = candidates.sort_values(
        ["NET","AVG"], ascending=False
    ).iloc[0]

    rr = robust[
        robust.VARIANT == best_name
    ].iloc[0]

    improvement = float(best_row.IMPROVEMENT)

    sample_ok = int(best_row.TRADES) >= 40
    economics_ok = (
        float(best_row.NET) >= 2.0
        and improvement >= 2.0
    )
    robust_ok = (
        float(rr.LOO_MIN_NET) > 0
        and float(rr.LOO_POS_RATE) >= 75.0
    )

    # Avoid "winning" just by suppressing almost all FULL deployment.
    full_preservation = (
        float(best_row.FULL) / max(float(base.FULL), 1.0)
    )
    deployment_ok = full_preservation >= 0.50

    print("BEST_VARIANT", best_name)
    print("BASE_NET", f"{float(base.NET):+.3f}%")
    print("BEST_NET", f"{float(best_row.NET):+.3f}%")
    print("IMPROVEMENT", f"{improvement:+.3f}%")
    print("FULL_PRESERVATION", f"{full_preservation*100:.1f}%")
    print("SAMPLE_OK", sample_ok)
    print("DEPLOYMENT_OK", deployment_ok)
    print("ECONOMICS_OK", economics_ok)
    print("ROBUST_OK", robust_ok)

    if sample_ok and deployment_ok and economics_ok and robust_ok:
        print("DECISION: FULL_CONFIRMATION PASSES.")
        print("NEXT: freeze this FULL gate and test EXIT_CAPTURE only.")
    else:
        print("DECISION: FULL_CONFIRMATION DOES NOT PASS ROBUSTLY.")
        print("NEXT: reject this FULL-gate family and audit exit-capture / cost sensitivity.")

    print()
    print("NOTE: BASE must reproduce V4 before any conclusion is trusted.")
    print("NOTE: No date or symbol exclusion was used.")


if __name__ == "__main__":
    main()
