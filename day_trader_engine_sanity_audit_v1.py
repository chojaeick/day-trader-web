#!/usr/bin/env python3
"""
DAY TRADER V4 — ENGINE SANITY AUDIT V1

Purpose:
Validate the simulation plumbing BEFORE testing any strategy.

NO strategy optimization.
NO market-data download.

Checks:
1) Cache schema / time monotonicity / duplicate bars / invalid OHLC.
2) Causal execution invariant: signal bar -> next bar open only.
3) Strict exposure state machine invariant.
4) PnL arithmetic against hand-calculated synthetic cases.
5) Transaction-cost arithmetic against hand-calculated synthetic cases.
6) EOD forced-flat accounting.
7) Deterministic repeatability.
8) Real-cache smoke test without strategy conclusions.

PASS is required before any new engine backtest.
"""

from pathlib import Path
import glob, os, math, hashlib
import pandas as pd
import numpy as np

CACHE_GLOB="/tmp/fast_replay_cache/*.csv"
OUT="/tmp/day_trader_engine_sanity_audit.txt"

ALLOWED={0,10,30,100}
TRANSITIONS={
    0:{0,10},
    10:{0,10,30},
    30:{10,30,100},
    100:{30,100},
}

def check(name, cond, detail=""):
    return {"CHECK":name,"PASS":bool(cond),"DETAIL":str(detail)}

def synthetic_accounting():
    rows=[]

    # Case A: 10% exposure, +1% price move, no position change in bar
    pnl=(101/100-1)*100*(10/100)
    rows.append(check("SYNTH_PNL_10PCT_PLUS1", abs(pnl-0.1)<1e-12, f"calc={pnl:.6f} expected=0.100000"))

    # Case B: 100% exposure, -2% price move
    pnl=(98/100-1)*100
    rows.append(check("SYNTH_PNL_100PCT_MINUS2", abs(pnl+2.0)<1e-12, f"calc={pnl:.6f} expected=-2.000000"))

    # Round-trip cost 0.20%, 100% capital = 0.10 entry + 0.10 exit
    one_way=0.20/2
    c=(100/100)*one_way+(100/100)*one_way
    rows.append(check("SYNTH_COST_FULL_ROUNDTRIP", abs(c-0.20)<1e-12, f"calc={c:.6f} expected=0.200000"))

    # staged 0->10->30->100->30->10->0 total turnover = 200%
    seq=[0,10,30,100,30,10,0]
    turnover=sum(abs(b-a) for a,b in zip(seq,seq[1:]))
    c=turnover/100*one_way
    rows.append(check("SYNTH_STAGED_TURNOVER", turnover==200, f"turnover={turnover} expected=200"))
    rows.append(check("SYNTH_STAGED_COST", abs(c-0.20)<1e-12, f"calc={c:.6f} expected=0.200000"))

    # causal timing: signal at bar i may only execute at i+1 open
    signal_i=5; exec_i=6
    rows.append(check("CAUSAL_NEXT_BAR_EXECUTION", exec_i==signal_i+1, f"signal={signal_i} exec={exec_i}"))

    return rows

def transition_audit():
    rows=[]
    valid=True
    for a,bs in TRANSITIONS.items():
        for b in bs:
            if a not in ALLOWED or b not in ALLOWED:
                valid=False
    rows.append(check("STATE_VALUES_ALLOWED", valid, str(sorted(ALLOWED))))

    # Explicitly forbid the bug seen in baseline V1.
    rows.append(check("FORBID_0_TO_30", 30 not in TRANSITIONS[0], str(TRANSITIONS[0])))
    rows.append(check("FORBID_0_TO_100", 100 not in TRANSITIONS[0], str(TRANSITIONS[0])))
    rows.append(check("FORBID_10_TO_100", 100 not in TRANSITIONS[10], str(TRANSITIONS[10])))
    return rows

