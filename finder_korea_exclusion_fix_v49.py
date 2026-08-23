from pathlib import Path

KOREA=Path('live_server/korea.py')

PATCH=r'''

    # ===== V49 BROAD FINDER EXCLUSION SAFETY FILTER =====
    @staticmethod
    def _v49_finder_exclude_reason(row):
        import re as _re
        name=str((row or {}).get('name') or '').strip()
        upper=name.upper()

        # Preferred shares: 삼성전자우, 하이트진로2우B, 두산2우B, etc.
        if _re.search(r'(?:\d+)?우(?:B|C)?$', name) or '우선주' in name:
            return 'PREFERRED'

        # SPAC / special purpose acquisition companies.
        if '스팩' in name or 'SPAC' in upper:
            return 'SPAC'

        # ETF / ETN families and explicit fund/bond products.
        etf_prefixes=(
            'KODEX ','TIGER ','RISE ','PLUS ','ACE ','SOL ','HANARO ',
            'KOSEF ','TIMEFOLIO ','KBSTAR ','ARIRANG ','FOCUS ','WOORI '
        )
        if upper.startswith(etf_prefixes):
            return 'ETF_ETN'
        if ' ETF' in upper or 'ETN' in upper:
            return 'ETF_ETN'
        if any(x in name for x in ('회사채','국고채','채권액티브','채권혼합','커버드콜')):
            return 'ETF_ETN'

        return None

    def broad_momentum_finder_v49(self, batch_size=20, limit=40):
        base=self.original_momentum_scan_v47(batch_size)
        evaluated=base.get('evaluated_rows') or []

        rows=[]; excluded=[]
        for r in evaluated:
            if not r.get('ok'):
                continue
            reason=self._v49_finder_exclude_reason(r)
            if reason:
                excluded.append({'symbol':r.get('symbol'),'name':r.get('name'),'reason':reason})
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
            tag='PRIMARY_SIGNAL' if strict else ('MACD_FRESH' if macd else ('LEADER_52W' if near52 else 'LIQUID_WATCH'))
            rows.append({**r,'finder_score':score,'finder_tag':tag,'primary_signal':strict})

        rows.sort(
            key=lambda r:(
                1 if r.get('primary_signal') else 0,
                float(r.get('finder_score') or 0),
                -min(int(r.get('value_rank',9999) or 9999),int(r.get('volume_rank',9999) or 9999))
            ),
            reverse=True
        )
        lim=max(10,min(int(limit),60))
        out=rows[:lim]
        return {
            'ok':True,
            'finder_mode':'BROAD_LIQUIDITY_WITH_EXCLUSION_V49',
            'formula':'PRIMARY_SIGNAL = MACD_ZERO_CROSS_0_TO_5 AND HIGH52_GAP_GE_-10; FINDER = LIQUIDITY TOP100 UNION',
            'candidate_count':base.get('candidate_count'),
            'evaluated_count':base.get('evaluated_count'),
            'finder_count':len(out),
            'excluded_output_count':len(excluded),
            'primary_signal_count':sum(1 for r in out if r.get('primary_signal')),
            'macd_fresh_count':sum(1 for r in out if r.get('macd_cross_5')),
            'near_52w_count':sum(1 for r in out if r.get('near_52w_high')),
            'cursor':base.get('cursor'),
            'rows':out,
            'excluded_rows':excluded,
            'updated_at':base.get('updated_at'),
        }
'''


def main():
    s=KOREA.read_text()
    if 'def broad_momentum_finder_v49' not in s:
        anchor='    def discover(self, limit=50):\n'
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: Korea discover')
        s=s.replace(anchor,PATCH+'\n'+anchor,1)
        KOREA.write_text(s)

    api=Path('live_server/api.py')
    a=api.read_text()
    if '/api/v5/korea-momentum-finder-v49' not in a:
        endpoint="""\n@app.get('/api/v5/korea-momentum-finder-v49')\nasync def v49_korea_momentum_finder(batch_size:int=20,limit:int=40):\n    return await asyncio.to_thread(korea.broad_momentum_finder_v49,batch_size,limit)\n"""
        anchor2="app=FastAPI(title='DAY TRADER LIVE API',version='3.5',lifespan=lifespan)\n"
        if anchor2 not in a:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: app=FastAPI')
        a=a.replace(anchor2,anchor2+endpoint+'\n',1)
        api.write_text(a)

    print('FINDER_KOREA_EXCLUSION_FIX_V49_OK')

if __name__=='__main__':
    main()
