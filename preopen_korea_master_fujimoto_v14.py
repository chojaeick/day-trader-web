from pathlib import Path
import re

APP=Path('app_v5.py')
API=Path('live_server/api.py')


def replace_once(s,pat,repl,label,flags=re.S):
    m=re.search(pat,s,flags)
    if not m:
        raise SystemExit(f'PATCH_TARGET_NOT_FOUND: {label}')
    return s[:m.start()]+repl+s[m.end():]


def patch_api():
    s=API.read_text()
    if 'import requests' not in s.split('\n',30):
        s=s.replace('import os\n','import os\nimport requests\n',1)

    master_code=r'''# V5.14: full Korean security master for human-friendly name/code search.
# Kiwoom ka10099 returns market security lists including ETFs/ETNs.
_v5_kr_master_cache={'ts':0.0,'rows':[]}

def _v5_korea_master(force=False):
    now=time.time()
    cache=_v5_kr_master_cache
    if (not force) and cache.get('rows') and now-float(cache.get('ts') or 0)<21600:
        return cache['rows']
    merged={}
    # KOSPI / KOSDAQ / ETF / ETN
    for mrkt_tp in ('0','10','8','60'):
        try:
            r=requests.post(
                k.s.rest_base+'/api/dostk/stkinfo',
                headers=k.headers('ka10099'),
                json={'mrkt_tp':mrkt_tp},
                timeout=30,
            )
            d=r.json()
            if d.get('return_code') not in (None,0):
                continue
            raw=d.get('list') or d.get('result_list') or d.get('data') or []
            if isinstance(raw,dict):
                raw=list(raw.values())
            for x in raw:
                if not isinstance(x,dict):
                    continue
                sym=str(x.get('code') or x.get('stk_cd') or x.get('symbol') or '').strip().upper()
                name=str(x.get('name') or x.get('stk_nm') or '').strip()
                if '_' in sym:
                    sym=sym.split('_',1)[0]
                m=re.match(r'^([0-9A-Z]{6})',sym)
                sym=m.group(1) if m else sym
                if len(sym)!=6 or not re.fullmatch(r'[0-9A-Z]{6}',sym):
                    continue
                if sym not in merged or (not merged[sym].get('name') and name):
                    merged[sym]={'symbol':sym,'name':name or sym,'market_type':mrkt_tp}
        except Exception as e:
            logging.warning('V5 korea master %s failed: %s',mrkt_tp,e)
    rows=list(merged.values())
    if rows:
        cache['ts']=now; cache['rows']=rows
        try:
            meta=getattr(korea,'stock_meta',None)
            if isinstance(meta,dict):
                for row in rows:
                    meta.setdefault(row['symbol'],row)
        except Exception:
            pass
    return rows or cache.get('rows') or []

def _v5_korea_detail_name(symbol):
    sym=str(symbol or '').strip().upper()
    if not re.fullmatch(r'[0-9A-Z]{6}',sym):
        return ''
    try:
        r=requests.post(
            k.s.rest_base+'/api/dostk/stkinfo',
            headers=k.headers('ka10100'),
            json={'stk_cd':sym},
            timeout=15,
        )
        d=r.json()
        if d.get('return_code') in (None,0):
            return str(d.get('name') or d.get('stk_nm') or '').strip()
    except Exception:
        pass
    return ''

'''
    if '_v5_kr_master_cache=' not in s:
        anchor="@app.get('/api/v5/korea-symbol-search')"
        if anchor not in s:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: korea search endpoint')
        s=s.replace(anchor,master_code+anchor,1)

    search_ep=r'''@app.get('/api/v5/korea-symbol-search')
async def v5_korea_symbol_search(q:str,limit:int=12):
    q=str(q or '').strip().upper()
    if not q:
        return {'ok':True,'rows':[]}
    lim=max(1,min(int(limit),30))
    rows=[]; seen=set()
    master=await asyncio.to_thread(_v5_korea_master,False)
    # Exact code first.
    if re.fullmatch(r'[0-9A-Z]{6}',q):
        for r in master:
            if str(r.get('symbol') or '').upper()==q:
                rows.append({'symbol':q,'name':r.get('name') or q}); seen.add(q); break
        if q not in seen:
            name=await asyncio.to_thread(_v5_korea_detail_name,q)
            try:
                snap=await asyncio.to_thread(_v5_korea_quote_snapshot,q)
            except Exception:
                snap={}
            if name or snap.get('valid'):
                rows.append({'symbol':q,'name':name or snap.get('name') or q}); seen.add(q)
    # Human name / partial code search across the full Kiwoom master.
    for r in master:
        sym=str(r.get('symbol') or '').upper()
        name=str(r.get('name') or '')
        if not sym or sym in seen:
            continue
        if q in sym or q in name.upper():
            rows.append({'symbol':sym,'name':name or sym}); seen.add(sym)
            if len(rows)>=lim:
                break
    return {'ok':True,'rows':rows[:lim],'master_count':len(master)}

'''
    s=replace_once(
        s,
        r"@app\.get\('/api/v5/korea-symbol-search'\).*?(?=@app\.get\('/api/v5/symbol-validate/\{market\}/\{query\}'\))",
        search_ep,
        'replace korea symbol search',
    )
    API.write_text(s)


def patch_app():
    s=APP.read_text()
    # Surface the actual tested Fujimoto v0.1 result. It remains informational
    # and is NOT allowed to vote in the aggregate decision until retuned/revalidated.
    old="{'엔진':'Fujimoto','상태':'대기','점수':'-','판단':'연결 예정','위험':'-'}"
    new="{'엔진':'Fujimoto','상태':'검증완료','점수':'PF 0.384','판단':'비채택 · v0.1','위험':'REJECT'}"
    if old in s:
        s=s.replace(old,new,1)
    else:
        s=re.sub(
            r"\{'엔진':'Fujimoto'.*?\}",
            new,
            s,
            count=1,
        )
    # Add a clear note so REJECT cannot be mistaken for a live bearish vote.
    marker="def engine_matrix(live):\n"
    if marker in s and 'FUJIMOTO_V01_REJECT' not in s:
        s=s.replace(marker,"# FUJIMOTO_V01_REJECT: 369 trades @ cost 0.20%, WR 20.33%, PF 0.384, NET -73.402%. Informational only; excluded from aggregate vote.\n"+marker,1)
    APP.write_text(s)


def main():
    patch_api(); patch_app()
    print('PREOPEN_KOREA_MASTER_FUJIMOTO_V14_OK')

if __name__=='__main__':
    main()
