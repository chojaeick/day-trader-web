from pathlib import Path

API=Path('live_server/api.py')

PATCH=r'''

# ===== V46 KIWOOOM SAVED CONDITION SEARCH (KOREA) =====
async def _v46_condition_ws_request(seq=None):
    import json as _json
    import asyncio as _asyncio
    import websockets as _websockets

    token=await _asyncio.to_thread(k.get_token)
    async with _websockets.connect(k.s.ws_url,ping_interval=None,close_timeout=5) as ws:
        await ws.send(_json.dumps({'trnm':'LOGIN','token':token}))
        while True:
            d=_json.loads(await _asyncio.wait_for(ws.recv(),timeout=20))
            if d.get('trnm')=='PING':
                await ws.send(_json.dumps(d)); continue
            if d.get('trnm')=='LOGIN':
                if d.get('return_code')!=0:
                    raise RuntimeError(f"LOGIN failed: {d}")
                break

        # Official API requires the saved condition list to be loaded first.
        await ws.send(_json.dumps({'trnm':'CNSRLST'}))
        while True:
            d=_json.loads(await _asyncio.wait_for(ws.recv(),timeout=20))
            if d.get('trnm')=='PING':
                await ws.send(_json.dumps(d)); continue
            if d.get('trnm')=='CNSRLST':
                if d.get('return_code')!=0:
                    raise RuntimeError(f"CNSRLST failed: {d}")
                raw=d.get('data') or []
                conditions=[]
                for x in raw:
                    if isinstance(x,(list,tuple)) and len(x)>=2:
                        conditions.append({'seq':str(x[0]),'name':str(x[1])})
                    elif isinstance(x,dict):
                        conditions.append({'seq':str(x.get('seq') or ''),'name':str(x.get('name') or '')})
                break

        if seq is None:
            return {'ok':True,'conditions':conditions,'count':len(conditions)}

        seq=str(seq)
        if seq not in {x['seq'] for x in conditions}:
            return {'ok':False,'reason':'CONDITION_SEQ_NOT_FOUND','seq':seq,'conditions':conditions}

        rows=[]; cont_yn='N'; next_key=''; pages=0
        while pages<20:
            req={'trnm':'CNSRREQ','seq':seq,'search_type':'0','stex_tp':'K','cont_yn':cont_yn,'next_key':next_key}
            await ws.send(_json.dumps(req))
            while True:
                d=_json.loads(await _asyncio.wait_for(ws.recv(),timeout=30))
                if d.get('trnm')=='PING':
                    await ws.send(_json.dumps(d)); continue
                if d.get('trnm')=='CNSRREQ':
                    break
            if d.get('return_code')!=0:
                raise RuntimeError(f"CNSRREQ failed: {d}")
            pages+=1
            for x in d.get('data') or []:
                if not isinstance(x,dict):
                    continue
                sym=str(x.get('9001') or x.get('stk_cd') or '').strip()
                if sym.startswith('A') and len(sym)>=7:
                    sym=sym[1:7]
                rows.append({
                    'symbol':sym,
                    'name':str(x.get('302') or x.get('stk_nm') or '').strip(),
                    'price':abs(_v5_num(x.get('10'))),
                    'change_pct':float(str(x.get('12') or '0').replace(',','').replace('+','') or 0),
                    'volume':abs(_v5_num(x.get('13'))),
                    'raw':x,
                })
            cont_yn=str(d.get('cont_yn') or 'N').upper()
            next_key=str(d.get('next_key') or '')
            if cont_yn!='Y' or not next_key:
                break

        # de-duplicate while preserving server order
        seen=set(); uniq=[]
        for r in rows:
            if not r['symbol'] or r['symbol'] in seen: continue
            seen.add(r['symbol']); uniq.append(r)
        name=next((x['name'] for x in conditions if x['seq']==seq),'')
        return {'ok':True,'seq':seq,'name':name,'count':len(uniq),'pages':pages,'rows':uniq}

@app.get('/api/v5/korea-condition-list')
async def v46_korea_condition_list():
    return await _v46_condition_ws_request(None)

@app.get('/api/v5/korea-condition-run/{seq}')
async def v46_korea_condition_run(seq:str):
    return await _v46_condition_ws_request(seq)
'''

def main():
    s=API.read_text()
    if '/api/v5/korea-condition-list' in s:
        print('FINDER_KOREA_CONDITION_API_V46_ALREADY_OK')
        return
    anchor="app=FastAPI(title='DAY TRADER LIVE API',version='3.5',lifespan=lifespan)\n"
    if anchor not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: app=FastAPI')
    s=s.replace(anchor,anchor+PATCH+'\n',1)
    API.write_text(s)
    print('FINDER_KOREA_CONDITION_API_V46_OK')

if __name__=='__main__':
    main()
