from pathlib import Path

API=Path('live_server/api.py')

PATCH=r'''

# ===== MARKET GATE V2 PROBE =====
def _mg_probe_post(api_id, path, bodies):
    out=[]
    for body in bodies:
        try:
            r=requests.post(
                s.rest_base+path,
                headers=k.headers(api_id),
                json=body,
                timeout=20,
            )
            try: d=r.json()
            except Exception: d={'_text':r.text[:1000]}
            out.append({
                'body':body,
                'http_status':r.status_code,
                'return_code':d.get('return_code') if isinstance(d,dict) else None,
                'return_msg':d.get('return_msg') if isinstance(d,dict) else None,
                'keys':list(d.keys())[:40] if isinstance(d,dict) else [],
                'sample':{kk:(vv[:2] if isinstance(vv,list) else vv) for kk,vv in list(d.items())[:20]} if isinstance(d,dict) else d,
            })
        except Exception as e:
            out.append({'body':body,'error':str(e)[:300]})
    return out

@app.get('/api/v5/market-gate-probe/KOREA')
async def v5_market_gate_probe_korea():
    def _run():
        # Use multiple conservative body candidates because Kiwoom field names differ by TR.
        flow_bodies=[
            {'mrkt_tp':'001','amt_qty_tp':'1','base_dt_tp':'0','stex_tp':'3'},
            {'mrkt_tp':'001','amt_qty_tp':'1','stex_tp':'3'},
            {'mrkt_tp':'001','stex_tp':'3'},
            {'mrkt_tp':'101','amt_qty_tp':'1','base_dt_tp':'0','stex_tp':'3'},
            {'mrkt_tp':'101','amt_qty_tp':'1','stex_tp':'3'},
            {'mrkt_tp':'101','stex_tp':'3'},
        ]
        index_bodies=[
            {'mrkt_tp':'001','inds_cd':'001'},
            {'mrkt_tp':'001','inds_cd':'101'},
            {'mrkt_tp':'001','sector_cd':'001'},
            {'mrkt_tp':'101','inds_cd':'101'},
            {'mrkt_tp':'101','sector_cd':'101'},
        ]
        return {
            'ok':True,
            'version':'MARKET_GATE_V2_PROBE',
            'ka10051':_mg_probe_post('ka10051','/api/dostk/sect',flow_bodies),
            'ka20009':_mg_probe_post('ka20009','/api/dostk/sect',index_bodies),
            'note':'Diagnostic only. No Market Gate scoring change yet.'
        }
    return await asyncio.to_thread(_run)
'''

def main():
    a=API.read_text()
    if 'MARKET GATE V2 PROBE' not in a:
        anchor="app=FastAPI(title='DAY TRADER LIVE API',version='3.5',lifespan=lifespan)\n"
        if anchor not in a:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: FastAPI app')
        a=a.replace(anchor,anchor+PATCH+'\n',1)
        API.write_text(a)
    print('MARKET_GATE_V2_PROBE_PATCH_OK')

if __name__=='__main__': main()
