from pathlib import Path
import re

KOREA=Path('live_server/korea.py')
API=Path('live_server/api.py')

PATCH=r'''

    # ===== V51 KOREA FINDER: ETF INCLUDED, RISK EXCLUSIONS ONLY =====
    @staticmethod
    def _v51_instrument_type(name, meta=None):
        n=str(name or '').upper().strip()
        m=meta or {}
        market_name=str(m.get('market_name') or '').upper()
        company_class=str(m.get('company_class_name') or '').upper()
        etf_prefixes=('KODEX ','TIGER ','RISE ','ACE ','SOL ','HANARO ','KBSTAR ','ARIRANG ','KOSEF ','TIMEFOLIO ','PLUS ','WOORI ','1Q ','HK ')
        is_etf=('ETF' in market_name or 'ETF' in company_class or n.startswith(etf_prefixes))
        is_etn=('ETN' in market_name or 'ETN' in company_class or ' ETN' in n or n.endswith('ETN'))
        if is_etn:
            return 'ETN'
        if is_etf:
            if any(k in n for k in ('레버리지','인버스','2X','2X선물','선물인버스')):
                return 'LEVERAGED_OR_INVERSE_ETF'
            return 'ETF'
        return 'STOCK'

    @staticmethod
    def _v51_preferred_name(name, company_class=''):
        n=str(name or '').strip()
        cc=str(company_class or '')
        if '우선' in cc:
            return True
        return bool(re.search(r'(?:우|우B|\d우B)$',n))

    @staticmethod
    def _v51_risk_exclusion(name, meta=None):
        m=meta or {}
        n=str(name or '').strip()
        state=str(m.get('state') or '')
        warning=str(m.get('order_warning') or '')
        company_class=str(m.get('company_class_name') or '')
        text=' '.join([n,state,warning,company_class]).upper()

        if KoreaMarketAdapter._v51_preferred_name(n,company_class):
            return 'PREFERRED'
        if '스팩' in n or 'SPAC' in text:
            return 'SPAC'
        if any(k in state for k in ('관리','정리','거래정지','상장폐지')):
            return 'BAD_SECURITY_STATE'
        if any(k in text for k in ('환기','불성실공시')):
            return 'CAUTION_OR_DISCLOSURE'
        if any(k in text for k in ('투자경고','투자위험')):
            return 'INVESTMENT_WARNING_RISK'
        # Generic orderWarning values are excluded only when clearly non-normal.
        w=warning.strip().upper()
        if w not in ('','0','N','NO','정상','FALSE','NONE'):
            return 'ORDER_WARNING'
        return None

    def broad_momentum_finder_v51(self, batch_size=20, limit=40):
        """Operational Korea finder.

        Includes STOCK + ETF + leveraged/inverse ETF.
        Excludes only risky/non-normal securities: preferred, SPAC, management,
        investment warning/risk, trading halt, ventilation, liquidation,
        unfaithful disclosure. ETN is also excluded for now.
        """
        base=self.original_momentum_scan_v47(batch_size)
        evaluated=base.get('evaluated_rows') or []

        try:
            meta,_=self._load_stock_metadata(False)
        except Exception:
            meta={}

        rows=[]; excluded=[]
        for r in evaluated:
            if not r.get('ok'):
                continue
            sym=r.get('symbol')
            m=meta.get(sym) or {}
            name=r.get('name') or m.get('name') or ''
            itype=self._v51_instrument_type(name,m)
            reason=self._v51_risk_exclusion(name,m)
            if itype=='ETN':
                reason=reason or 'ETN'
            if reason:
                excluded.append({**r,'instrument_type':itype,'exclude_reason':reason})
                continue

            vr=int(r.get('volume_rank',9999) or 9999)
            valr=int(r.get('value_rank',9999) or 9999)
            best=min(vr,valr)
            liq=max(0.0,100.0-(best-1)*0.7) if best<9999 else 0.0
            macd=bool(r.get('macd_cross_5'))
            near52=bool(r.get('near_52w_high'))
            strict=bool(r.get('momentum_match'))
            bonus=(25.0 if strict else 0.0)+(8.0 if macd else 0.0)+(6.0 if near52 else 0.0)
            score=round(min(140.0,liq+bonus),1)
            if strict:
                tag='PRIMARY_SIGNAL'
            elif macd:
                tag='MACD_FRESH'
            elif near52:
                tag='LEADER_52W'
            else:
                tag='LIQUID_WATCH'
            rows.append({**r,'instrument_type':itype,'finder_score':score,'finder_tag':tag,'primary_signal':strict})

        rows.sort(key=lambda r:(1 if r.get('primary_signal') else 0,float(r.get('finder_score') or 0),-min(int(r.get('value_rank',9999) or 9999),int(r.get('volume_rank',9999) or 9999))),reverse=True)
        lim=max(10,min(int(limit),60))
        out=rows[:lim]
        return {
            'ok':True,
            'finder_mode':'BROAD_LIQUIDITY_ETF_INCLUDED_V51',
            'candidate_count':base.get('candidate_count'),
            'evaluated_count':base.get('evaluated_count'),
            'finder_count':len(out),
            'excluded_output_count':len(excluded),
            'stock_count':sum(1 for r in out if r.get('instrument_type')=='STOCK'),
            'etf_count':sum(1 for r in out if r.get('instrument_type') in ('ETF','LEVERAGED_OR_INVERSE_ETF')),
            'primary_signal_count':sum(1 for r in out if r.get('primary_signal')),
            'macd_fresh_count':sum(1 for r in out if r.get('macd_cross_5')),
            'near_52w_count':sum(1 for r in out if r.get('near_52w_high')),
            'cursor':base.get('cursor'),
            'rows':out,
            'excluded_rows':excluded[:50],
            'updated_at':base.get('updated_at'),
        }
'''

API_PATCH=r'''

@app.get('/api/v5/korea-momentum-finder-v51')
async def v51_korea_momentum_finder(batch_size:int=20,limit:int=40):
    return await asyncio.to_thread(korea.broad_momentum_finder_v51,batch_size,limit)
'''

def main():
    s=KOREA.read_text()
    if 'def broad_momentum_finder_v51' not in s:
        anchor='    def discover(self, limit=50):\n'
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: Korea discover')
        s=s.replace(anchor,PATCH+'\n'+anchor,1)
        KOREA.write_text(s)

    a=API.read_text()
    if '/api/v5/korea-momentum-finder-v51' not in a:
        anchor="app=FastAPI(title='DAY TRADER LIVE API',version='3.5',lifespan=lifespan)\n"
        if anchor not in a:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: app=FastAPI')
        a=a.replace(anchor,anchor+API_PATCH+'\n',1)
        API.write_text(a)
    print('FINDER_KOREA_ETF_INCLUDED_V51_OK')

if __name__=='__main__':
    main()
