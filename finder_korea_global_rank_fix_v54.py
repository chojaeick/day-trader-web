from pathlib import Path

KOREA=Path('live_server/korea.py')
API=Path('live_server/api.py')

PATCH=r'''

    # ===== V54 GLOBAL KOREA TOP100 RANKS =====
    def momentum_rank_snapshot_v54(self):
        import time as _time
        t0=_time.time()
        # Kiwoom condition search uses overall market ranking; use mrkt_tp=000 once,
        # not KOSPI100 + KOSDAQ100.
        value_rows=self._trading_value('000') or []
        volume_rows=self._today_volume('000') or []

        def rank_map(rows, lane):
            ranks={}; raws={}; names={}
            fallback=0
            for x in rows:
                if not isinstance(x,dict): continue
                sym=_clean_code(x.get('stk_cd'))
                if not sym: continue
                fallback += 1
                try:
                    rk=int(float(str(x.get('now_rank') or '').replace(',','').strip()))
                except Exception:
                    rk=fallback
                if rk<1 or rk>100: continue
                # For ka10030 rank is response order when no now_rank exists.
                if sym not in ranks or rk<ranks[sym]:
                    ranks[sym]=rk; raws[sym]=x; names[sym]=str(x.get('stk_nm') or '').strip()
            return ranks,raws,names

        vrank,vraw,vname=rank_map(value_rows,'value')
        qrank,qraw,qname=rank_map(volume_rows,'volume')
        syms=set(vrank)|set(qrank)
        rows=[]
        for sym in syms:
            rows.append({
                'symbol':sym,'name':vname.get(sym) or qname.get(sym),
                'value_rank':vrank.get(sym,9999),'volume_rank':qrank.get(sym,9999),
                'rank_sources':(['VALUE_KA10032'] if sym in vrank else [])+(['VOLUME_KA10030'] if sym in qrank else []),
            })
        rows.sort(key=lambda r:(min(r['value_rank'],r['volume_rank']),r['symbol']))

        # Detect preopen/holiday state. ka10030 legitimately returns no rows before
        # today's trading starts; ka10032 may still return an ordered shell with zero values.
        meaningful_value=sum(1 for x in value_rows if abs(_num(x.get('trde_prica') or x.get('trde_amt') or x.get('acc_trde_prica'))) > 0)
        meaningful_volume=sum(1 for x in volume_rows if abs(_num(x.get('trde_qty') or x.get('now_trde_qty') or x.get('acc_trde_qty'))) > 0)
        if not qrank and meaningful_value==0:
            status='PREOPEN_OR_NO_TODAY_TRADING'
        elif not qrank:
            status='VOLUME_LANE_EMPTY'
        else:
            status='LIVE_RANKS'
        return {
            'ok':True,'rank_scope':'KRX_ALL_MARKET_TOP100','status':status,
            'value_top100_count':len(vrank),'volume_top100_count':len(qrank),'union_count':len(rows),
            'meaningful_value_rows':meaningful_value,'meaningful_volume_rows':meaningful_volume,
            'rows':rows,'elapsed_sec':round(_time.time()-t0,3),
            'updated_at':datetime.now(timezone.utc).isoformat(),
        }
'''

API_PATCH=r'''

@app.get('/api/v5/korea-momentum-ranks-v54')
async def v54_korea_momentum_ranks():
    return await asyncio.to_thread(korea.momentum_rank_snapshot_v54)
'''

def main():
    s=KOREA.read_text()
    if 'def momentum_rank_snapshot_v54' not in s:
        anchor='    def discover(self, limit=50):\n'
        if anchor not in s: raise SystemExit('PATCH_TARGET_NOT_FOUND: Korea discover')
        s=s.replace(anchor,PATCH+'\n'+anchor,1); KOREA.write_text(s)
    a=API.read_text()
    if '/api/v5/korea-momentum-ranks-v54' not in a:
        anchor="app=FastAPI(title='DAY TRADER LIVE API',version='3.5',lifespan=lifespan)\n"
        if anchor not in a: raise SystemExit('PATCH_TARGET_NOT_FOUND: FastAPI app')
        a=a.replace(anchor,anchor+API_PATCH+'\n',1); API.write_text(a)
    print('FINDER_KOREA_GLOBAL_RANK_FIX_V54_OK')

if __name__=='__main__': main()
