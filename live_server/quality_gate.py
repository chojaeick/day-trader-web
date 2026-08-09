
from __future__ import annotations
from datetime import datetime, timezone
import re

LEVERAGED_US_ETFS={'SOXL','SOXS','TQQQ','SQQQ','UPRO','SPXU','TECL','TECS','FAS','FAZ','LABU','LABD','TNA','TZA'}

def _f(v, default=0.0):
    try:
        return float(str(v).replace(',','').replace('+','').strip())
    except Exception:
        return default

def _kr_preferred(name:str, company_class:str='')->bool:
    n=str(name or '').strip()
    cc=str(company_class or '').strip()
    if '우선' in cc:
        return True
    return bool(re.search(r'(우|우B|\d우B)$', n))

def grade_usa_row(row:dict, is_core:bool=False)->dict:
    r=dict(row)
    sym=str(r.get('symbol') or '').upper()
    price=abs(_f(r.get('price')))
    volume=abs(_f(r.get('volume')))
    dollar_rank=int(_f(r.get('dollar_rank'),9999) or 9999)
    volume_rank=int(_f(r.get('volume_rank'),9999) or 9999)
    change=abs(_f(r.get('change_pct')))
    chase=str(r.get('chase_risk') or 'NORMAL').upper()
    asset=str(r.get('asset_type') or '').upper()
    sources=str(r.get('sources') or '')
    reasons=[]

    if sym in LEVERAGED_US_ETFS or asset=='LEVERAGED_ETF':
        grade='B_EVENT'; reasons.append('LEVERAGED_ETF')
    elif is_core and sym in {'QQQ','SPY','SMH'}:
        grade='A'; reasons.append('CORE_LIQUID_ETF')
    elif price and price < 5:
        grade='REJECT'; reasons.append('PRICE_LT_5')
    elif chase=='EXTREME' or change>=30:
        grade='C_HIGH_RISK'; reasons.append('EXTREME_MOVE')
    elif chase=='HIGH' or change>=18:
        grade='C_HIGH_RISK'; reasons.append('HIGH_CHASE_RISK')
    else:
        liquid_rank=(dollar_rank<=40 or volume_rank<=35)
        liquid_volume=volume>=1_000_000
        if liquid_rank and liquid_volume:
            grade='A'; reasons.append('LIQUID_RANK_AND_VOLUME')
        elif liquid_rank or liquid_volume:
            grade='B_EVENT'; reasons.append('LIQUIDITY_PARTIAL')
        elif any(x in sources for x in ('gainer','loser','surge')) and volume>=500_000:
            grade='B_EVENT'; reasons.append('EVENT_EXCEPTION')
        else:
            grade='REJECT'; reasons.append('LOW_LIQUIDITY')

    r['quality_grade']=grade
    r['quality_reasons']='|'.join(reasons)
    r['quality_gate']='QUALITY_GATE_USA_V1'
    r['quality_market_cap_check']='PENDING_VERIFIED_SOURCE'
    return r


def _kr_is_etf(name:str, market_name:str='', company_class:str='')->bool:
    n=str(name or '').upper()
    mk=str(market_name or '').upper()
    cc=str(company_class or '').upper()
    return (
        'ETF' in mk or 'ETF' in cc or
        n.startswith('KODEX ') or n.startswith('TIGER ') or n.startswith('RISE ') or
        n.startswith('ACE ') or n.startswith('SOL ') or n.startswith('HANARO ') or
        n.startswith('KBSTAR ') or n.startswith('ARIRANG ') or n.startswith('KOSEF ')
    )

def _kr_is_leveraged_etf(name:str)->bool:
    n=str(name or '').upper()
    return any(k in n for k in ('레버리지','인버스','2X','2X선물','선물인버스'))

def build_korea_metadata(rows:list[dict])->tuple[dict,bool]:
    meta={}; caps=[]
    for x in rows or []:
        code=str(x.get('code') or '').strip()
        if not code:
            continue
        shares=abs(_f(x.get('listCount')))
        last=abs(_f(x.get('lastPrice')))
        cap=shares*last if shares and last else 0.0
        d={
            'code':code,'name':x.get('name') or '',
            'list_count':shares,'last_price':last,'market_cap_est':cap,
            'audit_info':x.get('auditInfo'),
            'reg_day':str(x.get('regDay') or ''),
            'state':str(x.get('state') or ''),
            'market_name':x.get('marketName'),
            'up_size_name':x.get('upSizeName'),
            'order_warning':str(x.get('orderWarning') or ''),
            'company_class_name':str(x.get('companyClassName') or ''),
        }
        meta[code]=d
        if cap>0:
            caps.append((code,cap))
    cap_rank_enabled=len(meta)>=1000 and len(caps)>=800
    if cap_rank_enabled:
        caps.sort(key=lambda x:x[1],reverse=True)
        for rank,(code,_) in enumerate(caps,1):
            meta[code]['market_cap_rank']=rank
    return meta,cap_rank_enabled


