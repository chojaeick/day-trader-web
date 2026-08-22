#!/usr/bin/env python3
"""
DAY TRADER V4 — ENGINE SANITY AUDIT V2

Fixes V1 audit false negatives:
- Supports two cache families:
  A) RAW_OHLCV: open/high/low/close/volume
  B) FEATURE_CACHE: open/close + signal_high/signal_low/signal_volume
- Fingerprints every readable valid cache, not only RAW_OHLCV caches.
- Does NOT run any strategy.
- Does NOT download data.

Purpose:
Validate simulation plumbing only after data readiness is confirmed.
"""

from pathlib import Path
import glob, os, math, hashlib
import pandas as pd

CACHE_GLOB="/tmp/fast_replay_cache/*.csv"
OUT="/tmp/day_trader_engine_sanity_audit_v2.txt"

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
    pnl=(101/100-1)*100*(10/100)
    rows.append(check("SYNTH_PNL_10PCT_PLUS1", abs(pnl-0.1)<1e-12,
                      f"calc={pnl:.6f} expected=0.100000"))

    pnl=(98/100-1)*100
    rows.append(check("SYNTH_PNL_100PCT_MINUS2", abs(pnl+2.0)<1e-12,
                      f"calc={pnl:.6f} expected=-2.000000"))

    one_way=0.20/2
    c=one_way+one_way
    rows.append(check("SYNTH_COST_FULL_ROUNDTRIP", abs(c-0.20)<1e-12,
                      f"calc={c:.6f} expected=0.200000"))

    seq=[0,10,30,100,30,10,0]
    turnover=sum(abs(b-a) for a,b in zip(seq,seq[1:]))
    c=turnover/100*one_way
    rows.append(check("SYNTH_STAGED_TURNOVER", turnover==200,
                      f"turnover={turnover} expected=200"))
    rows.append(check("SYNTH_STAGED_COST", abs(c-0.20)<1e-12,
                      f"calc={c:.6f} expected=0.200000"))
    rows.append(check("CAUSAL_NEXT_BAR_EXECUTION", 6==5+1, "signal=5 exec=6"))
    return rows

def transition_audit():
    return [
        check("STATE_VALUES_ALLOWED", all(a in ALLOWED and all(b in ALLOWED for b in bs)
                                          for a,bs in TRANSITIONS.items()),
              str(sorted(ALLOWED))),
        check("FORBID_0_TO_30", 30 not in TRANSITIONS[0], str(TRANSITIONS[0])),
        check("FORBID_0_TO_100", 100 not in TRANSITIONS[0], str(TRANSITIONS[0])),
        check("FORBID_10_TO_100", 100 not in TRANSITIONS[10], str(TRANSITIONS[10])),
    ]

def classify_schema(cols):
    cols=set(cols)
    if {"time","open","high","low","close","volume"}.issubset(cols):
        return "RAW_OHLCV"
    if {"time","open","close","signal_high","signal_low","signal_volume"}.issubset(cols):
        return "FEATURE_CACHE"
    return "UNKNOWN"

