from __future__ import annotations
import requests
from dotenv import load_dotenv
load_dotenv("/home/ubuntu/day-trader-api/.env")
from .config import Settings
from .db import DB
from .kiwoom import KiwoomClient, num


def clamp(v,lo=0.0,hi=100.0):
    return max(lo,min(hi,v))


def fetch_industry_strength():
    s=Settings(); db=DB(s.db_path); k=KiwoomClient(s,db)
    k.get_token()

    r=requests.post(
        s.rest_base+"/api/us/sect",
        headers=k.headers("usa23000"),
        json={"stex_tp":"0","inds_cd":"0"},
        timeout=20
    )
    d=r.json()
    if d.get("return_code") not in (None,0):
        raise RuntimeError(f"usa23000: {d.get('return_code')} {d.get('return_msg')}")

    rows=[]
    for x in d.get("result_list") or []:
        p1=num(x.get("perf_1d"))
        p5=num(x.get("perf_5d"))
        p1m=num(x.get("perf_1m"))
        score=clamp(50 + p1*12 + p5*2 + p1m*0.5)
        rows.append({
            "inds_cd":str(x.get("inds_cd") or ""),
            "inds_nm":str(x.get("inds_nm") or ""),
            "perf_1d":p1,
            "perf_5d":p5,
            "perf_1m":p1m,
            "industry_power":round(score,1)
        })

    rows.sort(key=lambda z:z["industry_power"],reverse=True)
    return rows


if __name__=="__main__":
    rows=fetch_industry_strength()
    print("=== KIWOOM INDUSTRY POWER TOP10 ===")
    for i,x in enumerate(rows[:10],1):
        print(f"{i:2}. {x['inds_nm'][:24]:24} P={x['industry_power']:5.1f} 1D={x['perf_1d']:+6.2f}% 5D={x['perf_5d']:+6.2f}% 1M={x['perf_1m']:+6.2f}%")
