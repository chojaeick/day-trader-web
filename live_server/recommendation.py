
from __future__ import annotations
from datetime import datetime, timezone

def _f(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default

def _quality_map(discovery:dict)->dict:
    return {str(r.get('symbol') or '').upper():r for r in (discovery.get('rows') or [])}

def build_usa_final_recommendations(candidate_rows:list[dict], discovery:dict, signal_getter, limit:int=5)->dict:
    """Conservative actionable layer over Candidate TOP10.

    Candidate score is only a discovery priority. Final action requires live chart confirmation.
    """
    qmap=_quality_map(discovery)
    evaluated=[]
    for c in (candidate_rows or [])[:10]:
        sym=str(c.get('symbol') or '').upper()
        q=qmap.get(sym) or {}
        quality=q.get('quality_grade') or 'UNKNOWN'
        asset=q.get('asset_type') or ''
        sig=signal_getter(sym) or {}
        ind=sig.get('indicators') or {}
        ctx=sig.get('context') or {}

        candidate=_f(c.get('score'))
        signal=_f(sig.get('score'))
        confirm=_f(sig.get('confirm_5m'))
        bias=str(sig.get('bias') or 'NEUTRAL').upper()
        state=str(sig.get('state') or 'WAIT').upper()
        price=_f(c.get('price'))
        vwap=_f(ind.get('vwap'))
        ema9=_f(ind.get('ema9'))
        ema20=_f(ind.get('ema20'))
        rvol=_f(ind.get('rvol'))
        rsi=_f(ind.get('rsi14'))
        qqq=_f(ctx.get('qqq_pct'))
        smh=_f(ctx.get('smh_pct'))

        reasons=[]; blocks=[]; risk_penalty=0.0

        # Quality gate
        if quality=='A':
            quality_score=15; reasons.append('Quality A')
        elif quality=='B_EVENT' and 'ETF' in str(asset).upper():
            quality_score=8; reasons.append('B_EVENT ETF')
        else:
            quality_score=0; blocks.append('QUALITY_NOT_A')

        # Candidate prior, deliberately capped.
        candidate_score=min(20.0,candidate*0.20)

        # Live signal + 5m confirmation
        signal_score=min(25.0,signal*0.25)
        confirm_score=min(15.0,confirm/25.0*15.0)
        if state in ('TRIGGER','SETUP'):
            reasons.append(state)
        else:
            blocks.append('NO_SETUP_TRIGGER')

        # Long-only executable recommendation in V1.
        # Bearish views remain WAIT/AVOID; bearish exposure should be via approved inverse ETFs.
        if bias!='LONG':
            blocks.append('NOT_LONG')

        chart_score=0.0
        if price and vwap and price>vwap:
            chart_score+=5; reasons.append('Price>VWAP')
        else:
            blocks.append('BELOW_VWAP')
        if ema9 and ema20 and ema9>ema20:
            chart_score+=5; reasons.append('EMA9>EMA20')
        else:
            blocks.append('EMA_NOT_BULLISH')
        if rvol>=1.0:
            chart_score+=5; reasons.append(f'RVOL {rvol:.2f}x')
        else:
            blocks.append('RVOL_LT_1')

        market_score=0.0
        if qqq>=0:
            market_score+=3
        if ('SEMI' in str(c.get('sector','')).upper() or sym in ('NVDA','AMD','AVGO','SMH','MU','INTC')) and smh>=0:
            market_score+=2
        elif qqq>=0:
            market_score+=2

        # Conservative risk checks
        change=abs(_f(c.get('change_pct')))
        atr=abs(_f(c.get('atr_pct')))
        if change>=15:
            risk_penalty+=8; blocks.append('CHASE_MOVE_GE_15')
        elif change>=10:
            risk_penalty+=4
        if atr>=10:
            risk_penalty+=6
        elif atr>=7:
            risk_penalty+=3
        if rsi>=75:
            risk_penalty+=4

        final=max(0.0,min(100.0,round(quality_score+candidate_score+signal_score+confirm_score+chart_score+market_score-risk_penalty,1)))

        hard_ok=(
            quality in ('A','B_EVENT') and
            bias=='LONG' and
            state in ('TRIGGER','SETUP') and
            confirm>=13 and
            price>0 and vwap>0 and price>vwap and
            ema9>ema20 and rvol>=1.0 and
            change<15
        )

        if hard_ok and final>=78 and state=='TRIGGER':
            action='BUY_NOW'
        elif hard_ok and final>=70:
            action='WATCH'
        elif quality not in ('A','B_EVENT') or change>=20:
            action='AVOID'
        else:
            action='WAIT'

        evaluated.append({
            'symbol':sym,'name':q.get('name') or c.get('name') or sym,
            'quality_grade':quality,'candidate_score':candidate,
            'signal_score':signal,'confirm_5m':confirm,'final_score':final,
            'action':action,'bias':bias,'state':state,'price':price,
            'vwap':vwap or None,'ema9':ema9 or None,'ema20':ema20 or None,
            'rvol':rvol,'rsi':rsi,'change_pct':_f(c.get('change_pct')),
            'risk_penalty':risk_penalty,
            'reason':' · '.join(reasons[:5]),
            'blocks':'|'.join(dict.fromkeys(blocks)),
            'invalidation':sig.get('invalidation'),
            'target1':sig.get('target1'),'target2':sig.get('target2'),
        })

    evaluated.sort(key=lambda x:(0 if x['action']=='BUY_NOW' else 1 if x['action']=='WATCH' else 2 if x['action']=='WAIT' else 3,-x['final_score']))
    actionable=[x for x in evaluated if x['action'] in ('BUY_NOW','WATCH')][:limit]
    return {
        'market':'USA','model':'FINAL_RECOMMENDATION_V1',
        'generated_at':datetime.now(timezone.utc).isoformat(),
        'count':len(actionable),'data':actionable,'evaluated':evaluated,
        'policy':'Long-only actionable V1; SHORT is not a recommendation. BUY_NOW requires Quality + live 1m/5m chart confirmation.'
    }

def build_korea_final_recommendations(discovery:dict, pulse:dict, limit:int=5)->dict:
    """Conservative KOREA V1.

    Until a verified domestic minute-chart adapter is connected, BUY_NOW is blocked.
    Live Pulse may produce WATCH candidates only.
    """
    base={str(r.get('symbol') or ''):r for r in (discovery.get('top10') or [])}
    pulse_rows=(pulse.get('top10') or [])
    market_open=bool(pulse.get('market_open')) and pulse.get('status')=='LIVE'
    evaluated=[]
    source=pulse_rows if pulse_rows else list(base.values())
    for r in source[:10]:
        sym=str(r.get('symbol') or '')
        b=base.get(sym) or r
        quality=b.get('quality_grade') or 'UNKNOWN'
        instrument=b.get('instrument_type') or 'STOCK'
        bias=str(r.get('bias') or b.get('bias') or '').upper()
        live_score=_f(r.get('live_score',b.get('score')))
        gamma=_f(b.get('score'))
        strength=r.get('strength_composite')
        strength_f=_f(strength) if strength is not None else None
        vi=bool(r.get('vi_triggered'))
        chase=str(b.get('chase_risk') or 'NORMAL').upper()
        reasons=[]; blocks=['DOMESTIC_CHART_GATE_PENDING']

        score=min(55.0,live_score*0.55)
        if quality=='A':
            score+=15; reasons.append('Quality A')
        elif quality=='B_EVENT':
            score+=5; reasons.append('B_EVENT')
        else:
            blocks.append('QUALITY_NOT_A')

        if bias=='LONG':
            score+=8; reasons.append('LONG bias')
        else:
            blocks.append('NOT_LONG')

        if market_open and strength_f is not None:
            if strength_f>=115:
                score+=12; reasons.append(f'체결강도 {strength_f:.1f}')
            elif strength_f>=105:
                score+=6; reasons.append(f'체결강도 {strength_f:.1f}')
            elif strength_f<90:
                score-=8; blocks.append('WEAK_EXECUTION_STRENGTH')
        else:
            blocks.append('PULSE_NOT_LIVE')

        if vi:
            score-=12; blocks.append('VI_TRIGGERED')
        if chase in ('HIGH','EXTREME'):
            score-=8; blocks.append('CHASE_RISK')

        final=round(max(0,min(100,score)),1)

        # No BUY_NOW until domestic minute charts (VWAP/EMA/1m/5m) are verified.
        watch_ok=(market_open and quality=='A' and bias=='LONG' and not vi and chase=='NORMAL' and
                  strength_f is not None and strength_f>=110 and final>=70)
        action='WATCH' if watch_ok else ('AVOID' if quality not in ('A','B_EVENT') or vi else 'WAIT')

        evaluated.append({
            'symbol':sym,'name':b.get('name') or sym,'market':b.get('market'),
            'quality_grade':quality,'instrument_type':instrument,
            'candidate_score':gamma,'live_score':live_score,'final_score':final,
            'action':action,'bias':bias,'strength_composite':strength_f,
            'vi':vi,'chase_risk':chase,
            'reason':' · '.join(reasons),
            'blocks':'|'.join(dict.fromkeys(blocks)),
        })

    evaluated.sort(key=lambda x:(0 if x['action']=='WATCH' else 1 if x['action']=='WAIT' else 2,-x['final_score']))
    actionable=[x for x in evaluated if x['action']=='WATCH'][:limit]
    return {
        'market':'KOREA','model':'FINAL_RECOMMENDATION_V1',
        'generated_at':datetime.now(timezone.utc).isoformat(),
        'count':len(actionable),'data':actionable,'evaluated':evaluated,
        'buy_now_enabled':False,
        'policy':'BUY_NOW disabled until verified KOREA minute-chart VWAP/EMA 1m/5m confirmation is connected.'
    }
