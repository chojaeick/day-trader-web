from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from .quality_gate import grade_usa_row

def num(v, default=0.0):
    if v is None: return default
    try: return float(str(v).strip().replace(',','').lstrip('+'))
    except Exception: return default

def exchange_code(v: str) -> str:
    s=str(v or '').strip().upper()
    return {'1':'NY','2':'ND','3':'NA','NYSE':'NY','NASDAQ':'ND','AMEX':'NA','NY':'NY','ND':'ND','NA':'NA','AM':'NA'}.get(s,'ND')

def asset_type(name: str) -> str:
    n=str(name or '').upper()
    if 'ETF' in n or 'TRUST' in n or 'SPDR' in n or 'ISHARES' in n:
        if any(x in n for x in (' 2X',' 3X','2X ','3X ','ULTRA','BULL 2','BEAR 2','BULL 3','BEAR 3')):
            return 'LEVERAGED_ETF'
        return 'ETF'
    return 'STOCK'

@dataclass
class DiscoveryResult:
    symbols: list[str]
    rows: list[dict]
    updated_at: str
    extreme_rows: list[dict]
    quality_risk_rows: list[dict]
    quality_reject_rows: list[dict]

def merge_rankings(volume_rows:list[dict], dollar_rows:list[dict], core:list[str],
                   limit:int=40, min_price:float=5.0, min_dollar:float=5_000_000,
                   gainers:list[dict]|None=None, losers:list[dict]|None=None,
                   volume_surge:list[dict]|None=None) -> DiscoveryResult:
    merged={}
    source_weights={
        'volume': 1.0,
        'dollar': 1.2,
        'gainer': 0.9,
        'loser': 0.9,
        'surge': 1.15,
    }

    def ingest(rows, source):
        for pos,x in enumerate(rows or [],1):
            sym=str(x.get('stk_cd') or '').strip().upper()
            if not sym or len(sym)>12:
                continue
            price=abs(num(x.get('cur_prc')))
            dollar=abs(num(x.get('trde_prica')))
            volume=abs(num(x.get('acc_trde_qty') or x.get('trde_qty')))
            chg=num(x.get('flu_rt'))
            surge=num(x.get('sdnin_rt'))
            if price < min_price:
                continue
            rec=merged.setdefault(sym,{
                'symbol':sym,
                'exchange':exchange_code(x.get('stex_tp')),
                'name':x.get('stk_enm') or x.get('stk_nm') or '',
                'price':price,'change_pct':chg,'volume':volume,'dollar_volume':dollar,
                'volume_rank':9999,'dollar_rank':9999,'gainer_rank':9999,
                'loser_rank':9999,'surge_rank':9999,'surge_pct':0.0,
                'sources':set(),'asset_type':asset_type(x.get('stk_enm') or x.get('stk_nm') or '')
            })
            rec['price']=price or rec['price']
            if chg:
                rec['change_pct']=chg
            rec['volume']=max(rec['volume'],volume)
            rec['dollar_volume']=max(rec['dollar_volume'],dollar)
            if surge:
                rec['surge_pct']=surge
            rec[source+'_rank']=min(rec.get(source+'_rank',9999),pos)
            rec['sources'].add(source)

    ingest(volume_rows,'volume')
    ingest(dollar_rows,'dollar')
    ingest(gainers or [],'gainer')
    ingest(losers or [],'loser')
    ingest(volume_surge or [],'surge')

    for rec in merged.values():
        source_score=0.0
        for source,weight in source_weights.items():
            rank=rec.get(source+'_rank',9999)
            if rank < 9999:
                source_score += max(0, 24-rank*0.55)*weight

        # Strong dollar liquidity still matters most, but don't require it at the
        # discovery stage because change-rate and surge APIs don't always return it.
        liq=min(22,rec['dollar_volume']/100_000_000*5)
        move=min(18,abs(rec['change_pct'])*1.5)
        surge_bonus=min(16,max(0,rec['surge_pct'])/25) if rec['surge_pct'] else 0

        chase_penalty=0
        if abs(rec['change_pct']) >= 25:
            chase_penalty=22
        elif abs(rec['change_pct']) >= 18:
            chase_penalty=15
        elif abs(rec['change_pct']) >= 12:
            chase_penalty=9
        elif abs(rec['change_pct']) >= 8:
            chase_penalty=4

        rec['chase_risk']='HIGH' if chase_penalty>=15 else ('MEDIUM' if chase_penalty else 'NORMAL')
        rec['discovery_score']=round(source_score+liq+move+surge_bonus-chase_penalty,1)

    eligible=[]
    extreme_rows=[]
    for r in merged.values():
        liquid = r['dollar_volume'] >= min_dollar
        top_volume = r['volume_rank'] <= 35
        top_dollar = r['dollar_rank'] <= 35
        event_source = r['gainer_rank'] <= 25 or r['loser_rank'] <= 25 or r['surge_rank'] <= 25

        # V1.4.3 quality gate:
        # - regular AUTO names need either known dollar liquidity or meaningful share turnover.
        # - ±30% movers are separated into EXTREME rather than mixed into normal candidates.
        volume_fallback = r['volume'] >= 1_000_000 and r['price'] >= min_price
        if abs(r['change_pct']) >= 30:
            r['origin']='EXTREME'
            r['chase_risk']='EXTREME'
            extreme_rows.append(r)
            continue

        if liquid or top_dollar or (top_volume and volume_fallback) or (event_source and volume_fallback):
            eligible.append(r)

    q_eligible=[]; quality_risk_rows=[]; quality_reject_rows=[]
    for r in eligible:
        q=grade_usa_row(r,is_core=False)
        if q.get('quality_grade') in ('A','B_EVENT'):
            q_eligible.append(q)
        elif q.get('quality_grade')=='C_HIGH_RISK':
            quality_risk_rows.append(q)
        else:
            quality_reject_rows.append(q)

    eligible=q_eligible
    eligible.sort(key=lambda r:(r['discovery_score'],r['dollar_volume'],r['volume']), reverse=True)
    # V4.4: market-first discovery.
    # Keep the strongest AUTO candidates first. Core instruments remain guaranteed
    # in the universe, but they no longer occupy the front of the list merely
    # because they were hard-coded. This prevents a watchlist-biased Finder.
    picked=eligible[:limit]

    # V4.4.2: do not hide verified extreme movers.
    # They remain C_HIGH_RISK / EXTREME and are NOT converted to normal-quality names.
    # A small capped set is inserted into the active universe so the live engine can
    # collect quotes/minute bars and decide whether momentum is continuing or fading.
    extreme_watch=[]
    for r in extreme_rows[:10]:
        d=dict(r)
        d['sources']=','.join(sorted(d.get('sources') or [])) if isinstance(d.get('sources'),set) else d.get('sources')
        d['origin']='EXTREME_WATCH'
        d['quality_grade']='C_HIGH_RISK'
        d['quality_reasons']='EXTREME_MOVE'
        d['chase_risk']='EXTREME'
        extreme_watch.append(d)

    existing={r['symbol'] for r in picked}
    for r in extreme_watch:
        if r.get('symbol') and r['symbol'] not in existing:
            picked.append(r)
            existing.add(r['symbol'])

    for sym in core:
        if sym not in [r['symbol'] for r in picked]:
            picked.append({
                'symbol':sym,'exchange':'','name':'CORE','price':0,'change_pct':0,
                'volume':0,'dollar_volume':0,'volume_rank':9999,'dollar_rank':9999,
                'gainer_rank':9999,'loser_rank':9999,'surge_rank':9999,'surge_pct':0.0,
                'sources':{'core'},'chase_risk':'NORMAL','discovery_score':0,
                'asset_type':'LEVERAGED_ETF' if sym in ('SOXL','SOXS','TQQQ','SQQQ') else 'ETF',
                'quality_grade':'B_EVENT' if sym in ('SOXL','SOXS','TQQQ','SQQQ') else 'A',
                'quality_reasons':'LEVERAGED_ETF' if sym in ('SOXL','SOXS','TQQQ','SQQQ') else 'CORE_LIQUID_ETF',
                'quality_gate':'QUALITY_GATE_USA_V1',
                'quality_market_cap_check':'PENDING_VERIFIED_SOURCE'
            })

    seen=set(); symbols=[]
    for r in picked:
        if r['symbol'] not in seen:
            symbols.append(r['symbol']); seen.add(r['symbol'])

        # V4.6.0.1: sources may already be a normalized comma-separated string
        # (notably EXTREME_WATCH). Never join a string character-by-character.
        src=r.get('sources')
        if isinstance(src,(set,list,tuple)):
            r['sources']=','.join(sorted(str(x) for x in src if x))
        elif isinstance(src,str):
            r['sources']=src
        else:
            r['sources']=''

        # Preserve EXTREME_WATCH provenance instead of overwriting it as AUTO.
        if r['symbol'] in core:
            r['origin']='CORE'
        elif str(r.get('origin') or '').startswith('EXTREME'):
            r['origin']='EXTREME_WATCH'
        else:
            r['origin']='AUTO'

    extreme_rows.sort(key=lambda r:(abs(r['change_pct']),r['volume']),reverse=True)
    q_extreme=[]
    for r in extreme_rows:
        r['sources']=','.join(sorted(r['sources']))
        r['origin']='EXTREME'
        q=grade_usa_row(r,is_core=False)
        q['quality_grade']='C_HIGH_RISK'
        q_extreme.append(q)
    quality_risk_rows.extend(q_extreme)
    return DiscoveryResult(
        symbols=symbols,
        rows=picked,
        updated_at=datetime.now(timezone.utc).isoformat(),
        extreme_rows=q_extreme[:20],
        quality_risk_rows=quality_risk_rows[:50],
        quality_reject_rows=quality_reject_rows[:50]
    )
