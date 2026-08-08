import numpy as np
import pandas as pd
from datetime import datetime, timedelta

SYMS=['SOXL','SOXS','TQQQ','SQQQ','NVDA','AMD','AVGO','PLTR','TSLA','MU','META','AMZN','MSFT','GOOGL','AAPL']

def demo_candidates(seed=7):
    rng=np.random.default_rng(seed); rows=[]
    for i,s in enumerate(SYMS):
        price=float(rng.uniform(15,240)); day=float(rng.normal(2.2 if i%3 else 4.0,3.0))
        rows.append(dict(symbol=s, price=price, ma5=price/(1+max(-.03,min(.08,day/100*.6))),
            ma5_slope_pct=float(rng.uniform(-.3,1.6)), dollar_volume=float(rng.uniform(60e6,3e9)),
            rvol=float(rng.uniform(.8,4.2)), atr_pct=float(rng.uniform(.7,5.0)),
            premarket_pct=float(rng.normal(day*.4,1.2)), day_pct=day,
            sector_strength=float(rng.normal(.8,.8)), index_strength=float(rng.normal(.5,.5)),
            breakout_quality=float(rng.uniform(.2,1.0)), catalyst_score=float(rng.choice([0,0,2,5,8])),
            spread_pct=float(rng.uniform(.02,.35))))
    return pd.DataFrame(rows)

def demo_bars(symbol='SOXL', n=140, seed=11):
    rng=np.random.default_rng(seed + sum(map(ord,symbol))%31)
    base=100 + rng.uniform(10,50); drift=rng.uniform(.0001,.0009)
    rets=rng.normal(drift,.0035,n); close=base*np.cumprod(1+rets)
    vol=rng.integers(100_000,1_200_000,n).astype(float)
    # add a late momentum burst so demo can create signals
    close[-8:] *= np.linspace(1,1.018,8); vol[-5:] *= 3
    open_=np.r_[close[0],close[:-1]]; high=np.maximum(open_,close)*(1+rng.uniform(0,.002,n)); low=np.minimum(open_,close)*(1-rng.uniform(0,.002,n))
    start=datetime.now().replace(second=0,microsecond=0)-timedelta(minutes=n-1)
    return pd.DataFrame({'time':[start+timedelta(minutes=i) for i in range(n)],'open':open_,'high':high,'low':low,'close':close,'volume':vol})