def grade_korea_row(row:dict, meta:dict|None=None, cap_rank_enabled:bool=False)->dict:
    r=dict(row); m=meta or {}
    name=str(r.get('name') or m.get('name') or '')
    price=abs(_f(r.get('price') or m.get('last_price')))
    change=abs(_f(r.get('change_pct')))
    source_count=int(_f(r.get('source_count')))
    value_rank=int(_f(r.get('value_rank'),9999) or 9999)
    chase=str(r.get('chase_risk') or 'NORMAL').upper()
    state=str(m.get('state') or '')
    warning=str(m.get('order_warning') or '')
    company_class=str(m.get('company_class_name') or '')
    market_name=str(m.get('market_name') or '')
    cap_rank=m.get('market_cap_rank')
    reasons=[]

    bad_state=any(k in state for k in ('관리','정리','거래정지','상장폐지'))
    warning_yes=warning.strip().upper() not in ('','0','N','NO','정상','FALSE','NONE')
    preferred=_kr_preferred(name,company_class)
    is_etf=_kr_is_etf(name,market_name,company_class)
    leveraged_etf=_kr_is_leveraged_etf(name) if is_etf else False

    # ETFs are not evaluated using corporate market-cap rank.
    if is_etf:
        if leveraged_etf:
            grade='B_EVENT'; reasons.append('LEVERAGED_OR_INVERSE_ETF')
        else:
            grade='A'; reasons.append('ETF_CORE')
    elif preferred:
        grade='REJECT'; reasons.append('PREFERRED_SHARE')
    elif bad_state:
        grade='REJECT'; reasons.append('BAD_SECURITY_STATE')
    elif price and price<2000:
        grade='REJECT'; reasons.append('PRICE_LT_2000_KRW')
    else:
        event_exception=(source_count>=4 and value_rank<=20 and change<20)

        if cap_rank_enabled and cap_rank:
            if cap_rank<=500:
                grade='A'; reasons.append('MARKET_CAP_TOP500')
            elif cap_rank<=800:
                grade='B_EVENT'; reasons.append('MARKET_CAP_RANK_501_800')
            elif event_exception:
                grade='B_EVENT'; reasons.append('EVENT_EXCEPTION')
            else:
                grade='REJECT'; reasons.append('MARKET_CAP_RANK_GT_800')
        else:
            if source_count>=3 or value_rank<=25:
                grade='A'; reasons.append('CAP_RANK_PENDING_STRONG_LIQUIDITY')
            else:
                grade='B_EVENT'; reasons.append('CAP_RANK_PENDING_EVENT_ONLY')

        if warning_yes and grade!='REJECT':
            grade='C_HIGH_RISK'; reasons.append('INVESTMENT_WARNING')
        if (chase in ('HIGH','EXTREME') or change>=20) and grade!='REJECT':
            grade='C_HIGH_RISK'; reasons.append('HIGH_CHASE_RISK')

    # Listing-age caution applies only to company stocks, not ETFs.
    if not is_etf:
        reg=str(m.get('reg_day') or '')
        if len(reg)>=8 and reg[:8].isdigit():
            try:
                d=datetime.strptime(reg[:8],'%Y%m%d').replace(tzinfo=timezone.utc)
                age=(datetime.now(timezone.utc)-d).days
                r['listing_age_days']=age
                if age<90 and grade=='A':
                    grade='B_EVENT'; reasons.append('NEW_LISTING_LT_90D')
            except Exception:
                pass

    r.update({
        'quality_grade':grade,
        'quality_reasons':'|'.join(reasons),
        'quality_gate':'QUALITY_GATE_KOREA_V1_1',
        'market_cap_est':None if is_etf else m.get('market_cap_est'),
        'market_cap_rank':None if is_etf else cap_rank,
        'market_cap_rank_enabled':bool(cap_rank_enabled and not is_etf),
        'security_state':state or None,
        'company_class_name':company_class or None,
        'order_warning':warning or None,
        'instrument_type':'LEVERAGED_ETF' if leveraged_etf else ('ETF' if is_etf else 'STOCK'),
    })
    return r

