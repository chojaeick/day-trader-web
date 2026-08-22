#!/usr/bin/env python3
"""
TREND V5.2 minimal FULL participation gate sweep.

Purpose:
- V5.1 showed PART15 and FLOW_COMBO produced identical trades/results.
- This test checks whether participation alone is the active causal variable,
  and finds the weakest useful threshold around 1.5.

Baseline trading logic = TREND V4.
Only CONFIRM -> FULL permission changes.

Predeclared thresholds:
  BASE   : no extra FULL gate
  PART12 : participation >= 1.2
  PART13 : participation >= 1.3
  PART14 : participation >= 1.4
  PART15 : participation >= 1.5
  PART16 : participation >= 1.6
  PART18 : participation >= 1.8

No date/symbol exclusion.
"""

import glob
import os
import pandas as pd
import trend_v4_exit_debounce as v4

THRESHOLDS = [
    ("BASE", None),
    ("PART12", 1.2),
    ("PART13", 1.3),
    ("PART14", 1.4),
    ("PART15", 1.5),
    ("PART16", 1.6),
    ("PART18", 1.8),
]


def trade_one(x, i0, part_min):
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
    exit_i = min(i0 + v4.MAX_HOLD, len(x) - 1)

    peak_posret = 0.0
    floor_level = None
    below_refs_count = 0
    weak_full_count = 0
    gate_blocks = 0

    for i in range(i0 + 1, min(i0 + v4.MAX_HOLD, len(x) - 1) + 1):
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

        if confirm_done and not full_done and i >= i0 + 2:
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
                    gate_ok = True if part_min is None else part >= part_min
                    if gate_ok:
                        legs.append((px, v4.FULL_W, "FULL"))
                        deployed += v4.FULL_W
                        full_done = True
                        peak_posret = max(
                            0.0, v4.position_return(legs, sig, deployed)
                        )
                    else:
                        gate_blocks += 1
                        breakout_i = None
                        breakout_high = None
                elif bars_since >= 4 or sig < ema9 or sig < breakout_high * .995:
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

    w = x.loc[i0:min(i0 + 30, len(x) - 1)]
    mfe30 = (float(w.high.max()) / entry0 - 1) * 100
    mae30 = (float(w.low.min()) / entry0 - 1) * 100

    return dict(
        ENTRY_TIME=str(r0.time),
        EXIT_TIME=str(x.loc[exit_i, "time"]),
        SCORE=int(v4.score_row(x, i0)[0]),
        DEPLOYED=deployed,
        LEGS="+".join(name for _, _, name in legs),
        EXIT_REASON=exit_reason,
        GROSS=gross,
        COST=cost,
        NET=net,
        PEAK_POSRET=peak_posret,
        FLOOR_POSRET=floor_level,
        FINAL_POSRET=v4.position_return(legs, exit_px, deployed),
        MFE30=mfe30,
        MAE30=mae30,
        GATE_BLOCKS=gate_blocks,
    )


def collect(label, part_min):
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
        if not needed.issubset(x.columns):
            continue

        sessions += 1
        case = os.path.basename(f).replace(".csv", "")
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

            t = trade_one(x, i, part_min)
            t.update(
                CASE=case, SYMBOL=symbol, DATE=date,
                VARIANT=label, PART_MIN=part_min, **feat
            )
            rows.append(t)

            exit_idx = x.index[x.time.astype(str) == t["EXIT_TIME"]]
            if len(exit_idx):
                next_i = int(exit_idx[0]) + 1
            else:
                next_i = i + v4.MAX_HOLD + 1

    return pd.DataFrame(rows), sessions


def metrics(label, z):
    full = z[z.DEPLOYED >= .999]
    return {
        "VARIANT": label,
        "TRADES": len(z),
        "NET": z.NET.sum(),
        "AVG": z.NET.mean(),
        "WIN_RATE": (z.NET > 0).mean()*100,
        "FULL": len(full),
        "FULL_LOSERS": int((full.NET <= 0).sum()),
        "FULL_NET": full.NET.sum(),
        "GATE_BLOCKS": int(z.GATE_BLOCKS.sum()),
        "JUN12_NET": z[z.DATE.astype(str)=="20260612"].NET.sum(),
        "OTHER_NET": z[z.DATE.astype(str)!="20260612"].NET.sum(),
    }


