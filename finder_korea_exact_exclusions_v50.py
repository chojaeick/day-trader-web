from pathlib import Path

KOREA=Path('live_server/korea.py')
API=Path('live_server/api.py')

PATCH=r'''

    # ===== V50 EXACT KIWOOOM EXCLUSIONS (KOREA) =====
    @staticmethod
    def _v50_exclusion_reason(row, meta):
        import re as _re
        r=row or {}; m=meta or {}
        name=str(r.get('name') or m.get('name') or '').strip()
        upper=name.upper()
        state=str(m.get('state') or '').strip()
        warning=str(m.get('order_warning') or '').strip()
        company_class=str(m.get('company_class_name') or '').strip()
        market_name=str(m.get('market_name') or '').strip()
        blob=' '.join([state,warning,company_class,market_name,name]).upper()

        # Exact checked exclusions from the Kiwoom condition-search screen:
        # 관리종목 / 투자경고·위험 / 우선주 / 거래정지 / 환기종목 / 정리매매 /
        # 불성실공시기업 / ETF / 스팩 / ETN
        if '관리' in state or '관리종목' in blob:
            return 'MANAGEMENT'
        if ('투자경고' in blob or '투자위험' in blob or
            warning.upper() in ('WARNING','DANGER','INVESTMENT_WARNING','INVESTMENT_DANGER')):
            return 'INVESTMENT_WARNING_DANGER'
        if ('우선' in company_class or _re.search(r'(우|우B|\d우B)$', name)):
            return 'PREFERRED'
        if '거래정지' in state or '거래정지' in blob:
            return 'TRADING_HALT'
        if '환기' in state or '환기종목' in blob:
            return 'VENTILATION'
        if '정리' in state or '정리매매' in blob:
            return 'LIQUIDATION'
        if '불성실' in state or '불성실공시' in blob:
            return 'UNFAITHFUL_DISCLOSURE'
        if ('ETF' in market_name.upper() or 'ETF' in company_class.upper() or ' ETF' in (' '+upper) or
            upper.startswith(('KODEX ','TIGER ','RISE ','ACE ','SOL ','HANARO ','KBSTAR ','ARIRANG ','KOSEF ','TIMEFOLIO ','PLUS '))):
            return 'ETF'
        if '스팩' in name or 'SPAC' in blob:
            return 'SPAC'
        if 'ETN' in market_name.upper() or 'ETN' in company_class.upper() or 'ETN' in upper:
            return 'ETN'
        return None

    def exact_exclusion_finder_v50(self, batch_size=20, limit=40):
        # Ask v48 for a wider ranked set, then apply only the exclusions checked in Kiwoom.
        base=self.broad_momentum_finder_v48(batch_size,max(60,int(limit)))
        try:
            meta,_=self._load_stock_metadata(False)
        except Exception:
            meta={}

        kept=[]; excluded=[]
        for r in base.get('rows') or []:
            reason=self._v50_exclusion_reason(r,meta.get(r.get('symbol')) if meta else None)
            if reason:
                excluded.append({**r,'exclude_reason':reason})
            else:
                kept.append(r)

        lim=max(10,min(int(limit),60))
        out=kept[:lim]
        return {
            **{k:v for k,v in base.items() if k!='rows'},
            'finder_mode':'BROAD_LIQUIDITY_KIWOOM_EXCLUSIONS_V50',
            'finder_count':len(out),
            'excluded_output_count':len(excluded),
            'excluded_by_reason':{x:sum(1 for r in excluded if r.get('exclude_reason')==x) for x in sorted(set(r.get('exclude_reason') for r in excluded))},
            'primary_signal_count':sum(1 for r in out if r.get('primary_signal')),
            'macd_fresh_count':sum(1 for r in out if r.get('macd_cross_5')),
            'near_52w_count':sum(1 for r in out if r.get('near_52w_high')),
            'rows':out,
            'excluded_rows':excluded,
            'exclusion_policy':['관리종목','투자경고/위험','우선주','거래정지','환기종목','정리매매','불성실공시기업','ETF','스팩','ETN'],
        }
'''

API_PATCH=r'''

@app.get('/api/v5/korea-momentum-finder-v50')
async def v50_korea_momentum_finder(batch_size:int=20,limit:int=40):
    return await asyncio.to_thread(korea.exact_exclusion_finder_v50,batch_size,limit)
'''

def main():
    s=KOREA.read_text()
    if 'def exact_exclusion_finder_v50' not in s:
        anchor='    def discover(self, limit=50):\n'
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: Korea discover')
        s=s.replace(anchor,PATCH+'\n'+anchor,1)
        KOREA.write_text(s)

    a=API.read_text()
    if '/api/v5/korea-momentum-finder-v50' not in a:
        anchor="app=FastAPI(title='DAY TRADER LIVE API',version='3.5',lifespan=lifespan)\n"
        if anchor not in a:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: app=FastAPI')
        a=a.replace(anchor,anchor+API_PATCH+'\n',1)
        API.write_text(a)

    print('FINDER_KOREA_EXACT_EXCLUSIONS_V50_OK')

if __name__=='__main__':
    main()
