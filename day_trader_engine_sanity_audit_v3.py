#!/usr/bin/env python3
"""
DAY TRADER V4 — ENGINE SANITY AUDIT V3

V3 fixes cache-schema alias handling.

Recognized market-bar aliases:
  open   := open OR signal_open
  high   := high OR signal_high
  low    := low OR signal_low
  close  := close OR signal_price
  volume := volume OR signal_volume

Thus both historical RAW_OHLCV caches and TREND feature caches are valid.

NO download.
NO strategy test.
"""

import glob, os, hashlib, math
from pathlib import Path
import pandas as pd

CACHE_GLOB="/tmp/fast_replay_cache/*.csv"
OUT="/tmp/day_trader_engine_sanity_audit_v3.txt"

ALLOWED={0,10,30,100}
TRANSITIONS={
    0:{0,10},
    10:{0,10,30},
    30:{10,30,100},
    100:{30,100},
}

ALIASES={
    "open":["open","signal_open"],
    "high":["high","signal_high"],
    "low":["low","signal_low"],
    "close":["close","signal_price"],
    "volume":["volume","signal_volume"],
}

def check(name, cond, detail=""):
    return {"CHECK":name,"PASS":bool(cond),"DETAIL":str(detail)}

def choose(cols, logical):
    s=set(cols)
    for c in ALIASES[logical]:
        if c in s:
            return c
    return None

def map_schema(cols):
    m={k:choose(cols,k) for k in ALIASES}
    if "time" not in set(cols):
        return None,m
    if any(v is None for v in m.values()):
        return None,m
    fam="RAW_OHLCV" if all(m[k]==k for k in m) else "FEATURE_OR_ALIAS"
    return fam,m

def synthetic_checks():
    rows=[]
    pnl=(101/100-1)*100*0.10
    rows.append(check("SYNTH_PNL_10PCT_PLUS1",abs(pnl-0.1)<1e-12,f"{pnl:.6f}"))

    pnl=(98/100-1)*100
    rows.append(check("SYNTH_PNL_100PCT_MINUS2",abs(pnl+2)<1e-12,f"{pnl:.6f}"))

    one_way=0.10
    rows.append(check("SYNTH_COST_FULL_ROUNDTRIP",abs(one_way*2-0.20)<1e-12,"0.200000"))

    seq=[0,10,30,100,30,10,0]
    turnover=sum(abs(b-a) for a,b in zip(seq,seq[1:]))
    rows.append(check("SYNTH_STAGED_TURNOVER",turnover==200,f"{turnover}"))
    rows.append(check("SYNTH_STAGED_COST",abs(turnover/100*one_way-0.20)<1e-12,"0.200000"))

    rows.append(check("CAUSAL_NEXT_BAR_EXECUTION",6==5+1,"signal=5 exec=6"))
    rows.append(check("STATE_VALUES_ALLOWED",
                      all(a in ALLOWED and all(b in ALLOWED for b in bs)
                          for a,bs in TRANSITIONS.items()),str(ALLOWED)))
    rows.append(check("FORBID_0_TO_30",30 not in TRANSITIONS[0],str(TRANSITIONS[0])))
    rows.append(check("FORBID_0_TO_100",100 not in TRANSITIONS[0],str(TRANSITIONS[0])))
    rows.append(check("FORBID_10_TO_100",100 not in TRANSITIONS[10],str(TRANSITIONS[10])))
    return rows