def main():
    print("===== TREND V5.2 MINIMAL FULL PARTICIPATION GATE =====")

    results = {}
    summaries = []
    sessions = None

    for label, part_min in THRESHOLDS:
        z, sessions = collect(label, part_min)
        results[label] = z
        summaries.append(metrics(label, z))
        z.to_csv(f"/tmp/trend_v52_{label.lower()}_trades.csv", index=False)

    print("SESSIONS", sessions)
    print()

    s = pd.DataFrame(summaries)
    base_net = float(s[s.VARIANT=="BASE"].iloc[0].NET)
    base_full = float(s[s.VARIANT=="BASE"].iloc[0].FULL)
    s["IMPROVEMENT"] = s.NET - base_net
    s["FULL_PRESERVE"] = s.FULL / max(base_full, 1.0) * 100

    print("===== THRESHOLD SWEEP =====")
    print(
        s.round(3)
         .sort_values(["NET","FULL_PRESERVE"], ascending=[False,False])
         .to_string(index=False)
    )

    print()
    print("===== LEAVE-ONE-DATE-OUT =====")
    dates = sorted({d for z in results.values() for d in z.DATE.astype(str).unique()})
    rows = []

    for label, z in results.items():
        for holdout in dates:
            r = z[z.DATE.astype(str) != holdout]
            rows.append({
                "VARIANT": label,
                "EXCLUDED_DATE": holdout,
                "NET_REMAINING": r.NET.sum(),
                "TRADES_REMAINING": len(r),
            })

    loo = pd.DataFrame(rows)
    robust = loo.groupby("VARIANT").agg(
        LOO_MIN_NET=("NET_REMAINING","min"),
        LOO_AVG_NET=("NET_REMAINING","mean"),
        LOO_POSITIVE=("NET_REMAINING", lambda x: int((x>0).sum())),
        LOO_N=("NET_REMAINING","size"),
    ).reset_index()
    robust["LOO_POS_RATE"] = robust.LOO_POSITIVE / robust.LOO_N * 100

    print(
        robust.round(3)
              .sort_values(["LOO_MIN_NET","LOO_AVG_NET"], ascending=False)
              .to_string(index=False)
    )

    merged = s.merge(robust, on="VARIANT", how="left")
    cand = merged[merged.VARIANT!="BASE"].copy()

    # Prefer robust positive net, then weakest threshold / highest FULL preservation.
    cand["ROBUST_OK"] = (
        (cand.LOO_MIN_NET > 0) &
        (cand.LOO_POS_RATE >= 75.0)
    )
    cand["POSITIVE_OK"] = cand.NET > 0
    cand["SAMPLE_OK"] = cand.TRADES >= 40

    valid = cand[cand.ROBUST_OK & cand.POSITIVE_OK & cand.SAMPLE_OK].copy()

    print()
    print("===== DECISION =====")
    print("BASE_NET", f"{base_net:+.3f}%")

    if len(valid):
        # Among robust candidates, choose the least restrictive threshold
        # within 0.25% Net of the best result.
        best_net = valid.NET.max()
        near = valid[valid.NET >= best_net - 0.25].copy()

        order = {"PART12":1.2,"PART13":1.3,"PART14":1.4,"PART15":1.5,"PART16":1.6,"PART18":1.8}
        near["THR"] = near.VARIANT.map(order)
        chosen = near.sort_values(["THR","FULL_PRESERVE"], ascending=[True,False]).iloc[0]

        print("BEST_NET", f"{best_net:+.3f}%")
        print("CHOSEN", chosen.VARIANT)
        print("CHOSEN_NET", f"{float(chosen.NET):+.3f}%")
        print("IMPROVEMENT", f"{float(chosen.IMPROVEMENT):+.3f}%")
        print("FULL_PRESERVE", f"{float(chosen.FULL_PRESERVE):.1f}%")
        print("LOO_MIN_NET", f"{float(chosen.LOO_MIN_NET):+.3f}%")
        print("LOO_POS_RATE", f"{float(chosen.LOO_POS_RATE):.1f}%")
        print("DECISION: FREEZE MINIMAL PARTICIPATION FULL GATE.")
        print("NEXT: test EXIT_CAPTURE on top of this fixed gate only.")
    else:
        print("DECISION: NO ROBUST PARTICIPATION THRESHOLD.")
        print("NEXT: reject FULL participation gate and move to EXIT_CAPTURE.")

    print()
    print("NOTE: No date/symbol exclusions; only FULL permission threshold changes.")


if __name__ == "__main__":
    main()
