from pathlib import Path
import re

KOREA=Path('live_server/korea.py')

PATCH=r'''

    # ===== V48 BROAD FINDER OUTPUT (KOREA) =====
    def broad_momentum_finder_v48(self, batch_size=20, limit=40):
        """Return a broad liquid finder while keeping the original momentum formula as a priority tag.

        Finder goal: show dozens of tradable names every day.
        Strict original formula is NOT the universe gate; it is a PRIMARY_SIGNAL tag/boost.
        """
        base=self.original_momentum_scan_v47(batch_size)
        evaluated=base.get('evaluated_rows') or []

        # Score broad candidates from liquidity first, then momentum evidence.
        rows=[]
        for r in evaluated:
            if not r.get('ok'):
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
            rows.append({**r,'finder_score':score,'finder_tag':tag,'primary_signal':strict})

        rows.sort(key=lambda r:(1 if r.get('primary_signal') else 0,float(r.get('finder_score') or 0),-min(int(r.get('value_rank',9999) or 9999),int(r.get('volume_rank',9999) or 9999))),reverse=True)
        lim=max(10,min(int(limit),60))
        out=rows[:lim]
        return {
            'ok':True,
            'finder_mode':'BROAD_LIQUIDITY_WITH_ORIGINAL_SIGNAL_V48',
            'formula':'STRICT TAG = MACD_ZERO_CROSS_0_TO_5 AND HIGH52_GAP_GE_-10 AND (VALUE_TOP100 OR VOLUME_TOP100)',
            'candidate_count':base.get('candidate_count'),
            'evaluated_count':base.get('evaluated_count'),
            'finder_count':len(out),
            'primary_signal_count':sum(1 for r in out if r.get('primary_signal')),
            'macd_fresh_count':sum(1 for r in out if r.get('macd_cross_5')),
            'near_52w_count':sum(1 for r in out if r.get('near_52w_high')),
            'cursor':base.get('cursor'),
            'rows':out,
            'updated_at':base.get('updated_at'),
        }
'''

def main():
    s=KOREA.read_text()
    if 'def broad_momentum_finder_v48' in s:
        print('FINDER_KOREA_BROAD_V48_ALREADY_OK')
        return
    anchor='    def discover(self, limit=50):\n'
    if anchor not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: Korea discover')
    s=s.replace(anchor,PATCH+'\n'+anchor,1)
    KOREA.write_text(s)

    api=Path('live_server/api.py')
    a=api.read_text()
    if '/api/v5/korea-momentum-finder' not in a:
        endpoint="""\n@app.get('/api/v5/korea-momentum-finder')\nasync def v48_korea_momentum_finder(batch_size:int=20,limit:int=40):\n    return await asyncio.to_thread(korea.broad_momentum_finder_v48,batch_size,limit)\n"""
        anchor2="app=FastAPI(title='DAY TRADER LIVE API',version='3.5',lifespan=lifespan)\n"
        if anchor2 not in a:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: app=FastAPI')
        a=a.replace(anchor2,anchor2+endpoint+'\n',1)
        api.write_text(a)
    print('FINDER_KOREA_BROAD_V48_OK')

if __name__=='__main__':
    main()
