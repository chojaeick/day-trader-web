from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone

def num(v, default=0.0):
    if v is None: return default
    try: return float(str(v).strip().replace(',','').lstrip('+'))
    except Exception: return default

def exchange_code(v: str) -> str:
    s=str(v or '').strip().upper()
    return {'1':'NY','2':'ND','3':'AM','NYSE':'NY','NASDAQ':'ND','AMEX':'AM','NY':'NY','ND':'ND','AM':'AM'}.get(s,'ND')

@dataclass
class DiscoveryResult:
    symbols: list[str]
    rows: list[dict]
    updated_at: str

def merge_rankings(volume_rows:list[dict], dollar_rows:list[dict], core:list[str],
                   limit:int=35, min_price:float=5.0, min_dollar:float=20_000_000) -> DiscoveryResult:
    merged={}
    def ingest(rows, source):
        for pos,x in enumerate(rows,1):
            sym=str(x.get('stk_cd') or '').strip().upper()
            if not sym or len(sym)>12: continue
            price=abs(num(x.get('cur_prc'))); dollar=abs(num(x.get('trde_prica')))
            volume=abs(num(x.get('acc_trde_qty'))); chg=num(x.get('flu_rt'))
            if price < min_price: continue
            rec=merged.setdefault(sym,{
                'symbol':sym,'exchange':exchange_code(x.get('stex_tp')),
                'name':x.get('stk_enm') or x.get('stk_nm') or '',
                'price':price,'change_pct':chg,'volume':volume,'dollar_volume':dollar,
                'volume_rank':9999,'dollar_rank':9999,'sources':set()
            })
            rec['price']=price or rec['price']; rec['change_pct']=chg
            rec['volume']=max(rec['volume'],volume); rec['dollar_volume']=max(rec['dollar_volume'],dollar)
            rec[source+'_rank']=min(rec[source+'_rank'],pos); rec['sources'].add(source)
    ingest(volume_rows,'volume'); ingest(dollar_rows,'dollar')
    for rec in merged.values():
        vr=rec['volume_rank'] if rec['volume_rank']<9999 else 100
        dr=rec['dollar_rank'] if rec['dollar_rank']<9999 else 100
        rank_score=max(0,60-vr*0.35-dr*0.35)
        momentum=min(20,abs(rec['change_pct'])*2)
        liq=min(20,rec['dollar_volume']/100_000_000*4)
        chase_penalty=0
        if abs(rec['change_pct']) >= 20:
            chase_penalty=18
        elif abs(rec['change_pct']) >= 12:
            chase_penalty=10
        elif abs(rec['change_pct']) >= 8:
            chase_penalty=5
        rec['chase_risk']='HIGH' if chase_penalty>=10 else ('MEDIUM' if chase_penalty else 'NORMAL')
        rec['discovery_score']=round(rank_score+momentum+liq-chase_penalty,1)
    eligible=[r for r in merged.values() if r['dollar_volume']>=min_dollar or r['volume_rank']<=25]
    eligible.sort(key=lambda r:(r['discovery_score'],r['dollar_volume']), reverse=True)
    picked=eligible[:limit]
    for sym in reversed(core):
        if sym not in [r['symbol'] for r in picked]:
            picked.insert(0,{'symbol':sym,'exchange':'','name':'CORE','price':0,'change_pct':0,'volume':0,
                             'dollar_volume':0,'volume_rank':9999,'dollar_rank':9999,
                             'sources':{'core'},'discovery_score':999})
    seen=set(); symbols=[]
    for r in picked:
        if r['symbol'] not in seen:
            symbols.append(r['symbol']); seen.add(r['symbol'])
        r['sources']=','.join(sorted(r['sources']))
        r['origin']='CORE' if r['symbol'] in core else 'AUTO'
    return DiscoveryResult(symbols=symbols,rows=picked,updated_at=datetime.now(timezone.utc).isoformat())