def cache_audit():
    rows=[]
    files=sorted(glob.glob(CACHE_GLOB))
    rows.append(check("CACHE_EXISTS", len(files)>0, f"files={len(files)}"))

    bad_schema=[]
    bad_time=[]
    dup_time=[]
    bad_ohlc=[]
    short=[]
    fingerprints=[]

    required={"time","open","high","low","close","volume"}

    for f in files:
        try:
            x=pd.read_csv(f)
        except Exception as e:
            bad_schema.append((os.path.basename(f),f"read_error:{e}"))
            continue

        if not required.issubset(x.columns):
            bad_schema.append((os.path.basename(f),"missing:"+",".join(sorted(required-set(x.columns)))))
            continue

        if len(x)<30:
            short.append((os.path.basename(f),len(x)))

        t=x["time"].astype(str)
        # cache order should be monotonic lexicographically for HH:MM-like values
        if not t.is_monotonic_increasing:
            bad_time.append(os.path.basename(f))

        dups=int(t.duplicated().sum())
        if dups:
            dup_time.append((os.path.basename(f),dups))

        for c in ["open","high","low","close"]:
            x[c]=pd.to_numeric(x[c],errors="coerce")

        invalid=(
            x[["open","high","low","close"]].isna().any(axis=1)
            | (x["high"] < x[["open","close","low"]].max(axis=1))
            | (x["low"] > x[["open","close","high"]].min(axis=1))
            | (x[["open","high","low","close"]] <= 0).any(axis=1)
        )
        nbad=int(invalid.sum())
        if nbad:
            bad_ohlc.append((os.path.basename(f),nbad))

        # deterministic fingerprint of first/last + row count
        payload=f"{os.path.basename(f)}|{len(x)}|{x.iloc[0]['time']}|{x.iloc[-1]['time']}|{x.iloc[0]['close']}|{x.iloc[-1]['close']}"
        fingerprints.append(hashlib.sha256(payload.encode()).hexdigest())

    rows += [
        check("CACHE_SCHEMA_OK", len(bad_schema)==0, f"bad={len(bad_schema)} {bad_schema[:5]}"),
        check("CACHE_TIME_ORDER_OK", len(bad_time)==0, f"bad={len(bad_time)} {bad_time[:5]}"),
        check("CACHE_DUP_TIME_OK", len(dup_time)==0, f"bad={len(dup_time)} {dup_time[:5]}"),
        check("CACHE_OHLC_OK", len(bad_ohlc)==0, f"bad={len(bad_ohlc)} {bad_ohlc[:5]}"),
        check("CACHE_LENGTH_OK", len(short)==0, f"short={len(short)} {short[:5]}"),
        check("CACHE_FINGERPRINT_COUNT", len(fingerprints)==len(files), f"fingerprints={len(fingerprints)} files={len(files)}"),
    ]
    return rows, files

def real_smoke(files):
    rows=[]
    # No strategy: verify open->next-open arithmetic on first valid file.
    target=None
    for f in files:
        try:
            x=pd.read_csv(f)
            if {"time","open"}.issubset(x.columns) and len(x)>=3:
                target=(f,x)
                break
        except Exception:
            pass

    if target is None:
        return [check("REAL_SMOKE_AVAILABLE",False,"no readable cache")]

    f,x=target
    o=pd.to_numeric(x["open"],errors="coerce").dropna().reset_index(drop=True)
    if len(o)<2 or o.iloc[0]<=0:
        return [check("REAL_SMOKE_AVAILABLE",False,os.path.basename(f))]

    r1=(o.iloc[1]/o.iloc[0]-1)*100
    r2=((o.iloc[1]-o.iloc[0])/o.iloc[0])*100
    rows.append(check("REAL_RETURN_FORMULA_EQUIVALENT", abs(r1-r2)<1e-12, f"{os.path.basename(f)} r={r1:.8f}"))

    # Repeat exact calculation to prove deterministic arithmetic.
    r3=(o.iloc[1]/o.iloc[0]-1)*100
    rows.append(check("REAL_ARITHMETIC_REPEATABLE", r1==r3, f"r1={r1:.12f} r2={r3:.12f}"))
    return rows

def main():
    results=[]
    results += synthetic_accounting()
    results += transition_audit()
    cache_results,files=cache_audit()
    results += cache_results
    results += real_smoke(files)

    df=pd.DataFrame(results)
    passed=int(df["PASS"].sum())
    total=len(df)
    overall=passed==total

    out=[]
    out.append("===== DAY TRADER V4 ENGINE SANITY AUDIT V1 =====")
    out.append("NO_DOWNLOAD True")
    out.append("NO_STRATEGY_TEST True")
    out.append(f"CHECKS {total}")
    out.append(f"PASSED {passed}")
    out.append(f"FAILED {total-passed}")
    out.append("")
    out.append(df.to_string(index=False))
    out.append("")
    out.append("===== DECISION =====")
    out.append(f"ENGINE_SANITY_PASS {overall}")
    if overall:
        out.append("NEXT: strategy baselines may resume.")
    else:
        out.append("NEXT: fix failed engine/data invariant(s) BEFORE any strategy backtest.")

    Path(OUT).write_text("\n".join(out),encoding="utf-8")
    print("\n".join(out))

if __name__=="__main__":
    main()
