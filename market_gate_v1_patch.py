from pathlib import Path

KOREA=Path('live_server/korea.py')
API=Path('live_server/api.py')

KOREA_PATCH=r'''

    # ===== MARKET GATE V1 (PROVISIONAL, REAL DATA ONLY) =====
    def market_gate_v1(self):
        """Conservative day-trade market gate.

        Uses only live data already available in this adapter. Missing components are
        reported as UNKNOWN and are not silently scored. This avoids false AGGRESSIVE
        calls before investor/index/US-overnight feeds are wired.
        """
        from datetime import datetime, timezone
        components=[]

        # 1) Market breadth from live gainer/loser rank lanes.
        breadth_score=None
        breadth_detail={}
        try:
            gain=[]; lose=[]
            for mrkt_tp,market in [('001','KOSPI'),('101','KOSDAQ')]:
                g=self._change_rate(mrkt_tp,'1') or []
                l=self._change_rate(mrkt_tp,'4') or []
                gain.extend(g); lose.extend(l)
                breadth_detail[f'{market}_gainers']=len(g)
                breadth_detail[f'{market}_losers']=len(l)
            def avg_abs(rows):
                vals=[_num(x.get('flu_rt')) for x in rows if x.get('flu_rt') not in (None,'')]
                return sum(vals)/len(vals) if vals else 0.0
            gavg=avg_abs(gain); lavg=avg_abs(lose)
            breadth_detail['gainer_avg_pct']=round(gavg,3)
            breadth_detail['loser_avg_pct']=round(lavg,3)
            # 0..100: equal market ~50; stronger upside rank intensity lifts score.
            spread=gavg-abs(lavg)
            breadth_score=max(0.0,min(100.0,50.0+spread*10.0))
            components.append({'name':'breadth','status':'LIVE','weight':25,'score':round(breadth_score,1),'detail':breadth_detail})
        except Exception as e:
            components.append({'name':'breadth','status':'UNKNOWN','weight':25,'score':None,'error':str(e)[:160]})

        # 2) Liquidity / money concentration from value Top100.
        liquidity_score=None
        liquidity_detail={}
        try:
            rows=self._trading_value('000') or []
            vals=[]
            for x in rows[:100]:
                v=abs(_num(x.get('trde_prica') or x.get('trde_amt') or x.get('acc_trde_prica')))
                if v>0: vals.append(v)
            top20=sum(vals[:20]); top100=sum(vals)
            concentration=(top20/top100) if top100>0 else 0.0
            liquidity_detail={'rows':len(rows),'top100_value':top100,'top20_value':top20,'top20_share':round(concentration,4)}
            # concentration itself is not directional; treat healthy money concentration as tradability.
            liquidity_score=max(0.0,min(100.0,40.0+concentration*60.0)) if top100>0 else 0.0
            components.append({'name':'liquidity','status':'LIVE' if top100>0 else 'NO_TODAY_TRADING','weight':20,'score':round(liquidity_score,1),'detail':liquidity_detail})
        except Exception as e:
            components.append({'name':'liquidity','status':'UNKNOWN','weight':20,'score':None,'error':str(e)[:160]})

        # 3) Pre-open expected execution breadth. Useful before open; lower weight intraday.
        expected_score=None
        try:
            snap=self.expected_execution_snapshot()
            rows=snap.get('rows') or []
            ups=[r for r in rows if str(r.get('expected_side') or '').upper()=='UP']
            downs=[r for r in rows if str(r.get('expected_side') or '').upper()=='DOWN']
            upmag=sum(max(0.0,_num(r.get('expected_change_pct'))) for r in ups[:50])
            dnmag=sum(abs(min(0.0,_num(r.get('expected_change_pct')))) for r in downs[:50])
            total=upmag+dnmag
            expected_score=50.0 if total<=0 else 100.0*upmag/total
            components.append({'name':'preopen_expected','status':'LIVE' if rows else 'NO_DATA','weight':15,'score':round(expected_score,1),'detail':{'rows':len(rows),'up_count':len(ups),'down_count':len(downs)}})
        except Exception as e:
            components.append({'name':'preopen_expected','status':'UNKNOWN','weight':15,'score':None,'error':str(e)[:160]})

        # Planned components: expose explicitly, never fabricate values.
        components.append({'name':'foreign_institution_flow','status':'NOT_CONNECTED','weight':20,'score':None,'planned_source':'Kiwoom ka10051 / market investor flow'})
        components.append({'name':'index_20d_trend','status':'NOT_CONNECTED','weight':15,'score':None,'planned_source':'Kiwoom ka20006 KOSPI/KOSDAQ daily index'})
        components.append({'name':'us_overnight','status':'NOT_CONNECTED','weight':5,'score':None,'planned_source':'USA market close context'})

        live=[c for c in components if c.get('score') is not None]
        used_weight=sum(float(c.get('weight') or 0) for c in live)
        raw=sum(float(c['score'])*float(c.get('weight') or 0) for c in live)
        normalized=(raw/used_weight) if used_weight>0 else None
        confidence=round(used_weight/100.0,2)

        # Conservative state: until >=70% of intended inputs are live, never emit AGGRESSIVE.
        if normalized is None:
            state='UNKNOWN'
        elif normalized>=70:
            state='NORMAL' if confidence<0.70 else 'AGGRESSIVE'
        elif normalized>=55:
            state='NORMAL'
        elif normalized>=40:
            state='DEFENSIVE'
        else:
            state='CASH'

        return {
            'ok':True,'version':'MARKET_GATE_V1_PROVISIONAL','market':'KOREA',
            'state':state,'score':None if normalized is None else round(normalized,1),
            'confidence':confidence,'used_weight':used_weight,
            'components':components,
            'rule':'AGGRESSIVE>=70, NORMAL>=55, DEFENSIVE>=40, CASH<40; AGGRESSIVE disabled until confidence>=0.70',
            'updated_at':datetime.now(timezone.utc).isoformat(),
        }
'''

API_PATCH=r'''

@app.get('/api/v5/market-gate/KOREA')
async def v5_market_gate_korea():
    return await asyncio.to_thread(korea.market_gate_v1)
'''

def main():
    s=KOREA.read_text()
    if 'def market_gate_v1' not in s:
        anchor='    def discover(self, limit=50):\n'
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: Korea discover')
        s=s.replace(anchor,KOREA_PATCH+'\n'+anchor,1)
        KOREA.write_text(s)
    a=API.read_text()
    if '/api/v5/market-gate/KOREA' not in a:
        anchor="app=FastAPI(title='DAY TRADER LIVE API',version='3.5',lifespan=lifespan)\n"
        if anchor not in a:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: FastAPI app')
        a=a.replace(anchor,anchor+API_PATCH+'\n',1)
        API.write_text(a)
    print('MARKET_GATE_V1_PATCH_OK')

if __name__=='__main__': main()