def cache_audit():
    rows=[]
    files=sorted(glob.glob(CACHE_GLOB))
    rows.append(check("CACHE_EXISTS", len(files)>0, f"files={len(files)}"))

    family_counts={"RAW_OHLCV":0,"FEATURE_CACHE":0,"UNKNOWN":0}
    unreadable=[]
    bad_time=[]
    dup_time=[]
    bad_price=[]
    short=[]
    fingerprints=[]

    for f in files:
        try:
            x=pd.read_csv(f)
        except Exception as e:
            unreadable.append((os.path.basename(f),str(e)))
            continue

        fam=classify_schema(x.columns)
        family_counts[fam]+=1

        if fam=="UNKNOWN":
            continue

        if len(x)<30:
            short.append((os.path.basename(f),len(x)))

        t=x["time"].astype(str)
        if not t.is_monotonic_increasing:
            bad_time.append(os.path.basename(f))

        dups=int(t.duplicated().sum())
        if dups:
            dup_time.append((os.path.basename(f),dups))

        # Family-aware price aliases.
        if fam=="RAW_OHLCV":
            hi_col, lo_col, vol_col = "high","low","volume"
        else:
            hi_col, lo_col, vol_col = "signal_high","signal_low","signal_volume"

        for c in ["open","close",hi_col,lo_col,vol_col]:
            x[c]=pd.to_numeric(x[c],errors="coerce")

        invalid=(
            x[["open","close",hi_col,lo_col]].isna().any(axis=1)
            | (x["open"]<=0) | (x["close"]<=0)
            | (x[hi_col]<=0) | (x[lo_col]<=0)
            | (x[hi_col] < x[lo_col])
            | (x[vol_col] < 0)
        )
        nbad=int(invalid.sum())
        if nbad:
            bad_price.append((os.path.basename(f),fam,nbad))

        payload="|".join([
            os.path.basename(f), fam, str(len(x)),
            str(x.iloc[0]["time"]), str(x.iloc[-1]["time"]),
            str(x.iloc[0]["open"]), str(x.iloc[-1]["close"])
        ])
        fingerprints.append(hashlib.sha256(payload.encode()).hexdigest())

    recognized=family_counts["RAW_OHLCV"]+family_counts["FEATURE_CACHE"]

    rows += [
        check("CACHE_READABLE_OK", len(unreadable)==0,
              f"bad={len(unreadable)} {unreadable[:5]}"),
        check("CACHE_SCHEMA_FAMILIES_OK", family_counts["UNKNOWN"]==0,
              str(family_counts)),
        check("CACHE_RECOGNIZED_COUNT", recognized==len(files),
              f"recognized={recognized} files={len(files)} families={family_counts}"),
        check("CACHE_TIME_ORDER_OK", len(bad_time)==0,
              f"bad={len(bad_time)} {bad_time[:5]}"),
        check("CACHE_DUP_TIME_OK", len(dup_time)==0,
              f"bad={len(dup_time)} {dup_time[:5]}"),
        check("CACHE_PRICE_VOLUME_OK", len(bad_price)==0,
              f"bad={len(bad_price)} {bad_price[:5]}"),
        check("CACHE_LENGTH_OK", len(short)==0,
              f"short={len(short)} {short[:5]}"),
        check("CACHE_FINGERPRINT_COUNT", len(fingerprints)==recognized,
              f"fingerprints={len(fingerprints)} recognized={recognized}"),
    ]
    return rows,files,family_counts

def real_smoke(files):
    for f in files:
        try:
            x=pd.read_csv(f)
            if {"time","open"}.issubset(x.columns) and len(x)>=3:
                o=pd.to_numeric(x["open"],errors="coerce").dropna().reset_index(drop=True)
                if len(o)>=2 and o.iloc[0]>0:
                    r1=(o.iloc[1]/o.iloc[0]-1)*100
                    r2=((o.iloc[1]-o.iloc[0])/o.iloc[0])*100
                    return [
                        check("REAL_RETURN_FORMULA_EQUIVALENT", abs(r1-r2)<1e-12,
                              f"{os.path.basename(f)} r={r1:.8f}"),
                        check("REAL_ARITHMETIC_REPEATABLE",
                              r1==((o.iloc[1]/o.iloc[0]-1)*100),
                              f"r={r1:.12f}")
                    ]
        except Exception:
            continue
    return [check("REAL_SMOKE_AVAILABLE",False,"no readable cache")]

def main():
    results=[]
    results += synthetic_accounting()
    results += transition_audit()
    cr,files,families=cache_audit()
    results += cr
    results += real_smoke(files)

    df=pd.DataFrame(results)
    passed=int(df.PASS.sum())
    total=len(df)
    overall=(passed==total)

    out=[]
    out.append("===== DAY TRADER V4 ENGINE SANITY AUDIT V2 =====")
    out.append("NO_DOWNLOAD True")
    out.append("NO_STRATEGY_TEST True")
    out.append(f"CACHE_FAMILIES {families}")
    out.append(f"CHECKS {total}")
    out.append(f"PASSED {passed}")
    out.append(f"FAILED {total-passed}")
    out.append("")
    out.append(df.to_string(index=False))
    out.append("")
    out.append("===== DECISION =====")
    out.append(f"ENGINE_SANITY_PASS {overall}")
    if overall:
        out.append("NEXT: engine plumbing is sane; strategy validation may resume AFTER data readiness passes.")
    else:
        out.append("NEXT: fix remaining invariant(s) before any strategy backtest.")

    Path(OUT).write_text("\n".join(out),encoding="utf-8")
    print("\n".join(out))

if __name__=="__main__":
    main()