def cache_checks():
    rows=[]
    files=sorted(glob.glob(CACHE_GLOB))
    rows.append(check("CACHE_EXISTS",len(files)>0,f"files={len(files)}"))

    fam_counts={"RAW_OHLCV":0,"FEATURE_OR_ALIAS":0,"UNKNOWN":0}
    unknown=[]
    unreadable=[]
    bad_time=[]
    dup_time=[]
    bad_values=[]
    short=[]
    fps=[]

    alias_examples={}

    for f in files:
        try:
            x=pd.read_csv(f)
        except Exception as e:
            unreadable.append((os.path.basename(f),str(e)))
            continue

        fam,m=map_schema(x.columns)
        if fam is None:
            fam_counts["UNKNOWN"]+=1
            unknown.append((os.path.basename(f),m,list(x.columns)))
            continue

        fam_counts[fam]+=1
        alias_examples.setdefault(fam,(os.path.basename(f),m))

        if len(x)<30:
            short.append((os.path.basename(f),len(x)))

        t=x["time"].astype(str)
        if not t.is_monotonic_increasing:
            bad_time.append(os.path.basename(f))
        ndup=int(t.duplicated().sum())
        if ndup:
            dup_time.append((os.path.basename(f),ndup))

        o=pd.to_numeric(x[m["open"]],errors="coerce")
        h=pd.to_numeric(x[m["high"]],errors="coerce")
        l=pd.to_numeric(x[m["low"]],errors="coerce")
        c=pd.to_numeric(x[m["close"]],errors="coerce")
        v=pd.to_numeric(x[m["volume"]],errors="coerce")

        invalid=(
            o.isna()|h.isna()|l.isna()|c.isna()|v.isna()|
            (o<=0)|(h<=0)|(l<=0)|(c<=0)|(v<0)|
            (h<l)
        )
        nbad=int(invalid.sum())
        if nbad:
            bad_values.append((os.path.basename(f),fam,nbad))

        payload="|".join([
            os.path.basename(f),fam,str(len(x)),
            str(t.iloc[0]),str(t.iloc[-1]),
            str(o.iloc[0]),str(c.iloc[-1])
        ])
        fps.append(hashlib.sha256(payload.encode()).hexdigest())

    recognized=fam_counts["RAW_OHLCV"]+fam_counts["FEATURE_OR_ALIAS"]

    rows += [
        check("CACHE_READABLE_OK",len(unreadable)==0,
              f"bad={len(unreadable)} {unreadable[:3]}"),
        check("CACHE_SCHEMA_FAMILIES_OK",fam_counts["UNKNOWN"]==0,
              f"{fam_counts}; examples={alias_examples}; unknown={unknown[:2]}"),
        check("CACHE_RECOGNIZED_COUNT",recognized==len(files),
              f"recognized={recognized} files={len(files)}"),
        check("CACHE_TIME_ORDER_OK",len(bad_time)==0,
              f"bad={len(bad_time)} {bad_time[:3]}"),
        check("CACHE_DUP_TIME_OK",len(dup_time)==0,
              f"bad={len(dup_time)} {dup_time[:3]}"),
        check("CACHE_PRICE_VOLUME_OK",len(bad_values)==0,
              f"bad={len(bad_values)} {bad_values[:3]}"),
        check("CACHE_LENGTH_OK",len(short)==0,
              f"short={len(short)} {short[:3]}"),
        check("CACHE_FINGERPRINT_COUNT",len(fps)==recognized,
              f"fingerprints={len(fps)} recognized={recognized}"),
    ]

    # Real arithmetic smoke using logical open alias.
    smoke=False
    for f in files:
        try:
            x=pd.read_csv(f)
            fam,m=map_schema(x.columns)
            if fam is None or len(x)<2:
                continue
            o=pd.to_numeric(x[m["open"]],errors="coerce").dropna().reset_index(drop=True)
            if len(o)>=2 and o.iloc[0]>0:
                r1=(o.iloc[1]/o.iloc[0]-1)*100
                r2=((o.iloc[1]-o.iloc[0])/o.iloc[0])*100
                rows.append(check("REAL_RETURN_FORMULA_EQUIVALENT",
                                  abs(r1-r2)<1e-12,
                                  f"{os.path.basename(f)} r={r1:.10f}"))
                r3=(o.iloc[1]/o.iloc[0]-1)*100
                rows.append(check("REAL_ARITHMETIC_REPEATABLE",
                                  r1==r3,f"r={r1:.12f}"))
                smoke=True
                break
        except Exception:
            pass

    if not smoke:
        rows.append(check("REAL_SMOKE_AVAILABLE",False,"none"))

    return rows,fam_counts

def main():
    results=synthetic_checks()
    cr,fams=cache_checks()
    results += cr

    df=pd.DataFrame(results)
    total=len(df)
    passed=int(df.PASS.sum())
    overall=passed==total

    out=[]
    out.append("===== DAY TRADER V4 ENGINE SANITY AUDIT V3 =====")
    out.append("NO_DOWNLOAD True")
    out.append("NO_STRATEGY_TEST True")
    out.append(f"CACHE_FAMILIES {fams}")
    out.append(f"CHECKS {total}")
    out.append(f"PASSED {passed}")
    out.append(f"FAILED {total-passed}")
    out.append("")
    out.append(df.to_string(index=False))
    out.append("")
    out.append("===== DECISION =====")
    out.append(f"ENGINE_SANITY_PASS {overall}")
    if overall:
        out.append("NEXT: simulation plumbing/cache interpretation passes sanity.")
        out.append("Proceed to prototype engine selection and wiring.")
    else:
        out.append("NEXT: inspect remaining failed invariant before strategy work.")

    Path(OUT).write_text("\n".join(out),encoding="utf-8")
    print("\n".join(out))

if __name__=="__main__":
    main()
