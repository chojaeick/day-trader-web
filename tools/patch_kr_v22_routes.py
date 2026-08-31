from pathlib import Path
import re

p = Path('/home/ubuntu/day-trader-api/live_server/api.py')
s = p.read_text()

helper = r'''

def _v5_kr_market_gate_impl():
    kst=datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Seoul'))
    mins=kst.hour*60+kst.minute
    regular=bool(kst.weekday()<5 and 540<=mins<930)
    pulse=korea.refresh_intraday_pulse(top_n=10,force_probe=regular)
    status=str(pulse.get('status') or 'UNKNOWN')
    return {
        'ok':True,'version':'MARKET_GATE_V3_KR_RUNTIME','market':'KOREA',
        'kst_now':kst.isoformat(),'regular_open':regular,
        'gate_open':bool(regular and status=='LIVE'),'pulse_status':status,
        'pulse_updated_at':pulse.get('updated_at'),
        'candidate_count':len(pulse.get('rows') or []),
        'order_placement':False,
        'reason':'OPEN_LIVE' if regular and status=='LIVE' else ('MARKET_CLOSED' if not regular else 'PULSE_NOT_LIVE')
    }


def _v5_kr_daytrade_entry_impl(limit=10,eval_limit=5,max_pages=1):
    from live_server.engine5_v22_live_kr import evaluate_entry as _v22_kr_entry
    snap=v4.refresh_korea_tracker(korea)
    source=list(snap.get('rows') or [])
    lim=max(1,int(limit)); evlim=max(0,int(eval_limit))
    rows=[]; evaluated=0; ready=0
    for i,row in enumerate(source[:lim]):
        x=dict(row)
        if i<evlim:
            try:
                decision=_v22_kr_entry(x)
            except Exception as e:
                decision={'engine':'ENGINE5_V22_KR_LIVE','symbol':x.get('symbol'),'enter':False,'reason':'EVALUATION_ERROR','error':str(e)[:300]}
            evaluated+=1
        else:
            decision={'engine':'ENGINE5_V22_KR_LIVE','symbol':x.get('symbol'),'enter':False,'reason':'NOT_EVALUATED_LIMIT'}
        x['v22_entry']=decision
        x['entry_ready']=bool(decision.get('enter'))
        if x['entry_ready']:
            ready+=1
        rows.append(x)
    return {
        'ok':True,'version':'DAYTRADE_ENTRY_V22_KR_RUNTIME','market':'KOREA',
        'market_gate':_v5_kr_market_gate_impl(),'candidate_count':len(source),
        'evaluated_count':evaluated,'entry_candidate_count':ready,'ready_count':ready,
        'rows':rows,'signal_only':True,'order_placement':False
    }
'''

if '_v5_kr_daytrade_entry_impl' not in s:
    marker = "@app.get('/api/v5/daytrade-entry/KOREA')"
    if marker not in s:
        raise SystemExit('daytrade route marker not found')
    s = s.replace(marker, helper + '\n' + marker, 1)

repls = [
    (r"return\s+await\s+asyncio\.to_thread\(korea\.daytrade_entry_v12,\s*limit,\s*eval_limit,\s*max_pages\)",
     "return await asyncio.to_thread(_v5_kr_daytrade_entry_impl,limit,eval_limit,max_pages)"),
    (r"return\s+await\s+asyncio\.to_thread\(korea\.market_gate_v21\)",
     "return await asyncio.to_thread(_v5_kr_market_gate_impl)"),
    (r"result\s*=\s*await\s+asyncio\.to_thread\(korea\.daytrade_entry_v12,\s*10,\s*5,\s*1\)",
     "result=await asyncio.to_thread(_v5_kr_daytrade_entry_impl,10,5,1)"),
]

counts=[]
for pat, rep in repls:
    s, n = re.subn(pat, rep, s, count=1)
    counts.append(n)

if counts != [1,1,1]:
    raise SystemExit(f'unexpected replacement counts: {counts}')

p.write_text(s)
print('PATCH_OK', counts)
